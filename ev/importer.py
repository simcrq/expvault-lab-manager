"""GUI 共用的可取消、逐文件校验导入器，不依赖 Qt。"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import threading
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from filelock import FileLock, Timeout

from . import config, experiment as ex
from .file_tags import normalize_tags
from .util import CHUNK, now_iso, sha256_file

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".dng", ".raw", ".cr2", ".nef", ".arw"}
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".3gp", ".webm"}


def file_kind(path: Path) -> str:
    return "图片" if path.suffix.lower() in IMAGE_EXT else ("视频" if path.suffix.lower() in VIDEO_EXT else "数据")


def portable_name(name: str) -> str:
    """避免 NTFS/Windows 保留名，源文件名保持不变。"""
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "untitled"
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", name.split(".")[0]):
        name = "_" + name
    # 为重名后缀留空间（按 UTF-8 字节约束兼容常见文件系统）。
    while len(name.encode("utf-8")) > 200:
        stem, suffix = os.path.splitext(name)
        name = stem[:-1] + suffix if stem else name[:-1]
    return name


@dataclass
class ImportItem:
    source: Path
    relative: str
    size: int
    kind: str
    tags: list[str] = field(default_factory=list)
    origin: str = "本机 / U 盘"
    key: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "待导入"
    destination: str = ""
    destination_exp_id: str = ""


def scan_sources(paths, *, exclude=(), cancel=None) -> tuple[list[ImportItem], list[str]]:
    """保留所选文件夹层级，不遍历符号链接；错误逐项返回。"""
    items, errors, seen = [], [], set()

    def add(path, relative):
        if cancel and cancel.is_set():
            return
        try:
            if path.is_symlink():
                errors.append(f"跳过符号链接：{path}")
                return
            resolved = path.resolve(strict=True)
            if resolved in seen or not path.is_file():
                return
            if ex.is_excluded(relative, list(exclude)) or any(p.startswith(".") for p in PurePosixPath(relative).parts):
                return
            seen.add(resolved)
            items.append(ImportItem(resolved, relative, path.stat().st_size, file_kind(path)))
        except OSError as exc:
            errors.append(f"{path}：{exc}")

    for raw in paths:
        if cancel and cancel.is_set():
            break
        path = Path(raw).expanduser().absolute()
        if path.is_symlink():
            errors.append(f"跳过符号链接：{path}")
        elif not path.exists():
            errors.append(f"文件不存在或设备已拔出：{path}")
        elif path.is_dir():
            for directory, dirs, names in os.walk(path, onerror=lambda e: errors.append(str(e))):
                if cancel and cancel.is_set():
                    break
                dirs[:] = sorted(d for d in dirs if not d.startswith(".")
                                 and not (Path(directory) / d).is_symlink()
                                 and not ex.is_excluded(d, list(exclude)))
                for name in sorted(names):
                    p = Path(directory) / name
                    add(p, (Path(path.name) / p.relative_to(path)).as_posix())
        else:
            add(path, path.name)
    return items, errors


def experiment_folder(ctx, rec) -> Path:
    folder = ex.exp_dir(ctx, rec).resolve()
    root = ctx.root.resolve()
    if folder == root or root not in folder.parents or not folder.is_dir():
        raise ValueError("实验目录不存在或位于仓库之外")
    meta = ex.load_meta(folder)
    if not meta or meta.get("id") != rec["id"]:
        raise ValueError("实验 meta.json 缺失或编号不匹配")
    return folder


def target_relative(ctx, item: ImportItem, role="auto") -> str:
    selected_role = ("note" if item.kind in ("图片", "视频") else "raw") if role == "auto" else role
    structures = {r: name for name, _label, r in ctx.structure()}
    if selected_role not in structures:
        raise ValueError(f"仓库未配置文件角色：{selected_role}")
    sub = PurePosixPath(structures[selected_role].replace("\\", "/"))
    relative = PurePosixPath(item.relative.replace("\\", "/"))
    if not sub.parts or sub.is_absolute() or relative.is_absolute() or ".." in sub.parts or ".." in relative.parts:
        raise ValueError("导入目标必须是实验内的相对路径")
    parts = [portable_name(p) for p in relative.parts]
    if not parts:
        raise ValueError("文件名不能为空")
    media = ["photos" if item.kind == "图片" else "videos"] if role == "auto" and item.kind != "数据" else []
    return PurePosixPath(*sub.parts, *media, *parts).as_posix()


class ImportCancelled(Exception):
    pass


def _check_cancel(cancel):
    if cancel and cancel.is_set():
        raise ImportCancelled()


def _digest(path, cancel):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            _check_cancel(cancel)
            chunk = stream.read(CHUNK)
            if not chunk:
                return h.hexdigest()
            h.update(chunk)


def _destination(folder, relative):
    destination = folder / relative
    # 逐层检查，包括已存在的目标文件，拒绝符号链接和目录越界。
    current = folder
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink() or (current.resolve() != folder and folder not in current.resolve().parents):
            raise ValueError(f"目标不能通过链接指向其他目录：{current}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def _reserve_name(destination):
    """大小写不敏感的重名保护；独占预留后再原子替换。"""
    names = {p.name.casefold() for p in destination.parent.iterdir()}
    number = 0
    while True:
        candidate = destination if number == 0 else destination.with_name(f"{destination.stem}_{number}{destination.suffix}")
        number += 1
        if candidate.name.casefold() in names:
            continue
        try:
            with candidate.open("xb"):
                pass
            return candidate
        except FileExistsError:
            continue


def _save_file_metadata(ctx, rec, folder, relative, digest, tags, *, imported=False):
    # 每个文件提交一次权威元数据，取消/后续文件失败不丢此前成功的标签。
    meta = ex.load_meta(folder)
    if not meta or meta.get("id") != rec["id"]:
        raise ValueError("无法读取有效实验元数据；文件已复制时请保留原始文件并检查实验目录")
    meta.setdefault("file_tags", {})[relative] = normalize_tags(tags)
    meta["updated_at"] = now_iso()
    if imported:
        meta.setdefault("history", []).append({"ts": now_iso(), "action": "gui_import", "detail": relative})
    ex.save_meta(folder, meta)
    stat = (folder / relative).stat()
    ctx.db.upsert_experiment({**meta, "folder": rec["folder"]})
    ctx.db.upsert_file(rec["id"], {"relpath": relative, "size": stat.st_size, "mtime": stat.st_mtime,
                                 "sha256": digest, "role": ctx.role_for(relative), "tags": tags})
    meta_path = folder / ex.META_NAME
    meta_stat = meta_path.stat()
    ctx.db.upsert_file(rec["id"], {"relpath": ex.META_NAME, "size": meta_stat.st_size,
                                 "mtime": meta_stat.st_mtime, "sha256": sha256_file(meta_path), "role": "meta"})
    ctx.db.commit()


def import_items(root, exp_id, items: list[ImportItem], *, role="auto", deduplicate=True,
                 cancel=None, progress=None, item_done=None) -> dict:
    """调用线程拥有自己的 SQLite 连接；与 Harness 共用写锁。"""
    ctx = config.load(Path(root))
    from .nas import require_writer
    require_writer(ctx)
    results = {"imported": [], "duplicates": [], "errors": [], "cancelled": False}
    total, completed = sum(i.size for i in items), 0
    lock = FileLock(str(ctx.meta_dir / "harness.lock"), timeout=0)
    ctx.meta_dir.mkdir(parents=True, exist_ok=True)
    acquired = False
    try:
        try:
            lock.acquire()
            acquired = True
        except Timeout:
            raise ValueError("另一个导入或机器接口正在写入，请稍后重试") from None
        rec = ctx.db.get_experiment(exp_id)
        if not rec:
            raise ValueError("所选实验已不存在，请刷新实验列表")
        folder = experiment_folder(ctx, rec)
        known = {}
        for f in ctx.db.files_of(exp_id, include_missing=False):
            if f["sha256"] and f["role"] != "meta":
                known.setdefault((f["size"], f["sha256"]), []).append(f["relpath"])
        for item in items:
            temporary, reserved = None, None
            try:
                _check_cancel(cancel)
                item.tags = normalize_tags(item.tags)
                source = item.source
                if source.is_symlink() or not source.is_file():
                    raise ValueError("源文件不存在、为链接或设备已拔出")
                if folder == source.resolve() or folder in source.resolve().parents:
                    raise ValueError("文件已在目标实验中，请使用 scan 登记")
                before = source.stat()
                if before.st_size != item.size:
                    raise ValueError("源文件大小已变化，请移除后重新添加")
                destination = _destination(folder, target_relative(ctx, item, role))
                if shutil.disk_usage(destination.parent).free < item.size + 16 * 1024 * 1024:
                    raise OSError("目标磁盘可用空间不足，请释放空间后重试")
                temporary = destination.parent / f".expvault-{uuid.uuid4().hex}.part"
                h, copied = hashlib.sha256(), 0
                with source.open("rb") as src, temporary.open("xb") as dest:
                    while True:
                        _check_cancel(cancel)
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        dest.write(chunk)
                        h.update(chunk)
                        copied += len(chunk)
                        if progress:
                            progress(completed + copied, total, f"复制 · {item.relative}")
                    dest.flush()
                    os.fsync(dest.fileno())
                after = source.stat()
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or copied != before.st_size:
                    raise ValueError("复制期间源文件发生变化，未导入，请停止拍摄/导出后重试")
                digest = h.hexdigest()
                if progress:
                    progress(completed + copied, total, f"校验 · {item.relative}")
                if _digest(temporary, cancel) != digest:
                    raise OSError("复制校验失败，未导入，请检查磁盘和连接")
                duplicate = None
                if deduplicate:
                    for relative in known.get((copied, digest), []):
                        existing = folder / relative
                        if existing.is_file() and not existing.is_symlink() and folder in existing.resolve().parents and _digest(existing, cancel) == digest:
                            duplicate = relative
                            break
                _check_cancel(cancel)
                if duplicate:
                    old = (ex.load_meta(folder) or {}).get("file_tags", {}).get(duplicate, [])
                    _save_file_metadata(ctx, rec, folder, duplicate, digest, normalize_tags(old + item.tags))
                    item.destination, item.status = duplicate, "重复已跳过 · 标签已合并"
                    results["duplicates"].append(item.key)
                else:
                    shutil.copystat(source, temporary)
                    reserved = _reserve_name(destination)
                    os.replace(temporary, reserved)
                    temporary = None
                    relative = reserved.relative_to(folder).as_posix()
                    # 此后失败保留已验证的副本，不删除已经落盘的实验数据。
                    item.destination = relative
                    reserved = None
                    _save_file_metadata(ctx, rec, folder, relative, digest, item.tags, imported=True)
                    known.setdefault((copied, digest), []).append(relative)
                    item.status = "已导入"
                    results["imported"].append(item.key)
                item.destination_exp_id = exp_id
            except ImportCancelled:
                results["cancelled"] = True
                item.status = "已取消 · 可重试"
                break
            except Exception as exc:
                item.status = f"失败：{exc}"
                results["errors"].append({"key": item.key, "error": str(exc)})
            finally:
                for partial in (temporary, reserved):
                    if partial is not None:
                        try:
                            partial.unlink(missing_ok=True)
                        except PermissionError:
                            # Windows 上 copystat 可能从只读 U 盘继承只读位。
                            partial.chmod(partial.stat().st_mode | stat.S_IWRITE)
                            partial.unlink(missing_ok=True)
                completed += item.size
                if item_done:
                    item_done(item.key, item.status, item.destination)
                if progress:
                    progress(completed, total, item.status)
        if results["imported"] or results["duplicates"]:
            meta = ex.load_meta(folder)
            ex.write_readme(ctx, meta, ctx.db.files_of(exp_id, include_missing=False), folder=folder)
            ctx.db.log("gui_import", exp_id, f"导入 {len(results['imported'])}，重复 {len(results['duplicates'])}，失败 {len(results['errors'])}")
        return results
    finally:
        if ctx._db is not None:
            ctx.db.close()
        if acquired:
            lock.release()


def set_file_tags(root, exp_id, relative_paths, tags, *, merge=False, expected_revision=None):
    """GUI 中对已导入文件追加/修改标签；重建索引仍可恢复。"""
    tags = normalize_tags(tags)
    ctx = config.load(Path(root))
    from .nas import require_writer
    require_writer(ctx)
    try:
        with FileLock(str(ctx.meta_dir / "harness.lock"), timeout=0):
            rec = ctx.db.get_experiment(exp_id)
            if not rec:
                raise ValueError("实验不存在")
            folder = experiment_folder(ctx, rec)
            if expected_revision and sha256_file(folder / ex.META_NAME) != expected_revision:
                raise ValueError("实验已在其他窗口更新，请刷新后核对文件标签")
            targets = []
            for relative in relative_paths:
                f = ctx.db.get_file(exp_id, relative)
                if not f or f["missing"] or ".." in PurePosixPath(relative).parts or PurePosixPath(relative).is_absolute():
                    raise ValueError(f"文件未登记或已丢失：{relative}")
                path = folder / relative
                if path.is_symlink() or folder not in path.resolve().parents:
                    raise ValueError("文件路径越界")
                targets.append((relative, f))
            meta = ex.load_meta(folder)
            for relative, f in targets:
                labels = normalize_tags(meta.get("file_tags", {}).get(relative, []) + tags) if merge else tags
                _save_file_metadata(ctx, rec, folder, relative, f["sha256"], labels)
            ex.write_readme(ctx, ex.load_meta(folder), ctx.db.files_of(exp_id, include_missing=False), folder=folder)
    finally:
        if ctx._db is not None:
            ctx.db.close()
