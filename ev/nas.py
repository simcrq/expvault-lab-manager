"""Mounted NAS vaults: shared documents, local SQLite, and one explicit writer."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import threading
import time
import uuid
from pathlib import Path, PureWindowsPath

from filelock import FileLock, Timeout

REGISTRY_LOCK = threading.RLock()
WRITERS = {}
INDEX_INTERVAL = 2


def storage_root():
    override = os.environ.get("EXPVAULT_LOCAL_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()/"AppData/Local"))) / "expvault"
    if platform.system() == "Darwin":
        return Path.home()/"Library/Application Support/expvault"
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home()/".local/state")))/"expvault"


def read_object(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("NAS 配置格式无效")
    return value


def settings(root, cfg=None):
    root = Path(root).expanduser().resolve()
    if cfg is None:
        try:
            if (root/"config.json").is_symlink():
                raise ValueError("NAS 配置不能使用符号链接")
            cfg = read_object(root/"config.json")
        except FileNotFoundError:
            return None
    value = cfg.get("storage", {})
    if value and (not isinstance(value, dict) or value.get("kind") != "nas"):
        raise ValueError("存储设置无效，请恢复实验库配置")
    if not value:
        marker=root/".expvault/workspace.json"
        if marker.exists():
            if marker.is_symlink():raise ValueError("工作空间归属记录不能使用符号链接")
            reference=read_object(marker)
            parent=root.parent.parent
            identity=reference.get("id", "")
            if reference.get("home")!="../.." or not isinstance(identity,str) or not re.fullmatch(r"[a-f0-9]{32}",identity) or root!=parent/"工作空间"/("ws-"+identity):
                raise ValueError("工作空间归属记录无效")
            inherited=settings(parent)
            if inherited:return inherited
        if (root/".expvault/nas-checksums").exists():
            raise ValueError("NAS 存储设置缺失，请恢复 config.json；不会启用共享 SQLite 索引")
        return None
    if not re.fullmatch(r"[a-f0-9]{32}", str(value.get("id", ""))):
        raise ValueError("NAS 实验库标识无效")
    return value


def home(root):
    root = Path(root).expanduser().resolve()
    marker = root/".expvault/workspace.json"
    if marker.is_file():
        value = read_object(marker)
        if value.get("home") != "../.." or not re.fullmatch(r"[a-f0-9]{32}", str(value.get("id", ""))):
            raise ValueError("NAS 工作空间归属记录无效")
        parent = root.parent.parent
        if root != parent/"工作空间"/("ws-"+value["id"]):
            raise ValueError("NAS 工作空间归属路径无效")
        return parent
    return root


def is_nas(value):
    return settings(value.root, value.cfg) is not None if hasattr(value, "cfg") else settings(value) is not None


def local_dir(root, cfg=None):
    root = Path(root).expanduser().resolve()
    value = settings(root, cfg)
    if not value:
        raise ValueError("这不是 NAS 实验库")
    # Mount paths are client-specific; never write this path into shared configuration.
    identity = hashlib.sha256(str(root).encode()).hexdigest()[:20]
    return storage_root()/"nas"/(value["id"]+"-"+identity)


def checked_root(value):
    text = str(value).strip()
    if not text or "://" in text:
        raise ValueError("请先在系统中挂载 NAS，再填写挂载目录；不接受 smb:// 或网页地址")
    if os.name != "nt" and PureWindowsPath(text).drive:
        raise ValueError("请填写当前电脑实际挂载的路径，例如 /Volumes/共享/实验库")
    root = Path(text).expanduser().resolve()
    if root.is_symlink():
        raise ValueError("请直接选择 NAS 实验库目录")
    return root


def is_known_path(root):
    path=storage_root()/"nas-connections.json"
    try:
        return any(item.get("root")==str(Path(root).resolve()) for item in read_object(path).get("items",[]))
    except FileNotFoundError:
        return False


def assert_available(root, expected_id=None):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("NAS 目录不可访问，请先重新连接共享；不会创建本地替代库")
    try:
        value = settings(root)
    except (OSError, ValueError) as exc:
        raise ValueError("NAS 配置暂时无法读取，请检查连接") from exc
    if not value or expected_id and value["id"] != expected_id:
        raise ValueError("NAS 实验库标识缺失或已变化，请重新选择；不会初始化替代库")
    return value


def writer(root):
    root = home(root)
    with REGISTRY_LOCK:
        direct=WRITERS.get(str(root))
        if direct:return direct
        return next((lease for lease in WRITERS.values() if lease.root in root.parents),None)


def read_only(ctx):
    if not is_nas(ctx):
        return False
    explicit = getattr(ctx, "_nas_read_only", None)
    return explicit if explicit is not None else writer(ctx.root) is None


def require_writer(ctx):
    if not is_nas(ctx):
        return
    if read_only(ctx):
        raise ValueError("当前以只读方式查看 NAS；请在编辑主机中打开可编辑连接")
    lease = writer(ctx.root)
    if not lease:
        raise ValueError("没有持有 NAS 编辑权限，请重新连接编辑主机")
    lease.check()


class WriterLease:
    """Atomic directory creation on the share; never steal a stale writer automatically."""
    def __init__(self, root, *, initializing=False):
        self.root = Path(root).resolve()
        self.path = self.root/".expvault/nas-writer.lock"
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise ValueError("NAS 编辑占用目录不能使用符号链接")
        self.token = uuid.uuid4().hex
        self.references = 0
        self.initializing = initializing
        self.identity = None if initializing else assert_available(self.root)["id"]
        self.stop = threading.Event()
        self.thread = None

    def acquire(self):
        if not self.root.is_dir():
            raise ValueError("NAS 目录不可访问")
        self.path.parent.mkdir(exist_ok=True)
        try:
            self.path.mkdir()
        except FileExistsError:
            owner = self.owner(self.root)
            raise ValueError("NAS 已被编辑主机占用："+(owner.get("host") or "另一会话")+"。其他电脑可选择只读查看；异常退出后请先确认原主机已停止再解除占用") from None
        try:
            from .util import write_json, now_iso
            self.value = {"token":self.token,"host":platform.node() or "编辑主机","pid":os.getpid(),"started_at":now_iso(),"last_seen":now_iso()}
            write_json(self.path/"owner.json", self.value)
        except Exception:
            self.path.rmdir()
            raise
        self.thread = threading.Thread(target=self._heartbeat, daemon=True, name="nas-writer-heartbeat")
        self.thread.start()
        return self

    @staticmethod
    def owner(root):
        try:
            value = read_object(Path(root)/".expvault/nas-writer.lock/owner.json")
            return {key:value.get(key) for key in ("host","pid","started_at","last_seen")}
        except (OSError, ValueError):
            return {}

    def check(self):
        if not self.initializing:
            assert_available(self.root, self.identity)
        try:
            value = read_object(self.path/"owner.json")
        except (OSError, ValueError) as exc:
            raise ValueError("NAS 编辑占用已丢失或共享已断开；本次写入已停止") from exc
        if value.get("token") != self.token:
            raise ValueError("NAS 编辑占用已变化；本次写入已停止")

    def _heartbeat(self):
        while not self.stop.wait(10):
            try:
                self.check()
                from .util import write_json, now_iso
                self.value["last_seen"] = now_iso()
                write_json(self.path/"owner.json", self.value)
            except (OSError, ValueError):
                return

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join()
        try:
            if read_object(self.path/"owner.json").get("token") == self.token:
                (self.path/"owner.json").unlink()
                self.path.rmdir()
        except (OSError, ValueError):
            pass  # Retain the lock after an outage; the user must confirm the old writer stopped.


@contextlib.contextmanager
def access(root, mode="viewer", *, initializing=False):
    if mode not in ("viewer", "writer"):
        raise ValueError("NAS 连接方式请选择 writer 或 viewer")
    root = Path(root).resolve() if initializing else home(root)
    if mode == "viewer":
        assert_available(root)
        yield None
        return
    key = str(root)
    with REGISTRY_LOCK:
        lease = WRITERS.get(key)
        if lease is None:
            lease = WriterLease(root, initializing=initializing).acquire()
            WRITERS[key] = lease
        lease.references += 1
    try:
        lease.check()
        yield lease
    finally:
        with REGISTRY_LOCK:
            lease.references -= 1
            if lease.references == 0:
                lease.close()
                WRITERS.pop(key, None)


def portable_paths(cfg):
    if not isinstance(cfg.get("paths"),dict):
        raise ValueError("NAS 目录配置无效，请恢复 paths 设置")
    for key in ("data", "backup", "report", "db", "log"):
        value=cfg.get("paths",{}).get(key)
        if not isinstance(value,str) or not value.strip():
            raise ValueError("NAS 目录配置缺失："+key)
        value=value.replace("\\", "/")
        path=Path(value)
        if path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts or (key=="data" and path==Path('.')):
            raise ValueError("NAS 库的 paths 配置必须使用库内相对目录，请先调整："+key)
        cfg["paths"][key]=value
    return cfg


def initialize(root, *, project="NAS 实验库", operator=""):
    """Explicitly initialize a new NAS vault or convert an existing closed vault."""
    from . import config
    from .util import write_json, now_iso
    root = checked_root(root)
    if home(root) != root:
        raise ValueError("请设置 NAS 实验库总目录，再从工作空间列表进入子项目")
    if not root.exists():
        if not root.parent.is_dir():
            raise ValueError("NAS 上级目录不可访问，请先挂载共享；不会自动创建挂载点")
        root.mkdir()
    if not root.is_dir():
        raise ValueError("NAS 实验库路径必须是文件夹")
    with access(root, "writer", initializing=True):
        path = root/config.CONFIG_NAME
        if path.is_symlink():
            raise ValueError("NAS 配置不能使用符号链接")
        if path.exists():
            defaults=config.default_config(root)
            defaults["backup"]["targets"]=[{"name":"NAS 快照","path":"备份","enabled":True}]
            cfg = config.deep_merge(defaults,read_object(path))
            current = settings(root, cfg)
            if not current:
                old_server=root/".expvault/web/server.lock"
                if old_server.exists():
                    probe=FileLock(str(old_server),timeout=0)
                    try:probe.acquire()
                    except Timeout:raise ValueError("原库仍有网页程序在运行，请先完全关闭再转换") from None
                    else:probe.release()
            identity = current["id"] if current else uuid.uuid4().hex
        else:
            cfg = config.default_config(root)
            cfg.update(project=project.strip() or "NAS 实验库", operator=operator.strip(), created_at=now_iso())
            cfg["backup"]["targets"] = [{"name":"NAS 快照","path":"备份","enabled":True}]
            identity = uuid.uuid4().hex
        pending=root/".expvault/sync/transaction.json"
        if pending.exists() and not read_object(pending).get("committed"):
            raise ValueError("原库有尚未恢复的同步事务，请先在原电脑恢复，再设为 NAS 库")
        cfg["storage"] = {"kind":"nas", "id":identity}
        portable_paths(cfg)
        write_json(path, cfg)
        for name in (cfg["paths"]["data"], cfg["paths"]["backup"]):
            (root/name).mkdir(parents=True,exist_ok=True)
        (root/".expvault").mkdir(exist_ok=True)
        (root/".expvault/nas-checksums").mkdir(exist_ok=True)
    return root


def inherit_storage(root):
    root = Path(root).resolve()
    with REGISTRY_LOCK:
        for lease in WRITERS.values():
            if lease.root in root.parents:
                return settings(lease.root)
    return None


def unlock(root, *, confirmed=False):
    if not confirmed:
        raise ValueError("请先确认原编辑主机已经完全停止，再使用 --confirm-stopped 解除占用")
    root = home(checked_root(root))
    assert_available(root)
    with REGISTRY_LOCK:
        if str(root) in WRITERS:
            raise ValueError("当前程序仍在编辑这个库，请先关闭编辑会话")
    path = root/".expvault/nas-writer.lock"
    if not path.exists():
        return False
    if path.is_symlink() or any(item.name != "owner.json" or item.is_symlink() for item in path.iterdir()):
        raise ValueError("占用目录内容异常，请检查原编辑主机，不会自动删除")
    (path/"owner.json").unlink(missing_ok=True)
    path.rmdir()
    return True


def refresh_index(ctx, *, force=False):
    """Inventory metadata and file stats; opening a NAS never hashes whole videos."""
    from . import experiment as ex
    from .util import now_iso, sha256_file
    if not is_nas(ctx):
        return
    identity = assert_available(ctx.root)["id"]
    directory = local_dir(ctx.root, ctx.cfg)
    directory.mkdir(parents=True,exist_ok=True)
    with FileLock(str(directory/"index-refresh.lock"), timeout=30):
        db = ctx._db
        row = db.conn.execute("SELECT value FROM meta WHERE key='nas_refreshed'").fetchone()
        if not force and row and time.time()-float(row[0]) < INDEX_INTERVAL:
            return
        records=[]
        def fail(error): raise error
        if ctx.data_dir.is_symlink() or ctx.root not in ctx.data_dir.resolve().parents:
            raise ValueError("NAS 数据目录越界或使用了符号链接")
        if not ctx.data_dir.is_dir():
            raise ValueError("NAS 数据目录不可访问；本机索引保持原样")
        for current, folders, files in os.walk(ctx.data_dir, onerror=fail):
            folders[:]=[name for name in folders if not name.startswith('.') and not (Path(current)/name).is_symlink()]
            if ex.META_NAME not in files:
                continue
            folder=Path(current)
            meta=read_object(folder/ex.META_NAME)
            if not isinstance(meta.get("id"),str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}",meta["id"]):
                raise ValueError("NAS 实验元数据缺少编号："+folder.name)
            meta={**meta,"folder":folder.relative_to(ctx.root).as_posix()}
            known={value["relpath"]:value for value in db.files_of(meta["id"])}
            summary={}
            try:
                summary_path=ctx.root/".expvault/nas-checksums"/(meta["id"]+".json")
                if summary_path.is_symlink() or summary_path.stat().st_size>32*1024**2:
                    raise ValueError("校验摘要无效")
                summary=read_object(summary_path)
                if summary.get("folder")!=meta["folder"] or summary.get("vault_id")!=identity:
                    summary={}
            except (FileNotFoundError,ValueError):
                summary={}
            if not isinstance(summary.get("files"),dict):summary={}
            entries=[]
            for parent, names, filenames in os.walk(folder, onerror=fail):
                names[:]=[name for name in names if not name.startswith('.') and not (Path(parent)/name).is_symlink()]
                for name in filenames:
                    path=Path(parent)/name
                    relative=path.relative_to(folder).as_posix()
                    if path.is_symlink() or ex.is_excluded(relative,ctx.exclude_patterns()):
                        continue
                    stat=path.stat()
                    candidates=[summary.get("files",{}).get(relative,{}),known.get(relative,{})]
                    previous=next((value for value in candidates if isinstance(value,dict) and isinstance(value.get("sha256"),str)
                        and re.fullmatch(r"[a-f0-9]{64}",value["sha256"]) and value.get("size")==stat.st_size
                        and isinstance(value.get("mtime"),(int,float)) and abs(value["mtime"]-stat.st_mtime)<.000001),{})
                    digest=previous.get("sha256", "")
                    if not digest and relative in {"meta.json", "README.md", "04_notes/日志.md"} and stat.st_size<=2*1024**2:
                        digest=sha256_file(path)
                    entries.append({"relpath":relative,"size":stat.st_size,"mtime":stat.st_mtime,"sha256":digest,
                        "role":ctx.role_for(relative),"tags":meta.get("file_tags",{}).get(relative,[]),"is_link":False})
            records.append((meta,entries))
            folders[:]=[]
        ids=[meta["id"] for meta,_ in records]
        if len(ids)!=len(set(ids)):
            raise ValueError("NAS 中存在重复实验编号，请先整理；本机索引保持原样")
        assert_available(ctx.root, identity)
        with db.transaction():
            for meta, entries in records:
                db.upsert_experiment(meta)
                for entry in entries:db.upsert_file(meta["id"],entry)
                db.mark_missing(meta["id"],[entry["relpath"] for entry in entries])
            for old in db.list_experiments(include_archived=True):
                if old["id"] not in ids:
                    db.delete_experiment(old["id"])
            db.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('nas_refreshed',?)",(str(time.time()),))
        return {"experiments":len(records),"files":sum(len(entries) for _,entries in records),"updated_at":now_iso()}


def publish_checksums(ctx):
    if not is_nas(ctx):return
    require_writer(ctx)
    from .util import write_json
    target=ctx.root/".expvault/nas-checksums"
    if target.is_symlink():raise ValueError("NAS 校验摘要目录不能使用符号链接")
    target.mkdir(exist_ok=True)
    for rec in ctx.db.list_experiments(include_archived=True):
        files={item["relpath"]:{key:item[key] for key in ("size","mtime","sha256")} for item in ctx.db.files_of(rec["id"],include_missing=False) if item.get("sha256")}
        write_json(target/(rec["id"]+".json"),{"vault_id":settings(ctx.root,ctx.cfg)["id"],"folder":rec["folder"],"files":files})


def status(ctx):
    if not is_nas(ctx):return None
    assert_available(ctx.root,settings(ctx.root,ctx.cfg)["id"])
    return {"kind":"nas","mode":"viewer" if read_only(ctx) else "writer","read_only":read_only(ctx),
        "root":str(ctx.root),"local_index":str(ctx.db_path),"owner":WriterLease.owner(home(ctx.root))}


class NoSync:
    def __init__(self, root):self.root=root
    def start(self):pass
    def close(self):pass
    def public(self):return {"nas":True,"message":"当前直接使用 NAS 的同一份资料，无需双向自动同步。"}
    def configure(self, *_args, **_kwargs):raise ValueError("NAS 共享库不启用双向同步，请在其他独立副本中配置同步")
    def action(self, *_args, **_kwargs):raise ValueError("NAS 共享库不启用双向同步")
