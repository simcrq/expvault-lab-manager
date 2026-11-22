"""Model Context Protocol (MCP) JSON-RPC 2.0 server for expvault.

Supports:
- Stdio transport (`python expvault.py mcp` / `python lab_manager.py --mcp`),
  auto-detecting both newline-delimited JSON-RPC and Content-Length framing.
- In-process JSON-RPC dispatch (`POST /api/mcp`) inside the local web server.

All write tools serialize through `harness.lock` and respect NAS read-only mode.
"""
from __future__ import annotations

import json
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from typing import Any

from filelock import FileLock

from . import (
    conditions as proc_conditions,
    config,
    experiment as ex,
    groups as grouping,
    importer,
    journal,
    measurements,
    nas,
    references,
    steps,
)
from .file_tags import normalize_tags
from .rich_text import search_text
from .ui import Console
from .util import sha256_file

SUPPORTED_PROTOCOLS = ("2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "expvault-mcp", "version": "1.9.7"}


def _inside(path: Path, parent: Path) -> bool:
    path, parent = Path(path).resolve(), Path(parent).resolve()
    return path == parent or parent in path.parents


def _safe_file_path(ctx, rec: dict, relative: str) -> Path:
    if (
        not isinstance(relative, str)
        or not relative
        or Path(relative).is_absolute()
        or PureWindowsPath(relative).drive
        or any(part in ("..", ".expvault") for part in relative.replace("\\", "/").split("/"))
    ):
        raise ValueError("文件路径无效")
    folder = ex.exp_dir(ctx, rec).resolve()
    path = folder / relative.replace("\\", "/")
    if path.resolve() == folder or not _inside(path, folder) or path.is_symlink():
        raise ValueError("文件路径越过实验边界")
    indexed = ctx.db.get_file(rec["id"], path.relative_to(folder).as_posix())
    if not path.is_file() or not indexed or indexed["missing"]:
        raise ValueError("文件不存在或尚未登记")
    return path


def _tool_spec(name: str, description: str, properties: dict, required: list[str] | None = None, *, read_only: bool = False) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": read_only,
            "destructiveHint": False,
        },
    }


