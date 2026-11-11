"""实验问题记录：设备故障、工艺异常、数据异常，统一留痕。

写进库根目录的 问题记录.jsonl（纯文本、追加式，任何编辑器都能看），
同时会随备份一起保存。
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import Ctx
from .util import append_jsonl, now_iso, read_jsonl, table, truncate

ISSUE_FILE = "问题记录.jsonl"

TYPES = ["设备", "工艺", "数据", "安全", "其他"]
SEVERITY = ["低", "中", "高", "紧急"]


def issue_path(ctx: Ctx) -> Path:
    return ctx.root / ISSUE_FILE


def add_issue(
    ctx: Ctx,
    text: str,
    *,
    type_: str = "设备",
    severity: str = "中",
    target: str = "",
    exp_id: str = "",
    action: str = "",
) -> dict:
    rec = {
        "ts": now_iso(),
        "type": type_,
        "severity": severity,
        "target": target,
        "exp_id": exp_id,
        "desc": text,
        "action": action,
        "resolved": False,
    }
    append_jsonl(issue_path(ctx), rec)
    ctx.db.log("issue", exp_id, f"[{type_}/{severity}] {truncate(text, 80)}")
    return rec


def list_issues(ctx: Ctx, *, unresolved_only: bool = False, limit: int = 50) -> list[dict]:
    rows = read_jsonl(issue_path(ctx))
    if unresolved_only:
        rows = [r for r in rows if not r.get("resolved")]
    return list(reversed(rows))[:limit]


def format_issues(rows: list[dict]) -> str:
    data = []
    for r in rows:
        data.append([
            (r.get("ts") or "")[:16].replace("T", " "),
            r.get("type", ""),
            r.get("severity", ""),
            truncate(r.get("target", ""), 16),
            truncate(r.get("desc", "").replace("\n", " "), 46),
            "已解决" if r.get("resolved") else "未解决",
        ])
    return table(data, ["时间", "类型", "级别", "对象", "描述", "状态"],
                 ["left", "center", "center", "left", "left", "center"])
