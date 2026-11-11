"""「当前实验」状态：记住你在哪个工作区里。

状态存在 .expvault/session.json，独立于数据库，删了索引也不影响。
"""
from __future__ import annotations

from .config import Ctx
from .util import now_iso, read_json, truncate, write_json

STATE_REL = ".expvault/session.json"


def state_path(ctx: Ctx):
    return ctx.root / STATE_REL


def get_current_id(ctx: Ctx) -> str:
    data = read_json(state_path(ctx), {}) or {}
    return str(data.get("current") or "")


def set_current(ctx: Ctx, exp_id: str) -> None:
    write_json(state_path(ctx), {"current": exp_id, "updated_at": now_iso()})


def clear_current(ctx: Ctx) -> None:
    write_json(state_path(ctx), {"current": "", "updated_at": now_iso()})


def resolve_current(ctx: Ctx) -> dict | None:
    """返回当前实验记录；没有设置或已被删除则返回 None。"""
    eid = get_current_id(ctx)
    if not eid:
        return None
    return ctx.db.find_experiment(eid)


def require_current(ctx: Ctx) -> dict:
    rec = resolve_current(ctx)
    if rec:
        return rec
    recs = ctx.db.list_experiments(limit=5)
    hint = "\n".join(f"    {r['id']}  {truncate(r['title'], 36)}" for r in recs)
    raise ValueError(
        "还没有选定当前实验。\n"
        "  进入工作区：expvault enter\n"
        "  或指定一个：expvault use <编号>\n"
        + (f"  最近的实验：\n{hint}" if hint else "  （还没有任何实验，先 expvault new \"标题\"）")
    )


def choose(ctx: Ctx, current: dict | None = None, limit: int = 30) -> dict | None:
    """方向键选一个实验；不是终端时自动降级为输入编号。"""
    from . import experiment as ex
    from . import tui
    from .util import pad

    recs = ctx.db.list_experiments(limit=limit)
    if not recs:
        return None

    cur_id = current["id"] if current else ""
    lines: list[str] = []
    default = 0
    for i, r in enumerate(recs):
        status = ex.STATUS_LABEL.get(r["status"], r["status"])
        mark = "  ←当前" if r["id"] == cur_id else ""
        lines.append(
            f"{r['id']}  {pad(truncate(r['title'], 30), 32)}  {pad(status, 4)}{mark}"
        )
        if r["id"] == cur_id:
            default = i

    idx = tui.select(
        lines,
        header=f"\n  选择一个实验（共 {len(recs)} 个）\n",
        default=default,
        hint="↑↓ 选择   Enter 确认   q 取消",
    )
    if idx is None:
        return None
    return recs[idx]
