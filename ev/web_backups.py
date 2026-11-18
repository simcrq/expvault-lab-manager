"""Read backup snapshots and restore verified files to a separate directory."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import time
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import backup, config, experiment
from .util import CHUNK, read_json, write_json

SNAPSHOT_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
DIGEST = re.compile(r"[a-f0-9]{64}")


def target_directory(path):
    path = Path(path).resolve()
    marker = path / backup.TARGET_MARK
    folders = [path / name for name in (backup.SNAP_DIR, backup.OBJ_DIR)]
    if (not marker.is_file() or marker.is_symlink() or not (path / backup.SNAP_DIR).is_dir()
            or any(folder.resolve() != folder for folder in folders)):
        raise ValueError("请选择包含 expvault-target.json 和 snapshots 文件夹的备份根目录")
    return path


def snapshots(target):
    items = []
    for manifest in backup.list_snapshots(target):
        identity = manifest.get("id")
        files = manifest.get("files")
        if (not isinstance(identity, str) or not SNAPSHOT_ID.fullmatch(identity)
                or manifest["_file"] != identity + ".json" or not isinstance(files, list)):
            continue
        stats = manifest.get("stats")
        total = stats.get("bytes") if isinstance(stats, dict) else None
        items.append({"id": identity, "created_at": manifest.get("created_at") or "",
                      "files": len(files), "bytes": total if type(total) is int and total >= 0 else None})
    return items


def relative_file(name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("备份中存在无效文件路径")
    path = PurePosixPath(name)
    if (path.is_absolute() or PureWindowsPath(name).drive or path.as_posix() != name
            or any(part in (".", "..") or "\0" in part for part in path.parts)
            or not path.parts or path.parts[0].casefold() == ".expvault"):
        raise ValueError("备份中存在无效文件路径")
    if os.name == "nt" and any(part.rstrip(" .") != part or PureWindowsPath(part).is_reserved()
                               or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                               for part in path.parts):
        raise ValueError("备份中有 Windows 无法恢复的文件名")
    return path


def snapshot_files(target, identity, contains=""):
    if not isinstance(identity, str) or not SNAPSHOT_ID.fullmatch(identity):
        raise ValueError("快照编号无效")
    index = target / backup.SNAP_DIR / (identity + ".json")
    if index.is_symlink() or not index.is_file():
        raise ValueError("快照清单不存在或无法读取")
    manifest = backup.get_snapshot(target, identity)
    if manifest.get("id") != identity or not isinstance(manifest.get("files"), list):
        raise ValueError("快照清单无效")
    entries = {}
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise ValueError("快照文件记录无效")
        name, key, length = entry.get("p"), entry.get("h"), entry.get("s")
        relative_file(name)
        if name in entries or not isinstance(key, str) or not DIGEST.fullmatch(key) or type(length) is not int or length < 0:
            raise ValueError("快照文件记录无效")
        entries[name] = {"path": name, "sha256": key, "size": length}
    matches = [item for name, item in entries.items() if contains.casefold() in name.casefold()]
    return manifest, matches


def object_file(target, entry):
    path = backup.object_path(target, entry["sha256"])
    # Objects must be regular files inside the selected backup; never follow links.
    relative = path.relative_to(target)
    if not path.resolve().is_relative_to(target) or any((target / part).is_symlink() for part in (relative, *relative.parents)):
        raise ValueError("备份对象不能使用符号链接")
    if not path.is_file() or path.stat().st_size != entry["size"]:
        return None
    return path


def destination_path(value, target, library):
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("请输入恢复目录的绝对路径")
    path = Path(value).expanduser()
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("请选择一个独立目录的绝对路径")
    path = path.resolve()
    for protected in (target, library):
        if path == protected or protected in path.parents or path in protected.parents:
            raise ValueError("恢复目录须与当前实验库和备份目录分开")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError("请选择新目录或空目录")
    return path


def restore_preview(target, identity, contains, destination, library):
    _manifest, entries = snapshot_files(target, identity, contains)
    path = destination_path(destination, target, library)
    missing = [item["path"] for item in entries if object_file(target, item) is None]
    return {"destination": str(path), "files": len(entries), "bytes": sum(item["size"] for item in entries),
            "missing_count": len(missing), "missing": missing[:20]}


def restore(target, identity, contains, destination, library, *, job_id, progress, cancel):
    manifest, entries = snapshot_files(target, identity, contains)
    path = destination_path(destination, target, library)
    if not entries:
        raise ValueError("没有匹配的文件可恢复")
    path.parent.mkdir(parents=True, exist_ok=True)
    total = sum(item["size"] for item in entries)
    if shutil.disk_usage(path.parent).free < total:
        raise ValueError("恢复目录所在磁盘空间不足")
    with tempfile.TemporaryDirectory(prefix=".expvault-restore-", dir=path.parent) as staging:
        staged = Path(staging) / "files"
        staged.mkdir()
        done = 0
        report_at = 0.0
        for index, item in enumerate(entries, start=1):
            if cancel.is_set():
                raise InterruptedError
            source = object_file(target, item)
            name = item["path"]
            if source is None:
                raise ValueError(f"备份中缺少完整文件：{name}")
            output = staged / relative_file(name)
            output.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            message = f"正在恢复 {index}/{len(entries)} · {name}"
            if time.monotonic() >= report_at:
                progress(done if total else index-1, total or len(entries), message)
                report_at = time.monotonic() + .5
            with source.open("rb") as src, output.open("xb") as dst:
                timestamp = os.fstat(src.fileno())
                while block := src.read(CHUNK):
                    if cancel.is_set():
                        raise InterruptedError
                    dst.write(block)
                    digest.update(block)
                    done += len(block)
                    if time.monotonic() >= report_at:
                        progress(done, total, message)
                        report_at = time.monotonic() + .5
            if digest.hexdigest() != item["sha256"] or output.stat().st_size != item["size"]:
                raise ValueError(f"备份文件校验失败：{name}")
            os.utime(output, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
        can_open = not contains and (staged / config.CONFIG_NAME).is_file()
        if can_open:
            write_json(staged / ".expvault/restore.json", {"job_id": job_id})
        if cancel.is_set():
            raise InterruptedError
        # A concurrent restore or another process may have filled the destination.
        destination_path(str(path), target, library)
        if path.exists():
            path.rmdir()
        os.replace(staged, path)
    return {"destination": str(path), "files": len(entries), "bytes": total,
            "can_open": can_open,
            "source_root": manifest.get("root", "")}


def prepare_library(folder, source_root):
    """Keep the copy's paths local and rebuild its database from restored metadata."""
    config_path = folder / config.CONFIG_NAME
    if config_path.is_symlink():
        raise ValueError("恢复副本的配置不能是符号链接")
    raw = read_json(config_path, None)
    if not isinstance(raw, dict):
        raise ValueError("恢复副本缺少有效的实验库配置")
    defaults = config.default_config(folder)
    paths = raw.get("paths", {})
    if not isinstance(paths, dict) or not isinstance(source_root, str):
        raise ValueError("恢复副本的目录配置无效")
    data = paths.get("data", defaults["paths"]["data"])
    if not isinstance(data, str):
        raise ValueError("恢复副本的数据路径无效")
    flavour = PureWindowsPath if PureWindowsPath(data).drive else PurePosixPath
    parsed = flavour(data)
    if parsed.is_absolute():
        try:
            data = parsed.relative_to(flavour(source_root)).as_posix()
        except ValueError:
            raise ValueError("快照的数据路径位于原实验库之外") from None
    else:
        data = data.replace("\\", "/")
    if data != ".":
        relative_file(data)
    if not (folder / data).resolve().is_relative_to(folder):
        raise ValueError("恢复副本的数据目录超出副本范围")
    effective = {key: raw[key] for key in ("schema", "project", "operator", "structure", "naming", "export", "created_at", "version") if key in raw}
    effective["paths"] = {**defaults["paths"], "data": data, "db": ".expvault/restored-index/expvault.db",
                          "log": ".expvault/restored-index/events.jsonl"}
    effective["backup"] = {**defaults["backup"], "targets": defaults["backup"]["targets"][:1]}
    runtime = folder / ".expvault"
    if runtime.is_symlink():
        raise ValueError("恢复副本的状态目录不能是符号链接")
    runtime.mkdir(exist_ok=True)
    original = runtime / "restored-config.json"
    if not original.exists():
        with original.open("xb") as archive:
            archive.write(config_path.read_bytes())
    write_json(config_path, effective)
    ctx = config.load(folder)
    try:
        experiment.rebuild_index(ctx)
    finally:
        if ctx._db is not None:
            ctx.db.close()
