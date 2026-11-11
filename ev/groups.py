"""Workspace-local experiment groups, stored with each experiment's authoritative metadata."""
from __future__ import annotations

import copy
import json
import unicodedata

GROUPS_SCHEMA = {"type":"array","maxItems":20,"uniqueItems":True,
                 "items":{"type":"string","minLength":1,"maxLength":80,"pattern":r"\S"}}


def normalize_names(values, *, limit=20):
    if values is None:
        return []
    if not isinstance(values,list) or (limit is not None and len(values)>limit):
        raise ValueError("每个实验最多加入 20 个分组")
    result = set()
    for value in values:
        if not isinstance(value,str):
            raise ValueError("分组名称必须是文字")
        name = unicodedata.normalize("NFC",value.strip())
        if not 1 <= len(name) <= 80 or any(not char.isprintable() for char in name):
            raise ValueError("分组名称须为 1–80 字，不能包含换行或控制字符")
        if "," in name or "，" in name:
            raise ValueError("一个分组名称不能包含逗号；多个分组请分开填写")
        result.add(name)
    return sorted(result,key=lambda value:(value.casefold(),value))


def hydrate_index(ctx):
    from . import experiment as ex
    for path in ex.find_meta_files(ctx):
        meta=ex.load_meta(path.parent)
        if not meta or not meta.get("id"):
            continue
        try:
            names=normalize_names(meta.get("groups"), limit=None)
        except ValueError:
            names=[]
        ctx.db.conn.execute("UPDATE experiments SET experiment_groups=? WHERE id=?",(json.dumps(names,ensure_ascii=False),meta["id"]))
    ctx.db.commit()


def catalog(ctx):
    result={}
    ungrouped=0
    records=ctx.db.list_experiments(include_archived=True)
    for rec in records:
        if not rec.get("groups"):
            ungrouped+=1
        for name in rec.get("groups",[]):
            item=result.setdefault(name,{"name":name,"count":0,"active":0,"running":0})
            item["count"]+=1
            item["active"]+=int(not rec.get("archived"))
            item["running"]+=int(rec.get("status")=="running" and not rec.get("archived"))
    return {"items":sorted(result.values(),key=lambda item:(item["name"].casefold(),item["name"])),"ungrouped":ungrouped,"total":len(records)}


def apply(ctx, changes, *, progress=None):
    """changes contains prevalidated (record, original metadata, new names) tuples."""
    from . import experiment as ex
    changed=[]
    event_start=ctx.db.conn.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
    try:
        for index,(rec,meta,names) in enumerate(changes):
            if set(normalize_names(meta.get("groups"), limit=None)) != set(names):
                changed.append((rec,copy.deepcopy(meta)))
                ex.update(ctx,rec,{"groups":names})
            if progress:
                progress(index+1,len(changes),f"已处理 {index+1}/{len(changes)} 个实验")
        return {"updated":len(changed),"ids":[rec["id"] for rec,_meta in changed]}
    except Exception:
        remaining=[]
        for rec,meta in reversed(changed):
            try:
                folder=ex.exp_dir(ctx,rec)
                ex.save_meta(folder,meta)
                ex.register_from_disk(ctx,folder)
                ex.write_readme(ctx,meta,ctx.db.files_of(rec["id"],include_missing=False),folder=folder)
                ex.scan_files(ctx,folder,meta)
                ctx.db.conn.execute("DELETE FROM events WHERE exp_id=? AND id>?",(rec["id"],event_start))
                ctx.db.commit()
            except Exception:
                remaining.append(rec["id"])
        if remaining:
            raise ValueError("分组更新未完成，请核对这些实验："+", ".join(remaining)) from None
        raise
