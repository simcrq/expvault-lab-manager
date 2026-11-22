"""Process condition matrix stored in authoritative experiment metadata.

Allows a single experiment to record multiple process parameter conditions
(e.g., annealing temperatures, etch power/time grids), each with its own
parameters, measured metrics, observations, status, and attached images/files.
"""
from __future__ import annotations

import html
import os
import shutil
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path, PureWindowsPath
from urllib.parse import quote

from jsonschema import Draft202012Validator, FormatChecker

from . import experiment as ex
from .file_tags import normalize_tags
from .importer import IMAGE_EXT, VIDEO_EXT, file_kind, portable_name
from .rich_text import scientific_html
from .util import now_iso, sha256_file, truncate

CONDITION_STATUSES = ("pending", "tuning", "ok", "best", "failed")
CONDITION_STATUS_LABEL = {
    "pending": "待测",
    "tuning": "调试中",
    "ok": "合格",
    "best": "最优",
    "failed": "失败",
}

SHORT = {"type": "string", "maxLength": 256}
ID = {"type": "string", "pattern": "^[a-f0-9]{32}$"}
KV_MAP = {
    "type": "object",
    "maxProperties": 60,
    "propertyNames": {"minLength": 1, "maxLength": 128, "pattern": r"\S"},
    "additionalProperties": {"type": ["string", "number", "boolean", "null"], "maxLength": 2000},
}
ASSET = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path", "name"],
    "properties": {
        "path": {"type": "string", "minLength": 1, "maxLength": 4096},
        "name": {"type": "string", "minLength": 1, "maxLength": 512},
        "caption": {"type": "string", "maxLength": 512},
    },
}
CONDITION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "name"],
    "properties": {
        "id": ID,
        "name": {**SHORT, "minLength": 1, "maxLength": 160, "pattern": r"\S"},
        "sample": {"type": "string", "maxLength": 128},
        "status": {"enum": list(CONDITION_STATUSES)},
        "params": KV_MAP,
        "metrics": KV_MAP,
        "note": {"type": "string", "maxLength": 10000},
        "images": {"type": "array", "maxItems": 50, "items": ASSET},
        "files": {"type": "array", "maxItems": 50, "items": ASSET},
        "created_at": {"type": "string", "maxLength": 64},
        "updated_at": {"type": "string", "maxLength": 64},
    },
}
CONDITIONS_SCHEMA = {
    "type": "array",
    "maxItems": 100,
    "items": CONDITION_SCHEMA,
}
VALIDATOR = Draft202012Validator(CONDITIONS_SCHEMA, format_checker=FormatChecker())


def _safe_relpath(relative: str) -> str:
    if (
        not isinstance(relative, str)
        or not relative
        or Path(relative).is_absolute()
        or PureWindowsPath(relative).drive
        or "\\" in relative
        or any(part in ("..", ".expvault") for part in relative.split("/"))
    ):
        raise ValueError("条件关联文件路径无效")
    return relative


def _clean_assets(items: list[dict] | None) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for item in items or []:
        rel = _safe_relpath(item["path"])
        if rel in seen:
            continue
        seen.add(rel)
        out.append({
            "path": rel,
            "name": (item.get("name") or Path(rel).name).strip()[:512],
            "caption": str(item.get("caption") or "").strip()[:512],
        })
    return out


def _clean_kv(mapping: dict | None) -> dict:
    out: dict = {}
    for k, v in (mapping or {}).items():
        key = str(k).strip()
        if not key:
            continue
        out[key] = v.strip() if isinstance(v, str) else v
    return out


def normalize(conditions: list[dict] | None) -> list[dict]:
    if conditions is None:
        return []
    if not VALIDATOR.is_valid(conditions):
        raise ValueError("工艺条件格式无效：最多 100 组条件，每组最多 60 项参数和 50 个附件")
    result = deepcopy(conditions)
    seen: set[str] = set()
    stamp = now_iso()
    cleaned: list[dict] = []
    for item in result:
        cid = item["id"]
        if cid in seen:
            raise ValueError("工艺条件编号不能重复")
        seen.add(cid)
        created = item.get("created_at") or stamp
        updated = item.get("updated_at") or created
        cleaned.append({
            "id": cid,
            "name": item["name"].strip(),
            "sample": str(item.get("sample") or "").strip(),
            "status": item.get("status") or "pending",
            "params": _clean_kv(item.get("params")),
            "metrics": _clean_kv(item.get("metrics")),
            "note": str(item.get("note") or "").strip(),
            "images": _clean_assets(item.get("images")),
            "files": _clean_assets(item.get("files")),
            "created_at": created,
            "updated_at": updated,
        })
    return cleaned