TOOLS: list[dict] = [
    _tool_spec(
        "vault_overview",
        "获取当前实验库的统计概览、最近实验和最近操作记录。",
        {},
        read_only=True,
    ),
    _tool_spec(
        "experiments_list",
        "列出或筛选实验记录，支持关键词、状态、分组和分页。",
        {
            "q": {"type": "string", "description": "搜索关键词（匹配标题、样品、参数、工艺条件、测量、文件标签）"},
            "status": {"type": "string", "enum": ["planned", "running", "done", "aborted", "archived"]},
            "group": {"type": "string", "description": "按实验分组名称筛选"},
            "include_archived": {"type": "boolean", "description": "是否包含已归档实验"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "offset": {"type": "integer", "minimum": 0},
        },
        read_only=True,
    ),
    _tool_spec(
        "experiment_get",
        "读取单个实验的完整结构化元数据，包含基准参数、工艺条件矩阵、仪器对照测量、步骤、日志和 revision。",
        {"exp_id": {"type": "string", "description": "实验编号，如 E20261122-001"}},
        required=["exp_id"],
        read_only=True,
    ),
    _tool_spec(
        "experiments_search",
        "全局搜索实验记录和带标签的文件资料。",
        {"q": {"type": "string", "description": "搜索关键词"}},
        required=["q"],
        read_only=True,
    ),
    _tool_spec(
        "files_list",
        "列出指定实验中的已登记文件及其标签（tags）、大小、角色和 SHA256。",
        {
            "exp_id": {"type": "string"},
            "q": {"type": "string", "description": "按文件名或标签筛选"},
            "role": {"type": "string", "enum": ["raw", "processed", "figure", "note", "other"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "offset": {"type": "integer", "minimum": 0},
        },
        required=["exp_id"],
        read_only=True,
    ),
    _tool_spec(
        "file_read_text",
        "读取实验内的 UTF-8 文本数据文件（如 CSV / TXT / JSON / 日志，最多 200 KB）供分析。",
        {
            "exp_id": {"type": "string"},
            "path": {"type": "string", "description": "实验内相对路径，如 01_raw/IV.csv"},
        },
        required=["exp_id", "path"],
        read_only=True,
    ),
    _tool_spec(
        "conditions_list",
        "获取实验的工艺条件矩阵、自动识别的差异参数/公共参数/结果指标，以及 Markdown 对照表。",
        {
            "exp_id": {"type": "string"},
            "status": {"type": "string", "enum": list(proc_conditions.CONDITION_STATUSES)},
        },
        required=["exp_id"],
        read_only=True,
    ),
    _tool_spec(
        "conditions_export_table",
        "将指定实验的工艺条件矩阵导出为 Excel (.xlsx)、CSV (.csv) 或 Markdown (.md) 表格文件，并返回文件路径与 Markdown 内容。",
        {
            "exp_id": {"type": "string"},
            "format": {"type": "string", "enum": ["xlsx", "csv", "md", "markdown"]},
            "cond_ids": {"type": "array", "items": {"type": "string"}, "description": "可选：仅导出指定条件 ID 列表"},
        },
        required=["exp_id"],
        read_only=True,
    ),
    _tool_spec(
        "experiment_verify",
        "对指定实验执行全量 SHA256 文件完整性校验。",
        {"exp_id": {"type": "string"}},
        required=["exp_id"],
        read_only=True,
    ),
    _tool_spec(
        "experiment_create",
        "新建一项实验记录。",
        {
            "title": {"type": "string"},
            "date": {"type": "string", "description": "YYYY-MM-DD，默认当天"},
            "device": {"type": "string", "description": "器件或样品编号"},
            "batch": {"type": "string", "description": "工艺批次"},
            "project": {"type": "string"},
            "operator": {"type": "string"},
            "category": {"type": "string"},
            "status": {"type": "string", "enum": ["planned", "running", "done", "aborted"]},
            "tags": {"type": "array", "items": {"type": "string"}},
            "groups": {"type": "array", "items": {"type": "string"}},
            "params": {"type": "object", "description": "公共基准工艺参数键值对"},
            "purpose": {"type": "string"},
            "conditions": {"type": "string"},
            "notes": {"type": "string"},
        },
        required=["title"],
    ),
    _tool_spec(
        "experiment_update",
        "更新实验的标题、状态、基准参数、结果、结论、备注或分组等字段。",
        {
            "exp_id": {"type": "string"},
            "expected_revision": {"type": "string", "description": "可选：并发校验 revision"},
            "changes": {"type": "object", "description": "要更新的字段字典"},
        },
        required=["exp_id", "changes"],
    ),
    _tool_spec(
        "condition_save",
        "在指定实验内新增或更新一组工艺条件（参数、指标、状态、备注），并可关联已有实验文件或导入本地图片/数据文件。",
        {
            "exp_id": {"type": "string"},
            "expected_revision": {"type": "string"},
            "id": {"type": "string", "description": "更新已有条件时传入 32 位十六进制 id；留空为新增"},
            "name": {"type": "string", "description": "条件名称，如 350 °C · 20 min"},
            "sample": {"type": "string", "description": "样品子编号"},
            "status": {"type": "string", "enum": list(proc_conditions.CONDITION_STATUSES)},
            "params": {"type": "object", "description": "该条件的工艺参数键值对"},
            "metrics": {"type": "object", "description": "该条件的测试/结果指标键值对"},
            "note": {"type": "string", "description": "现场观察备注"},
            "existing_paths": {"type": "array", "items": {"type": "string"}, "description": "关联本实验已有文件相对路径"},
            "local_files": {"type": "array", "items": {"type": "string"}, "description": "从本机绝对路径复制并归档到此条件的图片或数据文件"},
        },
        required=["exp_id", "name"],
    ),
    _tool_spec(
        "conditions_batch_create",
        "在指定实验内批量创建多组工艺条件矩阵（单次 1–60 组）。",
        {
            "exp_id": {"type": "string"},
            "expected_revision": {"type": "string"},
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": 60,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "sample": {"type": "string"},
                        "status": {"type": "string", "enum": list(proc_conditions.CONDITION_STATUSES)},
                        "params": {"type": "object"},
                        "metrics": {"type": "object"},
                        "note": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
        },
        required=["exp_id", "items"],
    ),
    _tool_spec(
        "condition_update_status",
        "快速修改某组工艺条件的评估状态（pending / tuning / ok / best / failed）。",
        {
            "exp_id": {"type": "string"},
            "cond_id": {"type": "string"},
            "status": {"type": "string", "enum": list(proc_conditions.CONDITION_STATUSES)},
            "expected_revision": {"type": "string"},
        },
        required=["exp_id", "cond_id", "status"],
    ),
    _tool_spec(
        "condition_delete",
        "删除某组工艺条件（已归档的磁盘图片与数据文件仍会安全保留在文件资料中）。",
        {
            "exp_id": {"type": "string"},
            "cond_id": {"type": "string"},
            "expected_revision": {"type": "string"},
        },
        required=["exp_id", "cond_id"],
    ),
    _tool_spec(
        "step_add",
        "为实验追加一条带时间戳的操作步骤，可选引用资料库文件。",
        {
            "exp_id": {"type": "string"},
            "title": {"type": "string"},
            "text": {"type": "string"},
            "references": {
                "type": "array",
                "items": {"type": "object", "properties": {"exp_id": {"type": "string"}, "path": {"type": "string"}}, "required": ["exp_id", "path"]},
            },
        },
        required=["exp_id"],
    ),
    _tool_spec(
        "note_append",
        "为实验追加一条带时间戳的日志记录，可选引用资料库文件。",
        {
            "exp_id": {"type": "string"},
            "text": {"type": "string"},
            "author": {"type": "string"},
            "references": {
                "type": "array",
                "items": {"type": "object", "properties": {"exp_id": {"type": "string"}, "path": {"type": "string"}}, "required": ["exp_id", "path"]},
            },
        },
        required=["exp_id", "text"],
    ),
    _tool_spec(
        "measurement_upsert",
        "更新实验的仪器与多轮对照测量记录（measurement_groups）。",
        {
            "exp_id": {"type": "string"},
            "expected_revision": {"type": "string"},
            "measurement_groups": {"type": "array", "items": {"type": "object"}},
        },
        required=["exp_id", "measurement_groups"],
    ),
    _tool_spec(
        "files_set_tags",
        "为实验内一个或多个已登记文件设置或追加标签。",
        {
            "exp_id": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "tags": {"type": "array", "items": {"type": "string"}},
            "merge": {"type": "boolean", "description": "true 为追加标签，false 为整体替换"},
            "expected_revision": {"type": "string"},
        },
        required=["exp_id", "paths", "tags"],
    ),
]


def client_config(root: Path | str, *, http_url: str = "") -> dict:
    """Return ready-to-copy MCP client configuration snippets for this vault."""
    root_path = Path(root).expanduser().resolve()
    app_dir = Path(__file__).resolve().parents[1]
    entry = app_dir / ("lab_manager.py" if (app_dir / "release-manifest.json").is_file() else "expvault.py")
    args = [str(entry), "--mcp", "--root", str(root_path)] if entry.name == "lab_manager.py" else [str(entry), "--root", str(root_path), "mcp"]
    stdio_cfg = {
        "mcpServers": {
            "expvault": {
                "command": sys.executable,
                "args": args,
            }
        }
    }
    return {
        "server": SERVER_INFO,
        "protocol_versions": list(SUPPORTED_PROTOCOLS),
        "root": str(root_path),
        "stdio_config": stdio_cfg,
        "http_endpoint": f"{http_url.rstrip('/')}/api/mcp" if http_url else "/api/mcp",
        "tools": [{"name": t["name"], "description": t["description"], "read_only": t["annotations"]["readOnlyHint"]} for t in TOOLS],
    }


class MCPServer:
    """Stateful-per-vault MCP JSON-RPC 2.0 handler."""

    def __init__(self, root: Path | str, *, nas_mode: str | None = None, client_name: str = ""):
        self.root = Path(root).expanduser().resolve()
        self.client_name = (client_name or "").strip()[:64]
        if nas.is_nas(self.root):
            self.nas_mode = nas_mode or ("writer" if nas.writer(self.root) else "viewer")
        else:
            self.nas_mode = None

    @contextmanager
    def _context(self):
        ctx = config.load(self.root)
        if self.nas_mode:
            ctx._nas_read_only = self.nas_mode == "viewer"
        ctx._actor = {
            "actor": f"AI ({self.client_name})" if self.client_name else "AI",
            "actor_type": "ai",
            "source": "mcp",
        }
        ctx.con = Console(quiet=True, color=False)
        try:
            yield ctx
        finally:
            if ctx._db is not None:
                ctx.db.close()

    def _require_write(self, ctx) -> None:
        if self.nas_mode == "viewer":
            raise ValueError("当前为 NAS 只读连接，禁止写入操作")
        if self.nas_mode == "writer":
            nas.require_writer(ctx)

    def _record(self, ctx, exp_id: str) -> dict:
        rec = ctx.db.get_experiment(exp_id)
        if not rec:
            raise ValueError(f"实验不存在：{exp_id}")
        folder = ex.exp_dir(ctx, rec)
        if not _inside(folder, ctx.data_dir) or folder.resolve() == ctx.data_dir.resolve():
            raise ValueError("实验目录越界")
        meta = ex.load_meta(folder)
        if not meta or meta.get("id") != exp_id:
            raise ValueError("实验元数据缺失或编号不匹配")
        return rec

    def _check_revision(self, ctx, rec: dict, expected: str | None) -> None:
        if expected and sha256_file(ex.exp_dir(ctx, rec) / ex.META_NAME) != expected:
            raise ValueError("实验已在其他窗口更新（revision 不匹配），请重新读取后再写入")

    def _refresh(self, ctx, rec: dict) -> None:
        folder = ex.exp_dir(ctx, rec)
        meta = ex.load_meta(folder)
        ctx.db.upsert_experiment({**meta, "folder": rec["folder"]})
        if self.nas_mode:
            nas.refresh_index(ctx, force=True)
        else:
            ex.scan_files(ctx, folder, meta)
        ex.write_readme(ctx, meta, ctx.db.files_of(rec["id"], include_missing=False), folder=folder)
        for rel in (ex.META_NAME, "04_notes/日志.md"):
            p = folder / rel
            if p.is_file():
                st = p.stat()
                ctx.db.upsert_file(rec["id"], {"relpath": rel, "size": st.st_size, "mtime": st.st_mtime,
                                               "sha256": sha256_file(p), "role": ctx.role_for(rel)})
        ctx.db.commit()
        if self.nas_mode == "writer":
            nas.publish_checksums(ctx)

    def _experiment_detail(self, ctx, exp_id: str) -> dict:
        rec = self._record(ctx, exp_id)
        folder = ex.exp_dir(ctx, rec)
        meta = ex.load_meta(folder)
        note = folder / "04_notes" / "日志.md"
        notes_text = ""
        if note.is_file() and _inside(note, folder):
            notes_text = note.read_bytes()[:256 * 1024].decode("utf-8", errors="replace")
        for step in meta.get("steps", []):
            step["references"] = references.decorate(ctx, step.get("references", []))
        blocks = journal.blocks(notes_text, meta.get("journal_entries", []))
        for block in blocks:
            if block["kind"] == "entry":
                block["references"] = references.decorate(ctx, block.get("references", []))
        return {
            **rec,
            **meta,
            "process_conditions": meta.get("process_conditions", []),
            "revision": sha256_file(folder / ex.META_NAME),
            "history": meta.get("history", [])[-50:],
            "journal_blocks": blocks,
        }

    # ------------------------------------------------------------------
    # Tool Dispatcher
    # ------------------------------------------------------------------
    def call_tool(self, name: str, args: dict | None = None) -> Any:
        args = args or {}
        if not isinstance(args, dict):
            raise ValueError("工具参数必须是 JSON 对象")

        with self._context() as ctx:
            if name == "vault_overview":
                records = ctx.db.list_experiments(include_archived=True)
                return {
                    "root": str(self.root),
                    "project": ctx.cfg.get("project") or "我的实验库",
                    "experiments": len(records),
                    "running": sum(r["status"] == "running" for r in records),
                    "done": sum(r["status"] == "done" for r in records),
                    "files": sum(r["file_count"] for r in records),
                    "bytes": sum(r["total_bytes"] for r in records),
                    "recent": [
                        {
                            "id": r["id"], "title": r["title"], "date": r["date"], "status": r["status"],
                            "device": r["device"], "condition_count": len(r.get("process_conditions") or []),
                            "file_count": r["file_count"],
                        }
                        for r in records[:10]
                    ],
                    "events": ctx.db.events(limit=15),
                }

            if name == "experiments_list":
                records = ctx.db.list_experiments(include_archived=bool(args.get("include_archived", False)))
                if args.get("status"):
                    records = [r for r in records if r["status"] == args["status"]]
                if args.get("group"):
                    grp = grouping.normalize_names([args["group"]])[0]
                    records = [r for r in records if grp in r.get("groups", [])]
                kw = search_text(str(args.get("q") or "").strip())
                if kw:
                    file_hits = {
                        f["exp_id"]
                        for f in ctx.db.all_files()
                        if kw in search_text(f["relpath"] + " " + " ".join(f.get("tags", [])))
                    }
                    records = [
                        r for r in records
                        if kw in search_text(json.dumps(r, ensure_ascii=False)) or r["id"] in file_hits
                    ]
                offset = max(0, int(args.get("offset") or 0))
                limit = max(1, min(200, int(args.get("limit") or 50)))
                return {
                    "total": len(records),
                    "has_more": len(records) > offset + limit,
                    "items": records[offset:offset + limit],
                }

            if name == "experiment_get":
                return self._experiment_detail(ctx, str(args["exp_id"]))

            if name == "experiments_search":
                kw = search_text(str(args.get("q") or "").strip())
                if not kw:
                    raise ValueError("请提供搜索关键词 q")
                file_hits = [
                    f for f in ctx.db.all_files()
                    if kw in search_text(f["relpath"] + " " + " ".join(f.get("tags", [])))
                ]
                hit_ids = {f["exp_id"] for f in file_hits}
                exps = [
                    r for r in ctx.db.list_experiments(include_archived=True)
                    if kw in search_text(json.dumps(r, ensure_ascii=False)) or r["id"] in hit_ids
                ]
                return {"experiments": exps[:50], "files": file_hits[:100]}

            if name == "files_list":
                rec = self._record(ctx, str(args["exp_id"]))
                files = ctx.db.files_of(rec["id"])
                kw = search_text(str(args.get("q") or ""))
                role = str(args.get("role") or "")
                files = [
                    f for f in files
                    if f["role"] != "meta"
                    and (not role or f["role"] == role)
                    and kw in search_text(f["relpath"] + " " + " ".join(f.get("tags", [])))
                ]
                offset = max(0, int(args.get("offset") or 0))
                limit = max(1, min(500, int(args.get("limit") or 200)))
                return {"total": len(files), "items": files[offset:offset + limit]}

            if name == "file_read_text":
                rec = self._record(ctx, str(args["exp_id"]))
                path = _safe_file_path(ctx, rec, str(args["path"]))
                data = path.read_bytes()[:200000 + 1]
                if b"\0" in data:
                    raise ValueError("该文件为二进制格式，无法作为文本读取")
                text = data[:200000].decode("utf-8-sig")
                return {"path": args["path"], "text": text, "truncated": len(data) > 200000}

            if name == "conditions_list":
                rec = self._record(ctx, str(args["exp_id"]))
                meta = ex.load_meta(ex.exp_dir(ctx, rec)) or {}
                items = proc_conditions.normalize(meta.get("process_conditions", []))
                if args.get("status"):
                    items = [c for c in items if c.get("status") == args["status"]]
                analysis = proc_conditions.analyze_conditions(items)
                status_counts = {
                    st: sum(1 for c in items if c.get("status") == st)
                    for st in proc_conditions.CONDITION_STATUSES
                }
                return {
                    "exp_id": rec["id"],
                    "title": meta.get("title", ""),
                    "total": len(items),
                    "status_counts": status_counts,
                    "analysis": analysis,
                    "conditions": items,
                    "markdown": proc_conditions.export_markdown(meta, cond_ids=[c["id"] for c in items] if args.get("status") else None),
                }

            if name == "conditions_export_table":
                rec = self._record(ctx, str(args["exp_id"]))
                fmt = str(args.get("format") or "xlsx")
                cond_ids = args.get("cond_ids")
                res = proc_conditions.export_table(ctx, rec, fmt, cond_ids=cond_ids)
                return {
                    "exp_id": rec["id"],
                    "path": str(res["path"]),
                    "name": res["name"],
                    "format": res["format"],
                    "count": res["count"],
                    "columns": res["columns"],
                    "varying_keys": res["varying_keys"],
                    "markdown": res["markdown"],
                }

            if name == "experiment_verify":
                rec = self._record(ctx, str(args["exp_id"]))
                res = ex.verify(ctx, exp_id=rec["id"], rehash=True)
                return {
                    **res,
                    "healthy": not any(res.get(k) for k in ("modified", "missing", "unregistered", "bad_meta", "unverified")),
                }

            # ---- Write Tools (all serialized via harness.lock) ----
            self._require_write(ctx)
            with FileLock(str(ctx.meta_dir / "harness.lock"), timeout=10):
                if name == "experiment_create":
                    values = {k: v for k, v in args.items() if k in (
                        "title", "date", "device", "batch", "project", "operator",
                        "category", "status", "tags", "groups", "params", "purpose", "conditions", "notes",
                    )}
                    if "date" in values:
                        values["date_str"] = values.pop("date")
                    rec = ex.create(ctx, **values)
                    self._refresh(ctx, rec)
                    return self._experiment_detail(ctx, rec["id"])

                rec = self._record(ctx, str(args["exp_id"]))
                self._check_revision(ctx, rec, args.get("expected_revision"))

                if name == "experiment_update":
                    changes = args.get("changes") or {}
                    if not isinstance(changes, dict) or not changes:
                        raise ValueError("changes 不能为空")
                    ex.update(ctx, rec, changes)
                    self._refresh(ctx, rec)
                    return self._experiment_detail(ctx, rec["id"])

                if name == "condition_save":
                    existing = [
                        _safe_file_path(ctx, rec, rel).relative_to(ex.exp_dir(ctx, rec).resolve()).as_posix()
                        for rel in (args.get("existing_paths") or [])
                    ]
                    upload_sources = []
                    for local_str in (args.get("local_files") or []):
                        p = Path(local_str).expanduser().resolve()
                        if not p.is_file() or p.is_symlink():
                            raise ValueError(f"本地文件不存在或不可用：{local_str}")
                        upload_sources.append((p, p.name, []))
                    payload = {k: args[k] for k in ("id", "name", "sample", "status", "params", "metrics", "note") if k in args}
                    saved = proc_conditions.save_condition(ctx, rec, payload, upload_sources=upload_sources, existing_paths=existing)
                    self._refresh(ctx, rec)
                    return {"condition": saved, "revision": sha256_file(ex.exp_dir(ctx, rec) / ex.META_NAME)}

                if name == "conditions_batch_create":
                    created = proc_conditions.batch_create(ctx, rec, args["items"])
                    self._refresh(ctx, rec)
                    return {"created": len(created), "items": created, "revision": sha256_file(ex.exp_dir(ctx, rec) / ex.META_NAME)}

                if name == "condition_update_status":
                    updated = proc_conditions.update_status(ctx, rec, str(args["cond_id"]), str(args["status"]))
                    self._refresh(ctx, rec)
                    return {"condition": updated, "revision": sha256_file(ex.exp_dir(ctx, rec) / ex.META_NAME)}

                if name == "condition_delete":
                    proc_conditions.delete_condition(ctx, rec, str(args["cond_id"]))
                    self._refresh(ctx, rec)
                    return {"deleted": args["cond_id"], "revision": sha256_file(ex.exp_dir(ctx, rec) / ex.META_NAME)}

                if name == "step_add":
                    result = steps.record(
                        ctx, rec,
                        title=args.get("title"),
                        text=args.get("text"),
                        references=args.get("references"),
                    )
                    self._refresh(ctx, rec)
                    return {"step": result, "revision": sha256_file(ex.exp_dir(ctx, rec) / ex.META_NAME)}

                if name == "note_append":
                    ex.append_note(ctx, rec, str(args["text"]), str(args.get("author") or ""), references=args.get("references"))
                    self._refresh(ctx, rec)
                    return {"ok": True, "revision": sha256_file(ex.exp_dir(ctx, rec) / ex.META_NAME)}

                if name == "measurement_upsert":
                    normalized = measurements.normalize(args["measurement_groups"])
                    ex.update(ctx, rec, {"measurement_groups": normalized})
                    self._refresh(ctx, rec)
                    return {"measurement_groups": normalized, "revision": sha256_file(ex.exp_dir(ctx, rec) / ex.META_NAME)}

                if name == "files_set_tags":
                    # Validate paths belong to this experiment
                    for rel in args["paths"]:
                        _safe_file_path(ctx, rec, rel)
                    folder = ex.exp_dir(ctx, rec)
                    meta = ex.load_meta(folder)
                    new_tags = normalize_tags(args["tags"])
                    merge = bool(args.get("merge", False))
                    ft = meta.setdefault("file_tags", {})
                    for rel in args["paths"]:
                        ft[rel] = normalize_tags([*(ft.get(rel, []) if merge else []), *new_tags])
                    detail = f"更新 {len(args['paths'])} 个文件标签" + (f"：{', '.join(new_tags[:5])}" if new_tags else "（清空）")
                    ex.record_history(ctx, meta, "file_tags", detail)
                    ex.save_meta(folder, meta)
                    ex.log_event(ctx, "file_tags", rec["id"], detail)
                    self._refresh(ctx, rec)
                    return {"updated": len(args["paths"]), "revision": sha256_file(folder / ex.META_NAME)}

            raise ValueError(f"未知的 MCP 工具：{name}")

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------
    def list_resources(self) -> list[dict]:
        return [
            {
                "uri": "expvault://overview",
                "name": "实验库概览",
                "description": "当前实验库的统计概览与最近实验",
                "mimeType": "application/json",
            },
            {
                "uri": "expvault://experiments",
                "name": "实验列表",
                "description": "当前实验库的全部活跃实验记录",
                "mimeType": "application/json",
            },
        ]

    def list_resource_templates(self) -> list[dict]:
        return [
            {
                "uriTemplate": "expvault://experiment/{exp_id}",
                "name": "实验详情",
                "description": "读取指定实验的完整 JSON 元数据",
                "mimeType": "application/json",
            },
            {
                "uriTemplate": "expvault://experiment/{exp_id}/conditions",
                "name": "工艺条件矩阵",
                "description": "读取指定实验的工艺条件 Markdown 表格与分析结果",
                "mimeType": "text/markdown",
            },
        ]

    def read_resource(self, uri: str) -> list[dict]:
        if uri == "expvault://overview":
            data = self.call_tool("vault_overview")
            return [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, ensure_ascii=False, indent=2)}]
        if uri == "expvault://experiments":
            data = self.call_tool("experiments_list", {"limit": 200})
            return [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, ensure_ascii=False, indent=2)}]
        if uri.startswith("expvault://experiment/"):
            rest = uri.removeprefix("expvault://experiment/").strip("/")
            parts = rest.split("/")
            exp_id = parts[0]
            if len(parts) == 1:
                data = self.call_tool("experiment_get", {"exp_id": exp_id})
                return [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, ensure_ascii=False, indent=2)}]
            if len(parts) == 2 and parts[1] == "conditions":
                data = self.call_tool("conditions_list", {"exp_id": exp_id})
                return [{"uri": uri, "mimeType": "text/markdown", "text": data["markdown"]}]
        raise ValueError(f"未知的资源 URI：{uri}")

    # ------------------------------------------------------------------
    # JSON-RPC 2.0 Message Handler
    # ------------------------------------------------------------------
    def handle_message(self, msg: dict) -> dict | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return {
                "jsonrpc": "2.0",
                "id": msg.get("id") if isinstance(msg, dict) else None,
                "error": {"code": -32600, "message": "Invalid JSON-RPC 2.0 request"},
            }
        req_id = msg.get("id")
        method = msg["method"]
        params = msg.get("params") or {}
        is_notification = "id" not in msg

        def ok(result: Any) -> dict | None:
            return None if is_notification else {"jsonrpc": "2.0", "id": req_id, "result": result}

        def err(code: int, message: str) -> dict | None:
            return None if is_notification else {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

        try:
            if method == "initialize":
                if isinstance(params, dict):
                    cinfo = params.get("clientInfo")
                    if isinstance(cinfo, dict) and cinfo.get("name"):
                        self.client_name = str(cinfo["name"]).strip()[:64]
                requested = params.get("protocolVersion") if isinstance(params, dict) else None
                version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
                return ok({
                    "protocolVersion": version,
                    "serverInfo": SERVER_INFO,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                    },
                    "instructions": "expvault 实验与工艺条件管理 MCP 接口：可查询/创建实验、管理同一实验内的多组工艺条件与参数矩阵、读取文本数据文件、导出工艺条件表格与校验 SHA256。",
                })
            if method in ("notifications/initialized", "initialized"):
                return None
            if method == "ping":
                return ok({})
            if method == "tools/list":
                return ok({"tools": TOOLS})
            if method == "tools/call":
                tool_name = params.get("name", "")
                tool_args = params.get("arguments") or {}
                try:
                    result = self.call_tool(tool_name, tool_args)
                    return ok({
                        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
                        "structuredContent": result if isinstance(result, dict) else {"result": result},
                        "isError": False,
                    })
                except Exception as exc:
                    return ok({
                        "content": [{"type": "text", "text": str(exc)}],
                        "isError": True,
                    })
            if method == "resources/list":
                return ok({"resources": self.list_resources()})
            if method == "resources/templates/list":
                return ok({"resourceTemplates": self.list_resource_templates()})
            if method == "resources/read":
                uri = str(params.get("uri") or "")
                return ok({"contents": self.read_resource(uri)})
            return err(-32601, f"Method not found: {method}")
        except Exception as exc:
            return err(-32000, str(exc))


