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
    ex.record_history(ctx, meta, action, detail, ts=stamp)
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
    ex.log_event(ctx, action, rec["id"], detail)
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


# --------------------------------------------------------------------------
# Process Condition Table Export (Excel / CSV / Markdown)
# --------------------------------------------------------------------------

def analyze_conditions(items: list[dict]) -> dict:
    """Identify varying parameters, shared parameters, and metric keys."""
    key_order: list[str] = []
    value_sets: dict[str, set[str]] = {}
    metric_order: list[str] = []
    for cond in items:
        for k, v in (cond.get("params") or {}).items():
            if k not in value_sets:
                key_order.append(k)
                value_sets[k] = set()
            value_sets[k].add("" if v is None else str(v))
        for k in (cond.get("metrics") or {}):
            if k not in metric_order:
                metric_order.append(k)
    varying_keys: list[str] = []
    shared_keys: list[str] = []
    for k in key_order:
        present = sum(1 for c in items if k in (c.get("params") or {}))
        if len(value_sets[k]) > 1 or present < len(items):
            varying_keys.append(k)
        else:
            shared_keys.append(k)
    return {
        "all_param_keys": key_order,
        "varying_keys": varying_keys,
        "shared_keys": shared_keys,
        "metric_keys": metric_order,
    }


def _cell_str(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (dict, list)):
        import json
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def table_columns_and_rows(meta: dict, cond_ids: list[str] | None = None) -> tuple[list[str], list[list], dict]:
    all_items = normalize(meta.get("process_conditions", []))
    if cond_ids:
        wanted = set(cond_ids)
        by_id = {c["id"]: c for c in all_items}
        # Respect provided order if valid, else preserve meta order
        items = [by_id[cid] for cid in cond_ids if cid in by_id]
        if not items:
            items = [c for c in all_items if c["id"] in wanted]
    else:
        items = all_items

    analysis = analyze_conditions(items)
    param_cols = [*analysis["varying_keys"], *analysis["shared_keys"]]
    metric_cols = analysis["metric_keys"]
    headers = [
        "序号",
        "工艺条件",
        "样品编号",
        "状态",
        *param_cols,
        *metric_cols,
        "图片数",
        "图片文件",
        "数据附件",
        "观察备注",
    ]
    rows: list[list] = []
    for idx, cond in enumerate(items, 1):
        params = cond.get("params") or {}
        metrics = cond.get("metrics") or {}
        imgs = cond.get("images") or []
        files = cond.get("files") or []
        row = [
            idx,
            cond.get("name", ""),
            cond.get("sample", ""),
            CONDITION_STATUS_LABEL.get(cond.get("status", "pending"), cond.get("status", "")),
            *[_cell_str(params.get(k, "")) for k in param_cols],
            *[_cell_str(metrics.get(k, "")) for k in metric_cols],
            len(imgs),
            "; ".join(a.get("name") or Path(a.get("path", "")).name for a in imgs),
            "; ".join(a.get("name") or Path(a.get("path", "")).name for a in files),
            cond.get("note", ""),
        ]
        rows.append(row)
    return headers, rows, analysis


