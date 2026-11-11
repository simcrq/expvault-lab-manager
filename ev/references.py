"""Stable experiment/file citations; references never copy or move source data."""
from __future__ import annotations

import html
import os
from pathlib import Path, PureWindowsPath
from urllib.parse import quote

from .file_tags import normalize_tags


def file_info(ctx, exp_id, relative):
    from . import experiment as ex
    rec = ctx.db.get_experiment(exp_id)
    if not rec:
        raise ValueError("引用的来源实验不存在")
    folder = ex.exp_dir(ctx, rec).resolve()
    if ctx.data_dir.resolve() not in folder.parents:
        raise ValueError("引用的实验目录越界")
    if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
            or PureWindowsPath(relative).drive or "\\" in relative
            or any(part in ("..", ".expvault") for part in relative.split("/"))):
        raise ValueError("引用文件路径无效")
    path = folder / relative
    indexed = ctx.db.get_file(exp_id, relative)
    if (not indexed or indexed["missing"] or indexed["role"] == "meta" or path.is_symlink()
            or not path.is_file() or folder not in path.resolve().parents):
        raise ValueError("引用文件不存在、未登记或不可引用，请刷新资料库")
    return rec, path, indexed


def resolve(ctx, owner, items):
    from . import experiment as ex
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError("每条记录最多引用 100 个文件")
    result, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"exp_id", "path"}:
            raise ValueError("引用必须指定来源实验编号和相对文件路径")
        if not all(isinstance(item[key], str) for key in ("exp_id", "path")):
            raise ValueError("引用字段必须为文字")
        identity = (item["exp_id"], item["path"])
        if identity in seen:
            raise ValueError("同一条记录不能重复引用同一个文件")
        seen.add(identity)
        source, path, indexed = file_info(ctx, *identity)
        stat = path.stat()
        if stat.st_size != indexed["size"] or abs(stat.st_mtime-indexed["mtime"]) > .000001:
            raise ValueError("引用的原文件已在外部变化，请先扫描或校验来源实验，再重新选择")
        result.append({**item, "name": path.name, "experiment_title": source["title"],
                       "sha256": indexed.get("sha256", ""), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                       "tags": normalize_tags(indexed.get("tags", [])),
                       "relative_path": os.path.relpath(path, ex.exp_dir(ctx, owner)).replace("\\", "/")})
    return result


def decorate(ctx, items):
    result = []
    for item in items:
        value = {**item, "missing": False, "changed": False}
        try:
            rec, path, indexed = file_info(ctx, item["exp_id"], item["path"])
            stat = path.stat()
            value.update(experiment_title=rec["title"], current_tags=indexed.get("tags", []),
                         changed=stat.st_size != item.get("size") or abs(stat.st_mtime-indexed["mtime"]) > .000001
                         or indexed.get("sha256", "") != item.get("sha256", ""))
        except (ValueError, OSError, KeyError):
            value["missing"] = True
        result.append(value)
    return result


def incoming(ctx, exp_id):
    """Use authoritative metadata, so protection survives an index rebuild."""
    from . import experiment as ex
    paths = set()
    for info in ex.find_meta_files(ctx):
        if ctx.data_dir.resolve() not in info.resolve().parents:
            continue
        meta = ex.load_meta(info.parent) or {}
        for record in [*meta.get("steps", []), *meta.get("journal_entries", [])]:
            paths.update(item["path"] for item in record.get("references", []) if item.get("exp_id") == exp_id)
        if meta.get("id") == exp_id:
            paths.update(image["path"] for step in meta.get("steps", []) for image in step.get("images", []))
    return paths


def relative_url(item):
    relative = item.get("relative_path", "")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or PureWindowsPath(relative).drive or "\\" in relative:
        return ""
    return quote(relative, safe="/")


def markdown(items):
    lines = []
    for item in items:
        url = relative_url(item)
        name = html.escape(item.get("name", item.get("path", "文件")))
        link = f'<a href="{html.escape(url, quote=True)}">{name}</a>' if url else name
        lines.append(f"- {link} · 来源实验 {html.escape(item['exp_id'])} · SHA256 {html.escape(item.get('sha256', ''))}")
    return "\n".join(lines)
