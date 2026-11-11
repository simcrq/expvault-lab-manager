"""Independent project vaults, with a portable catalog and fixed server ownership."""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import unicodedata
import uuid
from pathlib import Path

from filelock import FileLock

from . import config
from .util import now_iso, write_json

DEFAULT = "default"
CONTAINER = "工作空间"
CATALOG = "workspaces.json"
MARKER = "workspace.json"


def normalized_name(value):
    return unicodedata.normalize("NFKC", value).strip().casefold()


class WorkspaceRegistry:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.state = self.root / ".expvault"
        self.container = self.root / CONTAINER
        if self.state.is_symlink() or self.container.is_symlink():
            raise ValueError("工作空间目录不能使用符号链接")
        self.state.mkdir(exist_ok=True)
        self.path = self.state / CATALOG
        if self.path.is_symlink() or (self.state / "workspaces.lock").is_symlink():
            raise ValueError("工作空间目录记录不能使用符号链接")
        self.lock = FileLock(str(self.state / "workspaces.lock"), timeout=5)

    @classmethod
    def locate(cls, root):
        root = Path(root).expanduser().resolve()
        marker = root / ".expvault" / MARKER
        if not marker.exists():
            return cls(root), DEFAULT
        if marker.is_symlink():
            raise ValueError("工作空间归属记录无效")
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
            if set(value) != {"id", "home"} or value["home"] != "../..":
                raise ValueError
            home = (root / value["home"]).resolve()
            registry = cls(home)
            if registry.location(value["id"]) != root or value["id"] == DEFAULT:
                raise ValueError
            registry.get(value["id"])
            return registry, value["id"]
        except (OSError, KeyError, TypeError, ValueError):
            raise ValueError("工作空间归属记录无效，请检查目录是否完整移动") from None

    def location(self, workspace_id):
        if workspace_id == DEFAULT:
            return self.root
        if not isinstance(workspace_id, str) or not re.fullmatch(r"[a-f0-9]{32}", workspace_id):
            raise ValueError("工作空间编号无效")
        path = self.container / ("ws-" + workspace_id)
        if self.container.is_symlink() or path.is_symlink() or self.root not in path.resolve().parents:
            raise ValueError("工作空间目录越界或使用了符号链接")
        return path

    def _read(self):
        if self.state.is_symlink() or self.path.is_symlink():
            raise ValueError("工作空间目录记录不能使用符号链接")
        if not self.path.exists():
            return {"version": 1, "selected": DEFAULT, "items": []}
        try:
            if self.path.stat().st_size > 2*1024**2:
                raise ValueError
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if set(value) != {"version", "selected", "items"} or value["version"] != 1 or not isinstance(value["items"], list):
                raise ValueError
            seen = {DEFAULT}
            for item in value["items"]:
                if set(item) != {"id", "name", "created_at"} or not isinstance(item["name"], str) or not item["name"].strip():
                    raise ValueError
                self.location(item["id"])
                if item["id"] in seen:
                    raise ValueError
                seen.add(item["id"])
            if value["selected"] not in seen:
                raise ValueError
            return value
        except (OSError, TypeError, KeyError, json.JSONDecodeError, ValueError):
            raise ValueError("工作空间目录记录损坏，请先恢复目录记录；不会自动覆盖") from None

    def _write(self, value):
        temporary = self.state / (".workspaces-" + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _public(self, item):
        folder = self.location(item["id"])
        cfg_path = folder / config.CONFIG_NAME
        value = {**item, "root": str(folder), "available": False, "project": "", "operator": ""}
        try:
            if cfg_path.is_symlink() or not cfg_path.is_file():
                raise ValueError
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict) or not all(isinstance(cfg.get(key, ""), str) for key in ("project", "operator")):
                raise ValueError
            value.update(available=True, project=cfg.get("project", ""), operator=cfg.get("operator", ""))
            if item["id"] == DEFAULT:
                value["name"] = cfg.get("project") or "默认工作空间"
        except (OSError, ValueError):
            value["error"] = "工作空间配置缺失或损坏，请检查原目录"
        return value

    def list(self):
        with self.lock:
            value = self._read()
            items = [{"id": DEFAULT, "name": "默认工作空间", "created_at": ""}, *value["items"]]
            return {"items": [self._public(item) for item in items], "selected": value["selected"]}

    def get(self, workspace_id):
        result = next((item for item in self.list()["items"] if item["id"] == workspace_id), None)
        if not result:
            raise ValueError("工作空间不存在，请刷新列表")
        return result

    def create(self, name, *, operator="", project=""):
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 80
                or any(ord(char) < 32 or ord(char) == 127 for char in name)):
            raise ValueError("请填写 1–80 字的工作空间名称，不含控制字符")
        if not all(isinstance(value, str) and len(value) <= 256 for value in (operator, project)):
            raise ValueError("操作人和默认课题最多 256 字")
        name = unicodedata.normalize("NFC", name.strip())
        with self.lock:
            value = self._read()
            existing = [self._public({"id": DEFAULT, "name": "默认工作空间", "created_at": ""}), *value["items"]]
            if normalized_name(name) in {normalized_name(item["name"]) for item in existing}:
                raise ValueError("已有同名工作空间，请使用便于区分的名称")
            workspace_id = uuid.uuid4().hex
            folder = self.location(workspace_id)
            ctx = config.load(self.root)
            for directory in (ctx.data_dir, ctx.backup_dir, ctx.report_dir):
                directory = directory.resolve()
                if directory == folder or directory in folder.parents or folder in directory.parents:
                    raise ValueError("新工作空间目录与当前实验的数据、备份或报告目录重叠，请先调整目录配置")
            self.container.mkdir(exist_ok=True)
            folder.mkdir()
            try:
                config.init_repo(folder, operator=operator.strip(), project=project.strip() or name)
                write_json(folder / ".expvault" / MARKER, {"id": workspace_id, "home": "../.."})
                item = {"id": workspace_id, "name": name, "created_at": now_iso()}
                public = self._public(item)
                value["items"].append(item)
                self._write(value)
                return public
            except Exception:
                if folder.is_dir() and not folder.is_symlink():
                    shutil.rmtree(folder)
                raise

    def select(self, workspace_id):
        with self.lock:
            value = self._read()
            if workspace_id != DEFAULT and workspace_id not in {item["id"] for item in value["items"]}:
                raise ValueError("工作空间不存在，请刷新列表")
            item = self._public(next((item for item in value["items"] if item["id"] == workspace_id),
                                     {"id": DEFAULT, "name": "默认工作空间", "created_at": ""}))
            if not item["available"]:
                raise ValueError(item["error"])
            value["selected"] = workspace_id
            self._write(value)
            return item