def is_image_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXT


def _unique_dest(directory: Path, filename: str) -> Path:
    safe = portable_name(filename)
    candidate = directory / safe
    if not candidate.exists():
        return candidate
    stem, suffix = os.path.splitext(safe)
    for idx in range(2, 10000):
        candidate = directory / f"{stem}_{idx}{suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}_{uuid.uuid4().hex[:6]}{suffix}"


def _store_upload_for_condition(ctx, folder: Path, exp_id: str, cond_id: str, cond_name: str,
                                source: Path, original_name: str, extra_tags: list[str],
                                meta: dict) -> tuple[dict, bool]:
    """Copy a staged upload into the experiment folder, hash & register it."""
    folder_res = folder.resolve()
    kind = file_kind(source)
    is_img = source.suffix.lower() in IMAGE_EXT
    if is_img or kind in ("图片", "视频"):
        base_rel = f"04_notes/conditions/{cond_id[:8]}"
    else:
        base_rel = f"01_raw/conditions/{cond_id[:8]}"
    dest_dir = folder / base_rel
    if folder_res not in dest_dir.resolve().parents:
        raise ValueError("条件存储目录越界")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(dest_dir, original_name or source.name)
    tmp = dest.with_name("." + dest.name + ".part")
    try:
        shutil.copy2(source, tmp)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    st = dest.stat()
    rel = dest.relative_to(folder).as_posix()
    digest = sha256_file(dest)
    merged_tags = normalize_tags([cond_name, *(extra_tags or [])])
    meta.setdefault("file_tags", {})[rel] = normalize_tags([*meta.get("file_tags", {}).get(rel, []), *merged_tags])
    ctx.db.upsert_file(exp_id, {
        "relpath": rel,
        "size": int(st.st_size),
        "mtime": float(st.st_mtime),
        "sha256": digest,
        "role": ctx.role_for(rel),
        "tags": meta["file_tags"][rel],
    })
    return {"path": rel, "name": Path(original_name or dest.name).name[:512], "caption": ""}, is_img


def _commit_meta(ctx, rec: dict, folder: Path, meta: dict, action: str, detail: str) -> None:
    stamp = now_iso()
    meta["updated_at"] = stamp
    meta.setdefault("history", []).append({"ts": stamp, "action": action, "detail": detail})
    ex.save_meta(folder, meta)
    ctx.db.upsert_experiment({**meta, "folder": rec["folder"]})
    meta_file = folder / ex.META_NAME
    st = meta_file.stat()
    ctx.db.upsert_file(rec["id"], {
        "relpath": ex.META_NAME,
        "size": int(st.st_size),
        "mtime": float(st.st_mtime),
        "sha256": sha256_file(meta_file),
        "role": "meta",
    })
    ctx.db.log(action, rec["id"], detail)
    ctx.db.commit()


def _attach_to_item(ctx, rec: dict, folder: Path, meta: dict, item: dict,
                    upload_sources: list[tuple[Path, str, list[str]]] | None = None,
                    existing_paths: list[str] | None = None) -> int:
    added = 0
    existing_img_paths = {a["path"] for a in item["images"]}
    existing_file_paths = {a["path"] for a in item["files"]}

    for source, orig_name, extra_tags in upload_sources or []:
        asset, is_img = _store_upload_for_condition(
            ctx, folder, rec["id"], item["id"], item["name"], source, orig_name, extra_tags, meta
        )
        if is_img:
            if len(item["images"]) >= 50:
                raise ValueError("单个工艺条件最多关联 50 张图片")
            item["images"].append(asset)
            existing_img_paths.add(asset["path"])
        else:
            if len(item["files"]) >= 50:
                raise ValueError("单个工艺条件最多关联 50 个数据文件")
            item["files"].append(asset)
            existing_file_paths.add(asset["path"])
        added += 1

    for rel in existing_paths or []:
        rel = _safe_relpath(rel)
        is_img = is_image_path(rel)
        asset = {"path": rel, "name": Path(rel).name[:512], "caption": ""}
        if is_img:
            if rel in existing_img_paths:
                continue
            if len(item["images"]) >= 50:
                raise ValueError("单个工艺条件最多关联 50 张图片")
            item["images"].append(asset)
            existing_img_paths.add(rel)
        else:
            if rel in existing_file_paths:
                continue
            if len(item["files"]) >= 50:
                raise ValueError("单个工艺条件最多关联 50 个数据文件")
            item["files"].append(asset)
            existing_file_paths.add(rel)
        added += 1

    return added