# --------------------------------------------------------------------------
# Stdio Transport Loop (auto-detects newline JSON-RPC vs Content-Length)
# --------------------------------------------------------------------------
def _read_stdio_message(stream) -> tuple[dict | None, str]:
    while True:
        line = stream.readline()
        if not line:
            return None, "line"
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(b"{"):
            return json.loads(stripped.decode("utf-8")), "line"
        # Content-Length framing
        headers: dict[str, str] = {}
        cur = line
        while cur and cur.strip():
            text = cur.decode("ascii", errors="ignore")
            if ":" in text:
                k, v = text.split(":", 1)
                headers[k.strip().lower()] = v.strip()
            cur = stream.readline()
        length = int(headers.get("content-length", "0"))
        if length <= 0:
            continue
        body = stream.read(length)
        if not body:
            return None, "framed"
        return json.loads(body.decode("utf-8")), "framed"


def _write_stdio_message(stream, payload: dict, mode: str) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if mode == "framed":
        header = f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii")
        stream.write(header + raw)
    else:
        stream.write(raw + b"\n")
    stream.flush()


def serve_stdio(root: Path | str, *, nas_mode: str | None = None) -> int:
    server = MCPServer(root, nas_mode=nas_mode)
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        try:
            msg, mode = _read_stdio_message(stdin)
        except (KeyboardInterrupt, EOFError):
            return 0
        except Exception as exc:
            _write_stdio_message(
                stdout,
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}},
                "line",
            )
            continue
        if msg is None:
            return 0
        resp = server.handle_message(msg)
        if resp is not None:
            _write_stdio_message(stdout, resp, mode)
