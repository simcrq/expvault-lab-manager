"""网页管理业务：明确操作、后台任务、上传暂存和可恢复的操作结果。"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path, PureWindowsPath

from filelock import FileLock, Timeout
from jsonschema import Draft202012Validator, FormatChecker

from . import backup, config, experiment as ex, report, steps, references, journal, templates, nas, groups as grouping
from .file_tags import normalize_tags
from .importer import ImportItem, file_kind, import_items, set_file_tags
from .rich_text import search_text
from .measurements import GROUPS_SCHEMA
from .ui import Console
from .util import now_iso, read_json, sha256_file, write_json


class WebError(Exception):
    def __init__(self, message, *, status=400, code="INVALID_ARGUMENTS", data=None):
        super().__init__(message)
        self.status, self.code, self.data = status, code, data


def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


TEXT = {"type": "string", "maxLength": 100000}
SHORT = {"type": "string", "maxLength": 256}
ID = {"type": "string", "minLength": 1, "maxLength": 128}
LABELS = {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 80}, "maxItems": 50, "uniqueItems": True}
PATHS = {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 4096}, "minItems": 1, "maxItems": 500, "uniqueItems": True}
UPLOADS = {"type": "array", "items": {"type": "string", "pattern": "^[a-f0-9]{32}$"}, "maxItems": 100, "uniqueItems": True}
FIELDS = {name: TEXT for name in ("project", "operator", "device", "batch", "category", "notes", "purpose", "conditions")}
FIELDS.update(measurement_groups=GROUPS_SCHEMA, title={**SHORT, "minLength": 1, "pattern": r"\S"}, date={"type": "string", "format": "date"},
              status={"enum": ["planned", "running", "done", "aborted"]}, tags=LABELS,
              groups=grouping.GROUPS_SCHEMA,
              params={"type": "object", "maxProperties": 200, "propertyNames": {"minLength": 1, "maxLength": 128},
                      "additionalProperties": {"type": ["string", "number", "boolean", "null"], "maxLength": 2000}})
EXPERIMENT = {"exp_id": ID}
REVISION = {"expected_revision": {"type": "string", "pattern": "^[a-f0-9]{64}$"}}
REFERENCES = {"type":"array", "maxItems":100, "uniqueItems":True,
              "items":obj({"exp_id":ID, "path":{**SHORT, "maxLength":4096, "minLength":1}}, ["exp_id", "path"])}
STEP = {**EXPERIMENT, "title": SHORT, "text": TEXT, "upload_ids": UPLOADS,
        "image_paths": {**PATHS, "minItems": 0}, "references":REFERENCES, **REVISION}
TEMPLATE_FIELDS = {key:value for key,value in FIELDS.items() if key != "measurement_groups"}
TEMPLATE_FIELDS["status"] = {"enum":["planned","running"]}
TEMPLATE_OPTIONS = {**EXPERIMENT, **REVISION,"copy_instruments":{"type":"boolean"},"copy_notes":{"type":"boolean"}}
SCHEMAS = {
    "experiments.create": obj(FIELDS, ["title"]),
    "experiments.update": obj({**EXPERIMENT, **REVISION, "changes": {**obj({**FIELDS, "results": TEXT, "conclusion": TEXT}), "minProperties": 1}}, ["exp_id", "changes"]),
    "experiments.archive": obj({**EXPERIMENT, **REVISION, "archived": {"type": "boolean"}}, ["exp_id", "archived"]),
    "experiments.duplicate": obj({**TEMPLATE_OPTIONS,"changes":obj(TEMPLATE_FIELDS)},["exp_id"]),
    "experiments.batch": obj({**TEMPLATE_OPTIONS,"variants":{"type":"array","minItems":1,"maxItems":100,"items":obj(TEMPLATE_FIELDS,["title"]) }},["exp_id","variants"]),
    "experiments.groups": obj({"exp_ids":{"type":"array","minItems":1,"maxItems":200,"uniqueItems":True,"items":ID},"groups":{**grouping.GROUPS_SCHEMA,"minItems":1},"mode":{"enum":["add","remove"]}},["exp_ids","groups","mode"]),
    "groups.rename": obj({"name":{**SHORT,"maxLength":80,"minLength":1},"new_name":{**SHORT,"maxLength":80,"minLength":1}},["name","new_name"]),
    "groups.dissolve": obj({"name":{**SHORT,"maxLength":80,"minLength":1}},["name"]),
    "notes.add": obj({**EXPERIMENT, "text": {**TEXT, "minLength": 1, "pattern": r"\S"}, "author": SHORT, "references":REFERENCES, **REVISION}, ["exp_id", "text"]),
    "notes.references": obj({**EXPERIMENT, "note_id":{"type":"string", "pattern":"^[a-f0-9]{32}$"}, "references":REFERENCES, **REVISION}, ["exp_id", "note_id", "references"]),
    "steps.add": obj(STEP, ["exp_id"]),
    "steps.update": obj({**STEP, "number": {"type": "integer", "minimum": 1}, "clear_images": {"type": "boolean"}}, ["exp_id", "number"]),
    "files.tags": obj({**EXPERIMENT, **REVISION, "paths": PATHS, "tags": LABELS, "merge": {"type": "boolean"}}, ["exp_id", "paths", "tags"]),
    "files.import": obj({**EXPERIMENT, "upload_ids": {**UPLOADS, "minItems": 1}, "role": {"enum": ["auto", "raw", "processed", "figure", "note", "other"]},
                         "deduplicate": {"type": "boolean"}, "tags": LABELS,
                         "file_tags": {"type": "object", "maxProperties": 100, "propertyNames": {"pattern": "^[a-f0-9]{32}$"}, "additionalProperties": LABELS}}, ["exp_id", "upload_ids"]),
    "files.remove": obj({**EXPERIMENT, **REVISION, "paths": PATHS, "confirmation": {"const": "DELETE"}}, ["exp_id", "paths", "confirmation"]),
    "experiments.scan": obj(EXPERIMENT, ["exp_id"]),
    "experiments.verify": obj(EXPERIMENT, ["exp_id"]),
    "backup.create": obj({"target": {**SHORT, "minLength": 1}}, ["target"]),
    "reports.export": obj({"format": {"enum": ["html", "xlsx"]}}, ["format"]),
    "uploads.discard": obj({**EXPERIMENT, "upload_ids": {**UPLOADS, "minItems": 1}}, ["exp_id", "upload_ids"]),
}
VALIDATORS = {name: Draft202012Validator(schema, format_checker=FormatChecker()) for name, schema in SCHEMAS.items()}


def inside(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    return path == parent or parent in path.parents


class WebService:
    def __init__(self, root, *, nas_mode=None):
        self.root = Path(root).expanduser().resolve()
        from . import nas
        self.nas_mode = (nas_mode or ("writer" if nas.writer(self.root) else "viewer")) if nas.is_nas(self.root) else None
        self.nas_id = nas.settings(self.root)["id"] if self.nas_mode else None
        self.guard = threading.RLock()
        self.active = {}
        self.closed = False
        with self.context() as ctx:
            self.state = ctx.meta_dir / "web"
            if self.nas_mode:
                self.state = self.state / self.nas_mode
            if not inside(self.state, self.root) and not (self.nas_mode and inside(self.state, nas.local_dir(self.root, ctx.cfg))):
                raise WebError("网页状态目录越过仓库边界")
            self.state.mkdir(parents=True, exist_ok=True)
            self.jobs_dir = self.state / "jobs"
            self.inbox = self.state / "inbox"
            self.exports = self.state / "exports"
            for directory in (self.jobs_dir, self.inbox, self.exports):
                if directory.is_symlink():
                    raise WebError("网页状态目录不能是符号链接")
                directory.mkdir(exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="expvault-web")
        self.instance_lock = FileLock(str(self.state / "server.lock"), timeout=0, thread_local=False)
        try:
            self.instance_lock.acquire()
        except Timeout:
            self.executor.shutdown(wait=False)
            raise WebError("这个实验库已有网页管理服务，请使用已打开的管理窗口", status=409, code="BUSY") from None
        try:
            # Initialize WAL/schema before the first parallel dashboard reads in an empty vault.
            with self.context() as ctx:
                ctx.db
                if not self.nas_mode:
                    grouping.hydrate_index(ctx)
        except Exception:
            self.instance_lock.release()
            self.executor.shutdown(wait=False)
            raise

    def context(self):
        from contextlib import contextmanager

        @contextmanager
        def opened():
            from . import nas
            if self.nas_mode:
                try:
                    nas.assert_available(self.root, self.nas_id)
                except (ValueError, OSError) as exc:
                    raise WebError(str(exc), status=409, code="NAS_UNAVAILABLE") from None
            ctx = config.load(self.root)
            if self.nas_mode:
                ctx._nas_read_only = self.nas_mode == "viewer"
            ctx.con = Console(quiet=True, color=False)
            try:
                for path in (self.root / config.CONFIG_NAME, ctx.data_dir, ctx.db_path, ctx.report_dir, ctx.log_path, ctx.meta_dir):
                    if not inside(path, self.root) and not (self.nas_mode and inside(path, nas.local_dir(self.root, ctx.cfg))):
                        raise WebError("配置的数据、索引、报告和状态目录必须位于本实验库内")
                for name, _label, _role in ctx.structure():
                    if not name or Path(name).is_absolute() or PureWindowsPath(name).drive or ".." in name.replace("\\", "/").split("/"):
                        raise WebError("实验子目录配置无效")
                prefix = ctx.cfg.get("naming", {}).get("id_prefix", "E")
                if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,15}", prefix):
                    raise WebError("实验编号前缀配置无效")
                yield ctx
            finally:
                if ctx._db is not None:
                    ctx.db.close()
        return opened()

    def require_write(self):
        if not self.nas_mode:
            return
        if self.nas_mode == "viewer":
            raise WebError("当前为 NAS 只读连接，请在编辑主机中修改实验", status=403, code="READ_ONLY")
        from . import nas
        with self.context() as ctx:
            nas.require_writer(ctx)

    def refresh_nas(self):
        if not self.nas_mode:
            raise WebError("当前不是 NAS 连接")
        from . import nas
        with self.context() as ctx:
            ctx.db
            result=nas.refresh_index(ctx, force=True)
            return {**result, "storage":nas.status(ctx)}

    def record(self, ctx, exp_id):
        rec = ctx.db.get_experiment(exp_id)
        if not rec:
            raise WebError("实验不存在，请刷新列表", status=404, code="NOT_FOUND")
        folder = ex.exp_dir(ctx, rec)
        if not inside(folder, ctx.data_dir) or folder.resolve() == ctx.data_dir.resolve():
            raise WebError("实验目录越过数据目录边界", status=403)
        for name in (ex.META_NAME, ex.README_NAME, "04_notes", ".expvault", *[row[0] for row in ctx.structure()]):
            if not inside(folder / name, folder):
                raise WebError("实验目录通过链接越界", status=403)
        meta = ex.load_meta(folder)
        if not meta or meta.get("id") != exp_id:
            raise WebError("实验元数据缺失或编号不匹配", status=409, code="CONFLICT")
        return rec

    def file_path(self, ctx, rec, relative, *, registered=True):
        if (not isinstance(relative, str) or not relative or Path(relative).is_absolute() or PureWindowsPath(relative).drive
                or any(p in ("..", ".expvault") for p in relative.replace("\\", "/").split("/"))):
            raise WebError("文件路径无效", status=403)
        folder = ex.exp_dir(ctx, rec).resolve()
        path = folder / relative.replace("\\", "/")
        if path.resolve() == folder or not inside(path, folder) or path.is_symlink():
            raise WebError("文件路径越过实验边界", status=403)
        f = ctx.db.get_file(rec["id"], path.relative_to(folder).as_posix()) if registered else None
        if not path.is_file() or (registered and (not f or f["missing"])):
            raise WebError("文件不存在或尚未登记", status=404, code="NOT_FOUND")
        return path

    def experiment(self, ctx, exp_id):
        rec = self.record(ctx, exp_id)
        folder = ex.exp_dir(ctx, rec)
        meta = ex.load_meta(folder)
        try:
            names=grouping.normalize_names(meta.get("groups"), limit=None)
            invalid_groups=False
        except ValueError:
            names=[]
            invalid_groups=True
        note = folder / "04_notes" / "日志.md"
        if note.is_file() and inside(note, folder):
            with note.open("rb") as stream:
                raw_notes = stream.read(256 * 1024 + 1)
            notes_text = raw_notes[:256 * 1024].decode("utf-8", errors="replace")
        else:
            notes_text, raw_notes = "", b""
        for step in meta.get("steps", []):
            step["references"] = references.decorate(ctx, step.get("references", []))
        blocks = journal.blocks(notes_text, meta.get("journal_entries", []))
        for block in blocks:
            if block["kind"] == "entry":
                block["references"] = references.decorate(ctx, block.get("references", []))
        return {**rec, **meta, "groups":names,"groups_invalid":invalid_groups,"revision": sha256_file(folder / ex.META_NAME), "history": meta.get("history", [])[-100:],
                "journal": notes_text, "journal_blocks":blocks, "journal_truncated": len(raw_notes) > 256 * 1024}

    def read(self, name, query):
        with self.context() as ctx:
            if name == "session":
                return {"project": ctx.cfg.get("project") or "我的实验库", "operator": ctx.cfg.get("operator", ""),
                        "root": str(self.root), "roles": [{"role": role, "label": label, "directory": directory} for directory, label, role in ctx.structure()],
                        "max_upload_bytes": 20 * 1024**3, "operations": list(SCHEMAS),
                        "storage": nas.status(ctx)}
            if name == "overview":
                records = ctx.db.list_experiments(include_archived=True)
                return {"experiments": len(records), "running": sum(r["status"] == "running" for r in records),
                        "done": sum(r["status"] == "done" for r in records), "files": sum(r["file_count"] for r in records),
                        "bytes": sum(r["total_bytes"] for r in records), "recent": records[:6], "events": ctx.db.events(limit=15)}
            if name == "experiments":
                limit = self.integer(query.get("limit", "30"), 1, 200)
                offset = self.integer(query.get("offset", "0"), 0, 100000)
                records = ctx.db.list_experiments(include_archived=query.get("archived") == "1")
                keyword = search_text(query.get("q", "").strip())
                if query.get("status"):
                    records = [r for r in records if r["status"] == query["status"]]
                if query.get("group"):
                    group=grouping.normalize_names([query["group"]])[0]
                    records=[r for r in records if group in r.get("groups",[])]
                if query.get("ungrouped") == "1":
                    records=[r for r in records if not r.get("groups")]
                if keyword:
                    file_hits = {f["exp_id"] for f in ctx.db.all_files() if keyword in search_text(f["relpath"] + " " + " ".join(f.get("tags", [])))}
                    records = [r for r in records if keyword in search_text(json.dumps(r, ensure_ascii=False)) or r["id"] in file_hits]
                return {"items": records[offset:offset+limit], "total": len(records), "has_more": len(records) > offset+limit}
            if name == "groups":
                return grouping.catalog(ctx)
            if name == "experiment":
                return self.experiment(ctx, query.get("exp_id", ""))
            if name == "files":
                rec = self.record(ctx, query.get("exp_id", ""))
                files = ctx.db.files_of(rec["id"])
                keyword, role = search_text(query.get("q", "")), query.get("role", "")
                files = [f for f in files if f["role"] != "meta" and (not role or f["role"] == role)
                         and keyword in search_text(f["relpath"] + " " + " ".join(f.get("tags", [])))]
                offset = self.integer(query.get("offset", "0"), 0, 100000)
                limit = self.integer(query.get("limit", "200"), 1, 500)
                return {"items": files[offset:offset+limit], "total": len(files), "has_more": len(files) > offset+limit}
            if name == "uploads":
                exp_id = query.get("exp_id", "")
                self.record(ctx, exp_id)
                return {"items": self.uploads(exp_id)}
            if name == "file-text":
                rec = self.record(ctx, query.get("exp_id", ""))
                path = self.file_path(ctx, rec, query.get("path", ""))
                with path.open("rb") as stream:
                    data = stream.read(200000 + 1)
                if b"\0" in data:
                    raise WebError("该文件为二进制格式，请下载后使用对应软件打开")
                try:
                    value = data[:200000].decode("utf-8-sig")
                except UnicodeDecodeError:
                    raise WebError("该文件不是 UTF-8 文本，请下载后打开") from None
                return {"text": value, "truncated": len(data) > 200000}
            if name == "delete-preview":
                paths = json.loads(query.get("paths", "[]"))
                self.validate("files.remove", {"exp_id": query.get("exp_id", ""), "paths": paths, "confirmation": "DELETE"})
                rec = self.record(ctx, query["exp_id"])
                return self.delete_plan(ctx, rec, paths)
            if name == "backups":
                targets = []
                for target in ctx.cfg["backup"].get("targets", []):
                    path = ctx.resolve_target_path(target)
                    targets.append({"name": target["name"], "path": str(path), "enabled": bool(target.get("enabled", True)),
                                    "available": backup.path_available(path)})
                return {"targets": targets, "history": ctx.db.backups(limit=100)}
        raise WebError("接口不存在", status=404)

    @staticmethod
    def integer(value, minimum, maximum):
        try:
            result = int(value)
            if not minimum <= result <= maximum:
                raise ValueError
            return result
        except (ValueError, TypeError):
            raise WebError("分页参数无效") from None

    def validate(self, operation, arguments):
        if not isinstance(operation, str) or operation not in VALIDATORS:
            raise WebError("不支持的网页操作")
        error = next(VALIDATORS[operation].iter_errors(arguments), None)
        if error:
            raise WebError("参数无效，请检查填写的字段、日期和标签", data={"field": ".".join(str(p) for p in error.absolute_path), "constraint": error.validator})
        values = arguments.get("variants", []) if operation == "experiments.batch" else [arguments.get("changes", arguments)]
        try:
            for value in values:
                if "groups" in value:
                    grouping.normalize_names(value["groups"])
            if operation in ("groups.rename", "groups.dissolve"):
                grouping.normalize_names([arguments["name"]])
                if operation == "groups.rename":
                    grouping.normalize_names([arguments["new_name"]])
        except ValueError as exc:
            raise WebError(str(exc)) from None
        if operation == "files.import" and set(arguments.get("file_tags", {})) - set(arguments["upload_ids"]):
            raise WebError("文件标签只能用于本次导入清单中的文件")

    def upload_manifest(self, upload_id, exp_id):
        if not re.fullmatch(r"[a-f0-9]{32}", upload_id):
            raise WebError("上传编号无效")
        directory = self.inbox / upload_id
        info = directory / ".upload.json"
        if directory.is_symlink() or info.is_symlink() or not inside(directory, self.inbox):
            raise WebError("上传暂存路径无效", status=403)
        value = read_json(info, None)
        if not value or value.get("exp_id") != exp_id:
            raise WebError("暂存文件不存在或属于其他实验", status=404)
        name = value.get("name", "")
        path = directory / name
        if not name or Path(name).name != name or path.is_symlink() or not path.is_file() or not inside(path, directory):
            raise WebError("暂存文件丢失或路径无效", status=404)
        return value, path

    def uploads(self, exp_id):
        result = []
        for directory in sorted(self.inbox.iterdir()):
            if not directory.is_dir() or not re.fullmatch(r"[a-f0-9]{32}", directory.name):
                continue
            try:
                value, path = self.upload_manifest(directory.name, exp_id)
                result.append({"id": directory.name, "name": value["name"], "relative": value["relative"], "size": path.stat().st_size,
                               "tags": value.get("tags", []), "kind": file_kind(path)})
            except WebError:
                continue
        return result

    def import_phone_file(self, exp_id, item, cancel):
        if self.nas_mode != "writer":
            raise WebError("请在 NAS 编辑主机上开启扫码传入", status=403)
        self.require_write()
        result = import_items(self.root, exp_id, [item], cancel=cancel)
        if not (result["imported"] or result["duplicates"]):
            raise WebError("文件未能导入 NAS")
        with self.context() as ctx:
            nas.publish_checksums(ctx)

    def submit(self, request_id, operation, arguments):
        if operation not in ("reports.export", "experiments.verify"):
            self.require_write()
        transaction = getattr(self, "sync_root", self.root)/".expvault/sync/transaction.json"
        pending = read_json(transaction, None)
        if transaction.exists() and (not isinstance(pending,dict) or not pending.get("committed")):
            raise WebError("同步正在提交或恢复记录，请稍后再保存", status=409, code="BUSY")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request_id):
            raise WebError("操作编号无效")
        self.validate(operation, arguments)
        digest = hashlib.sha256(json.dumps([operation, arguments], sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        path = self.jobs_dir / f"{request_id}.json"
        with self.guard, FileLock(str(self.state / "requests.lock"), timeout=2):
            if self.closed:
                raise WebError("网页服务正在关闭", status=503)
            old = read_json(path, None)
            if old:
                if old.get("digest") != digest:
                    raise WebError("操作编号已用于其他内容，请刷新后重试", status=409, code="CONFLICT")
                return self.job(request_id)
            job = {"id": request_id, "operation": operation, "digest": digest, "status": "queued", "progress": 0,
                   "message": "等待执行", "created_at": now_iso(), "can_cancel": operation == "files.import", "exp_id": arguments.get("exp_id", "")}
            write_json(path, job)
            cancel = threading.Event()
            self.active[request_id] = cancel
            self.executor.submit(self.run, job, arguments, cancel)
            return self.public_job(job)

    def public_job(self, job):
        return {key: value for key, value in job.items() if key != "digest"}

    def job(self, request_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request_id):
            raise WebError("任务编号无效")
        with self.guard:
            job = read_json(self.jobs_dir / f"{request_id}.json", None)
            if not job:
                raise WebError("任务不存在", status=404)
            if job["status"] in ("queued", "running") and request_id not in self.active:
                job.update(status="interrupted", message="服务曾中断，结果未确认；请先检查实际数据，不会自动重复执行")
        return self.public_job(job)

    def cancel(self, request_id):
        job = self.job(request_id)
        if not job.get("can_cancel"):
            raise WebError("这项操作已开始后需要等待完成")
        with self.guard:
            if request_id in self.active:
                self.active[request_id].set()
        return job

    def running_jobs(self):
        with self.guard:
            return [self.job(key) for key in self.active]

    def run(self, job, arguments, cancel):
        path = self.jobs_dir / f"{job['id']}.json"
        def progress(done, total, message):
            with self.guard:
                job.update(progress=min(99, int(done*100/max(total, 1))), message=message)
                write_json(path, job)
        try:
            if job["operation"] not in ("reports.export", "experiments.verify"):
                self.require_write()
            job.update(status="running", message="正在执行", started_at=now_iso())
            write_json(path, job)
            op = job["operation"]
            if op == "files.import":
                items = []
                for upload_id in arguments["upload_ids"]:
                    value, source = self.upload_manifest(upload_id, arguments["exp_id"])
                    items.append(ImportItem(source, value["relative"], source.stat().st_size, file_kind(source),
                                            tags=normalize_tags(arguments.get("file_tags", {}).get(upload_id, value.get("tags", [])) + arguments.get("tags", [])), key=upload_id, origin="网页上传"))
                result = import_items(self.root, arguments["exp_id"], items, role=arguments.get("role", "auto"),
                                      deduplicate=arguments.get("deduplicate", True), cancel=cancel, progress=progress)
                for item in items:
                    if item.key in result["imported"] + result["duplicates"]:
                        self.discard_upload(item.key, arguments["exp_id"])
                job.update(status="cancelled" if result["cancelled"] else ("partial" if result["errors"] else "succeeded"),
                           result=result, message=f"导入 {len(result['imported'])} 个，重复 {len(result['duplicates'])} 个，失败 {len(result['errors'])} 个")
            elif op == "files.tags":
                set_file_tags(self.root, arguments["exp_id"], arguments["paths"], arguments["tags"], merge=arguments.get("merge", False),
                              expected_revision=arguments.get("expected_revision"))
                job.update(status="succeeded", result={"updated": len(arguments["paths"])}, message="文件标签已保存")
            else:
                with self.context() as ctx, FileLock(str(ctx.meta_dir / "harness.lock"), timeout=0):
                    result = self.execute(ctx, op, arguments, progress=progress) if op in ("experiments.batch","experiments.groups","groups.rename","groups.dissolve") else self.execute(ctx, op, arguments)
                message=f'已更新 {result["updated"]} 个实验的分组' if op in ("experiments.groups","groups.rename","groups.dissolve") else "操作已完成"
                job.update(status="succeeded", result=result, message=message)
            if self.nas_mode == "writer" and job.get("status") in ("succeeded", "partial", "cancelled"):
                from . import nas
                with self.context() as ctx:
                    nas.publish_checksums(ctx)
        except WebError as exc:
            job.update(status="failed", message=str(exc), error={"code": exc.code, "data": exc.data})
        except Timeout:
            job.update(status="failed", message="另一个写操作正在执行，请等待完成后再试", error={"code": "BUSY"})
        except ValueError as exc:
            job.update(status="failed", message=str(exc), error={"code": "INVALID_ARGUMENTS"})
        except Exception:
            job.update(status="failed", message="存储操作未完成，请检查磁盘、权限和实际数据后再重试", error={"code": "IO_ERROR"})
        finally:
            with self.guard:
                job.update(finished_at=now_iso(), progress=100)
                try:
                    write_json(path, job)
                finally:
                    self.active.pop(job["id"], None)

    def revision(self, ctx, rec, args):
        expected = args.get("expected_revision")
        if expected and sha256_file(ex.exp_dir(ctx, rec) / ex.META_NAME) != expected:
            raise WebError("实验已在其他窗口更新，请刷新后核对再保存", status=409, code="CONFLICT")

    def refresh(self, ctx, rec):
        folder = ex.exp_dir(ctx, rec)
        meta = ex.load_meta(folder)
        ctx.db.upsert_experiment({**meta, "folder": rec["folder"]})
        if self.nas_mode:
            nas.refresh_index(ctx, force=True)
        else:
            ex.scan_files(ctx, folder, meta)
        ex.write_readme(ctx, meta, ctx.db.files_of(rec["id"], include_missing=False), folder=folder)
        for relative in (ex.META_NAME, "04_notes/日志.md"):
            path = folder / relative
            if path.is_file():
                stat = path.stat()
                ctx.db.upsert_file(rec["id"], {"relpath": relative, "size": stat.st_size, "mtime": stat.st_mtime,
                                              "sha256": sha256_file(path), "role": ctx.role_for(relative)})
        ctx.db.commit()

    def delete_plan(self, ctx, rec, paths):
        meta = ex.load_meta(ex.exp_dir(ctx, rec))
        referenced = references.incoming(ctx, rec["id"])
        size = 0
        for relative in paths:
            path = self.file_path(ctx, rec, relative)
            f = ctx.db.get_file(rec["id"], path.relative_to(ex.exp_dir(ctx, rec)).as_posix())
            if f["role"] == "meta" or f["relpath"] in referenced or f["relpath"] == "04_notes/日志.md":
                raise WebError("所选文件是元数据、日志或仍被步骤 / 日志引用；请先在引用记录中解除关联", status=409, code="PROTECTED_FILE")
            size += path.stat().st_size
        return {"paths": paths, "files": len(paths), "bytes": size}

    def discard_upload(self, upload_id, exp_id):
        _value, path = self.upload_manifest(upload_id, exp_id)
        shutil.rmtree(path.parent)

    def execute(self, ctx, operation, args, *, progress=None):
        if operation in ("experiments.groups","groups.rename","groups.dissolve"):
            if operation == "experiments.groups":
                names=grouping.normalize_names(args["groups"])
                records=[self.record(ctx,key) for key in args["exp_ids"]]
            else:
                name=grouping.normalize_names([args["name"]])[0]
                new_name=grouping.normalize_names([args["new_name"]])[0] if operation == "groups.rename" else None
                records=[self.record(ctx,rec["id"]) for rec in ctx.db.list_experiments(include_archived=True)]
            changes=[]
            for rec in records:
                meta=ex.load_meta(ex.exp_dir(ctx,rec))
                old=grouping.normalize_names(meta.get("groups"), limit=None)
                if operation == "experiments.groups":
                    new=grouping.normalize_names(list(set(old)|set(names)), limit=max(20,len(old))) if args["mode"] == "add" else [value for value in old if value not in names]
                else:
                    if name not in old:
                        continue
                    new=grouping.normalize_names([new_name if value==name else value for value in old], limit=max(20,len(old))) if new_name else [value for value in old if value!=name]
                changes.append((rec,meta,new))
            result=grouping.apply(ctx,changes,progress=progress)
            if operation == "groups.rename":result["name"]=new_name
            return result
        if operation == "experiments.create":
            values = dict(args)
            if "date" in values:
                values["date_str"] = values.pop("date")
            rec = ex.create(ctx, **values)
            self.refresh(ctx, rec)
            return self.experiment(ctx, rec["id"])
        if operation == "reports.export":
            export_id = uuid.uuid4().hex
            path = ctx.report_dir / f"web-{datetime.now():%Y%m%d-%H%M%S}-{export_id[:8]}.{args['format']}"
            output = report.export_html(ctx, path) if args["format"] == "html" else report.export_xlsx(ctx, path)
            write_json(self.exports / f"{export_id}.json", {"path": output.relative_to(ctx.report_dir).as_posix(), "name": output.name})
            return {"id": export_id, "name": output.name, "url": f"/api/report?id={export_id}"}
        if operation == "backup.create":
            target = ctx.target_by_name(args["target"])
            if not target or not target.get("enabled", True):
                raise WebError("备份目标不存在或未启用")
            destination = ctx.resolve_target_path(target)
            if inside(destination, ctx.data_dir):
                raise WebError("备份目标不能位于实验数据目录中")
            if not backup.path_available(destination):
                raise WebError("备份目标未连接")
            destination.mkdir(parents=True, exist_ok=True)
            with FileLock(str(destination / ".expvault-harness.lock"), timeout=0):
                result = backup.backup(ctx, destination, name=target["name"], prune=False)
            if result["stats"]["errors"]:
                raise WebError("部分文件备份失败，请检查结果", data=result)
            return result
        rec = self.record(ctx, args["exp_id"])
        self.revision(ctx, rec, args)
        if operation in ("experiments.duplicate","experiments.batch"):
            source = ex.load_meta(ex.exp_dir(ctx,rec))
            plans = templates.prepare(source,args["variants"] if operation == "experiments.batch" else [args.get("changes",{})],
                                      copy_instruments=args.get("copy_instruments",True),copy_notes=args.get("copy_notes",False),
                                      replace_params=operation == "experiments.duplicate")
            for plan in plans:
                self.validate("experiments.create",plan)
            created, batch_id = templates.create_many(ctx,source,plans,progress=progress,source_revision=sha256_file(ex.exp_dir(ctx,rec)/ex.META_NAME))
            if operation == "experiments.duplicate":
                return self.experiment(ctx,created[0]["id"])
            return {"items":[{key:item[key] for key in ("id","title","date","device","status")} for item in created],"count":len(created),"batch_id":batch_id,"source_id":rec["id"]}
        if operation == "experiments.update":
            if "status" in args["changes"] and ex.load_meta(ex.exp_dir(ctx, rec)).get("archived"):
                raise WebError("实验已归档，请先恢复实验再修改状态", status=409, code="CONFLICT")
            ex.update(ctx, rec, args["changes"])
        elif operation == "experiments.archive":
            ex.archive(ctx, rec, args["archived"])
        elif operation == "notes.add":
            ex.append_note(ctx, rec, args["text"], args.get("author", ""), references=args.get("references"))
        elif operation == "notes.references":
            journal.update_references(ctx, rec, args["note_id"], args["references"])
        elif operation in ("steps.add", "steps.update"):
            staged = [self.upload_manifest(upload_id, rec["id"])[1] for upload_id in args.get("upload_ids", [])]
            existing = [self.file_path(ctx, rec, value) for value in args.get("image_paths", [])]
            result = steps.record(ctx, rec, title=args.get("title"), text=args.get("text"),
                                  images=[str(path) for path in staged + existing], edit=args.get("number"), clear_images=args.get("clear_images", False), references=args.get("references"))
            sources = staged + existing
            if sources:
                folder = ex.exp_dir(ctx, rec)
                meta = ex.load_meta(folder)
                labels = [self.upload_manifest(upload_id, rec["id"])[0].get("tags", []) for upload_id in args.get("upload_ids", [])]
                labels.extend(ctx.db.get_file(rec["id"], path.relative_to(folder.resolve()).as_posix()).get("tags", []) for path in existing)
                for image, tags in zip(result["images"][-len(sources):], labels):
                    meta.setdefault("file_tags", {})[image["path"]] = normalize_tags(tags)
                ex.save_meta(folder, meta)
            for upload_id in args.get("upload_ids", []):
                self.discard_upload(upload_id, rec["id"])
            self.refresh(ctx, rec)
            return result
        elif operation == "files.remove":
            self.delete_plan(ctx, rec, args["paths"])
            result = ex.remove_files(ctx, rec, args["paths"], literal=True)
            self.refresh(ctx, rec)
            if result["failed"]:
                raise WebError("部分文件未删除，请刷新核对", data=result)
            return result
        elif operation == "experiments.verify":
            result = ex.verify(ctx, exp_id=rec["id"], rehash=True)
            return {**result, "healthy": not any(result.get(key) for key in ("modified", "missing", "unregistered", "bad_meta", "unverified"))}
        elif operation == "uploads.discard":
            for upload_id in args["upload_ids"]:
                self.discard_upload(upload_id, rec["id"])
            return {"discarded": len(args["upload_ids"])}
        elif operation == "experiments.scan" and self.nas_mode:
            ex.scan_files(ctx, ex.exp_dir(ctx, rec), ex.load_meta(ex.exp_dir(ctx, rec)))
        elif operation != "experiments.scan":
            raise WebError("不支持的操作")
        self.refresh(ctx, rec)
        return self.experiment(ctx, rec["id"])

    def export_path(self, export_id):
        if not re.fullmatch(r"[a-f0-9]{32}", export_id):
            raise WebError("导出编号无效")
        info = read_json(self.exports / f"{export_id}.json", None)
        if not info:
            raise WebError("导出文件不存在", status=404)
        with self.context() as ctx:
            path = ctx.report_dir / info["path"]
            if not inside(path, ctx.report_dir) or not path.is_file():
                raise WebError("导出文件丢失或路径无效", status=404)
        return path

    def close(self):
        with self.guard:
            self.closed = True
            for event in self.active.values():
                event.set()
        self.executor.shutdown(wait=True)
        self.instance_lock.release()
