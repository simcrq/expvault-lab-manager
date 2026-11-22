"""Create fresh experiments from settings, with no inherited observations or raw files."""
from __future__ import annotations

import copy
import shutil
import uuid

from . import experiment as ex
from .util import append_jsonl, now_iso, today


def prepare(source, variants, *, copy_instruments=True, copy_notes=False, replace_params=False):
    plans = []
    for variant in variants:
        values = {key:copy.deepcopy(source.get(key, [] if key in ("tags","groups") else {} if key == "params" else ""))
                  for key in ("project","operator","device","batch","category","tags","groups","params","purpose","conditions")}
        values.update(title=source["title"][:252]+"（副本）",date=today(),status="planned",notes=source.get("notes", "") if copy_notes else "")
        changes = copy.deepcopy(variant)
        if "params" in changes and not replace_params:
            changes["params"] = {**values["params"],**changes["params"]}
        values.update(changes)
        values["title"] = values["title"].strip()
        values["measurement_groups"] = [{"id":uuid.uuid4().hex,"name":group["name"],"model":group.get("model", ""),"runs":[]}
                                         for group in source.get("measurement_groups", [])] if copy_instruments else []
        if "process_conditions" not in changes and source.get("process_conditions"):
            values["process_conditions"] = [
                {
                    "id": uuid.uuid4().hex,
                    "name": cond.get("name", ""),
                    "sample": "",
                    "status": "pending",
                    "params": copy.deepcopy(cond.get("params", {})),
                    "metrics": {},
                    "note": "",
                    "images": [],
                    "files": [],
                }
                for cond in source.get("process_conditions", [])
                if cond.get("name")
            ]
        plans.append(values)
    return plans


def create_many(ctx, source, plans, *, progress=None, source_revision=""):
    if not 1 <= len(plans) <= 100:
        raise ValueError("每次可从模板生成 1–100 个实验")
    batch_id = uuid.uuid4().hex
    created, creation_log = [], []
    event_start = ctx.db.conn.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
    try:
        for index, plan in enumerate(plans):
            values = copy.deepcopy(plan)
            values["date_str"] = values.pop("date")
            rec = ex.create(ctx,**values,_creation_log=creation_log)
            folder = ex.exp_dir(ctx,rec)
            meta = ex.load_meta(folder)
            meta["template_source"] = {"id":source["id"],"sync_id":source.get("sync_id", ""),"title":source["title"],"revision":source_revision,"batch_id":batch_id}
            ex.record_history(ctx, meta, "from_template", "根据实验模板创建："+source["id"]+" · "+source["title"])
            ex.save_meta(folder,meta)
            ex.write_readme(ctx,meta,[],folder=folder)
            ex.scan_files(ctx,folder,meta)
            created.append(rec)
            if progress:
                progress(index+1,len(plans),f'已创建 {index+1}/{len(plans)}：{rec["title"]}')
        return created, batch_id
    except Exception:
        failures = []
        for item in reversed(creation_log):
            try:
                shutil.rmtree(item["folder"])
                ctx.db.delete_experiment(item["id"])
                ctx.db.conn.execute("DELETE FROM events WHERE exp_id=? AND id>?",(item["id"],event_start))
                ctx.db.commit()
            except Exception:
                failures.append(item["id"])
        append_jsonl(ctx.log_path,{"ts":now_iso(),"action":"template_rollback","detail":"模板生成未完成", "ids":[item["id"] for item in creation_log],"remaining":failures})
        if failures:
            raise ValueError("模板生成未完成，部分新目录无法清理，请核对这些编号："+", ".join(failures)) from None
        raise
