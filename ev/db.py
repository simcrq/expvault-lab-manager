"""SQLite 索引层。

数据库只是「加速检索的缓存」，真正的权威数据在每个实验目录的 meta.json 里。
因此：数据库坏了可以 `rebuild` 重建，实验目录本身永远自解释。
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .util import ensure_dir, now_iso

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS experiments (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    date        TEXT NOT NULL DEFAULT '',
    project     TEXT DEFAULT '',
    operator    TEXT DEFAULT '',
    device      TEXT DEFAULT '',
    batch       TEXT DEFAULT '',
    category    TEXT DEFAULT '',
    status      TEXT DEFAULT 'running',
    tags        TEXT DEFAULT '',
    folder      TEXT NOT NULL,
    notes       TEXT DEFAULT '',
    params      TEXT DEFAULT '{}',
    measurement_groups TEXT DEFAULT '[]',
    process_conditions TEXT DEFAULT '[]',
    experiment_groups TEXT DEFAULT '[]',
    created_at  TEXT DEFAULT '',
    updated_at  TEXT DEFAULT '',
    archived    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    exp_id        TEXT NOT NULL,
    relpath       TEXT NOT NULL,
    size          INTEGER DEFAULT 0,
    mtime         REAL DEFAULT 0,
    sha256        TEXT DEFAULT '',
    role          TEXT DEFAULT 'other',
    registered_at TEXT DEFAULT '',
    last_seen     TEXT DEFAULT '',
    missing       INTEGER DEFAULT 0,
    is_link       INTEGER DEFAULT 0,
    tags          TEXT DEFAULT '[]',
    UNIQUE(exp_id, relpath)
);

CREATE INDEX IF NOT EXISTS idx_files_exp  ON files(exp_id);
CREATE INDEX IF NOT EXISTS idx_files_hash ON files(sha256);
CREATE INDEX IF NOT EXISTS idx_exp_date   ON experiments(date);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT,
    exp_id     TEXT,
    action     TEXT,
    detail     TEXT,
    actor      TEXT DEFAULT '',
    actor_type TEXT DEFAULT '',
    source     TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS backups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT,
    target      TEXT,
    snapshot    TEXT,
    files       INTEGER DEFAULT 0,
    bytes_total INTEGER DEFAULT 0,
    bytes_new   INTEGER DEFAULT 0,
    objects_new INTEGER DEFAULT 0,
    ok          INTEGER DEFAULT 1
);
"""


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._transaction_depth = 0
        ensure_dir(self.path.parent)
        self.conn = sqlite3.connect(str(self.path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.commit()

    def _migrate(self) -> None:
        """旧库补字段（只加列，不动数据；出错就算了）。"""
        try:
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(files)").fetchall()}
            if "is_link" not in cols:
                self.conn.execute("ALTER TABLE files ADD COLUMN is_link INTEGER DEFAULT 0")
            if "tags" not in cols:
                self.conn.execute("ALTER TABLE files ADD COLUMN tags TEXT DEFAULT '[]'")
            exp_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(experiments)").fetchall()}
            if "measurement_groups" not in exp_cols:
                self.conn.execute("ALTER TABLE experiments ADD COLUMN measurement_groups TEXT DEFAULT '[]'")
            if "process_conditions" not in exp_cols:
                self.conn.execute("ALTER TABLE experiments ADD COLUMN process_conditions TEXT DEFAULT '[]'")
            if "experiment_groups" not in exp_cols:
                self.conn.execute("ALTER TABLE experiments ADD COLUMN experiment_groups TEXT DEFAULT '[]'")
            ev_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(events)").fetchall()}
            if "actor" not in ev_cols:
                self.conn.execute("ALTER TABLE events ADD COLUMN actor TEXT DEFAULT ''")
            if "actor_type" not in ev_cols:
                self.conn.execute("ALTER TABLE events ADD COLUMN actor_type TEXT DEFAULT ''")
            if "source" not in ev_cols:
                self.conn.execute("ALTER TABLE events ADD COLUMN source TEXT DEFAULT ''")
        except Exception:
            pass

    # ------------------------------------------------------------------
    def close(self) -> None:
        try:
            self.commit()
            self.conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def integrity_ok(self) -> tuple[bool, str]:
        try:
            row = self.conn.execute("PRAGMA integrity_check").fetchone()
            msg = row[0] if row else "unknown"
            return msg.lower() == "ok", msg
        except Exception as e:  # pragma: no cover
            return False, str(e)

    def count(self, table: str) -> int:
        try:
            return int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # 实验
    # ------------------------------------------------------------------
    def upsert_experiment(self, rec: dict) -> None:
        fields = [
            "id", "title", "date", "project", "operator", "device", "batch",
            "category", "status", "tags", "folder", "notes", "params", "measurement_groups", "process_conditions", "experiment_groups",
            "created_at", "updated_at", "archived",
        ]
        vals = []
        for f in fields:
            v = rec.get(f, "")
            if f == "experiment_groups":
                v = json.dumps(rec.get("groups") or [],ensure_ascii=False)
            if f == "params" and not isinstance(v, str):
                v = json.dumps(v or {}, ensure_ascii=False)
            if f in ("measurement_groups", "process_conditions"):
                v = json.dumps(v or [], ensure_ascii=False) if not isinstance(v, str) or not v else v
            if f == "tags" and isinstance(v, (list, tuple)):
                v = ",".join(str(t).strip() for t in v if str(t).strip())
            if f == "archived":
                v = int(v or 0)
            if v is None:
                v = ""
            vals.append(v)
        self.conn.execute(
            f"INSERT INTO experiments ({','.join(fields)}) VALUES ({','.join('?' * len(fields))}) "
            "ON CONFLICT(id) DO UPDATE SET "
            + ",".join(f"{f}=excluded.{f}" for f in fields if f != "id"),
            vals,
        )
        self.commit()

    def get_experiment(self, exp_id: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM experiments WHERE id=?", (exp_id,)).fetchone()
        return self._decorate(_row(r))

    def find_experiment(self, key: str) -> dict | None:
        """支持完整 ID、ID 前缀、目录名片段。"""
        key = (key or "").strip()
        if not key:
            return None
        r = self.conn.execute("SELECT * FROM experiments WHERE id=?", (key,)).fetchone()
        if r:
            return self._decorate(_row(r))
        r = self.conn.execute(
            "SELECT * FROM experiments WHERE id LIKE ? OR folder LIKE ? ORDER BY id LIMIT 1",
            (key + "%", "%" + key + "%"),
        ).fetchone()
        return self._decorate(_row(r))

    def _decorate(self, rec: dict | None) -> dict | None:
        if not rec:
            return None
        try:
            rec["params"] = json.loads(rec.get("params") or "{}")
        except Exception:
            rec["params"] = {}
        try:
            rec["measurement_groups"] = json.loads(rec.get("measurement_groups") or "[]")
        except (TypeError, ValueError):
            rec["measurement_groups"] = []
        try:
            rec["process_conditions"] = json.loads(rec.get("process_conditions") or "[]")
        except (TypeError, ValueError):
            rec["process_conditions"] = []
        try:
            from .groups import normalize_names
            rec["groups"] = normalize_names(json.loads(rec.pop("experiment_groups", "[]") or "[]"), limit=None)
        except (TypeError,ValueError):
            rec["groups"]=[]
        rec["tag_list"] = split_tags(rec.get("tags", ""))
        stats = self.conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(size),0) b, "
            "       COALESCE(SUM(is_link),0) nl, "
            "       COALESCE(SUM(CASE WHEN is_link=1 THEN size ELSE 0 END),0) bl "
            "FROM files WHERE exp_id=? AND missing=0",
            (rec["id"],),
        ).fetchone()
        rec["file_count"] = int(stats["n"])
        rec["total_bytes"] = int(stats["b"])
        rec["link_count"] = int(stats["nl"])
        rec["link_bytes"] = int(stats["bl"])
        return rec

    def list_experiments(
        self,
        *,
        project: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        since: str | None = None,
        until: str | None = None,
        grep: str | None = None,
        include_archived: bool = False,
        limit: int | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM experiments WHERE 1=1"
        args: list[Any] = []
        if not include_archived:
            sql += " AND archived=0"
        if project:
            sql += " AND project LIKE ?"
            args.append(f"%{project}%")
        if status:
            sql += " AND status=?"
            args.append(status)
        if tag:
            sql += " AND (','||tags||',') LIKE ?"
            args.append(f"%,{tag},%")
        if since:
            sql += " AND date>=?"
            args.append(since)
        if until:
            sql += " AND date<=?"
            args.append(until)
        if grep:
            like = f"%{grep}%"
            sql += (
                " AND (title LIKE ? OR project LIKE ? OR device LIKE ? OR batch LIKE ?"
                " OR notes LIKE ? OR tags LIKE ? OR id LIKE ? OR folder LIKE ?)"
            )
            args.extend([like] * 8)
        sql += " ORDER BY date DESC, id DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self._decorate(_row(r)) for r in self.conn.execute(sql, args).fetchall()]

    def all_experiments(self) -> list[dict]:
        return [
            self._decorate(_row(r))
            for r in self.conn.execute("SELECT * FROM experiments ORDER BY date, id").fetchall()
        ]

    def delete_experiment(self, exp_id: str) -> None:
        self.conn.execute("DELETE FROM files WHERE exp_id=?", (exp_id,))
        self.conn.execute("DELETE FROM experiments WHERE id=?", (exp_id,))
        self.commit()

    def next_id(self, date_str: str, prefix: str = "E") -> str:
        compact = date_str.replace("-", "")
        stem = f"{prefix}{compact}-"
        row = self.conn.execute(
            "SELECT id FROM experiments WHERE id LIKE ? ORDER BY id DESC LIMIT 1",
            (stem + "%",),
        ).fetchone()
        seq = 1
        if row:
            try:
                seq = int(str(row["id"]).split("-")[-1]) + 1
            except Exception:
                seq = 1
        return f"{stem}{seq:03d}"

    # ------------------------------------------------------------------
    # 文件
    # ------------------------------------------------------------------
    def upsert_file(self, exp_id: str, rec: dict) -> None:
        self.conn.execute(
            """INSERT INTO files (exp_id, relpath, size, mtime, sha256, role,
                                   registered_at, last_seen, missing, is_link, tags)
               VALUES (?,?,?,?,?,?,?,?,0,?,?)
               ON CONFLICT(exp_id, relpath) DO UPDATE SET
                   size=excluded.size, mtime=excluded.mtime, sha256=excluded.sha256,
                   role=excluded.role, last_seen=excluded.last_seen, missing=0,
                   is_link=excluded.is_link,
                   tags=CASE WHEN ? THEN excluded.tags ELSE files.tags END""",
            (
                exp_id,
                rec["relpath"],
                int(rec.get("size", 0)),
                float(rec.get("mtime", 0)),
                rec.get("sha256", ""),
                rec.get("role", "other"),
                rec.get("registered_at") or now_iso(),
                now_iso(),
                int(rec.get("is_link", 0)),
                json.dumps(rec.get("tags", []), ensure_ascii=False),
                "tags" in rec,
            ),
        )

    def commit(self) -> None:
        if not self._transaction_depth:
            self.conn.commit()

    @contextlib.contextmanager
    def transaction(self):
        if self._transaction_depth:
            raise RuntimeError("Nested index transactions are not supported")
        self.conn.execute("BEGIN IMMEDIATE")
        self._transaction_depth += 1
        try:
            yield
        except BaseException:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()
        finally:
            self._transaction_depth -= 1

    def get_file(self, exp_id: str, relpath: str) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM files WHERE exp_id=? AND relpath=?", (exp_id, relpath)
        ).fetchone()
        return self._file_row(r)

    @staticmethod
    def _file_row(row) -> dict | None:
        rec = _row(row)
        if rec is not None:
            rec["tags"] = json.loads(rec.get("tags") or "[]")
        return rec

    def files_of(self, exp_id: str, include_missing: bool = True) -> list[dict]:
        sql = "SELECT * FROM files WHERE exp_id=?"
        if not include_missing:
            sql += " AND missing=0"
        sql += " ORDER BY relpath"
        return [self._file_row(r) for r in self.conn.execute(sql, (exp_id,)).fetchall()]

    def all_files(self, include_missing: bool = True) -> list[dict]:
        sql = "SELECT f.*, e.folder AS exp_folder, e.title AS exp_title, e.date AS exp_date "
        sql += "FROM files f LEFT JOIN experiments e ON e.id=f.exp_id"
        if not include_missing:
            sql += " WHERE f.missing=0"
        sql += " ORDER BY f.exp_id, f.relpath"
        return [self._file_row(r) for r in self.conn.execute(sql).fetchall()]

    def mark_missing(self, exp_id: str, known_relpaths: Iterable[str]) -> int:
        known = set(known_relpaths)
        n = 0
        for r in self.conn.execute(
            "SELECT relpath FROM files WHERE exp_id=? AND missing=0", (exp_id,)
        ).fetchall():
            if r["relpath"] not in known:
                self.conn.execute(
                    "UPDATE files SET missing=1 WHERE exp_id=? AND relpath=?",
                    (exp_id, r["relpath"]),
                )
                n += 1
        self.commit()
        return n

    def missing_files(self) -> list[dict]:
        return [
            _row(r)
            for r in self.conn.execute(
                "SELECT f.*, e.title AS exp_title FROM files f "
                "LEFT JOIN experiments e ON e.id=f.exp_id "
                "WHERE f.missing=1 ORDER BY f.exp_id, f.relpath"
            ).fetchall()
        ]

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def log(self, action: str, exp_id: str = "", detail: str = "", *,
            actor: str = "", actor_type: str = "", source: str = "") -> None:
        self.conn.execute(
            "INSERT INTO events (ts, exp_id, action, detail, actor, actor_type, source) VALUES (?,?,?,?,?,?,?)",
            (now_iso(), exp_id or "", action, detail or "", actor or "", actor_type or "", source or ""),
        )
        self.commit()

    def events(self, exp_id: str | None = None, limit: int = 50) -> list[dict]:
        if exp_id:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE exp_id=? ORDER BY id DESC LIMIT ?",
                (exp_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row(r) for r in rows]

    # ------------------------------------------------------------------
    # 备份记录
    # ------------------------------------------------------------------
    def record_backup(self, rec: dict) -> None:
        self.conn.execute(
            "INSERT INTO backups (ts, target, snapshot, files, bytes_total, bytes_new, objects_new, ok) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                rec.get("ts") or now_iso(),
                rec.get("target", ""),
                rec.get("snapshot", ""),
                int(rec.get("files", 0)),
                int(rec.get("bytes_total", 0)),
                int(rec.get("bytes_new", 0)),
                int(rec.get("objects_new", 0)),
                1 if rec.get("ok", True) else 0,
            ),
        )
        self.commit()

    def last_backup(self, target: str | None = None) -> dict | None:
        if target:
            r = self.conn.execute(
                "SELECT * FROM backups WHERE target=? ORDER BY id DESC LIMIT 1", (target,)
            ).fetchone()
        else:
            r = self.conn.execute(
                "SELECT * FROM backups ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return _row(r)

    def backups(self, limit: int = 50) -> list[dict]:
        return [
            _row(r)
            for r in self.conn.execute(
                "SELECT * FROM backups ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        ]


def split_tags(tags: str | list[str] | None) -> list[str]:
    if not tags:
        return []
    if isinstance(tags, list):
        return [t.strip() for t in tags if str(t).strip()]
    return [t.strip() for t in str(tags).replace("，", ",").split(",") if t.strip()]
