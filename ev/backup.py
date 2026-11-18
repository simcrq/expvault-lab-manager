"""内容寻址（CAS）快照备份。

设计要点：
  1. 每个文件按 sha256 存一份内容 -> 相同内容只存一次，天然增量 + 自动去重；
  2. 每次备份写一个「快照清单」，记录当时所有文件的路径 -> 哈希；
  3. 恢复 = 按清单把内容取回来，任意一个快照都能完整还原；
  4. 快照之间互相独立，删中间某个快照不会破坏其它快照的完整性；
  5. 备份盘可以是移动硬盘、网盘同步目录、NAS，插上就增量同步，拔走就是离线副本。
"""
from __future__ import annotations

import os
import shutil
import socket
import uuid
from datetime import datetime
from pathlib import Path

from .config import Ctx
from .util import (
    FileLock,
    ensure_dir,
    human_size,
    is_ascii,
    now_iso,
    posix,
    read_json,
    rel_posix,
    sha256_file,
    table,
    truncate,
    write_json,
)

TARGET_MARK = "expvault-target.json"
LATEST = "latest.json"
SNAP_DIR = "snapshots"
OBJ_DIR = "objects"
SKIP_TOP = {"备份", "报告", "索引", ".git", "__pycache__", ".expvault"}


# --------------------------------------------------------------------------
# 目标盘
# --------------------------------------------------------------------------
def open_target(ctx: Ctx, path: Path, *, create: bool = True, name: str = "") -> dict:
    path = Path(path)
    if not path.exists():
        if not create:
            raise FileNotFoundError(f"备份目标不存在：{path}")
        ensure_dir(path)
        write_json(path / TARGET_MARK, {
            "schema": 1,
            "target_id": uuid.uuid4().hex[:12],
            "name": name or path.name,
            "created_at": now_iso(),
        })
        ensure_dir(path / OBJ_DIR)
        ensure_dir(path / SNAP_DIR)
    info = read_json(path / TARGET_MARK, None)
    if info is None:
        if not create:
            raise ValueError(f"{path} 不是 expvault 备份目录（缺少 {TARGET_MARK}）")
        info = {
            "schema": 1,
            "target_id": uuid.uuid4().hex[:12],
            "name": name or path.name,
            "created_at": now_iso(),
        }
        write_json(path / TARGET_MARK, info)
    ensure_dir(path / OBJ_DIR)
    ensure_dir(path / SNAP_DIR)
    info["path"] = str(path)
    return info


def object_path(target: Path, digest: str) -> Path:
    return target / OBJ_DIR / digest[:2] / digest[2:4] / digest


def path_available(p: Path) -> bool:
    """目标路径是否可用：目录不存在但所在盘在，也算可用（首次备份会创建）。"""
    p = Path(p)
    if p.exists():
        return True
    cur = p
    while cur.parent != cur:
        cur = cur.parent
        if cur.exists():
            return True
    return False


def list_snapshots(target: Path) -> list[dict]:
    d = target / SNAP_DIR
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        if p.is_symlink() or not p.is_file():
            continue
        m = read_json(p, None)
        if isinstance(m, dict) and isinstance(m.get("id"), str):
            m["_file"] = p.name
            out.append(m)
    out.sort(key=lambda x: x.get("id", ""), reverse=True)
    return out


def get_snapshot(target: Path, snap_id: str) -> dict:
    if snap_id in ("latest", "last", ""):
        latest = read_json(target / LATEST, {}) or {}
        snap_id = latest.get("snapshot", "")
    if not snap_id:
        raise ValueError("目标盘上还没有任何快照")
    p = target / SNAP_DIR / f"{snap_id}.json"
    m = read_json(p, None)
    if not isinstance(m, dict):
        raise ValueError(f"找不到快照：{snap_id}")
    m["_file"] = p.name
    return m


# --------------------------------------------------------------------------
# 收集待备份文件
# --------------------------------------------------------------------------
def collect_sources(ctx: Ctx) -> list[tuple[str, Path]]:
    """返回 [(相对库根的 posix 路径, 绝对路径)]。"""
    items: list[tuple[str, Path]] = []
    cfg = ctx.root / "config.json"
    if cfg.exists():
        items.append(("config.json", cfg))
    problems = ctx.root / "问题记录.jsonl"
    if problems.exists():
        items.append(("问题记录.jsonl", problems))

    data = ctx.data_dir
    if data.exists():
        for dirpath, dirnames, filenames in os.walk(data):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                p = Path(dirpath) / fn
                if fn.startswith("~$") or fn in ("Thumbs.db", "desktop.ini"):
                    continue
                items.append((rel_posix(p, ctx.root), p))
    return items


