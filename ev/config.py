"""配置与运行上下文。

配置文件 `config.json` 就放在库根目录（和 expvault.py 同级）。
所有路径都相对库根目录解析，整个文件夹可以整体搬到别的电脑/硬盘。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .util import ensure_dir, read_json, write_json
from .ui import Console

CONFIG_NAME = "config.json"
ENV_ROOT = "EXPVAULT_ROOT"

# 每个实验的标准子目录：目录名 -> (中文说明, 文件角色)
DEFAULT_STRUCTURE = [
    ["01_raw", "原始数据", "raw"],
    ["02_processed", "处理数据", "processed"],
    ["03_figures", "图表", "figure"],
    ["04_notes", "记录", "note"],
    ["05_misc", "其他", "other"],
]

DEFAULT_EXCLUDE = [
    "Thumbs.db", "desktop.ini", ".DS_Store", "~$*", "*.tmp", "*.part",
    ".git", "*/.git/*", "__pycache__", "*.pyc", ".ipynb_checkpoints",
]


def default_config(root: Path) -> dict:
    home = Path.home()
    targets = [{"name": "本机快照", "path": "备份", "enabled": True}]

    # 自动发现常见的同步盘目录，省得用户手填
    for label, cand in (
        ("OneDrive", home / "OneDrive"),
        ("坚果云", home / "Nutstore" / "我的坚果云"),
        ("百度网盘", home / "BaiduNetdiskDownload"),
    ):
        if cand.exists():
            targets.append({"name": label, "path": str(cand / "实验数据备份"), "enabled": False})

    return {
        "schema": 1,
        "operator": "",
        "project": "",
        "paths": {
            "data": "数据",
            "backup": "备份",
            "report": "报告",
            "db": "索引/expvault.db",
            "log": "索引/events.jsonl",
        },
        "naming": {
            "ascii_only": True,
            "date_prefix": False,
            "slug_max": 40,
        },
        "structure": [list(x) for x in DEFAULT_STRUCTURE],
        "backup": {
            "targets": targets,
            "keep_recent": 15,
            "keep_monthly": 6,
            "exclude": list(DEFAULT_EXCLUDE),
            "warn_unbacked_hours": 24,
            "max_file_mb_warn": 2048,
        },
        "export": {"sheet_rows_per_page": 5000},
        "created_at": "",
        "version": "1.0",
    }


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def script_root() -> Path:
    """库根目录 = 本包所在目录的上一级。"""
    return Path(__file__).resolve().parents[1]


def find_root(start: Path | None = None) -> Path:
    """优先环境变量，其次从 start 逐级向上找 config.json，最后退回脚本目录。"""
    env = os.environ.get(ENV_ROOT)
    if env:
        return Path(env).expanduser().resolve()

    cur = Path(start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / CONFIG_NAME).exists():
            return p

    sr = script_root()
    if (sr / CONFIG_NAME).exists():
        return sr
    return cur


@dataclass
class Ctx:
    """一次命令执行所需的全部上下文。"""

    root: Path
    cfg: dict
    con: Console = field(default_factory=Console)
    _db: object = None
    _nas_read_only: bool | None = None
    _actor: dict | None = None

    # ---------------- 路径 ----------------
    @property
    def data_dir(self) -> Path:
        return self.root / self.cfg["paths"]["data"]

    @property
    def backup_dir(self) -> Path:
        return self.root / self.cfg["paths"]["backup"]

    @property
    def report_dir(self) -> Path:
        from .nas import is_nas, local_dir
        if is_nas(self):
            return local_dir(self.root, self.cfg) / "reports"
        return self.root / self.cfg["paths"]["report"]

    @property
    def db_path(self) -> Path:
        from .nas import is_nas, local_dir
        if is_nas(self):
            return local_dir(self.root, self.cfg) / "index.sqlite3"
        return self.root / self.cfg["paths"]["db"]

    @property
    def log_path(self) -> Path:
        from .nas import is_nas, local_dir
        if is_nas(self):
            return local_dir(self.root, self.cfg) / "events.jsonl"
        return self.root / self.cfg["paths"]["log"]

    @property
    def meta_dir(self) -> Path:
        from .nas import is_nas, local_dir
        if is_nas(self):
            return local_dir(self.root, self.cfg) / "state"
        return self.root / ".expvault"

    # ---------------- 数据库 ----------------
    @property
    def db(self):
        if self._db is None:
            from .db import Database

            self._db = Database(self.db_path)
            from .nas import is_nas, refresh_index
            if is_nas(self):
                try:
                    refresh_index(self)
                except BaseException:
                    self._db.close()
                    self._db = None
                    raise
        return self._db

    # ---------------- 小工具 ----------------
    def save_cfg(self) -> None:
        from .nas import require_writer
        require_writer(self)
        write_json(self.root / CONFIG_NAME, self.cfg)

    def rel(self, p: Path) -> str:
        from .util import rel_posix

        return rel_posix(p, self.root)

    def exclude_patterns(self) -> list[str]:
        return list(self.cfg["backup"].get("exclude", []))

    def structure(self) -> list[list[str]]:
        return [list(x) for x in self.cfg.get("structure", DEFAULT_STRUCTURE)]

    def role_for(self, relpath: str) -> str:
        """按所在子目录推断文件角色。"""
        name = relpath.rsplit("/", 1)[-1]
        if name in ("meta.json", "README.md"):
            return "meta"
        first = relpath.split("/")[0]
        for name_, _label, role in self.structure():
            if name_ == first:
                return role
        return "other"

    def target_by_name(self, name: str) -> dict | None:
        for t in self.cfg["backup"]["targets"]:
            if t["name"] == name or t.get("id") == name:
                return t
        return None

    def resolve_target_path(self, target: dict) -> Path:
        p = Path(os.path.expandvars(str(target["path"]))).expanduser()
        return p if p.is_absolute() else (self.root / p)


def load(ctx_free_root: Path | None = None) -> Ctx:
    # 必须转成绝对路径：enter 会把工作目录切到实验目录，相对路径会失效
    root = Path(ctx_free_root or find_root()).expanduser().resolve()
    raw = read_json(root / CONFIG_NAME, None)
    if raw is None:
        raise FileNotFoundError(
            f"在 {root} 找不到 {CONFIG_NAME}。请先运行：python expvault.py init"
        )
    cfg = deep_merge(default_config(root), raw)
    from .nas import settings, portable_paths
    inherited=settings(root,cfg)
    if inherited:
        cfg["storage"]=dict(inherited)
        portable_paths(cfg)
    return Ctx(root=root, cfg=cfg)


def init_repo(root: Path, operator: str = "", project: str = "") -> Ctx:
    from .nas import is_known_path, assert_available
    if is_known_path(root):
        assert_available(root)
    root = ensure_dir(Path(root).resolve())
    cfg_path = root / CONFIG_NAME
    if cfg_path.exists():
        cfg = deep_merge(default_config(root), read_json(cfg_path, {}) or {})
    else:
        cfg = default_config(root)
    if operator:
        cfg["operator"] = operator
    if project:
        cfg["project"] = project
    from .util import now_iso

    cfg.setdefault("created_at", now_iso())
    from .nas import inherit_storage, require_writer
    inherited = inherit_storage(root)
    if inherited:
        cfg["storage"] = dict(inherited)
    require_writer(Ctx(root=root, cfg=cfg))
    write_json(cfg_path, cfg)

    ctx = Ctx(root=root, cfg=cfg)
    for p in (ctx.data_dir, ctx.backup_dir, ctx.report_dir, ctx.db_path.parent, ctx.meta_dir):
        ensure_dir(p)
    return ctx


def ensure_windows_console() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