def export_markdown(meta: dict, cond_ids: list[str] | None = None) -> str:
    headers, rows, _analysis = table_columns_and_rows(meta, cond_ids=cond_ids)

    def esc(val) -> str:
        return str(val if val is not None else "").replace("|", "\\|").replace("\r\n", " ").replace("\n", " ")

    title = meta.get("title") or meta.get("id") or "工艺条件表"
    lines = [
        f"# {title} · 工艺条件表",
        "",
        f"- 实验编号：`{meta.get('id', '')}`  |  日期：{meta.get('date', '')}  |  样品：{meta.get('device', '') or '—'}  |  条件数：{len(rows)}",
        "",
    ]
    if not rows:
        lines.append("_暂无工艺条件记录_")
        return "\n".join(lines) + "\n"
    lines.append("| " + " | ".join(esc(h) for h in headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(esc(c) for c in row) + " |")
    return "\n".join(lines) + "\n"


def export_csv(meta: dict, path: Path, cond_ids: list[str] | None = None) -> Path:
    import csv

    headers, rows, _analysis = table_columns_and_rows(meta, cond_ids=cond_ids)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
    return path


def export_xlsx(meta: dict, path: Path, cond_ids: list[str] | None = None) -> Path:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except Exception:
        return export_csv(meta, path.with_suffix(".csv"), cond_ids=cond_ids)

    headers, rows, analysis = table_columns_and_rows(meta, cond_ids=cond_ids)
    varying_set = set(analysis["varying_keys"])
    metric_set = set(analysis["metric_keys"])

    wb = Workbook()
    ws = wb.active
    ws.title = "工艺条件表"
    ws.append(headers)
    for row in rows:
        ws.append(row)
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"

    # Style header row
    header_fill = PatternFill("solid", fgColor="1F3864")
    varying_fill = PatternFill("solid", fgColor="1E4DB7")
    metric_fill = PatternFill("solid", fgColor="146C43")
    header_font = Font(color="FFFFFF", bold=True, size=10)
    thin = Side(style="thin", color="D9E2EC")
    for col_idx, name in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = varying_fill if name in varying_set else (metric_fill if name in metric_set else header_fill)
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = Border(bottom=thin)
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 24
    if ws.max_row >= 1 and len(headers) >= 1:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{ws.max_row}"

    # Highlight status rows subtly
    status_fills = {
        "最优": PatternFill("solid", fgColor="FFF4D6"),
        "合格": PatternFill("solid", fgColor="E9F7EF"),
        "失败": PatternFill("solid", fgColor="FDECEC"),
    }
    for row_idx in range(2, ws.max_row + 1):
        st_val = ws.cell(row=row_idx, column=4).value
        if st_val in status_fills:
            ws.cell(row=row_idx, column=4).fill = status_fills[st_val]

    # Auto-size columns reasonably
    for col_idx, name in enumerate(headers, start=1):
        max_len = len(str(name))
        for row_idx in range(2, min(ws.max_row + 1, 200)):
            val = ws.cell(row=row_idx, column=col_idx).value
            if val is not None:
                max_len = max(max_len, min(42, len(str(val))))
        width = max(8, min(44, int(max_len * 1.45) + 4))
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # Sheet 2: Experiment overview & baseline params
    ws_info = wb.create_sheet("实验信息与基准参数")
    ws_info.append(["字段", "内容"])
    for label, val in (
        ("实验编号", meta.get("id", "")),
        ("实验标题", meta.get("title", "")),
        ("实验日期", meta.get("date", "")),
        ("器件 / 样品", meta.get("device", "")),
        ("工艺批次", meta.get("batch", "")),
        ("操作人", meta.get("operator", "")),
        ("所属课题", meta.get("project", "")),
        ("状态", ex.STATUS_LABEL.get(meta.get("status", ""), meta.get("status", ""))),
        ("导出条件数", len(rows)),
    ):
        ws_info.append([label, val])
    base_params = meta.get("params") or {}
    if base_params:
        ws_info.append(["", ""])
        ws_info.append(["公共基准参数", "数值"])
        for k, v in base_params.items():
            ws_info.append([str(k), _cell_str(v)])
    for row_idx in range(2, ws_info.max_row + 1):
        for cell in ws_info[row_idx]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    for col_idx in (1, 2):
        cell = ws_info.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
    ws_info.column_dimensions["A"].width = 22
    ws_info.column_dimensions["B"].width = 48

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def export_table(ctx, rec: dict, fmt: str = "xlsx", *, output: Path | None = None,
                 cond_ids: list[str] | None = None) -> dict:
    fmt = (fmt or "xlsx").lower()
    if fmt == "markdown":
        fmt = "md"
    if fmt not in ("xlsx", "csv", "md"):
        raise ValueError("导出格式仅支持 xlsx、csv 或 md")
    folder = ex.exp_dir(ctx, rec)
    meta = ex.load_meta(folder)
    if not meta or meta.get("id") != rec["id"]:
        raise ValueError("实验元数据缺失或编号不匹配")
    headers, rows, analysis = table_columns_and_rows(meta, cond_ids=cond_ids)
    if not rows:
        raise ValueError("当前没有可导出的工艺条件")
    md_text = export_markdown(meta, cond_ids=cond_ids)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"conditions-{rec['id']}-{stamp}"
    target = Path(output) if output else (ctx.report_dir / f"{stem}.{fmt}")
    if fmt == "xlsx":
        written = export_xlsx(meta, target, cond_ids=cond_ids)
    elif fmt == "csv":
        written = export_csv(meta, target, cond_ids=cond_ids)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(md_text, encoding="utf-8", newline="\n")
        written = target
    actual_fmt = written.suffix.lstrip(".").lower()
    return {
        "path": written,
        "name": written.name,
        "format": actual_fmt,
        "count": len(rows),
        "columns": headers,
        "varying_keys": analysis["varying_keys"],
        "markdown": md_text,
    }