class WorkspaceHub:
    """Each opened workspace keeps its own server, token, jobs and phone inbox."""
    def __init__(self, registry, *, host, ttl):
        self.registry = registry
        self.host = host
        self.expires_at = time.monotonic() + ttl
        self.lock = threading.RLock()
        self.servers = {}
        self.closed = False
        from .sync import SyncCoordinator
        self.sync = SyncCoordinator(registry)

    def register(self, workspace_id, server):
        with self.lock:
            if self.closed or workspace_id in self.servers:
                raise ValueError("工作空间服务已关闭或重复开启")
            self.servers[workspace_id] = server

    def open(self, workspace_id):
        from .web import WebServer
        with self.lock:
            if self.closed or time.monotonic() >= self.expires_at:
                raise ValueError("网页会话已关闭或过期，请重新启动")
            if workspace_id in self.servers:
                return self.servers[workspace_id].start()
            item = self.registry.get(workspace_id)
            if not item["available"]:
                raise ValueError(item["error"])
            return WebServer(item["root"], host=self.host, port=0, hub=self, workspace_id=workspace_id).start()

    def create(self, **values):
        with self.lock:
            if self.closed:
                raise ValueError("网页服务正在关闭")
            return self.registry.create(**values)

    def select(self, workspace_id):
        with self.lock:
            server = self.open(workspace_id)
            item = self.registry.select(workspace_id)
            return {"workspace": item, "url": server.url}

    def close(self):
        self.sync.close()
        with self.lock:
            self.closed = True
            servers = list(self.servers.values())
            self.servers.clear()
        for server in servers:
            server.close(_hub_closing=True)
