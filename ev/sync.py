"""Automatic bidirectional experiment sync with an immutable relay and local rollback."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import re
import shutil
import threading
import time
import unicodedata
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

from filelock import FileLock, Timeout

from . import config, experiment as ex, journal, steps
from .file_tags import normalize_tags
from .measurements import normalize as normalize_measurements
from .sync_merge import encoded, digest, merge_states, normalize_state, choose_conflict
from .sync_transport import Repository, HTTPSRepository, LANServer, MAX_COMMIT, HEX, CHUNK
from .sync_activity import SyncActivity
from .sync_transport import machine_name
from .sync_webdav import WebDAVRepository, WebDAVError, coordinates, device_id, DEFAULT_URL
from .util import now_iso, read_json, write_json
from .workspaces import WorkspaceRegistry, DEFAULT

VIDEOS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mpeg", ".mpg", ".mts", ".m2ts", ".3gp", ".wmv"}
PHOTOS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".dng", ".raw", ".cr2", ".cr3", ".nef", ".arw"}
EMPTY = {"workspaces":{}, "experiments":{}, "issues":{}, "conflicts":{}}


def safe_relative(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or "\\" in value:
        raise ValueError("同步文件路径无效")
    path = PurePosixPath(value)
    if path.is_absolute() or PureWindowsPath(value).drive or str(path) != value:
        raise ValueError("同步文件路径无效")
    for part in path.parts:
        if (part.casefold() in (".", "..", ".expvault", ".git", "__pycache__") or any(ord(char)<32 or char in '<>:"|?*' for char in part)
                or part.endswith((" ", ".")) or len(part.encode("utf-8")) > 240
                or part.split(".")[0].upper() in {"CON","PRN","AUX","NUL",*[f"COM{i}" for i in range(1,10)],*[f"LPT{i}" for i in range(1,10)]}):
            raise ValueError("文件名无法安全地跨 Mac/Windows 同步：" + value)
    return value


def uid(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value)


class SyncEngine:
    def __init__(self, registry):
        from .nas import is_nas
        if is_nas(registry.root):
            raise ValueError("NAS 共享库直接读取同一份资料，不能同时启用双向同步")
        self.registry = registry
        self.root = registry.root
        self.state_dir = self.root / ".expvault" / "sync"
        if self.state_dir.is_symlink():
            raise ValueError("同步状态目录不能使用符号链接")
        self.state_dir.mkdir(mode=0o700, exist_ok=True)
        self.cache = Repository(self.state_dir / "cache", create=True)
        self.current_path = self.state_dir / "current.json"
        self.transaction = self.state_dir / "transaction.json"
        self.lock = FileLock(str(self.state_dir / "engine.lock"), timeout=0)
        self.stop = threading.Event()
        self.hash_cache = {}
        self.skipped = 0
        self.report = lambda **values: None

    def current(self):
        return read_json(self.current_path, {"head":None,"state":copy.deepcopy(EMPTY),"deferred":{}})

    def excluded(self, relative, value, settings):
        suffix = PurePosixPath(relative).suffix.lower()
        return (suffix in VIDEOS and not settings.get("include_videos", True)
                or suffix in PHOTOS and (not settings.get("include_photos", True)
                    or settings.get("photo_limit_mb", 0) > 0
                    and value.get("size", 0) > settings["photo_limit_mb"]*1024**2))

    def blob(self, path):
        path = Path(path)
        if path.is_symlink():
            raise ValueError("请先将符号链接转换为实际文件再同步：" + path.name)
        before = path.stat()
        signature = (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino)
        cached = self.hash_cache.get(str(path))
        if cached and cached[0] == signature and self.cache.has("objects", cached[1]):
            return cached[1]
        temporary = self.state_dir / (".capture-" + uuid.uuid4().hex + ".part")
        hasher = hashlib.sha256()
        try:
            with path.open("rb") as source, temporary.open("xb") as output:
                while block := source.read(CHUNK):
                    if self.stop.is_set():
                        raise InterruptedError("同步已停止")
                    hasher.update(block)
                    output.write(block)
            after = path.stat()
            if signature != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
                raise ValueError("文件在同步扫描期间发生变化，将在下一轮重试：" + path.name)
            key = hasher.hexdigest()
            if not self.cache.has("objects", key):
                os.replace(temporary, self.cache.path("objects", key))
            self.hash_cache[str(path)] = (signature, key)
            return key
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def signature(path):
        if not path.is_file():
            return None
        stat = path.stat()
        return [stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,stat.st_ino]

    @contextlib.contextmanager
    def contexts(self):
        values = self.registry.list()["items"]
        if any(not value["available"] for value in values):
            raise ValueError("有工作空间不可用，请先恢复其目录后同步")
        with contextlib.ExitStack() as stack:
            result = {}
            for value in values:
                ctx = config.load(Path(value["root"]))
                if ctx.data_dir.resolve() == ctx.root or ctx.root not in ctx.data_dir.resolve().parents:
                    raise ValueError("自动同步要求实验数据目录位于各工作空间内部，且不能是空间根目录")
                stack.enter_context(FileLock(str(ctx.meta_dir / "harness.lock"), timeout=0))
                stack.callback(lambda ctx=ctx: ctx._db.close() if ctx._db else None)
                result[value["id"]] = (value, ctx)
            yield result

    def collect(self, contexts, settings, current):
        result = copy.deepcopy(EMPTY)
        result["conflicts"] = copy.deepcopy(current["state"].get("conflicts", {}))
        locations, refs = {}, {}
        self.skipped = 0
        for workspace, (info, ctx) in contexts.items():
            self.report(current_file=info["name"])
            metas = ex.find_meta_files(ctx)
            if workspace != DEFAULT or metas or workspace in current["state"].get("workspaces", {}) or ctx.cfg.get("project") not in ("", "默认实验库"):
                result["workspaces"][workspace] = {"name":info["name"], "project":ctx.cfg.get("project", ""),
                    "structure":ctx.cfg["structure"], "naming":ctx.cfg.get("naming", {}), "created_at":info.get("created_at", "")}
            for path in metas:
                if path.is_symlink():
                    raise ValueError("实验元数据不能使用符号链接")
                meta = ex.load_meta(path.parent)
                if not meta or not isinstance(meta.get("id"), str):
                    raise ValueError("实验元数据无效：" + str(path))
                identity = meta.get("sync_id")
                if not uid(identity):
                    original = next((event.get("detail", "") for event in meta.get("history", []) if event.get("action") == "create"), "")
                    identity = uuid.uuid5(uuid.NAMESPACE_URL, encoded([workspace,meta["id"],meta.get("created_at"),original]).decode()).hex
                key = workspace + ":" + identity
                if key in locations:
                    raise ValueError("实验同步编号重复，请先核对重复拷贝的实验目录")
                locations[key] = path.parent
                refs[(workspace, meta["id"])] = key
                record = {"workspace":workspace, "original_id":meta.get("sync_original_id", meta["id"]),
                          "meta":copy.deepcopy(meta), "files":{}, "journal_legacy":"", "readme_tail":"", "steps_preview":False}
                for ignored in ("id", "folder", "sync_id", "sync_original_id"):
                    record["meta"].pop(ignored, None)
                for step in record["meta"].get("steps", []):
                    step.setdefault("sync_id", uuid.uuid5(uuid.NAMESPACE_URL, key+"/step/"+str(step["number"])+"/"+step.get("created_at", "")+"/"+step.get("author", "")).hex)
                previous = current["state"].get("experiments", {}).get(key, {}).get("files", {})
                case_names = set()
                for folder, dirs, names in os.walk(path.parent, followlinks=False):
                    dirs[:] = [name for name in dirs if name not in (".expvault",".git","__pycache__") and not name.endswith((".part",".tmp"))]
                    for name in list(dirs):
                        item = Path(folder)/name
                        if item.is_symlink():
                            raise ValueError("实验目录含符号链接，请先转换为实际文件")
                    for name in names:
                        item = Path(folder)/name
                        relative = item.relative_to(path.parent).as_posix()
                        if name in (".DS_Store","Thumbs.db","desktop.ini") or name.startswith("~$") or name.endswith((".tmp", ".part")) or relative == ex.META_NAME:
                            continue
                        if item.is_symlink():
                            raise ValueError("请先将符号链接转换为实际文件再同步："+relative)
                        if relative == ex.README_NAME:
                            raw = item.read_text(encoding="utf-8")
                            record["readme_tail"] = raw.split(ex.MANUAL_BEGIN,1)[1] if ex.MANUAL_BEGIN in raw else ""
                            continue
                        if relative == steps.PREVIEW:
                            record["steps_preview"] = True
                            continue
                        if relative == "04_notes/日志.md":
                            raw = item.read_text(encoding="utf-8")
                            for entry in meta.get("journal_entries", []):
                                raw = raw.replace(journal.block(entry), "")
                            record["journal_legacy"] = raw.strip()
                            continue
                        safe_relative(relative)
                        canonical = unicodedata.normalize("NFC", relative).casefold()
                        if canonical in case_names:
                            raise ValueError("实验含大小写冲突的文件名，请先重命名再同步")
                        case_names.add(canonical)
                        size = item.stat().st_size
                        self.report(current_file=meta["id"]+" / "+relative)
                        value = {"size":size}
                        if self.excluded(relative, value, settings):
                            self.skipped += 1
                            record["files"][relative] = copy.deepcopy(previous.get(relative, {"size":size,"omitted":True}))
                        else:
                            value["sha256"] = self.blob(item)
                            deferred = current.get("deferred", {}).get(key+"/"+relative)
                            if deferred and previous.get(relative, {}).get("sha256"):
                                known = previous[relative]
                                if self.signature(item) == deferred.get("signature"):
                                    value = copy.deepcopy(known)
                                elif value["sha256"] != known["sha256"] and (deferred.get("base") or {}).get("sha256") != known["sha256"]:
                                    choices = sorted([{"value":value},{"value":known}],key=digest)
                                    conflict = {"path":["experiments",key,"files",relative],"base": {"value":deferred.get("base")},"choices":choices,"kind":"file","resolved":None}
                                    conflict["id"] = digest({k:v for k,v in conflict.items() if k != "resolved"})
                                    result["conflicts"][conflict["id"]] = conflict
                            record["files"][relative] = value
                for relative, value in previous.items():
                    if relative not in record["files"] and (self.excluded(relative, value, settings)
                            or key+"/"+relative in current.get("deferred", {})):
                        record["files"][relative] = copy.deepcopy(value)
                result["experiments"][key] = record
            issues = ctx.root/"问题记录.jsonl"
            if issues.exists():
                result["issues"][workspace] = [json.loads(line) for line in issues.read_text(encoding="utf-8").splitlines() if line.strip()]
        for key, record in result["experiments"].items():
            for group in [*record["meta"].get("steps", []), *record["meta"].get("journal_entries", [])]:
                for ref in group.get("references", []):
                    target = refs.get((record["workspace"], ref.get("exp_id")))
                    if target:
                        ref["sync_exp"] = target
                        for field in ("exp_id", "relative_path", "experiment_title"):
                            ref.pop(field, None)
        result = normalize_state(result)
        # Fast-forward histories also need explicit confirmation before deleting a whole record.
        for field in ("experiments","workspaces"):
            for key in set(current["state"].get(field, {}))-set(result[field]):
                old = current["state"][field][key]
                choices = sorted([{"deleted":True},{"value":old}],key=digest)
                conflict = {"path":[field,key],"base":{"value":old},"choices":choices}
                conflict_id = digest(conflict)
                result["conflicts"][conflict_id] = {**conflict,"id":conflict_id,"kind":"record","resolved":None}
                result[field][key] = copy.deepcopy(old)
        return normalize_state(result), locations

    def validate(self, state):
        if not isinstance(state, dict) or set(state) != set(EMPTY) or not all(isinstance(value, dict) for value in state.values()):
            raise ValueError("同步清单结构无效")
        for workspace, value in state["workspaces"].items():
            if workspace != DEFAULT and not uid(workspace):
                raise ValueError("同步工作空间编号无效")
            if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not 1 <= len(value["name"]) <= 80:
                raise ValueError("同步工作空间名称无效")
            if not isinstance(value.get("structure"), list) or not isinstance(value.get("naming", {}), dict):
                raise ValueError("同步工作空间配置无效")
            for row in value.get("structure", []):
                if not isinstance(row, list) or len(row) != 3:
                    raise ValueError("同步实验目录结构无效")
                safe_relative(row[0])
                if not all(isinstance(item,str) for item in row):
                    raise ValueError("同步实验目录结构无效")
            naming = value.get("naming", {})
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,15}",naming.get("id_prefix", "E")):
                raise ValueError("同步实验编号前缀无效")
        for key, record in state["experiments"].items():
            workspace, _, identity = key.partition(":")
            if not uid(identity) or workspace not in state["workspaces"] or record.get("workspace") != workspace:
                raise ValueError("同步实验编号或工作空间无效")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", record.get("original_id", "")):
                raise ValueError("实验编号不可跨平台同步")
            meta = record["meta"]
            if (not isinstance(meta, dict) or not isinstance(meta.get("title"), str) or not isinstance(meta.get("params", {}), dict)
                    or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}",meta.get("date", ""))
                    or meta.get("status") not in ex.STATUS_LABEL or not isinstance(record.get("files"), dict)
                    or not all(isinstance(record.get(field, ""),str) for field in ("readme_tail","journal_legacy"))):
                raise ValueError("同步实验元数据无效")
            normalize_measurements(meta.get("measurement_groups", []))
            from .groups import normalize_names
            normalize_names(meta.get("groups"), limit=None)
            canonical_names = set()
            for relative, value in record["files"].items():
                safe_relative(relative)
                canonical = unicodedata.normalize("NFC",relative).casefold()
                if canonical in canonical_names:
                    raise ValueError("同步文件名存在跨平台大小写或 Unicode 冲突")
                canonical_names.add(canonical)
                if canonical in {unicodedata.normalize("NFC",name).casefold() for name in (ex.META_NAME, ex.README_NAME, steps.PREVIEW, "04_notes/日志.md")}:
                    raise ValueError("同步清单不能覆盖受保护的生成文件")
                if not isinstance(value.get("size"), int) or isinstance(value["size"], bool) or value["size"] < 0:
                    raise ValueError("同步文件大小无效")
                if not value.get("omitted") and not HEX.fullmatch(value.get("sha256", "")):
                    raise ValueError("同步文件摘要无效")
            if any(str(parent) in canonical_names for name in canonical_names for parent in PurePosixPath(name).parents if str(parent) != "."):
                raise ValueError("同步清单中的文件与目录路径冲突")
            for step in meta.get("steps", []):
                if not uid(step.get("sync_id")) or not isinstance(step.get("number"), int):
                    raise ValueError("同步步骤格式无效")
                for image in step.get("images", []):
                    safe_relative(image["path"])
            for entry in meta.get("journal_entries", []):
                if not uid(entry.get("id")):
                    raise ValueError("同步日志编号无效")
            for item in [*meta.get("steps", []), *meta.get("journal_entries", [])]:
                for ref in item.get("references", []):
                    safe_relative(ref["path"])
                    if "sync_exp" in ref and ref["sync_exp"].split(":",1)[0] != workspace:
                        raise ValueError("同步引用不能跨工作空间")
        for key, conflict in state["conflicts"].items():
            path = conflict.get("path", [])
            if (not HEX.fullmatch(key) or conflict.get("id") != key or len(conflict.get("choices", [])) != 2
                    or not isinstance(path,list) or not path or path[0] not in EMPTY or path[0] == "conflicts"
                    or not all(isinstance(part,str) for part in path) or conflict.get("kind") not in ("file","record")
                    or not all(isinstance(choice,dict) and (set(choice) == {"value"} or choice == {"deleted":True}) for choice in conflict["choices"])):
                raise ValueError("同步冲突记录无效")

    def commit(self, state, parents):
        self.validate(state)
        value = {"protocol":1,"parents":sorted(set(parent for parent in parents if parent)),"state":state}
        key = self.cache.put_bytes("commits", encoded(value))
        return key

    def graph(self, transport):
        result = {}
        commits = transport.list_commits()
        for index, key in enumerate(commits):
            self.report(done_files=index,total_files=len(commits))
            if self.stop.is_set():
                raise InterruptedError("同步已停止")
            if not self.cache.has("commits", key):
                transport.send(self.cache, "commits", key, self.stop)
            value = json.loads(self.cache.get("commits", key))
            if value.get("protocol") != 1 or not isinstance(value.get("parents"), list) or not all(HEX.fullmatch(parent) for parent in value["parents"]):
                raise ValueError("同步提交协议无效")
            self.validate(value["state"])
            result[key] = value
        self.report(done_files=len(commits),total_files=len(commits))
        # A cloud folder can deliver a child before its parents. Wait; never guess a base.
        if any(parent not in result for value in result.values() for parent in value["parents"]):
            raise ValueError("同步目录尚未传完历史提交，稍后自动重试")
        return result

    @staticmethod
    def common(graph, left, right):
        def distances(start):
            values, queue = {}, [(start,0)]
            while queue:
                key, distance = queue.pop(0)
                if key in values:
                    continue
                values[key] = distance
                if len(values) > 50000:
                    raise ValueError("同步历史过长，请归档后建立新的同步目录")
                queue.extend((parent,distance+1) for parent in graph[key]["parents"])
            return values
        l, r = distances(left), distances(right)
        candidates = set(l)&set(r)
        if not candidates:
            return EMPTY
        key = min(candidates, key=lambda item:(max(l[item],r[item]),l[item]+r[item],item))
        return graph[key]["state"]

    def transfer_objects(self, state, transport, settings, *, upload):
        seen, pending = set(), []
        direction = "上传" if upload else "下载"
        self.report(phase="prepare-upload" if upload else "prepare-download",label="核对待"+direction+"文件",reset=True)
        for record in state["experiments"].values():
            for relative, value in record["files"].items():
                key = value.get("sha256")
                if not key or value.get("omitted") or key in seen or self.excluded(relative, value, settings):
                    continue
                seen.add(key)
                if upload:
                    if self.cache.has("objects", key) and not transport.has("objects",key):
                        pending.append((key,value["size"],record.get("display_id",record["original_id"])+" / "+relative))
                elif not self.cache.has("objects", key):
                    pending.append((key,value["size"],record.get("display_id",record["original_id"])+" / "+relative))
        self.report(phase="upload" if upload else "download",label=direction+"实验文件",reset=True,
                    total_files=len(pending),bytes_total=sum(size for _,size,_ in pending))
        for index, (key, size, label) in enumerate(pending):
            self.report(current_file=label)
            progress = lambda count: self.report(bytes_delta=count)
            if upload:
                transport.receive(self.cache,"objects",key,self.stop,progress)
            else:
                transport.send(self.cache,"objects",key,self.stop,progress)
            self.report(done_files=index+1,file_complete=direction+"完成")

    def checked_path(self, relative):
        path = self.root / relative
        if self.root not in path.resolve().parents:
            raise ValueError("同步写入越过实验库边界")
        for parent in [path, *path.parents]:
            if parent == self.root:
                break
            if parent.is_symlink():
                raise ValueError("同步写入路径包含符号链接")
        return path

    def put_file(self, relative, key):
        target = self.checked_path(relative)
        if key is None:
            target.unlink(missing_ok=True)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name("." + target.name + "-sync-" + uuid.uuid4().hex + ".part")
        try:
            with self.cache.path("objects", key).open("rb") as source, temporary.open("xb") as stream:
                hasher = hashlib.sha256()
                while block := source.read(CHUNK):
                    if self.stop.is_set():
                        raise InterruptedError("同步已停止")
                    stream.write(block)
                    hasher.update(block)
                stream.flush()
                os.fsync(stream.fileno())
            if hasher.hexdigest() != key:
                raise ValueError("本机同步缓存校验失败，原文件未覆盖")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def rebuild(self):
        for value in self.registry.list()["items"]:
            if not value["available"]:
                continue
            ctx = config.load(Path(value["root"]))
            try:
                # SQLite is a local cache; never copy or merge its WAL files.
                ctx.db.conn.execute("DELETE FROM files")
                ctx.db.conn.execute("DELETE FROM experiments")
                ctx.db.commit()
                ex.rebuild_index(ctx)
                for path in ex.find_meta_files(ctx):
                    meta = ex.load_meta(path.parent)
                    rec = ctx.db.get_experiment(meta["id"])
                    ex.write_readme(ctx, meta, ctx.db.files_of(meta["id"], include_missing=False), folder=path.parent)
                    if (path.parent/steps.PREVIEW).exists():
                        steps._write_preview(path.parent, meta)
                        ex.scan_files(ctx, path.parent, meta, rehash=True)
            finally:
                if ctx._db:
                    ctx.db.close()

    def recover(self, locked=False):
        if not self.transaction.exists():
            return
        try:
            pending = json.loads(self.transaction.read_bytes())
            if not isinstance(pending,dict) or not isinstance(pending.get("operations"),list) or not isinstance(pending.get("roots"),list):
                raise ValueError
        except (ValueError, OSError):
            raise ValueError("同步恢复记录损坏，请保留本机资料与恢复记录后核对；已停止新的同步写入") from None
        if pending.get("committed"):
            self.transaction.unlink(missing_ok=True)
            return
        with contextlib.ExitStack() as locks:
            for relative in ([] if locked else pending["roots"]):
                path = self.root/relative/".expvault"
                path.mkdir(parents=True, exist_ok=True)
                locks.enter_context(FileLock(str(path/"harness.lock"), timeout=0))
            # Ignore cancellation while restoring an interrupted transaction.
            cancelled = self.stop.is_set()
            self.stop.clear()
            try:
                for operation in reversed(pending["operations"]):
                    self.put_file(operation["path"], operation["before"])
                self.rebuild()
                self.transaction.unlink(missing_ok=True)
            finally:
                if cancelled:
                    self.stop.set()

    def apply(self, state, locations, current, contexts, head, settings):
        self.report(phase="apply",label="保存合并结果并更新本机索引",reset=True)
        self.validate(state)
        state = normalize_state(state)
        operations = {}
        generated = {}
        file_changes = {}
        deferred = {}
        roots = {}

        def plan(path, data=None, key=None, remove=False):
            relative = path.relative_to(self.root).as_posix()
            target = self.checked_path(relative)
            before = self.blob(target) if target.is_file() else None
            after = None if remove else self.cache.put_bytes("objects", data) if data is not None else key
            if before != after:
                operations[relative] = {"path":relative,"before":before,"after":after}

        for workspace, value in state["workspaces"].items():
            root = self.registry.location(workspace)
            existing = contexts.get(workspace)
            cfg = copy.deepcopy(existing[1].cfg if existing else config.default_config(root))
            cfg["project"] = value.get("project", value["name"])
            cfg["structure"] = value["structure"]
            cfg["naming"] = value.get("naming", cfg["naming"])
            roots[workspace] = config.Ctx(root=root, cfg=cfg)
            plan(root/config.CONFIG_NAME, encoded(cfg))
            if workspace != DEFAULT:
                plan(root/".expvault/workspace.json", encoded({"id":workspace,"home":"../.."}))
        # Empty local default space remains valid even if the relay has no experiments yet.
        if DEFAULT not in roots:
            roots[DEFAULT] = contexts[DEFAULT][1]
        items = [{"id":key,"name":value["name"],"created_at":value.get("created_at", "")} for key,value in state["workspaces"].items() if key != DEFAULT]
        catalog = self.registry._read()
        plan(self.registry.path, encoded({"version":1,"selected":catalog["selected"] if catalog["selected"] in {DEFAULT,*[item['id'] for item in items]} else DEFAULT,"items":items}))
        folders = {}
        for key, rec in state["experiments"].items():
            ctx = roots[rec["workspace"]]
            folder = locations.get(key)
            if folder is None:
                folder = ctx.data_dir/rec["meta"].get("date", "unknown")[:4]/(rec["display_id"]+"_sync-"+key.rsplit(":",1)[-1][:12])
                if (folder/ex.META_NAME).exists():
                    raise ValueError("同步导入目录已被其他实验占用")
            folders[key] = folder
        for key, rec in state["experiments"].items():
            ctx, folder = roots[rec["workspace"]], folders[key]
            meta = copy.deepcopy(rec["meta"])
            meta.update(id=rec["display_id"], sync_id=key.rsplit(":",1)[-1], sync_original_id=rec["original_id"])
            for item in [*meta.get("steps", []), *meta.get("journal_entries", [])]:
                for ref in item.get("references", []):
                    target = ref.pop("sync_exp", None)
                    if target in folders:
                        other = state["experiments"][target]
                        ref.update(exp_id=other["display_id"], experiment_title=other["meta"]["title"],
                                   relative_path=os.path.relpath(folders[target]/ref["path"], folder).replace("\\", "/"))
            previous = current["state"].get("experiments", {}).get(key, {}).get("files", {})
            for relative, value in rec["files"].items():
                target = folder/relative
                if (self.excluded(relative, value, settings) or value.get("omitted")
                        or target.is_file() and self.excluded(relative, {"size":target.stat().st_size}, settings)):
                    old = current.get("deferred", {}).get(key+"/"+relative)
                    deferred[key+"/"+relative] = old or {"signature":self.signature(target),"base":previous.get(relative)}
                    continue
                if not self.cache.has("objects", value["sha256"]):
                    raise ValueError("同步对象尚未到齐，已保留当前实验，将自动重试")
                plan(target, key=value["sha256"])
                file_changes[target.relative_to(self.root).as_posix()] = (rec["display_id"]+" / "+relative,"已保存到本机")
            for relative, old in previous.items():
                if relative not in rec["files"] and not self.excluded(relative, old, settings):
                    plan(folder/relative, remove=True)
                    file_changes[(folder/relative).relative_to(self.root).as_posix()] = (rec["display_id"]+" / "+relative,"已删除本机文件")
            plan(folder/ex.META_NAME, encoded(meta))
            content = rec.get("journal_legacy", "")
            for entry in meta.get("journal_entries", []):
                content += "\n\n" + journal.block(entry)
            if content or (folder/"04_notes/日志.md").exists():
                plan(folder/"04_notes/日志.md", (content.rstrip()+"\n").encode())
            preview = folder/steps.PREVIEW
            if rec.get("steps_preview") and not preview.exists():
                plan(preview, b"")
            for path in (folder/ex.README_NAME, preview):
                if path == preview and not rec.get("steps_preview"):
                    continue
                relative = path.relative_to(self.root).as_posix()
                generated[relative] = {"path":relative,"before":self.blob(path) if path.is_file() else None,"after":None,"generated":True}
            # The local README manual section is part of the merged state.
            text = ex.render_readme(ctx, meta).split(ex.MANUAL_BEGIN)[0]+ex.MANUAL_BEGIN+rec.get("readme_tail", "")
            plan(folder/ex.README_NAME, text.encode())
        for key, rec in current["state"].get("experiments", {}).items():
            if key in state["experiments"] or key not in locations:
                continue
            folder = locations[key]
            for relative in [*rec["files"],ex.META_NAME,ex.README_NAME,"04_notes/日志.md",steps.PREVIEW]:
                if relative in rec["files"] and self.excluded(relative, rec["files"][relative], settings):
                    continue
                plan(folder/relative, remove=True)
        for workspace, values in state["issues"].items():
            if workspace in roots:
                plan(roots[workspace].root/"问题记录.jsonl", b"".join(encoded(value)+b"\n" for value in values))
        next_current = {"head":head,"state":state,"deferred":deferred,
                        "filters":{"include_photos":settings.get("include_photos", True),"include_videos":settings.get("include_videos", True),"photo_limit_mb":settings.get("photo_limit_mb", 0)}}
        plan(self.current_path, encoded(next_current))
        values = list(operations.values())
        for relative, operation in generated.items():
            if relative not in operations:
                values.append(operation)
        transaction = {"roots":[ctx.root.relative_to(self.root).as_posix() for ctx in roots.values()],"operations":values}
        write_json(self.transaction, transaction)
        try:
            self.report(total_files=len(operations))
            for index, operation in enumerate(operations.values()):
                if self.stop.is_set():
                    raise InterruptedError("同步已停止")
                change = file_changes.get(operation["path"])
                if change:
                    self.report(current_file=change[0])
                self.put_file(operation["path"], operation["after"])
                self.report(done_files=index+1, file_complete=change[1] if change else None)
            self.report(label="更新本机实验索引")
            self.rebuild()
            transaction["committed"] = True
            write_json(self.transaction, transaction)
            self.transaction.unlink(missing_ok=True)
        except Exception:
            self.report(phase="rollback",label="恢复本机原始数据",reset=True)
            self.recover(locked=True)
            raise

    def cycle(self, transport, settings, resolve=None):
        with self.lock:
            self.recover()
            current = self.current()
            self.report(phase="scan",label="扫描本机实验资料",reset=True)
            with self.contexts() as contexts:
                local, locations = self.collect(contexts, settings, current)
            if resolve:
                local = choose_conflict(local, resolve["id"], resolve["choice"])
            head = current["head"]
            if not head or local != current["state"]:
                head = self.commit(local, [head])
            self.transfer_objects(local, transport, settings, upload=True)
            self.report(phase="history-upload",label="发布本机记录",reset=True)
            # Repair a completed local transaction whose announcement was interrupted.
            def announce(key):
                if not key or transport.has("commits", key):
                    return
                value = json.loads(self.cache.get("commits", key))
                for parent in value["parents"]:
                    announce(parent)
                transport.receive(self.cache, "commits", key, self.stop)
            announce(head)
            if isinstance(transport, WebDAVRepository):
                transport.publish_head(head)
            self.report(phase="history",label="读取远端变更记录",reset=True)
            graph = self.graph(transport)
            graph.setdefault(head, json.loads(self.cache.get("commits", head)))
            parents = {parent for value in graph.values() for parent in value["parents"]}
            heads = sorted(set(graph)-parents)
            merged, merged_head = local, head
            self.report(phase="merge",label="比较并合并实验记录",reset=True)
            def ancestor(first, second):
                seen, queue = set(), [second]
                while queue:
                    key = queue.pop()
                    if key == first:
                        return True
                    if key not in seen:
                        seen.add(key)
                        queue.extend(graph[key]["parents"])
                return False
            for other in heads:
                if other == merged_head:
                    continue
                if ancestor(merged_head, other):
                    merged, merged_head = graph[other]["state"], other
                    continue
                if ancestor(other, merged_head):
                    continue
                base = self.common(graph, merged_head, other)
                merged = merge_states(base, merged, graph[other]["state"])
                merged_head = self.commit(merged, [merged_head, other])
                graph[merged_head] = json.loads(self.cache.get("commits", merged_head))
            self.transfer_objects(merged, transport, settings, upload=False)
            filters = {"include_photos":settings.get("include_photos", True),"include_videos":settings.get("include_videos", True),"photo_limit_mb":settings.get("photo_limit_mb", 0)}
            if merged_head == current["head"] and merged == current["state"] and current.get("filters") == filters:
                return {"last_sync":now_iso(),"experiments":len(merged["experiments"]),"workspaces":len(merged["workspaces"]),
                        "skipped_files":self.skipped,"conflicts":sum(not value.get("resolved") for value in merged["conflicts"].values())}
            with self.contexts() as contexts, self.registry.lock:
                self.report(phase="verify",label="复查本机修改，准备保存",reset=True)
                fresh, locations = self.collect(contexts, settings, current)
                if resolve:
                    fresh = choose_conflict(fresh, resolve["id"], resolve["choice"])
                if fresh != local:
                    raise ValueError("扫描后本机记录发生变化，已保留最新内容，下一轮重新合并")
                with contextlib.ExitStack() as added:
                    for workspace in merged["workspaces"]:
                        if workspace not in contexts:
                            folder = self.registry.location(workspace)/".expvault"
                            folder.mkdir(parents=True, exist_ok=True)
                            added.enter_context(FileLock(str(folder/"harness.lock"),timeout=0))
                    self.apply(merged, locations, current, contexts, merged_head, settings)
            self.transfer_objects(merged, transport, settings, upload=True)
            self.report(phase="publish",label="发布合并结果",reset=True)
            announce(merged_head)
            if isinstance(transport, WebDAVRepository):
                transport.publish_head(merged_head)
            return {"last_sync":now_iso(),"experiments":len(merged["experiments"]),"workspaces":len(merged["workspaces"]),
                    "skipped_files":self.skipped,"conflicts":sum(not value.get("resolved") for value in merged["conflicts"].values())}


class SyncCoordinator:
    def __init__(self, registry):
        self.engine = SyncEngine(registry)
        self.settings_path = self.engine.state_dir/"settings.json"
        self.status_path = self.engine.state_dir/"status.json"
        self.settings = read_json(self.settings_path, {"enabled":False,"mode":"","interval":30,"include_photos":True,"include_videos":True,"photo_limit_mb":0})
        self.status = read_json(self.status_path, {})
        self.status["running"] = False
        self.connection = {}
        self.last_applied_at = 0
        self.activity = SyncActivity(self.engine.state_dir/"activity.json", self.redact)
        self.engine.report = self.activity.notify
        self.guard = threading.RLock()
        self.work_lock = threading.Lock()
        self.wake = threading.Event()
        self.closed = threading.Event()
        self.thread = None
        self.server = None
        self.webdav = None
        self.pending_resolution = None

    def redact(self, value):
        password = self.settings.get("webdav_password")
        basic = None
        if password:
            import base64
            basic = base64.b64encode((self.settings.get("webdav_username", "") + ":" + password).encode()).decode()
        for secret in (self.settings.get("token"),self.settings.get("pairing",{}).get("token"),password,basic):
            if secret:
                value = value.replace(secret,"[已隐藏密钥]")
        return value

    def connected_peer(self, name, address):
        self.activity.log("info", "已认证电脑连接："+name+"（"+address+"）",phase="connection")

    def received_file(self, address, size, accepted):
        self.activity.log("info" if accepted else "warning",f'从 {address} 接收文件{"完成" if accepted else "中断"}（{size} 字节）',phase="receiving")

    def connection_info(self):
        settings = self.settings
        server = self.server
        mode = settings.get("mode")
        result = {"mode":mode,"local_hostname":machine_name(),"state":"unverified",**self.connection}
        if mode == "lan-client":
            result["address"] = settings["pairing"]["url"]
        elif mode == "folder":
            result.update(address=settings["folder"],hostname="共享目录中转")
        elif mode == "webdav":
            result.update(address=settings["webdav_url"] + settings["webdav_folder"] + "/expvault-webdav-v1/",
                          hostname="坚果云 WebDAV" if urlsplit(settings["webdav_url"]).hostname == "dav.jianguoyun.com" else "WebDAV 云端中转")
        elif mode == "lan-host":
            result.update(address=f'https://{settings["host"]}:{settings["port"]}',hostname=machine_name(),
                          peers=server.connections() if server else [],state="listening" if server else "unverified")
        if not settings.get("enabled"):
            result["state"] = "paused" if mode else "unconfigured"
        return result

    def check_connection(self, transport):
        if isinstance(transport, (HTTPSRepository, WebDAVRepository)):
            info = transport.info()
        else:
            info = {"hostname":"共享目录中转" if self.settings["mode"] == "folder" else machine_name()}
        self.connection = {**info,"hostname":self.redact(info.get("hostname","")),"state":"verified","last_seen":now_iso()}

    def save_settings(self):
        temporary = self.engine.state_dir/(".settings-"+uuid.uuid4().hex+".tmp")
        try:
            with os.fdopen(os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600),"wb") as stream:
                stream.write(encoded(self.settings))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary,self.settings_path)
        finally:
            temporary.unlink(missing_ok=True)

    def start(self):
        with self.guard:
            if not self.thread and not self.closed.is_set():
                self.thread = threading.Thread(target=self.worker, name="expvault-sync", daemon=True)
                self.thread.start()

    def transport(self):
        with self.guard:
            return self._transport()

    def _transport(self):
        settings = self.settings
        if settings["mode"] == "folder":
            folder = Path(settings["folder"])/"expvault-sync-v1"
            channel = read_json(folder/"channel.json", {})
            if channel.get("id") != settings["channel"]:
                raise ValueError("共享目录未挂载或同步组已变化，保留本机数据并等待重新连接")
            return Repository(folder)
        if settings["mode"] == "lan-host":
            repository = Repository(self.engine.state_dir/"lan-relay", create=True)
            if not self.server:
                self.server = LANServer(repository, self.engine.state_dir/"tls", settings["host"], port=settings["port"], token=settings["token"],on_peer=self.connected_peer,on_received=self.received_file)
                self.settings["port"] = self.server.port
                self.save_settings()
            return repository
        if settings["mode"] == "lan-client":
            return HTTPSRepository(**settings["pairing"])
        if settings["mode"] == "webdav":
            if not self.webdav:
                self.webdav = WebDAVRepository(settings["webdav_url"], settings["webdav_username"], settings["webdav_password"], settings["webdav_folder"],
                    cache=self.engine.cache, device=device_id(self.engine.root), channel=settings["channel"], stop=self.engine.stop)
            return self.webdav
        raise ValueError("请先配置同步方式")

    def configure(self, values):
        allowed = {"mode","folder","host","port","pairing","interval","include_photos","include_videos","photo_limit_mb",
                   "webdav_url","webdav_username","webdav_password","webdav_folder"}
        if not isinstance(values, dict) or set(values)-allowed:
            raise ValueError("同步配置字段无效")
        mode = values.get("mode")
        interval, limit = values.get("interval",300 if mode == "webdav" else 30), values.get("photo_limit_mb",0)
        if mode not in ("folder","lan-host","lan-client","webdav") or type(interval) is not int or not 5 <= interval <= 3600:
            raise ValueError("请选择同步方式，自动同步间隔为 5–3600 秒")
        if mode == "webdav" and interval < 60:
            raise ValueError("WebDAV 自动检查间隔至少 60 秒，坚果云建议使用 300 秒或更长")
        if type(limit) not in (int,float) or not 0 <= limit <= 100000 or any(not isinstance(values.get(key,True),bool) for key in ("include_photos","include_videos")):
            raise ValueError("照片上限必须是非负 MB 数值，0 表示不限")
        settings = {"mode":mode,"enabled":True,"interval":interval,"include_photos":values.get("include_photos",True),"include_videos":values.get("include_videos",True),"photo_limit_mb":limit}
        candidate = None
        if mode == "folder":
            folder = Path(values.get("folder", "")).expanduser()
            if not folder.is_absolute():
                raise ValueError("请填写两台电脑都能访问的共享目录绝对路径")
            folder = folder.resolve()
            relay = folder/"expvault-sync-v1"
            if relay == self.engine.root or self.engine.root in relay.parents or relay in self.engine.root.parents:
                raise ValueError("共享同步目录必须放在实验库之外")
            Repository(relay, create=True)
            path = relay/"channel.json"
            if not path.exists():
                try:
                    with path.open("x",encoding="utf-8") as stream:
                        json.dump({"protocol":1,"id":uuid.uuid4().hex}, stream)
                except FileExistsError:
                    pass
            channel = read_json(path, {})
            if not uid(channel.get("id")) or channel.get("protocol") != 1:
                raise ValueError("共享同步目录记录无效；不会覆盖原记录")
            settings.update(folder=str(folder), channel=channel["id"])
        elif mode == "lan-host":
            from .sync_transport import private_host
            host = private_host(values.get("host", ""))
            port = values.get("port",8766)
            if type(port) is not int or not 0 <= port <= 65535:
                raise ValueError("同步端口无效")
            import secrets
            old = self.settings
            token = old.get("token") if old.get("mode") == mode and old.get("host") == host and old.get("port") == port else None
            settings.update(host=host, port=port, token=token or secrets.token_urlsafe(32))
        elif mode == "lan-client":
            pairing = values.get("pairing", self.settings.get("pairing") if self.settings.get("mode") == mode else None)
            if not isinstance(pairing, dict) or set(pairing) != {"url","token","fingerprint"}:
                raise ValueError("请粘贴另一台电脑提供的完整配对信息")
            HTTPSRepository(**pairing)
            settings["pairing"] = pairing
        else:
            url = values.get("webdav_url", DEFAULT_URL)
            username = values.get("webdav_username", "")
            password = values.get("webdav_password", "")
            folder = values.get("webdav_folder", "ExpVault")
            # A blank password only reuses credentials for the very same endpoint and account.
            if password == "" and self.settings.get("mode") == mode:
                old_url = self.settings.get("webdav_url")
                old_user = self.settings.get("webdav_username")
                normalized = coordinates(url, username, self.settings["webdav_password"], folder)
                if normalized[:2] == (old_url, old_user):
                    password = self.settings["webdav_password"]
            url, username, password, folder = coordinates(url, username, password, folder)
            device = device_id(self.engine.root)
            candidate = WebDAVRepository(url, username, password, folder, cache=self.engine.cache, device=device)
            channel = candidate.initialize()
            settings.update(webdav_url=url, webdav_username=username, webdav_password=password, webdav_folder=folder, channel=channel)
        self.engine.stop.set()
        if not self.work_lock.acquire(timeout=20):
            raise ValueError("正在完成文件回滚，请稍后再配置")
        try:
            with self.engine.lock:
                self.engine.recover()
            previous = self.settings
            if self.server:
                self.server.close()
                self.server = None
            def identity(value):
                return (value.get("mode"),value.get("channel"),value.get("pairing",{}).get("url"),value.get("pairing",{}).get("fingerprint"),
                        value.get("webdav_url"),value.get("webdav_folder"),value.get("webdav_username"))
            changed_channel = identity(previous) != identity(settings)
            previous_webdav, self.webdav = self.webdav, candidate
            if candidate:
                if previous_webdav and not changed_channel:
                    candidate.present.update(previous_webdav.present)
                    candidate.known_directories.update(previous_webdav.known_directories)
                    candidate.head_cache.update(previous_webdav.head_cache)
                    if candidate.device == previous_webdav.device:
                        candidate.published = previous_webdav.published
                candidate.stop = self.engine.stop
            self.settings = settings
            try:
                self.transport()
            except Exception:
                self.settings = previous
                self.webdav = previous_webdav
                raise
            if changed_channel and self.engine.current_path.exists():
                shutil.copy2(self.engine.current_path, self.engine.state_dir/("previous-channel-"+uuid.uuid4().hex+".json"))
                self.engine.current_path.unlink()
            if changed_channel:
                self.status = {}
                self.connection = {}
                write_json(self.status_path,self.status)
            self.save_settings()
            self.status.pop("retry_at", None)
            write_json(self.status_path,self.status)
            self.engine.stop.clear()
            self.activity.log("info", "同步设置已保存："+{"folder":"共享目录中转","lan-host":"局域网接收电脑","lan-client":"连接另一电脑","webdav":"WebDAV 云端中转"}[mode],phase="configuration",force=True)
        finally:
            self.work_lock.release()
        self.start()
        self.wake.set()
        return self.public()

    def public(self):
        with self.guard:
            settings = {key:value for key,value in self.settings.items() if key not in ("token","pairing","channel","webdav_password","webdav_device")}
            if self.settings.get("mode") == "webdav":
                settings["webdav_password_saved"] = bool(self.settings.get("webdav_password"))
            if self.settings.get("pairing"):
                settings["peer_url"] = self.settings["pairing"]["url"]
            current = self.engine.current()["state"]
            pending = []
            for item in current.get("conflicts", {}).values():
                if item.get("resolved"):
                    continue
                record = current.get("experiments", {}).get(item["path"][1]) if len(item["path"]) > 1 and item["path"][0] == "experiments" else None
                pending.append({"id":item["id"],"kind":item["kind"],"path":item["path"],
                    "title":record["meta"]["title"] if record else "工作空间记录", "exp_id":record.get("display_id") if record else "",
                    "workspace":record["workspace"] if record else DEFAULT,
                    "choices":["（删除）" if value.get("deleted") else json.dumps(value.get("value"),ensure_ascii=False,indent=2)[:5000] for value in item["choices"]]})
            progress = self.activity.snapshot()
            server = self.server
            incoming = server.incoming_progress() if server else None
            if incoming and (incoming["active"] or not self.status.get("running") and incoming.get("finished_at",0) > self.last_applied_at):
                progress = incoming
                progress["current_file"] = self.redact(progress.get("current_file",""))
            if not settings.get("enabled") and not self.status.get("running"):
                progress.update(phase="paused" if settings.get("mode") else "idle",label="自动同步已暂停" if settings.get("mode") else "等待配置同步",percent=None,indeterminate=True)
            return {"settings":settings,"status":copy.deepcopy(self.status),"configured":bool(settings.get("mode")),
                    "connection":self.connection_info(),"progress":progress,"logs":self.activity.logs(50),
                    "pending_count":len(pending),"conflicts":pending[:100],"more_conflicts":len(pending)>100}

    def action(self, action, **values):
        if action != "resolve" and values:
            raise ValueError("同步操作包含多余字段")
        if action == "pairing":
            with self.work_lock:
                if self.settings.get("mode") != "lan-host":
                    raise ValueError("请先配置为局域网直连的接收电脑")
                if not self.settings.get("enabled"):
                    raise ValueError("请先恢复自动同步，再显示配对信息")
                self.transport()
                return self.server.pairing()
        if not self.settings.get("mode"):
            raise ValueError("请先配置同步")
        with self.guard:
            if action in ("pause","resume"):
                self.settings["enabled"] = action == "resume"
                self.save_settings()
                if action == "pause":
                    self.engine.stop.set()
                    if self.server:
                        self.server.close()
                        self.server = None
                else:
                    self.engine.stop.clear()
                self.activity.log("info", "已暂停自动同步" if action == "pause" else "已恢复自动同步",phase="paused" if action == "pause" else "resume",force=True)
            elif action == "resolve":
                if set(values) != {"id","choice"} or not isinstance(values["id"],str) or not HEX.fullmatch(values["id"]) or type(values["choice"]) is not int or values["choice"] not in (0,1,2):
                    raise ValueError("冲突处理参数无效")
                if self.pending_resolution:
                    raise ValueError("上一条冲突正在处理，请等待完成")
                self.pending_resolution = values
                self.settings["enabled"] = True
                self.save_settings()
                self.engine.stop.clear()
                self.activity.log("info", "已提交冲突处理选择",phase="resolution",force=True)
            elif action != "run":
                raise ValueError("同步操作无效")
            else:
                self.activity.log("info", "已请求立即同步",phase="manual",force=True)
        self.start()
        self.wake.set()
        return self.public()

    def worker(self):
        while not self.closed.is_set():
            if self.engine.transaction.exists():
                try:
                    self.activity.notify(phase="recovery",label="恢复上次中断的同步",reset=True)
                    with self.work_lock, self.engine.lock:
                        self.engine.recover()
                except Exception as exc:
                    self.status.update(running=False,error="正在等待恢复中断的同步："+str(exc))
                    self.activity.finish("error",self.status["error"])
                    self.wake.wait(5)
                    self.wake.clear()
                    continue
            if self.settings.get("enabled") and self.settings.get("mode"):
                retry = self.status.get("retry_at", 0) - time.time()
                if retry > 0:
                    self.wake.wait(min(retry, self.settings.get("interval",30)))
                    self.wake.clear()
                    continue
                with self.work_lock:
                    if self.closed.is_set():
                        break
                    if not self.settings.get("enabled"):
                        continue
                    self.engine.stop.clear()
                    self.status.update(running=True, error="")
                    self.activity.begin()
                    self.connection["state"] = "checking"
                    try:
                        transport = self.transport()
                        self.check_connection(transport)
                        result = self.engine.cycle(transport, self.settings, self.pending_resolution)
                        self.pending_resolution = None
                        self.status.update(result)
                        self.status.pop("retry_at", None)
                        self.last_applied_at = time.time()
                        self.activity.finish("complete",f'同步完成：{result["experiments"]} 个实验，{result["workspaces"]} 个工作空间；跳过 {result["skipped_files"]} 个文件，待处理冲突 {result["conflicts"]} 处')
                    except Timeout:
                        self.status["error"] = "等待本机写入完成，下一轮自动重试"
                        self.activity.finish("waiting",self.status["error"])
                    except InterruptedError:
                        self.status["error"] = "同步已暂停，完整数据仍保留"
                        self.activity.finish("paused",self.status["error"])
                    except WebDAVError as exc:
                        self.status["error"] = self.redact(str(exc))
                        if exc.retry_after:
                            self.status["retry_at"] = time.time() + exc.retry_after
                        self.connection["state"] = "unverified"
                        self.activity.finish("error",self.status["error"])
                    except Exception as exc:
                        self.status["error"] = self.redact(str(exc))
                        self.connection["state"] = "unverified"
                        self.activity.finish("error",self.status["error"])
                        self.pending_resolution = None
                    finally:
                        self.status["running"] = False
                        write_json(self.status_path, self.status)
            self.wake.wait(self.settings.get("interval",30))
            self.wake.clear()

    def close(self):
        self.closed.set()
        self.engine.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join()
        if self.server:
            self.server.close()
            self.server = None
        self.activity.flush()