def save_condition(ctx, rec: dict, payload: dict, *,
                   upload_sources: list[tuple[Path, str, list[str]]] | None = None,
                   existing_paths: list[str] | None = None) -> dict:
    from .nas import require_writer
    require_writer(ctx)
    folder = ex.exp_dir(ctx, rec)
    meta = ex.load_meta(folder)
    if not meta or meta.get("id") != rec["id"]:
        raise ValueError("实验元数据缺失或编号不匹配")
    items = normalize(meta.get("process_conditions", []))
    cid = payload.get("id") or uuid.uuid4().hex
    stamp = now_iso()
    idx = next((i for i, it in enumerate(items) if it["id"] == cid), None)
    if idx is not None:
        item = deepcopy(items[idx])
        item["name"] = str(payload.get("name", item["name"])).strip()
        if "sample" in payload:
            item["sample"] = str(payload["sample"] or "").strip()
        if "status" in payload:
            item["status"] = payload["status"]
        if "params" in payload:
            item["params"] = _clean_kv(payload["params"])
        if "metrics" in payload:
            item["metrics"] = _clean_kv(payload["metrics"])
        if "note" in payload:
            item["note"] = str(payload["note"] or "").strip()
        if "images" in payload:
            item["images"] = _clean_assets(payload["images"])
        if "files" in payload:
            item["files"] = _clean_assets(payload["files"])
        item["updated_at"] = stamp
    else:
        if len(items) >= 100:
            raise ValueError("单个实验最多记录 100 组工艺条件")
        item = {
            "id": cid,
            "name": str(payload.get("name") or "").strip(),
            "sample": str(payload.get("sample") or "").strip(),
            "status": payload.get("status") or "pending",
            "params": _clean_kv(payload.get("params")),
            "metrics": _clean_kv(payload.get("metrics")),
            "note": str(payload.get("note") or "").strip(),
            "images": _clean_assets(payload.get("images")),
            "files": _clean_assets(payload.get("files")),
            "created_at": stamp,
            "updated_at": stamp,
        }
    if not item["name"]:
        raise ValueError("请填写工艺条件名称")
    _attach_to_item(ctx, rec, folder, meta, item, upload_sources=upload_sources, existing_paths=existing_paths)
    if idx is not None:
        items[idx] = item
        detail = f"更新工艺条件：{truncate(item['name'], 60)}"
    else:
        items.append(item)
        detail = f"新增工艺条件：{truncate(item['name'], 60)}"
    meta["process_conditions"] = normalize(items)
    _commit_meta(ctx, rec, folder, meta, "condition_save", detail)
    return item


def batch_create(ctx, rec: dict, entries: list[dict]) -> list[dict]:
    from .nas import require_writer
    require_writer(ctx)
    if not entries or len(entries) > 60:
        raise ValueError("每次最多批量添加 1–60 组工艺条件")
    folder = ex.exp_dir(ctx, rec)
    meta = ex.load_meta(folder)
    if not meta or meta.get("id") != rec["id"]:
        raise ValueError("实验元数据缺失或编号不匹配")
    items = normalize(meta.get("process_conditions", []))
    if len(items) + len(entries) > 100:
        raise ValueError(f"工艺条件总数不能超过 100 组（当前已有 {len(items)} 组）")
    stamp = now_iso()
    created: list[dict] = []
    for entry in entries:
        name = str(entry.get("name") or "").strip()
        if not name:
            raise ValueError("批量生成的每组工艺条件都必须有名称")
        item = {
            "id": uuid.uuid4().hex,
            "name": name[:160],
            "sample": str(entry.get("sample") or "").strip()[:128],
            "status": entry.get("status") or "pending",
            "params": _clean_kv(entry.get("params")),
            "metrics": _clean_kv(entry.get("metrics")),
            "note": str(entry.get("note") or "").strip(),
            "images": [],
            "files": [],
            "created_at": stamp,
            "updated_at": stamp,
        }
        items.append(item)
        created.append(item)
    meta["process_conditions"] = normalize(items)
    _commit_meta(ctx, rec, folder, meta, "condition_batch", f"批量添加 {len(created)} 组工艺条件")
    return created


