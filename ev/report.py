"""报表导出（Excel / HTML）、统计与体检。"""
from __future__ import annotations

import html
import json
import os
import shutil
import socket
from datetime import datetime, timedelta
from pathlib import Path

from . import backup as bk
from .config import Ctx
from .experiment import STATUS_LABEL, exp_dir, load_meta
from .conditions import CONDITION_STATUS_LABEL, summary_html as conditions_summary_html
from .measurements import comparison_html
from .rich_text import notes_html
from .util import (
    human_age,
    human_size,
    human_time,
    now_iso,
    posix,
    table,
    truncate,
)

try:  # openpyxl 可选：没有就退化为 CSV
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    HAS_XLSX = True
except Exception:  # pragma: no cover
    HAS_XLSX = False


STATUS_COLOR = {
    "running": "#2563eb",
    "done": "#16a34a",
    "planned": "#a16207",
    "aborted": "#dc2626",
    "archived": "#6b7280",
}


# --------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------
def _style_header(ws, ncol: int) -> None:
    fill = PatternFill("solid", fgColor="1F3864")
    font = Font(color="FFFFFF", bold=True, size=10)
    thin = Side(style="thin", color="D9D9D9")
    for c in range(1, ncol + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = Border(bottom=thin)
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 22


def _autosize(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def export_xlsx(ctx: Ctx, path: Path) -> Path:
    if not HAS_XLSX:
        return export_csv(ctx, path.with_suffix(".csv"))

    recs = ctx.db.list_experiments(include_archived=True)
    wb = Workbook()

    # ---- 实验台账 ----
    ws = wb.active
    ws.title = "实验台账"
    headers = ["编号", "日期", "标题", "状态", "课题", "器件/样品", "工艺批次",
               "类别", "标签", "文件数", "占用", "目录", "创建时间", "最后更新", "实验分组"]
    ws.append(headers)
    for r in recs:
        ws.append([
            r["id"], r["date"], r["title"],
            STATUS_LABEL.get(r["status"], r["status"]),
            r["project"], r["device"], r["batch"], r["category"],
            "、".join(r["tag_list"]),
            r["file_count"], r["total_bytes"], r["folder"],
            human_time(r["created_at"]), human_time(r["updated_at"]), "、".join(r.get("groups", [])),
        ])
    _style_header(ws, len(headers))
    _autosize(ws, [16, 12, 42, 9, 16, 18, 14, 12, 20, 8, 11, 46, 17, 17, 24])
    ws.auto_filter.ref = f"A1:O{max(1, ws.max_row)}"
    for row in ws.iter_rows(min_row=2, min_col=11, max_col=11):
        for c in row:
            c.number_format = "#,##0"

    # ---- 文件清单 ----
    ws2 = wb.create_sheet("文件清单")
    h2 = ["实验编号", "实验标题", "相对路径", "大小(字节)", "角色", "SHA256", "登记时间", "状态", "文件标签"]
    ws2.append(h2)
    for f in ctx.db.all_files():
        ws2.append([
            f["exp_id"], f.get("exp_title", ""), f["relpath"], f["size"], f["role"],
            f["sha256"], human_time(f["registered_at"]),
            "丢失" if f["missing"] else "正常",
            "、".join(f.get("tags", [])),
        ])
    _style_header(ws2, len(h2))
    _autosize(ws2, [16, 30, 60, 13, 11, 66, 17, 8, 30])
    ws2.auto_filter.ref = f"A1:I{max(1, ws2.max_row)}"

    # ---- 备份记录 ----
    ws3 = wb.create_sheet("备份记录")
    h3 = ["时间", "目标", "快照", "文件数", "总字节", "新增字节", "新增对象", "结果"]
    ws3.append(h3)
    for b in ctx.db.backups(limit=500):
        ws3.append([
            human_time(b["ts"]), b["target"], b["snapshot"], b["files"],
            b["bytes_total"], b["bytes_new"], b["objects_new"], "成功" if b["ok"] else "有错误",
        ])
    _style_header(ws3, len(h3))
    _autosize(ws3, [18, 16, 20, 9, 14, 14, 11, 8])

    # ---- 仪器与每次对照测量，按参数逐行导出 ----
    measurements = wb.create_sheet("仪器对照测量")
    measurements.append(["实验编号", "仪器名称/编号", "仪器型号", "测量组", "测量时间", "操作人", "参数", "值"])
    for rec in recs:
        for group in rec.get("measurement_groups", []):
            for run in group["runs"] or [None]:
                for key, value in (run["params"].items() if run else [("", "")]):
                    measurements.append([rec["id"], group["name"], group["model"], run["name"] if run else "",
                                         run["measured_at"] if run else "", run["operator"] if run else "", key,
                                         json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value])
                    for cell in measurements[measurements.max_row]:
                        if isinstance(cell.value, str):
                            cell.data_type = "s"
    _style_header(measurements, 8)
    _autosize(measurements, [18, 24, 26, 24, 27, 18, 28, 35])
    measurements.auto_filter.ref = f"A1:H{max(1, measurements.max_row)}"

    # ---- 工艺条件矩阵 ----
    cond_ws = wb.create_sheet("工艺条件矩阵")
    cond_ws.append(["实验编号", "实验标题", "条件名称", "样品编号", "状态", "工艺参数", "测量指标", "图片数", "附件数", "观察备注"])
    for rec in recs:
        for cond in rec.get("process_conditions", []):
            cond_ws.append([
                rec["id"],
                rec["title"],
                cond.get("name", ""),
                cond.get("sample", ""),
                CONDITION_STATUS_LABEL.get(cond.get("status", "pending"), cond.get("status", "")),
                "; ".join(f"{k}={v}" for k, v in (cond.get("params") or {}).items()),
                "; ".join(f"{k}={v}" for k, v in (cond.get("metrics") or {}).items()),
                len(cond.get("images") or []),
                len(cond.get("files") or []),
                cond.get("note", ""),
            ])
            for cell in cond_ws[cond_ws.max_row]:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
    _style_header(cond_ws, 10)
    _autosize(cond_ws, [18, 28, 22, 16, 10, 38, 30, 8, 8, 36])
    cond_ws.auto_filter.ref = f"A1:J{max(1, cond_ws.max_row)}"

    # ---- 统计 ----
    ws4 = wb.create_sheet("统计")
    ws4.append(["维度", "取值", "实验数", "文件数", "占用字节"])
    for label, counter in (
        ("课题", _group(recs, "project")),
        ("状态", _group(recs, "status")),
        ("类别", _group(recs, "category")),
        ("月份", _group(recs, lambda r: r["date"][:7])),
    ):
        for key, (n, files, size) in counter.items():
            ws4.append([label, key or "（未填）", n, files, size])
    _style_header(ws4, 5)
    _autosize(ws4, [10, 24, 10, 10, 14])

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _group(recs: list[dict], key) -> dict[str, tuple[int, int, int]]:
    out: dict[str, list[int]] = {}
    for r in recs:
        k = key(r) if callable(key) else r.get(key, "")
        k = k or "（未填）"
        cur = out.setdefault(k, [0, 0, 0])
        cur[0] += 1
        cur[1] += r.get("file_count", 0)
        cur[2] += r.get("total_bytes", 0)
    return {k: (v[0], v[1], v[2]) for k, v in sorted(out.items())}


def export_csv(ctx: Ctx, path: Path) -> Path:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    recs = ctx.db.list_experiments(include_archived=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["编号", "日期", "标题", "状态", "课题", "器件", "批次", "类别", "标签",
                    "文件数", "占用字节", "目录", "仪器与对照测量(JSON)", "工艺条件(JSON)", "实验分组(JSON)"])
        for r in recs:
            w.writerow([r["id"], r["date"], r["title"], r["status"], r["project"],
                        r["device"], r["batch"], r["category"], "、".join(r["tag_list"]),
                        r["file_count"], r["total_bytes"], r["folder"], json.dumps(r.get("measurement_groups", []), ensure_ascii=False), json.dumps(r.get("process_conditions", []), ensure_ascii=False), json.dumps(r.get("groups", []), ensure_ascii=False)])
    return path


# --------------------------------------------------------------------------
# HTML 看板
# --------------------------------------------------------------------------
def export_html(ctx: Ctx, path: Path) -> Path:
    recs = ctx.db.list_experiments(include_archived=True)
    missing = ctx.db.missing_files()
    last = ctx.db.last_backup()
    total_bytes = sum(r["total_bytes"] for r in recs)
    total_files = sum(r["file_count"] for r in recs)

    cards = []
    for r in recs:
        color = STATUS_COLOR.get(r["status"], "#6b7280")
        tags = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in r["tag_list"])
        groups = "".join(f'<span class="tag">分组：{html.escape(name)}</span>' for name in r.get("groups", []))
        params = r.get("params") or {}
        params_html = "".join(
            f'<span class="kv"><b>{html.escape(str(k))}</b> {html.escape(str(v))}</span>'
            for k, v in list(params.items())[:6]
        )
        cards.append(f"""
    <div class="card" data-search="{html.escape((r['id']+' '+r['title']+' '+r['project']+' '+r['device']+' '+r['category']+' '+' '.join(r['tag_list'])+' '+' '.join(r.get('groups', []))+' '+json.dumps(r.get('measurement_groups', []), ensure_ascii=False)).lower())}">
      <div class="card-top">
        <span class="id">{html.escape(r['id'])}</span>
        <span class="date">{html.escape(r['date'])}</span>
        <span class="badge" style="background:{color}">{html.escape(STATUS_LABEL.get(r['status'], r['status']))}</span>
      </div>
      <h3>{html.escape(r['title'])}</h3>
      <div class="meta">
        <span>📁 {html.escape(r['category'] or '—')}</span>
        <span>🔬 {html.escape(r['device'] or '—')}</span>
        <span>🧪 {html.escape(r['batch'] or '—')}</span>
      </div>
      {f'<div class="tags">{groups}</div>' if groups else ''}
      {f'<div class="tags">{tags}</div>' if tags else ''}
      {f'<div class="params">{params_html}</div>' if params_html else ''}
      <div class="measurements">{conditions_summary_html(r.get('process_conditions', []))}</div>
      <div class="measurements">{comparison_html(r.get('measurement_groups', []))}</div>
      {f'<p class="notes">{notes_html(r.get("notes", ""))}</p>' if r.get('notes') else ''}
      <div class="foot">
        <span>{r['file_count']} 个文件</span>
        <span>{human_size(r['total_bytes'])}</span>
        <span class="dim">{html.escape(r['folder'])}</span>
      </div>
    </div>""")

    health_cls = "ok"
    health_txt = "暂无备份记录"
    if last:
        age = datetime.now().astimezone() - datetime.fromisoformat(last["ts"])
        hours = age.total_seconds() / 3600
        health_cls = "ok" if hours < 24 else ("warn" if hours < 72 else "bad")
        health_txt = f"最后一次备份：{human_time(last['ts'])}（{human_age(last['ts'])}，{last['files']} 个文件）"

    targets = []
    for t in ctx.cfg["backup"]["targets"]:
        if not t.get("enabled"):
            continue
        p = ctx.resolve_target_path(t)
        s = bk.target_summary(p)
        targets.append(
            f"{html.escape(s['name'])}：{s['snapshots']} 个快照，{human_size(s['bytes'])}，"
            f"{human_time(s['latest_at'])}"
        )
    targets_html = (
        '<div class="backups">备份目标：<ul>'
        + "".join(f"<li>{x}</li>" for x in targets)
        + "</ul></div>"
        if targets else ""
    )

    missing_html = ""
    if missing:
        rows = "".join(
            f"<li><code>{html.escape(m['exp_id'])}</code> {html.escape(m['relpath'])}</li>"
            for m in missing[:50]
        )
        missing_html = f"""
    <section class="alert">
      <h2>⚠️ 有 {len(missing)} 个已登记文件在磁盘上不见了</h2>
      <p>这些文件曾被登记，现在目录里找不到。若确认误删，可从最近备份恢复：</p>
      <pre>expvault restore latest --to 恢复目录</pre>
      <ul>{rows}</ul>
    </section>"""

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>实验数据台账 · {html.escape(ctx.root.name)}</title>
<style>
:root {{ color-scheme: light dark; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
       background:#f5f6f8; color:#1f2328; }}
header {{ background:linear-gradient(135deg,#1f3864,#2f5597); color:#fff; padding:28px 32px; }}
header h1 {{ margin:0 0 6px; font-size:22px; letter-spacing:.5px; }}
header .sub {{ opacity:.85; font-size:13px; }}
.stats {{ display:flex; gap:28px; margin-top:18px; flex-wrap:wrap; }}
.stat b {{ display:block; font-size:22px; }}
.stat span {{ font-size:12px; opacity:.8; }}
.health {{ padding:10px 32px; font-size:13px; }}
.health.ok {{ background:#e7f6ec; color:#146c2e; }}
.health.warn {{ background:#fff4e0; color:#8a5a00; }}
.health.bad {{ background:#fde8e8; color:#a11; }}
main {{ padding:24px 32px 60px; }}
.toolbar {{ display:flex; gap:12px; margin-bottom:18px; flex-wrap:wrap; }}
input[type=search] {{ flex:1; min-width:240px; padding:10px 14px; border:1px solid #d0d7de;
  border-radius:8px; font-size:14px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:14px; }}
.card {{ background:#fff; border:1px solid #e4e7ec; border-radius:10px; padding:14px 16px;
  box-shadow:0 1px 2px rgba(0,0,0,.04); }}
.card-top {{ display:flex; align-items:center; gap:8px; font-size:12px; color:#57606a; }}
.card-top .id {{ font-family:ui-monospace,Consolas,monospace; }}
.badge {{ margin-left:auto; color:#fff; border-radius:20px; padding:1px 9px; font-size:11px; }}
.card h3 {{ margin:8px 0; font-size:15px; line-height:1.4; }}
.meta {{ font-size:12px; color:#57606a; display:flex; gap:12px; flex-wrap:wrap; }}
.tags {{ margin-top:8px; display:flex; gap:6px; flex-wrap:wrap; }}
.tag {{ background:#eef2ff; color:#3b4ea8; border-radius:4px; padding:1px 7px; font-size:11px; }}
.params {{ margin-top:8px; display:flex; gap:10px; flex-wrap:wrap; font-size:11px; color:#444; }}
.kv b {{ color:#1f3864; font-weight:600; }}
.measurements {{ margin-top:10px; font-size:12px; }}
.measurements table {{ border-collapse:collapse; margin-top:8px; }}
.measurements th,.measurements td {{ border:1px solid #d0d7de; padding:6px; min-width:100px; }}
.notes {{ white-space:pre-wrap; overflow-wrap:anywhere; font-size:12px; }}
.foot {{ margin-top:10px; padding-top:8px; border-top:1px dashed #e4e7ec; font-size:11px;
  color:#57606a; display:flex; gap:12px; }}
.foot .dim {{ margin-left:auto; opacity:.7; }}
.backups {{ background:#fff; border:1px solid #e4e7ec; border-radius:8px; padding:10px 16px;
  margin-bottom:18px; font-size:12px; color:#57606a; }}
.backups ul {{ margin:6px 0 0; padding-left:18px; }}
.alert {{ background:#fff; border:1px solid #f5c2c7; border-left:4px solid #dc2626;
  border-radius:8px; padding:16px 20px; margin-bottom:20px; }}
.alert h2 {{ margin:0 0 8px; font-size:15px; color:#a11; }}
.alert pre {{ background:#f6f8fa; padding:10px; border-radius:6px; overflow:auto; }}
footer {{ text-align:center; color:#8b949e; font-size:12px; padding:20px; }}
@media (prefers-color-scheme: dark) {{
  body {{ background:#0d1117; color:#e6edf3; }}
  .card,.alert {{ background:#161b22; border-color:#30363d; }}
  .card h3, .card-top, .meta, .foot {{ color:#c9d1d9; }}
}}
</style>
</head>
<body>
<header>
  <h1>实验数据台账</h1>
  <div class="sub">{html.escape(str(ctx.root))} · 生成于 {human_time(now_iso())} · {socket.gethostname()}</div>
  <div class="stats">
    <div class="stat"><b>{len(recs)}</b><span>实验记录</span></div>
    <div class="stat"><b>{total_files}</b><span>登记文件</span></div>
    <div class="stat"><b>{human_size(total_bytes)}</b><span>数据总量</span></div>
    <div class="stat"><b>{len(missing)}</b><span>丢失文件</span></div>
  </div>
</header>
<div class="health {health_cls}">{html.escape(health_txt)}</div>
<main>
  {missing_html}
  {targets_html}
  <div class="toolbar">
    <input type="search" id="q" placeholder="搜索：编号 / 标题 / 器件 / 批次 / 标签 …">
  </div>
  <div class="grid" id="grid">{''.join(cards) if cards else '<p>还没有实验记录。</p>'}</div>
</main>
<footer>generated by expvault · 本页为静态快照，数据以 数据/ 目录与 meta.json 为准</footer>
<script>
const q = document.getElementById('q');
const cards = [...document.querySelectorAll('.card')];
q.addEventListener('input', () => {{
  const v = q.value.trim().toLowerCase();
  let n = 0;
  cards.forEach(c => {{
    const hit = !v || c.dataset.search.includes(v);
    c.style.display = hit ? '' : 'none';
    if (hit) n++;
  }});
}});
</script>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8", newline="\n")
    return path


# --------------------------------------------------------------------------
# 统计
# --------------------------------------------------------------------------
def stats(ctx: Ctx) -> str:
    recs = ctx.db.list_experiments(include_archived=True)
    files = ctx.db.all_files(include_missing=False)
    lines = [f"实验总数    {len(recs)}", f"登记文件    {len(files)}",
             f"数据总量    {human_size(sum(r['total_bytes'] for r in recs))}", ""]

    by_status = _group(recs, "status")
    rows = [[STATUS_LABEL.get(k, k), str(v[0]), str(v[1]), human_size(v[2])]
            for k, v in by_status.items()]
    lines += ["【按状态】", table(rows, ["状态", "实验数", "文件", "占用"],
                                ["left", "right", "right", "right"]), ""]

    by_project = _group(recs, "project")
    rows = [[truncate(k, 24), str(v[0]), str(v[1]), human_size(v[2])]
            for k, v in by_project.items()]
    lines += ["【按课题】", table(rows, ["课题", "实验数", "文件", "占用"],
                                ["left", "right", "right", "right"]), ""]

    by_month = _group(recs, lambda r: r["date"][:7])
    rows = [[k, str(v[0]), human_size(v[2])] for k, v in by_month.items()]
    lines += ["【按月】", table(rows, ["月份", "实验数", "新增数据"], ["left", "right", "right"]), ""]

    by_role = {}
    for f in files:
        cur = by_role.setdefault(f["role"], [0, 0])
        cur[0] += 1
        cur[1] += f["size"]
    rows = [[k, str(v[0]), human_size(v[1])] for k, v in sorted(by_role.items())]
    lines += ["【按文件类型】", table(rows, ["角色", "文件数", "占用"],
                                   ["left", "right", "right"]), ""]

    top = sorted(recs, key=lambda r: r["total_bytes"], reverse=True)[:10]
    rows = [[r["id"], truncate(r["title"], 34), human_size(r["total_bytes"])] for r in top]
    lines += ["【最占空间的实验】", table(rows, ["编号", "标题", "占用"],
                                       ["left", "left", "right"])]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 体检
# --------------------------------------------------------------------------
def doctor(ctx: Ctx) -> tuple[bool, list[str]]:
    lines: list[str] = []
    problems: list[str] = []
    ok = True

    def check(cond: bool, good: str, bad, *, fatal: bool = False) -> None:
        nonlocal ok
        if cond:
            lines.append(f"✅ {good}")
        else:
            msg = bad() if callable(bad) else bad
            lines.append(f"❌ {msg}")
            problems.append(msg)
            ok = False
            if fatal:
                raise SystemExit(1)

    # 1. 目录
    for p, label in (
        (ctx.data_dir, "数据目录"), (ctx.report_dir, "报告目录"),
        (ctx.db_path.parent, "索引目录"),
    ):
        check(p.exists(), f"{label}存在：{p}", f"{label}缺失：{p}")
    if ctx.backup_dir.exists():
        lines.append(f"✅ 备份目录存在：{ctx.backup_dir}")
    else:
        lines.append(f"⚠️  备份目录还没有（第一次 backup 时会自动创建）")

    # 2. 数据库
    good, msg = ctx.db.integrity_ok()
    check(good, f"索引数据库完整（{ctx.db.count('experiments')} 个实验）",
          f"索引数据库损坏：{msg}（可运行 expvault rebuild 重建）")

    # 3. 实验目录 vs 索引
    metas = []
    from .experiment import find_meta_files

    metas = find_meta_files(ctx)
    db_ids = {r["id"] for r in ctx.db.all_experiments()}
    disk_ids = set()
    for mp in metas:
        meta = load_meta(mp.parent) or {}
        if meta.get("id"):
            disk_ids.add(meta["id"])
    check(not (db_ids - disk_ids),
          "索引中的实验在磁盘上都能找到",
          lambda: f"{len(db_ids - disk_ids)} 个实验目录丢失："
                  f"{', '.join(sorted(db_ids - disk_ids)[:5])}")
    if disk_ids - db_ids:
        lines.append(f"⚠️  {len(disk_ids - db_ids)} 个实验目录未登记（运行 expvault sync）")

    # 4. 文件状态
    missing = ctx.db.missing_files()
    check(not missing, "没有丢失的文件",
          lambda: f"{len(missing)} 个文件丢失（例如："
                  f"{missing[0]['exp_id']}/{missing[0]['relpath']}）")

    # 5. 备份新鲜度（数据库没有记录时，退回到看备份盘上的快照时间）
    last = ctx.db.last_backup()
    warn_h = float(ctx.cfg["backup"].get("warn_unbacked_hours", 24))
    last_ts = last["ts"] if last else ""
    if not last_ts:
        for t in ctx.cfg["backup"]["targets"]:
            s = bk.target_summary(ctx.resolve_target_path(t))
            if s["latest_at"] and s["latest_at"] > last_ts:
                last_ts = s["latest_at"]
    if last_ts:
        age_h = (datetime.now().astimezone() - datetime.fromisoformat(last_ts)).total_seconds() / 3600
        check(age_h < warn_h,
              f"最近备份 {human_age(last_ts)}",
              f"已经 {human_age(last_ts)} 没有备份，建议立即运行 expvault backup")
    else:
        check(False, "", "从未做过备份！立即运行 expvault backup")

    # 6. 备份目标
    enabled = [t for t in ctx.cfg["backup"]["targets"] if t.get("enabled")]
    check(bool(enabled), f"{len(enabled)} 个备份目标已启用", "没有启用任何备份目标")
    for t in enabled:
        p = ctx.resolve_target_path(t)
        s = bk.target_summary(p)
        if s["exists"] and s["snapshots"]:
            lines.append(f"✅ 目标「{t['name']}」：{s['snapshots']} 个快照，{human_size(s['bytes'])}，"
                         f"最近 {human_age(s['latest_at'])}")
        elif s["exists"]:
            lines.append(f"⚠️  目标「{t['name']}」存在但还没有快照")
        elif bk.path_available(p):
            lines.append(f"⚠️  目标「{t['name']}」还没有快照（首次备份时自动创建）")
        else:
            lines.append(f"⚠️  目标「{t['name']}」当前不可用（未插盘？）：{p}")

    # 7. 磁盘空间
    try:
        usage = shutil.disk_usage(str(ctx.root))
        free_gb = usage.free / 1024**3
        check(free_gb > 2, f"数据盘剩余 {free_gb:.1f} GB", f"数据盘仅剩 {free_gb:.1f} GB，空间不足")
    except Exception:
        pass

    # 8. 路径中的非 ASCII 字符（仪器软件兼容性）
    bad_dirs = []
    for mp in metas:
        folder = mp.parent
        nm = folder.name
        if any(ord(c) > 127 for c in nm):
            bad_dirs.append(nm)
    if bad_dirs:
        lines.append(f"⚠️  {len(bad_dirs)} 个实验目录名含非 ASCII 字符，"
                     f"部分仪器软件/Origin 可能读不了：{', '.join(bad_dirs[:3])}")

    # 9. 未登记的新文件
    unregistered = 0
    for rec in ctx.db.all_experiments():
        folder = exp_dir(ctx, rec)
        if not folder.exists():
            continue
        known = {f["relpath"] for f in ctx.db.files_of(rec["id"], include_missing=False)}
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                rel = posix(os.path.relpath(Path(dirpath) / fn, folder))
                if rel not in known:
                    unregistered += 1
    if unregistered:
        lines.append(f"⚠️  {unregistered} 个文件尚未登记入索引（运行 expvault sync）")

    lines.append("")
    lines.append("结论：" + ("一切正常 ✅" if ok else "发现需要处理的问题 ❌"))
    return ok, lines