# --------------------------------------------------------------------------
# 备份
# --------------------------------------------------------------------------
def backup(
    ctx: Ctx,
    target_path: Path,
    *,
    name: str = "",
    rehash: bool = False,
    prune: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:
    target_path = Path(target_path)
    info = open_target(ctx, target_path, name=name)
    name = info.get("name") or target_path.name

    ensure_dir(target_path)
    snapshots = list_snapshots(target_path)
    prev = snapshots[0] if snapshots else None

    sources = collect_sources(ctx)

    # 库内「相对库根的路径」-> (size, mtime, sha256)，用于跳过重复哈希
    cache: dict[str, tuple[int, float, str]] = {}
    for rec in ctx.db.all_experiments():
        for f in ctx.db.files_of(rec["id"], include_missing=False):
            if f["sha256"]:
                cache[posix(f"{rec['folder']}/{f['relpath']}")] = (
                    int(f["size"]), float(f["mtime"]), f["sha256"]
                )

    files_meta = []
    new_objects = 0
    new_bytes = 0
    total_bytes = 0
    errors = []

    for rel, abspath in sources:
        try:
            st = abspath.stat()
        except OSError as e:
            errors.append(f"{rel}: {e}")
            continue
        size, mtime = int(st.st_size), float(st.st_mtime)
        total_bytes += size
        digest = ""
        if not rehash:
            hit = cache.get(posix(rel))
            if hit and hit[0] == size and abs(hit[1] - mtime) < 2:
                digest = hit[2]
        if not digest:
            try:
                digest = sha256_file(abspath)
            except OSError as e:
                errors.append(f"{rel}: 读取失败 {e}")
                continue
        files_meta.append({"p": posix(rel), "h": digest, "s": size})

        dest = object_path(target_path, digest)
        if dest.exists():
            continue
        new_objects += 1
        new_bytes += size
        if verbose:
            print(f"  + {rel}  {human_size(size)}")
        if dry_run:
            continue
        ensure_dir(dest.parent)
        tmp = dest.with_name(dest.name + ".part")
        try:
            shutil.copy2(abspath, tmp)
            os.replace(tmp, dest)
        except OSError as e:
            errors.append(f"{rel}: 写入失败 {e}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    stats = {
        "files": len(files_meta),
        "bytes": total_bytes,
        "new_objects": new_objects,
        "new_bytes": new_bytes,
        "errors": errors,
    }
    if dry_run:
        stats["dry_run"] = True
        return {
            "target": name, "path": str(target_path), "snapshot": "(dry-run)",
            "stats": stats, "bytes_skipped": total_bytes - new_bytes,
        }

    snap_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    existing = {s.get("id") for s in snapshots}
    base = snap_id
    n = 2
    while snap_id in existing:
        snap_id = f"{base}-{n}"
        n += 1

    manifest = {
        "schema": 1,
        "id": snap_id,
        "created_at": now_iso(),
        "host": socket.gethostname(),
        "user": os.environ.get("USERNAME", ""),
        "root": str(ctx.root),
        "kind": "full" if not prev else "incremental",
        "parent": prev.get("id") if prev else None,
        "stats": {
            "files": len(files_meta),
            "bytes": total_bytes,
            "new_objects": new_objects,
            "new_bytes": new_bytes,
        },
        "files": files_meta,
    }
    write_json(target_path / SNAP_DIR / f"{snap_id}.json", manifest, indent=None)
    write_json(target_path / LATEST, {
        "snapshot": snap_id,
        "created_at": manifest["created_at"],
        "files": len(files_meta),
        "bytes": total_bytes,
    })
    info["last_backup"] = manifest["created_at"]
    write_json(target_path / TARGET_MARK, {k: v for k, v in info.items() if k != "path"})

    ctx.db.record_backup({
        "target": name,
        "snapshot": snap_id,
        "files": len(files_meta),
        "bytes_total": total_bytes,
        "bytes_new": new_bytes,
        "objects_new": new_objects,
        "ok": not errors,
    })
    ctx.db.log("backup", "", f"{name} -> {snap_id}")

    result = {
        "target": name,
        "path": str(target_path),
        "snapshot": snap_id,
        "stats": stats,
        "bytes_skipped": total_bytes - new_bytes,
    }
    if prune:
        result["prune"] = prune_target(ctx, target_path)
    return result


# --------------------------------------------------------------------------
# 恢复
# --------------------------------------------------------------------------
def restore(
    ctx: Ctx,
    target_path: Path,
    snap_id: str,
    dest: Path,
    *,
    only: str = "",
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict:
    target_path = Path(target_path)
    snap = get_snapshot(target_path, snap_id)
    dest = Path(dest)
    out = {"restored": 0, "skipped": 0, "missing_objects": [], "bytes": 0, "snapshot": snap["id"]}

    for f in snap.get("files", []):
        rel = f["p"]
        if only:
            if only.lower() not in rel.lower():
                continue
        src = object_path(target_path, f["h"])
        dst = dest / rel
        if dst.exists() and not overwrite:
            out["skipped"] += 1
            continue
        if not src.exists():
            out["missing_objects"].append(rel)
            continue
        if not dry_run:
            ensure_dir(dst.parent)
            shutil.copy2(src, dst)
        out["restored"] += 1
        out["bytes"] += int(f.get("s", 0))
    if out["missing_objects"]:
        out["ok"] = False
    else:
        out["ok"] = True
    return out


# --------------------------------------------------------------------------
# 校验目标盘
# --------------------------------------------------------------------------
def verify_target(ctx: Ctx, target_path: Path, *, deep: bool = False, snap_id: str = "") -> dict:
    target_path = Path(target_path)
    snap = get_snapshot(target_path, snap_id)
    out = {"snapshot": snap["id"], "files": 0, "ok": 0, "missing": [], "corrupt": [], "bytes": 0}
    checked: set[str] = set()
    for f in snap.get("files", []):
        out["files"] += 1
        out["bytes"] += int(f.get("s", 0))
        h = f["h"]
        p = object_path(target_path, h)
        if not p.exists():
            out["missing"].append(f["p"])
            continue
        if deep and h not in checked:
            checked.add(h)
            if sha256_file(p) != h:
                out["corrupt"].append(f["p"])
                continue
        out["ok"] += 1
    out["healthy"] = not out["missing"] and not out["corrupt"]
    return out


# --------------------------------------------------------------------------
# 清理
# --------------------------------------------------------------------------
def prune_target(ctx: Ctx, target_path: Path, *, keep_recent: int | None = None,
                 keep_monthly: int | None = None, dry_run: bool = False) -> dict:
    target_path = Path(target_path)
    cfg = ctx.cfg["backup"]
    keep_recent = int(keep_recent if keep_recent is not None else cfg.get("keep_recent", 15))
    keep_monthly = int(keep_monthly if keep_monthly is not None else cfg.get("keep_monthly", 6))

    snaps = list_snapshots(target_path)
    keep: dict[str, dict] = {}
    for s in snaps[:keep_recent]:
        keep[s["id"]] = s
    # 每月保留最后一份
    by_month: dict[str, dict] = {}
    for s in sorted(snaps, key=lambda x: x.get("id", "")):
        month = (s.get("created_at") or "")[:7]
        by_month[month] = s
    for month in sorted(by_month)[-keep_monthly:]:
        keep[by_month[month]["id"]] = by_month[month]

    drop = [s for s in snaps if s["id"] not in keep]
    if not dry_run:
        for s in drop:
            try:
                (target_path / SNAP_DIR / s["_file"]).unlink()
            except OSError:
                pass
        referenced = set()
        for s in list_snapshots(target_path):
            referenced.update(f["h"] for f in s.get("files", []))
        removed, freed = 0, 0
        obj_root = target_path / OBJ_DIR
        if obj_root.exists():
            for dirpath, _dirnames, filenames in os.walk(obj_root):
                for fn in filenames:
                    if fn.endswith(".part"):
                        try:
                            Path(dirpath, fn).unlink()
                        except OSError:
                            pass
                        continue
                    if fn not in referenced:
                        p = Path(dirpath) / fn
                        try:
                            freed += p.stat().st_size
                            p.unlink()
                            removed += 1
                        except OSError:
                            pass
            # 清掉空目录
            for dirpath, dirnames, filenames in os.walk(obj_root, topdown=False):
                if not dirnames and not filenames:
                    try:
                        Path(dirpath).rmdir()
                    except OSError:
                        pass
    else:
        removed, freed = 0, 0

    return {"dropped": [s["id"] for s in drop], "kept": len(keep),
            "objects_removed": removed, "bytes_freed": freed}


# --------------------------------------------------------------------------
# 目标盘概览
# --------------------------------------------------------------------------
def target_summary(target_path: Path) -> dict:
    target_path = Path(target_path)
    snaps = list_snapshots(target_path)
    info = read_json(target_path / TARGET_MARK, {}) or {}
    size = 0
    count = 0
    obj_root = target_path / OBJ_DIR
    if obj_root.exists():
        for dirpath, _d, filenames in os.walk(obj_root):
            for fn in filenames:
                try:
                    size += (Path(dirpath) / fn).stat().st_size
                    count += 1
                except OSError:
                    pass
    latest = snaps[0] if snaps else None
    return {
        "path": str(target_path),
        "name": info.get("name") or target_path.name,
        "exists": target_path.exists(),
        "snapshots": len(snaps),
        "objects": count,
        "bytes": size,
        "latest": latest.get("id") if latest else "",
        "latest_at": latest.get("created_at") if latest else "",
    }


def format_snapshots(ctx: Ctx, target_path: Path, limit: int = 20) -> str:
    snaps = list_snapshots(target_path)[:limit]
    kind_cn = {"full": "全量", "incremental": "增量"}
    rows = []
    for s in snaps:
        st = s.get("stats", {})
        rows.append([
            s.get("id", ""),
            (s.get("created_at") or "")[:16].replace("T", " "),
            kind_cn.get(s.get("kind", ""), s.get("kind", "")),
            str(st.get("files", 0)),
            human_size(st.get("bytes", 0)),
            human_size(st.get("new_bytes", 0)),
        ])
    return table(rows, ["快照", "时间", "类型", "文件数", "总大小", "新增"],
                 ["left", "left", "center", "right", "right", "right"])