def attach_files(ctx, rec: dict, cond_id: str, *,
                 upload_sources: list[tuple[Path, str, list[str]]] | None = None,
                 existing_paths: list[str] | None = None) -> dict:
    from .nas import require_writer
    require_writer(ctx)
    folder = ex.exp_dir(ctx, rec)
    meta = ex.load_meta(folder)
    if not meta or meta.get("id") != rec["id"]:
        raise ValueError("实验元数据缺失或编号不匹配")
    items = normalize(meta.get("process_conditions", []))
    idx = next((i for i, it in enumerate(items) if it["id"] == cond_id), None)
    if idx is None:
        raise ValueError("工艺条件不存在，请刷新页面")
    item = items[idx]
    added = _attach_to_item(ctx, rec, folder, meta, item, upload_sources=upload_sources, existing_paths=existing_paths)
    item["updated_at"] = now_iso()
    meta["process_conditions"] = normalize(items)
    _commit_meta(ctx, rec, folder, meta, "condition_attach", f"工艺条件「{truncate(item['name'], 40)}」关联 {added} 个文件")
    return item


def detach_file(ctx, rec: dict, cond_id: str, path: str) -> dict:
    from .nas import require_writer
    require_writer(ctx)
    folder = ex.exp_dir(ctx, rec)
    meta = ex.load_meta(folder)
    if not meta or meta.get("id") != rec["id"]:
        raise ValueError("实验元数据缺失或编号不匹配")
    items = normalize(meta.get("process_conditions", []))
    idx = next((i for i, it in enumerate(items) if it["id"] == cond_id), None)
    if idx is None:
        raise ValueError("工艺条件不存在，请刷新页面")
    item = items[idx]
    before = len(item["images"]) + len(item["files"])
    item["images"] = [a for a in item["images"] if a["path"] != path]
    item["files"] = [a for a in item["files"] if a["path"] != path]
    if len(item["images"]) + len(item["files"]) == before:
        raise ValueError("该文件未关联到此工艺条件")
    item["updated_at"] = now_iso()
    meta["process_conditions"] = normalize(items)
    _commit_meta(ctx, rec, folder, meta, "condition_detach", f"工艺条件「{truncate(item['name'], 40)}」移出文件 {Path(path).name}")
    return item


def update_status(ctx, rec: dict, cond_id: str, status: str) -> dict:
    from .nas import require_writer
    require_writer(ctx)
    if status not in CONDITION_STATUSES:
        raise ValueError("无效的工艺条件状态")
    folder = ex.exp_dir(ctx, rec)
    meta = ex.load_meta(folder)
    if not meta or meta.get("id") != rec["id"]:
        raise ValueError("实验元数据缺失或编号不匹配")
    items = normalize(meta.get("process_conditions", []))
    idx = next((i for i, it in enumerate(items) if it["id"] == cond_id), None)
    if idx is None:
        raise ValueError("工艺条件不存在，请刷新页面")
    item = items[idx]
    item["status"] = status
    item["updated_at"] = now_iso()
    meta["process_conditions"] = normalize(items)
    _commit_meta(ctx, rec, folder, meta, "condition_status",
                 f"工艺条件「{truncate(item['name'], 40)}」状态 → {CONDITION_STATUS_LABEL.get(status, status)}")
    return item


def update_image(ctx, rec: dict, cond_id: str, path: str, *, caption: str | None = None, set_cover: bool = False) -> dict:
    from .nas import require_writer
    require_writer(ctx)
    folder = ex.exp_dir(ctx, rec)
    meta = ex.load_meta(folder)
    if not meta or meta.get("id") != rec["id"]:
        raise ValueError("实验元数据缺失或编号不匹配")
    items = normalize(meta.get("process_conditions", []))
    idx = next((i for i, it in enumerate(items) if it["id"] == cond_id), None)
    if idx is None:
        raise ValueError("工艺条件不存在，请刷新页面")
    item = items[idx]
    img_idx = next((i for i, a in enumerate(item["images"]) if a["path"] == path), None)
    if img_idx is None:
        raise ValueError("图片未关联到此工艺条件")
    if caption is not None:
        item["images"][img_idx]["caption"] = str(caption).strip()[:512]
    if set_cover and img_idx > 0:
        img = item["images"].pop(img_idx)
        item["images"].insert(0, img)
    item["updated_at"] = now_iso()
    meta["process_conditions"] = normalize(items)
    _commit_meta(ctx, rec, folder, meta, "condition_save", f"更新条件「{truncate(item['name'], 40)}」图片信息")
    return item


def reorder(ctx, rec: dict, order_ids: list[str]) -> list[dict]:
    from .nas import require_writer
    require_writer(ctx)
    folder = ex.exp_dir(ctx, rec)
    meta = ex.load_meta(folder)
    if not meta or meta.get("id") != rec["id"]:
        raise ValueError("实验元数据缺失或编号不匹配")
    items = normalize(meta.get("process_conditions", []))
    by_id = {it["id"]: it for it in items}
    if set(order_ids) != set(by_id) or len(order_ids) != len(items):
        raise ValueError("排序列表与当前工艺条件不一致，请刷新后重试")
    reordered = [by_id[cid] for cid in order_ids]
    meta["process_conditions"] = reordered
    _commit_meta(ctx, rec, folder, meta, "condition_reorder", f"调整 {len(reordered)} 组工艺条件顺序")
    return reordered


def delete_condition(ctx, rec: dict, cond_id: str) -> None:
    from .nas import require_writer
    require_writer(ctx)
    folder = ex.exp_dir(ctx, rec)
    meta = ex.load_meta(folder)
    if not meta or meta.get("id") != rec["id"]:
        raise ValueError("实验元数据缺失或编号不匹配")
    items = normalize(meta.get("process_conditions", []))
    target = next((it for it in items if it["id"] == cond_id), None)
    if target is None:
        raise ValueError("工艺条件不存在，请刷新页面")
    meta["process_conditions"] = [it for it in items if it["id"] != cond_id]
    _commit_meta(ctx, rec, folder, meta, "condition_delete", f"删除工艺条件：{truncate(target['name'], 60)}")


def summary_html(conditions: list[dict] | None) -> str:
    """Safe, offline-readable HTML matrix for README and HTML exports."""
    items = conditions or []
    if not items:
        return ""
    param_keys = list(dict.fromkeys(k for c in items for k in (c.get("params") or {})))
    metric_keys = list(dict.fromkeys(k for c in items for k in (c.get("metrics") or {})))
    headers = ["<th>条件</th>", "<th>样品</th>", "<th>状态</th>"]
    headers.extend(f"<th>{scientific_html(k)}</th>" for k in param_keys)
    headers.extend(f"<th>{scientific_html(k)}</th>" for k in metric_keys)
    headers.extend(["<th>图片 / 附件</th>", "<th>观察备注</th>"])
    rows = []
    for c in items:
        cells = [
            f"<td><strong>{scientific_html(c.get('name', ''))}</strong></td>",
            f"<td>{scientific_html(c.get('sample', '') or '—')}</td>",
            f"<td>{html.escape(CONDITION_STATUS_LABEL.get(c.get('status', 'pending'), c.get('status', '')))}</td>",
        ]
        params = c.get("params") or {}
        metrics = c.get("metrics") or {}
        cells.extend(f"<td>{scientific_html(params[k]) if k in params else '—'}</td>" for k in param_keys)
        cells.extend(f"<td>{scientific_html(metrics[k]) if k in metrics else '—'}</td>" for k in metric_keys)
        links = []
        for asset in [*(c.get("images") or []), *(c.get("files") or [])]:
            rel = asset.get("path", "")
            name = html.escape(asset.get("name") or Path(rel).name)
            url = quote(rel, safe="/")
            links.append(f'<a href="{html.escape(url, quote=True)}">{name}</a>')
        cells.append(f"<td>{', '.join(links) or '—'}</td>")
        cells.append(f"<td>{scientific_html(c.get('note', '') or '—')}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<div style="overflow:auto"><table><thead><tr>'
        + "".join(headers)
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )
