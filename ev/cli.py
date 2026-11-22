"""命令行入口。"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import backup as bk
from . import config
from . import experiment as ex
from . import issue as iss
from . import report
from .ui import Console
from .util import (
    confirm_delete,
    disp_width,
    ensure_dir,
    human_size,
    human_time,
    now_iso,
    pad,
    setup_stdout,
    table,
    truncate,
)

VERSION = "1.1.0"


# --------------------------------------------------------------------------
# 参数解析
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="expvault",
        description="实验数据管理系统 —— 让每一次实验都有编号、有记录、有副本。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
常用流程（推荐）
  expvault init                         初始化数据仓库，只需一次
  expvault new "器件A 转移特性测试" -c IV    建一次实验
  expvault enter                        进入工作区，之后只敲短命令：
                                          add / ls / note / scan / backup / exit
  expvault nas --help                   连接已挂载的 NAS，主机编辑 / 其他电脑只读
  expvault backup                       一键增量备份到所有已启用目标
  expvault doctor                       体检：有没有丢文件、多久没备份

不想进工作区时
  expvault use E20261018-001            设定当前实验
  expvault put "D:\\Keithley\\*.csv"     导入到当前实验
  expvault scan                         登记直接拖进目录的文件
""",
    )
    p.add_argument("--root", help="数据仓库根目录（默认自动向上查找 config.json）")
    p.add_argument("--quiet", "-q", action="store_true", help="少打印")
    p.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    p.add_argument("--no-color", action="store_true", help="关闭颜色（也可设 NO_COLOR=1）")
    p.add_argument("--version", action="version", version=f"expvault {VERSION}")

    sub = p.add_subparsers(dest="cmd", metavar="命令")

    s = sub.add_parser("gui", help="打开桌面导入插件（文件 / U 盘 / 手机扫码，支持文件标签）")
    s.add_argument("exp", nargs="?", default="", help="预选实验编号，默认当前实验")

    s = sub.add_parser("web", help="启动网页实验管理（实验、步骤、文件标签、校验和备份）")
    s.add_argument("--host", default="127.0.0.1", help="默认仅本机；手机访问可指定可互通的局域网 IPv4 地址")
    s.add_argument("--port", type=int, default=8765, help="服务端口，0 表示自动分配")
    s.add_argument("--no-open", action="store_true", help="不自动打开浏览器")

    sub.add_parser("mcp", help="启动标准 MCP (Model Context Protocol) stdio AI 接口服务")

    s = sub.add_parser("export-conditions", help="导出单个实验的工艺条件表格（Excel / CSV / Markdown）")
    s.add_argument("exp", help="实验编号（或前缀）")
    s.add_argument("--format", choices=("xlsx", "csv", "md"), default="xlsx", help="导出格式（默认 xlsx）")
    s.add_argument("-o", "--output", default="", help="输出文件路径（默认写入报告目录）")


    # init
    s = sub.add_parser("init", help="初始化数据仓库")
    s.add_argument("--operator", default="", help="你的名字（默认操作人）")
    s.add_argument("--project", default="", help="默认课题名")
    s.add_argument("path", nargs="?", default=".", help="仓库位置，默认当前目录")

    # new
    s = sub.add_parser("new", help="新建一次实验记录")
    s.add_argument("title", nargs="?", help="实验标题")
    s.add_argument("-d", "--date", default="", help="实验日期 YYYY-MM-DD，默认今天")
    s.add_argument("-p", "--project", default="", help="所属课题")
    s.add_argument("-c", "--category", default="", help="类别，如 IV/CV/XRD/SEM/工艺")
    s.add_argument("-t", "--tag", action="append", default=[], help="标签，可重复")
    s.add_argument("--device", default="", help="器件/样品编号")
    s.add_argument("--batch", default="", help="工艺批次")
    s.add_argument("--operator", default="", help="操作人")
    s.add_argument("--slug", default="", help="目录名英文短名，默认由标题生成")
    s.add_argument("--param", action="append", default=[], help="工艺参数 k=v，可重复")
    s.add_argument("--purpose", default="", help="实验目的")
    s.add_argument("--conditions", default="", help="测试条件")
    s.add_argument("-i", "--interactive", action="store_true", help="交互式逐项填写")

    # add
    s = sub.add_parser("add", help="把文件/文件夹加入某个实验")
    s.add_argument("exp", help="实验编号（可只写前缀）")
    s.add_argument("paths", nargs="+", help="要加入的文件或文件夹")
    s.add_argument("-r", "--role", default="", choices=["raw", "processed", "figure", "note", "other"],
                   help="放进哪个子目录")
    s.add_argument("--move", action="store_true", help="移动而非复制（省空间，慎用）")
    s.add_argument("--link", action="store_true",
                   help="只建链接不复制文件（优先硬链接，失败退回复制）")
    s.add_argument("--link-hard", action="store_true", help="只建硬链接（同盘、不占额外空间）")
    s.add_argument("--link-soft", action="store_true", help="只建符号链接（可跨盘，源移走会断）")

    # sync
    s = sub.add_parser("sync", help="扫描磁盘，登记新文件、更新变更、标记丢失")
    s.add_argument("--rehash", action="store_true", help="全部重新计算哈希（慢但最准）")

    # list
    s = sub.add_parser("list", aliases=["ls"], help="列出实验")
    s.add_argument("-p", "--project", default="", help="按课题过滤")
    s.add_argument("-s", "--status", default="", help="按状态过滤")
    s.add_argument("-t", "--tag", default="", help="按标签过滤")
    s.add_argument("--since", default="", help="起始日期 YYYY-MM-DD")
    s.add_argument("--until", default="", help="结束日期 YYYY-MM-DD")
    s.add_argument("-g", "--grep", default="", help="关键词")
    s.add_argument("--all", action="store_true", help="包含已归档")
    s.add_argument("-n", "--limit", type=int, default=50)

    # ids
    s = sub.add_parser("ids", aliases=["id"], help="只列出实验编号（方便复制/脚本处理）")
    s.add_argument("-p", "--project", default="", help="按课题过滤")
    s.add_argument("-s", "--status", default="", help="按状态过滤")
    s.add_argument("-t", "--tag", default="", help="按标签过滤")
    s.add_argument("--since", default="", help="起始日期 YYYY-MM-DD")
    s.add_argument("--until", default="", help="结束日期 YYYY-MM-DD")
    s.add_argument("-g", "--grep", default="", help="关键词")
    s.add_argument("--all", action="store_true", help="包含已归档")
    s.add_argument("-l", "--long", action="store_true", help="同时输出日期和标题（Tab 分隔）")
    s.add_argument("-n", "--limit", type=int, default=0, help="只取前 N 个，0 表示全部")

    # show
    s = sub.add_parser("show", help="查看实验详情")
    s.add_argument("exp")

    # edit
    s = sub.add_parser("edit", help="修改实验元数据")
    s.add_argument("exp")
    s.add_argument("--title")
    s.add_argument("--status", choices=["planned", "running", "done", "aborted"])
    s.add_argument("--device")
    s.add_argument("--batch")
    s.add_argument("--category")
    s.add_argument("--project")
    s.add_argument("--tags", help="整体替换标签，逗号分隔")
    s.add_argument("--purpose")
    s.add_argument("--conditions")
    s.add_argument("--results")
    s.add_argument("--conclusion")
    s.add_argument("--notes")
    s.add_argument("--param", action="append", default=[], help="新增/覆盖参数 k=v")
    s.add_argument("--doc", action="store_true", help="同时重新生成 README.md")

    # note
    s = sub.add_parser("note", help="给实验追加一条带时间戳的记录")
    s.add_argument("exp")
    s.add_argument("text", nargs="+")

    # search
    s = sub.add_parser("search", help="全局搜索（元数据 + 文件名）")
    s.add_argument("keyword")

    # verify
    s = sub.add_parser("verify", help="校验文件完整性（重新计算哈希）")
    s.add_argument("exp", nargs="?", default="", help="只校验某个实验，默认全部")
    s.add_argument("--quick", action="store_true", help="只查缺失，不重算哈希")

    # backup
    s = sub.add_parser("backup", help="增量快照备份")
    s.add_argument("--to", default="", help="直接指定备份路径（临时目标）")
    s.add_argument("--target", default="", help="只备份到指定名字的目标")
    s.add_argument("--rehash", action="store_true", help="全部重算哈希")
    s.add_argument("--prune", action="store_true", help="备份后清理旧快照")
    s.add_argument("--dry-run", action="store_true", help="只看会复制多少，不写入")
    s.add_argument("-v", "--verbose", action="store_true")

    # snapshots
    s = sub.add_parser("snapshots", help="列出备份快照")
    s.add_argument("--target", default="")
    s.add_argument("-n", "--limit", type=int, default=20)

    # restore
    s = sub.add_parser("restore", help="从快照恢复数据")
    s.add_argument("snapshot", nargs="?", default="latest", help="快照 ID，默认最新")
    s.add_argument("--target", default="")
    s.add_argument("--to", default="", help="恢复到哪个目录，默认 恢复/<快照ID>")
    s.add_argument("--only", default="", help="只恢复路径包含该子串的文件")
    s.add_argument("--overwrite", action="store_true")
    s.add_argument("--dry-run", action="store_true")

    # prune
    s = sub.add_parser("prune", help="清理旧快照与无引用数据块")
    s.add_argument("--target", default="")
    s.add_argument("--keep", type=int, default=None, help="保留最近 N 个快照")
    s.add_argument("--keep-monthly", type=int, default=None, help="每月另保留 N 个")
    s.add_argument("--dry-run", action="store_true")

    # targets
    s = sub.add_parser("targets", help="查看/管理备份目标")
    s.add_argument("--enable", default="")
    s.add_argument("--disable", default="")
    s.add_argument("--add", default="", help="新增目标，格式 名称=路径")
    s.add_argument("--remove", default="", help="删除目标")
    s.add_argument("--verify", default="", help="深度校验某个目标")

    # export
    s = sub.add_parser("export", help="导出 Excel 台账与 HTML 看板")
    s.add_argument("--xlsx", action="store_true")
    s.add_argument("--html", action="store_true")
    s.add_argument("--csv", action="store_true")
    s.add_argument("--out", default="", help="输出目录，默认 报告/")

    # stats
    sub.add_parser("stats", help="统计概览")

    # doctor
    sub.add_parser("doctor", help="体检：丢文件、备份新鲜度、磁盘空间")

    # rebuild
    sub.add_parser("rebuild", help="从磁盘 meta.json 重建索引（数据库损坏时用）")

    # adopt
    s = sub.add_parser("adopt", help="把已有文件夹纳入管理")
    s.add_argument("path")
    s.add_argument("--move", action="store_true", help="移动原目录（同盘瞬间完成）")
    s.add_argument("--title", default="")
    s.add_argument("-c", "--category", default="")
    s.add_argument("-p", "--project", default="")
    s.add_argument("--device", default="")
    s.add_argument("-t", "--tag", action="append", default=[])

    # archive
    s = sub.add_parser("archive", help="归档实验")
    s.add_argument("exp")
    s.add_argument("--undo", action="store_true", help="取消归档")

    # open
    s = sub.add_parser("open", help="在资源管理器中打开实验目录")
    s.add_argument("exp", nargs="?", default="")

    # issue
    s = sub.add_parser("issue", help="记录一个设备/工艺问题")
    s.add_argument("text")
    s.add_argument("--type", default="设备", choices=iss.TYPES)
    s.add_argument("--severity", default="中", choices=iss.SEVERITY)
    s.add_argument("--target", default="", help="设备名或批次号")
    s.add_argument("--exp", default="", help="关联实验")
    s.add_argument("--action", default="", help="已采取的措施")

    # issues
    s = sub.add_parser("issues", help="查看问题记录")
    s.add_argument("--open", action="store_true", help="只看未解决")
    s.add_argument("-n", "--limit", type=int, default=30)

    # doc
    s = sub.add_parser("doc", help="重新生成实验 README.md")
    s.add_argument("exp", nargs="?", default="")

    # clean
    s = sub.add_parser("clean", help="清空所有实验数据（示例数据/重新开始时用）")
    s.add_argument("--yes", action="store_true", help="跳过确认")
    s.add_argument("--keep-reports", action="store_true", help="保留 报告/ 目录")

    # enter
    s = sub.add_parser("enter", aliases=["shell", "work"],
                       help="进入实验工作区，之后只敲短命令（add/ls/note/scan…）")
    s.add_argument("exp", nargs="?", default="", help="实验编号，省略则询问/沿用上次")
    s.add_argument("--no-cd", action="store_true", help="不把工作目录切到实验目录")

    # use
    s = sub.add_parser("use", help="设定当前实验（不带编号会弹出方向键列表）")
    s.add_argument("exp", nargs="?", default="", help="实验编号；省略则用方向键选")

    # put
    s = sub.add_parser("put", help="把文件导入当前实验（不用写实验编号）")
    s.add_argument("paths", nargs="+", help="文件或文件夹，支持通配符")
    s.add_argument("-r", "--role", default="", choices=["raw", "processed", "figure", "note", "other"],
                   help="放进哪个子目录，默认 raw")
    s.add_argument("--move", action="store_true")
    s.add_argument("--link", action="store_true",
                   help="只建链接不复制文件（优先硬链接，失败退回复制）")
    s.add_argument("--link-hard", action="store_true", help="只建硬链接（同盘、不占额外空间）")
    s.add_argument("--link-soft", action="store_true", help="只建符号链接（可跨盘，源移走会断）")

    # scan
    s = sub.add_parser("scan", help="扫描当前实验目录，登记直接拖进去的文件")
    s.add_argument("--rehash", action="store_true")

    # current
    sub.add_parser("current", help="显示当前实验")

    # rm
    s = sub.add_parser("rm", aliases=["del"], help="从实验里删除文件（默认连磁盘一起删）")
    s.add_argument("exp", help="实验编号，写 - 表示当前实验")
    s.add_argument("paths", nargs="*", help="实验目录内的相对路径")
    s.add_argument("--missing", action="store_true",
                   help="清掉本实验所有「已丢失」的登记（文件已经不在磁盘上了）")
    s.add_argument("-r", "--recursive", action="store_true", help="允许删除整个目录")
    s.add_argument("--keep-disk", action="store_true", help="只从登记表删除，磁盘文件保留")
    s.add_argument("-y", "--yes", action="store_true", help="跳过两遍确认（脚本用，慎用）")

    return p


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
def fail(msg: str, code: int = 2):
    print(f"❌ {msg}", file=sys.stderr)
    raise SystemExit(code)


def parse_kv(items: list[str]) -> dict:
    out = {}
    for item in items:
        if "=" not in item:
            fail(f"参数格式应为 k=v：{item}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def get_rec(ctx, key: str) -> dict:
    rec = ctx.db.find_experiment(key)
    if not rec:
        fail(f"找不到实验：{key}。用 expvault list 看看有哪些。")
    return rec


def target_or_default(ctx, name: str) -> Path:
    if name:
        t = ctx.target_by_name(name)
        if not t:
            fail(f"没有名为「{name}」的备份目标，用 expvault targets 查看")
        return ctx.resolve_target_path(t)
    enabled = [t for t in ctx.cfg["backup"]["targets"] if t.get("enabled")]
    if not enabled:
        fail("没有启用任何备份目标。用 expvault targets --enable 名称 启用，或 backup --to 路径")
    return ctx.resolve_target_path(enabled[0])


def interactive_new(ctx) -> dict:
    print("─" * 60)
    print("新建实验记录（直接回车用括号里的默认值）")
    print("─" * 60)

    def ask(label: str, default: str = "") -> str:
        hint = f" [{default}]" if default else ""
        try:
            v = input(f"{label}{hint}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(1)
        return v or default

    title = ask("实验标题（必填）")
    while not title:
        title = ask("实验标题（必填，不能为空）")
    date_str = ask("日期", ex.today())
    project = ask("所属课题", ctx.cfg.get("project", ""))
    category = ask("类别（IV/CV/XRD/SEM/工艺/…）", "")
    device = ask("器件/样品编号", "")
    batch = ask("工艺批次", "")
    tags = ask("标签（逗号分隔）", "")
    print("工艺参数：一行一个 k=v，空行结束")
    params = {}
    while True:
        line = ask("  参数", "")
        if not line:
            break
        if "=" in line:
            k, v = line.split("=", 1)
            params[k.strip()] = v.strip()
    purpose = ask("实验目的", "")
    conditions = ask("测试条件", "")
    return {
        "title": title, "date_str": date_str, "project": project,
        "category": category, "device": device, "batch": batch,
        "tags": [t.strip() for t in tags.replace("，", ",").split(",") if t.strip()],
        "params": params, "purpose": purpose, "conditions": conditions,
    }


# --------------------------------------------------------------------------
# 命令实现
# --------------------------------------------------------------------------
def cmd_init(args, con: Console) -> int:
    root = Path(args.path).expanduser().resolve()
    existed = (root / config.CONFIG_NAME).exists()
    ctx = config.init_repo(root, operator=args.operator, project=args.project)
    ctx.con = con
    if args.json:
        con.emit_json({"root": str(root), "created": not existed})
        return 0

    t = con.t
    con.banner("让每一次实验都有编号、有记录、有副本", f"v{VERSION}")
    con.ok(f"{'配置已更新' if existed else '数据仓库已就绪'}  {t.dim(str(root))}")
    con.blank()
    con.panel([
        f"{t.dim('数据')}   {ctx.data_dir}",
        f"{t.dim('备份')}   {ctx.backup_dir}",
        f"{t.dim('索引')}   {ctx.db_path}",
    ], min_width=52)
    con.header("下一步")

    def step(cmd: str, desc: str, width: int = 40) -> None:
        if disp_width(cmd) <= width:
            con.step(pad(cmd, width) + t.dim(desc))
        else:
            con.step(cmd)
            con.tip(desc)

    step('expvault new "第一次实验" -i', "建一条实验记录")
    step("expvault enter", "进入工作区，之后只敲 add / note / backup")
    step("expvault backup", "立刻做第一次快照")
    step("expvault targets --add 移动硬盘=E:\\Backup", "加一块离线副本")
    con.blank()
    con.tip("想省事就让系统每天自动备份：powershell -File tools\\install_task.ps1")
    con.blank()
    return 0


def cmd_new(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    ctx.con = con if not con.quiet else Console(quiet=True)
    if args.interactive or not args.title:
        info = interactive_new(ctx)
        if args.title:
            info["title"] = args.title
        for key, val in (("project", args.project), ("category", args.category),
                         ("device", args.device), ("batch", args.batch)):
            if val:
                info[key] = val
        if args.date:
            info["date_str"] = args.date
        if args.tag:
            info["tags"] = args.tag
        if args.param:
            info["params"] = parse_kv(args.param)
        if args.purpose:
            info["purpose"] = args.purpose
        if args.conditions:
            info["conditions"] = args.conditions
    else:
        info = dict(title=args.title, date_str=args.date or ex.today(),
                    project=args.project, category=args.category, device=args.device,
                    batch=args.batch, tags=args.tag, params=parse_kv(args.param),
                    purpose=args.purpose, conditions=args.conditions)

    rec = ex.create(
        ctx, info["title"], date_str=info.get("date_str") or ex.today(),
        project=info.get("project", ""), operator=args.operator,
        device=info.get("device", ""), batch=info.get("batch", ""),
        category=info.get("category", ""), tags=info.get("tags", []),
        params=info.get("params", {}), slug=args.slug,
        purpose=info.get("purpose", ""), conditions=info.get("conditions", ""),
    )
    if con.json:
        con.emit_json(rec)
        return 0
    from . import session

    session.set_current(ctx, rec["id"])     # 新建后自动成为当前实验，put 直接进这里
    t = con.t
    con.blank()
    con.ok(f"已创建  {t.accent(rec['id'])}  {t.bold(rec['title'])}")
    con.detail(f"目录  {t.dim(str(ex.exp_dir(ctx, rec)))}")
    if rec.get("params"):
        con.detail("参数  " + "  ".join(f"{k}={v}" for k, v in rec["params"].items()))
    con.blank()
    con.tip("next: expvault enter   进去以后直接 add / note / backup")
    con.blank()
    return 0


def _link_mode(args) -> str:
    """把三个 --link 开关收敛成一个模式字符串。"""
    if getattr(args, "link_hard", False):
        return "hard"
    if getattr(args, "link_soft", False):
        return "soft"
    if getattr(args, "link", False):
        return "auto"
    return ""


def cmd_add(args, con: Console) -> int:
    import glob

    ctx = config.load(Path(args.root)) if args.root else config.load()
    rec = get_rec(ctx, args.exp)
    # Windows 的 cmd 不会展开通配符，这里替用户展开
    expanded: list[Path] = []
    for p in args.paths:
        if not Path(p).exists() and any(ch in p for ch in "*?["):
            hits = sorted(glob.glob(p))
            if hits:
                expanded.extend(Path(h) for h in hits)
                continue
        expanded.append(Path(p))
    link = _link_mode(args)
    res = ex.add_files(ctx, rec, expanded, role=args.role, move=args.move, link=link)
    for line in res["copied"]:
        con.info(f"  {con.t.ok('+')} {line}")
    for line in res["skipped"]:
        con.info(f"  {con.t.dim('·')} 跳过 {line}")
    for line in res.get("already", []):
        con.info(f"  {con.t.dim('·')} 已在实验目录（直接 expvault scan）：{line}")
    for line in res["errors"]:
        con.warn(line)
    if link:
        _report_links(con, res["link_stats"])
    if not res["copied"]:
        if res.get("already"):
            con.ok("已登记，无需再导入")
            return 0
        if res["errors"]:
            con.warn("没有文件被加入，原因见上面的错误")
        else:
            con.warn("没有文件被加入，检查路径是否正确")
        return 1
    con.ok(f"{len(res['copied'])} 项已加入 {rec['id']}，"
           f"共 {ctx.db.get_experiment(rec['id'])['file_count']} 个文件")
    return 0 if not res["errors"] else 1


def _report_links(con: Console, stats: dict) -> None:
    hard = stats.get("hard", 0)
    soft = stats.get("soft", 0)
    copy = stats.get("copy", 0)
    parts = []
    if hard:
        parts.append(f"硬链接 {hard}")
    if soft:
        parts.append(f"符号链接 {soft}")
    if copy:
        parts.append(f"退回复制 {copy}")
    if not parts:
        return
    con.tip("链接模式：" + "，".join(parts))
    if hard:
        con.tip("硬链接与源文件共享同一份数据，改动会互相影响；同一磁盘内不额外占用空间")
    if soft:
        con.tip("符号链接只是指针，源文件被移走/删除就会失效（verify 能查出来）")


def cmd_sync(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    con.info("扫描中…")
    stat = ex.sync(ctx, rehash=args.rehash)
    if con.json:
        con.emit_json(stat)
        return 0
    con.ok(f"扫描 {stat['experiments']} 个实验：新增 {stat['new_files']}，"
           f"变更 {stat['changed']}，丢失 {stat['missing']}，"
           f"总计 {human_size(stat['bytes'])}")
    if stat.get("orphans"):
        lines = [f"⚠️  {stat['orphans']} 个文件放在 数据\\ 里但不属于任何实验（不会被登记）："]
        lines += [f"     {s}" for s in stat.get("orphan_samples", [])]
        lines.append("     → 用 expvault adopt <目录> 纳入管理，或把它们移进某个实验目录")
        print("\n".join(lines), file=sys.stderr, flush=True)
    if stat["missing"]:
        con.warn("有文件丢失，运行 expvault doctor 查看详情")
    return 0


def cmd_list(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    recs = ctx.db.list_experiments(
        project=args.project or None, status=args.status or None, tag=args.tag or None,
        since=args.since or None, until=args.until or None, grep=args.grep or None,
        include_archived=args.all, limit=args.limit,
    )
    if con.json:
        con.emit_json(recs)
        return 0
    if not recs:
        con.warn("没有匹配的实验")
        con.tip("expvault new \"标题\" 建一条")
        return 0
    con.blank()
    con.info(ex.format_list(ctx, recs))
    con.blank()
    return 0


def cmd_ids(args, con: Console) -> int:
    """只输出实验编号，一行一个，方便复制或喂给脚本。"""
    ctx = config.load(Path(args.root)) if args.root else config.load()
    recs = ctx.db.list_experiments(
        project=args.project or None, status=args.status or None, tag=args.tag or None,
        since=args.since or None, until=args.until or None, grep=args.grep or None,
        include_archived=args.all, limit=args.limit or None,
    )
    if con.json:
        if args.long:
            con.emit_json([{"id": r["id"], "date": r["date"], "title": r["title"],
                            "status": r["status"]} for r in recs])
        else:
            con.emit_json([r["id"] for r in recs])
        return 0
    if not recs:
        con.warn("没有匹配的实验")
        return 1
    for r in recs:
        if args.long:
            print(f"{r['id']}\t{r['date']}\t{r['title']}")
        else:
            print(r["id"])
    return 0


def cmd_show(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    rec = get_rec(ctx, args.exp)
    if con.json:
        rec = dict(rec)
        rec["files"] = ctx.db.files_of(rec["id"])
        con.emit_json(rec)
        return 0
    print(ex.format_show(ctx, rec))
    return 0


def cmd_edit(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    rec = get_rec(ctx, args.exp)
    changes = {}
    for f in ("title", "status", "device", "batch", "category", "project",
              "purpose", "conditions", "results", "conclusion", "notes"):
        v = getattr(args, f, None)
        if v is not None:
            changes[f] = v
    if args.tags is not None:
        changes["tags"] = args.tags
    if args.param:
        params = dict(rec.get("params") or {})
        params.update(parse_kv(args.param))
        changes["params"] = params
    if not changes:
        con.warn("没有要修改的内容")
        return 0
    ex.update(ctx, rec, changes)
    if args.doc:
        meta = ex.load_meta(ex.exp_dir(ctx, rec)) or {}
        ex.write_readme(ctx, meta, ctx.db.files_of(rec["id"], include_missing=False))
    con.ok(f"已更新 {rec['id']}：{', '.join(changes)}")
    return 0


def cmd_note(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    rec = get_rec(ctx, args.exp)
    text = " ".join(args.text)
    path = ex.append_note(ctx, rec, text)
    con.ok(f"已写入 {path}")
    return 0


def cmd_search(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    hits = ex.search(ctx, args.keyword)
    if con.json:
        con.emit_json(hits)
        return 0
    print(f"实验命中 {len(hits['experiments'])} 个，文件命中 {len(hits['files'])} 个\n")
    if hits["experiments"]:
        print(ex.format_list(ctx, hits["experiments"]))
    if hits["files"]:
        print()
        rows = [[h["exp_id"], truncate(h["relpath"], 56), human_size(h["size"]), "、".join(h.get("tags", []))]
                for h in hits["files"][:40]]
        print(table(rows, ["实验", "文件", "大小", "文件标签"], ["left", "left", "right", "left"]))
    return 0


def cmd_verify(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    t = con.t
    con.info(t.dim(f"  正在校验（{'快速模式' if args.quick else '逐文件重算 SHA256'}）…"))
    rep = ex.verify(ctx, rehash=not args.quick, exp_id=args.exp)
    bad = len(rep["modified"]) + len(rep["missing"]) + len(rep["unregistered"]) + len(rep["bad_meta"])
    if con.json:
        con.emit_json(rep)
        return 1 if bad else 0

    con.blank()
    con.ok(f"检查 {rep['checked']} 个文件  ·  {human_size(rep['bytes'])}")
    con.detail(f"正常 {rep['ok']}   内容变化 {len(rep['modified'])}   "
               f"丢失 {len(rep['missing'])}   未登记 {len(rep['unregistered'])}")
    if rep["bad_meta"]:
        con.detail(f"元数据异常 {len(rep['bad_meta'])}")

    if rep["modified"]:
        con.header("内容已变化", 40)
        con.tip("文件被改过，或磁盘静默损坏")
        for m in rep["modified"][:30]:
            con.info(f"  {t.accent(m['exp_id'])}/{m['relpath']}  "
                     f"{t.dim(m['registered'])} → {t.warn(m['actual'])}")
    if rep["missing"]:
        con.header("文件丢失", 40)
        for m in rep["missing"][:30]:
            con.info(f"  {t.accent(m['exp_id'])}/{m['relpath']}")
    if rep["unregistered"]:
        con.header("未登记的新文件", 40)
        con.tip("运行 expvault sync 补登记")
        for m in rep["unregistered"][:20]:
            con.info(f"  {t.accent(m['exp_id'])}/{m['relpath']}")
    if rep["bad_meta"]:
        con.header("元数据异常", 40)
        for m in rep["bad_meta"]:
            con.info(f"  {m}")

    con.blank()
    if not bad:
        con.ok("全部一致")
    else:
        con.warn("发现问题：尽快从备份恢复，或补登记")
    return 1 if bad else 0


def cmd_backup(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()

    targets: list[tuple[str, Path]] = []
    if args.to:
        targets.append((Path(args.to).name, Path(args.to).expanduser()))
    elif args.target:
        t = ctx.target_by_name(args.target)
        if not t:
            fail(f"没有名为「{args.target}」的目标")
        targets.append((t["name"], ctx.resolve_target_path(t)))
    else:
        for t in ctx.cfg["backup"]["targets"]:
            if not t.get("enabled"):
                continue
            p = ctx.resolve_target_path(t)
            targets.append((t["name"], p))
    if not targets:
        fail("没有可用的备份目标。用 expovault targets --add 名称=路径 添加")

    rc = 0
    results = []
    t = con.t
    for name, path in targets:
        if not bk.path_available(path):
            con.warn(f"目标「{name}」不可用（未插盘？）  {t.dim(str(path))}")
            rc = max(rc, 1)
            continue
        con.blank()
        con.info(f"  {t.brand('●')} {t.bold(name)}  {t.dim(str(path))}")
        try:
            r = bk.backup(ctx, path, name=name, rehash=args.rehash,
                          prune=args.prune, dry_run=args.dry_run, verbose=args.verbose)
        except Exception as e:
            con.error(f"备份失败：{e}")
            rc = 2
            continue
        results.append(r)
        st = r["stats"]
        con.detail(f"快照 {t.accent(r['snapshot'])}  ·  {st['files']} 个文件  ·  "
                   f"{human_size(st['bytes'])}")
        con.detail(f"新增 {human_size(st['new_bytes'])}"
                   f"（{st['new_objects']} 个数据块）  ·  "
                   f"去重省下 {human_size(r.get('bytes_skipped', 0))}")
        if st.get("errors"):
            con.warn(f"{len(st['errors'])} 个文件出错：{st['errors'][0]}")
            rc = 2
        if "prune" in r:
            pr = r["prune"]
            con.detail(f"清理 {len(pr['dropped'])} 个旧快照，释放 {human_size(pr['bytes_freed'])}")
    if con.json:
        con.emit_json(results)
        return rc
    con.blank()
    if rc == 0:
        con.ok("备份完成" if not args.dry_run else "预览完成（未写入任何数据）")
    else:
        con.warn("备份结束，但有目标失败或被跳过，请看上面的提示")
    return rc


def cmd_snapshots(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    path = target_or_default(ctx, args.target)
    if not path.exists():
        fail(f"目标不存在：{path}")
    snaps = bk.list_snapshots(path)[: args.limit]
    if con.json:
        con.emit_json([{k: v for k, v in s.items() if k != "files"} for s in snaps])
        return 0
    s = bk.target_summary(path)
    con.blank()
    con.ok(f"{s['name']}  ·  {s['snapshots']} 个快照  ·  {s['objects']} 个数据块  ·  "
           f"{human_size(s['bytes'])}")
    con.detail(con.t.dim(str(path)))
    con.blank()
    con.info(bk.format_snapshots(ctx, path, args.limit))
    con.blank()
    return 0


def cmd_restore(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    path = target_or_default(ctx, args.target)
    dest = Path(args.to) if args.to else ctx.root / "恢复" / args.snapshot
    if not args.dry_run:
        ensure_dir(dest)
        if any(dest.iterdir()) and not args.overwrite:
            fail(f"目标目录非空：{dest}。加 --overwrite 或换 --to")
    r = bk.restore(ctx, path, args.snapshot, dest, only=args.only,
                   overwrite=args.overwrite, dry_run=args.dry_run)
    if con.json:
        con.emit_json(r)
        return 0 if r.get("ok") else 1
    con.blank()
    con.ok(f"快照 {con.t.accent(r['snapshot'])} → {dest}")
    con.detail(f"恢复 {r['restored']} 个文件  ·  {human_size(r['bytes'])}  ·  "
               f"跳过 {r['skipped']} 个")
    if r["missing_objects"]:
        con.warn(f"{len(r['missing_objects'])} 个文件在备份盘上找不到数据块！")
        for m in r["missing_objects"][:10]:
            con.info(f"  {m}")
        return 1
    con.blank()
    if args.dry_run:
        con.ok("演练完成（未写入任何文件）")
        return 0
    con.ok("恢复完成")
    return 0


def cmd_prune(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    path = target_or_default(ctx, args.target)
    r = bk.prune_target(ctx, path, keep_recent=args.keep,
                        keep_monthly=args.keep_monthly, dry_run=args.dry_run)
    if con.json:
        con.emit_json(r)
        return 0
    con.blank()
    con.ok(f"保留 {r['kept']} 个快照，删除 {len(r['dropped'])} 个"
           + ("（预览，未真正删除）" if args.dry_run else ""))
    for d in r["dropped"]:
        con.detail(con.t.dim(d))
    con.detail(f"回收数据块 {r['objects_removed']} 个，释放 {human_size(r['bytes_freed'])}")
    return 0


def cmd_targets(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    changed = False
    if args.add:
        if "=" not in args.add:
            fail("格式：--add 名称=路径")
        name, path = args.add.split("=", 1)
        ctx.cfg["backup"]["targets"].append({"name": name.strip(), "path": path.strip(), "enabled": True})
        changed = True
    if args.remove:
        ctx.cfg["backup"]["targets"] = [
            t for t in ctx.cfg["backup"]["targets"] if t["name"] != args.remove
        ]
        changed = True
    for name, flag in ((args.enable, True), (args.disable, False)):
        if name:
            t = ctx.target_by_name(name)
            if not t:
                fail(f"没有名为「{name}」的目标")
            t["enabled"] = flag
            changed = True
    if changed:
        ctx.save_cfg()
        con.ok("配置已更新")

    if args.verify:
        t = ctx.target_by_name(args.verify)
        if not t:
            fail(f"没有名为「{args.verify}」的目标")
        path = ctx.resolve_target_path(t)
        con.info(con.t.dim("  正在逐块重算哈希…"))
        r = bk.verify_target(ctx, path, deep=True)
        con.blank()
        if r["missing"] or r["corrupt"]:
            con.warn(f"快照 {r['snapshot']}：{r['ok']}/{r['files']} 完好，"
                     f"缺失 {len(r['missing'])}，损坏 {len(r['corrupt'])}")
            return 1
        con.ok(f"快照 {r['snapshot']}：{r['ok']}/{r['files']} 个文件完好")
        return 0

    rows = []
    for t in ctx.cfg["backup"]["targets"]:
        p = ctx.resolve_target_path(t)
        s = bk.target_summary(p)
        state = "可用" if s["exists"] else "不可用"
        if s["exists"] and not s["snapshots"]:
            state = "空"
        rows.append([
            t["name"], "启用" if t.get("enabled") else "停用", state,
            str(s["snapshots"]), human_size(s["bytes"]),
            human_time(s["latest_at"]) if s["latest_at"] else "—",
            str(p),
        ])
    con.blank()
    con.info(table(rows, ["名称", "状态", "介质", "快照", "占用", "最近备份", "路径"],
                   ["left", "center", "center", "right", "right", "left", "left"]))
    con.blank()
    con.tip("加一块离线副本：expvault targets --add 移动硬盘=D:\\ExpVaultBackup")
    con.blank()
    return 0


def cmd_export(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    out = Path(args.out) if args.out else ctx.report_dir
    ensure_dir(out)
    any_flag = args.xlsx or args.html or args.csv
    do_xlsx = args.xlsx or not any_flag
    do_html = args.html or not any_flag
    do_csv = args.csv
    stamp = "实验台账"
    made = []
    if do_xlsx:
        p = report.export_xlsx(ctx, out / f"{stamp}.xlsx")
        made.append(p)
    if do_csv:
        p = report.export_csv(ctx, out / f"{stamp}.csv")
        made.append(p)
    if do_html:
        p = report.export_html(ctx, out / "实验看板.html")
        made.append(p)
    con.blank()
    con.ok(f"已导出 {len(made)} 个文件")
    for p in made:
        con.detail(str(p))
    con.blank()
    return 0


def cmd_stats(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    con.blank()
    con.info(report.stats(ctx))
    con.blank()
    return 0


def cmd_doctor(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    ok, lines = report.doctor(ctx)
    con.blank()
    for line in lines:
        if line.startswith("结论："):
            continue
        if line.strip():
            con.status_line(line)
    con.blank()
    if ok:
        con.ok("体检通过，一切正常")
    else:
        con.warn("发现问题，按上面提示处理；实在不行先 expvault backup")
    con.blank()
    return 0 if ok else 1


def cmd_rebuild(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    r = ex.rebuild_index(ctx)
    con.ok(f"索引已重建：{r['experiments']} 个实验")
    return 0


def cmd_adopt(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    src = Path(args.path).expanduser()
    if not src.exists():
        fail(f"路径不存在：{src}")
    rec = ex.adopt(ctx, src, move=args.move, title=args.title,
                   category=args.category, project=args.project,
                   device=args.device, tags=args.tag)
    con.ok(f"已纳入管理：{rec['id']} → {ex.exp_dir(ctx, rec)}")
    con.info(f"   {rec['file_count']} 个文件，{human_size(rec['total_bytes'])}")
    if not args.move:
        con.info("   原目录未被改动；确认无误后可自行删除")
    return 0


def cmd_archive(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    rec = get_rec(ctx, args.exp)
    ex.archive(ctx, rec, not args.undo)
    con.ok(("已归档 " if not args.undo else "已取消归档 ") + rec["id"])
    return 0


def cmd_open(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    if args.exp:
        rec = get_rec(ctx, args.exp)
        target = ex.exp_dir(ctx, rec)
    else:
        target = ctx.data_dir
    if hasattr(os, "startfile"):
        os.startfile(str(target))
    else:
        print(target)
    return 0


def cmd_issue(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    rec = iss.add_issue(ctx, args.text, type_=args.type, severity=args.severity,
                        target=args.target, exp_id=args.exp, action=args.action)
    con.ok(f"已记录问题（{rec['type']}/{rec['severity']}）")
    return 0


def cmd_issues(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    rows = iss.list_issues(ctx, unresolved_only=args.open, limit=args.limit)
    if con.json:
        con.emit_json(rows)
        return 0
    print(iss.format_issues(rows))
    return 0


def cmd_doc(args, con: Console) -> int:
    ctx = config.load(Path(args.root)) if args.root else config.load()
    if args.exp:
        recs = [get_rec(ctx, args.exp)]
    else:
        recs = ctx.db.all_experiments()
    for rec in recs:
        meta = ex.load_meta(ex.exp_dir(ctx, rec)) or {}
        ex.write_readme(ctx, meta, ctx.db.files_of(rec["id"], include_missing=False))
    con.ok(f"已重新生成 {len(recs)} 个 README.md")
    return 0


def cmd_clean(args, con: Console) -> int:
    import shutil

    ctx = config.load(Path(args.root)) if args.root else config.load()
    recs = ctx.db.all_experiments()
    if not recs:
        con.ok("本来就没有实验数据")
        return 0
    total = sum(r["total_bytes"] for r in recs)
    print(f"将删除 {len(recs)} 个实验、{human_size(total)} 数据：")
    for r in recs[:20]:
        print(f"  {r['id']}  {truncate(r['title'], 40)}")
    if len(recs) > 20:
        print(f"  …… 其余 {len(recs) - 20} 个")
    print("\n备份目录不会被删除（如需清理请用 expvault prune / 手动删除）。")
    if not args.yes:
        try:
            ans = input('确认删除请输入 DELETE：').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        if ans != "DELETE":
            print("已取消")
            return 1

    data_root = ctx.data_dir.resolve()
    removed = 0
    for r in recs:
        folder = (ctx.root / r["folder"]).resolve()
        # 安全护栏：只删 数据/ 之内的目录
        if folder == data_root or data_root not in folder.parents:
            con.warn(f"跳过可疑路径：{folder}")
            continue
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    for table in ("files", "experiments", "events", "backups"):
        ctx.db.conn.execute(f"DELETE FROM {table}")
    ctx.db.conn.commit()
    if not args.keep_reports and ctx.report_dir.exists():
        shutil.rmtree(ctx.report_dir, ignore_errors=True)
        ensure_dir(ctx.report_dir)
    recover = ctx.root / "恢复"
    if recover.exists():
        shutil.rmtree(recover, ignore_errors=True)
    for p in ctx.log_path.parent.glob("*.jsonl"):
        try:
            p.unlink()
        except OSError:
            pass
    con.ok(f"已删除 {removed} 个实验目录，索引与报告已清空")
    return 0


def pick_experiment(ctx, con: Console, current: dict | None = None) -> dict | None:
    """选择实验（方向键菜单，见 session.choose）。"""
    from . import session

    rec = session.choose(ctx, current=current)
    if rec is None and not ctx.db.list_experiments(limit=1):
        con.warn('还没有任何实验，先运行：expvault new "标题"')
    return rec



def cmd_enter(args, con: Console) -> int:
    from . import shell, session

    ctx = config.load(Path(args.root)) if args.root else config.load()
    if args.exp:
        rec = get_rec(ctx, args.exp)
    else:
        rec = session.resolve_current(ctx)
        rec = session.choose(ctx, current=rec)
        if rec is None:
            con.warn("已取消")
            return 1
    session.set_current(ctx, rec["id"])
    shell.run(ctx, con, rec, change_dir=not args.no_cd)
    return 0


def cmd_use(args, con: Console) -> int:
    from . import session

    ctx = config.load(Path(args.root)) if args.root else config.load()
    if args.exp:
        rec = get_rec(ctx, args.exp)
    else:
        rec = session.choose(ctx, current=session.resolve_current(ctx))
        if rec is None:
            con.warn("已取消")
            return 1
    session.set_current(ctx, rec["id"])
    con.blank()
    con.ok(f"当前实验  {con.t.accent(rec['id'])}  {con.t.bold(rec['title'])}")
    con.detail(con.t.dim(str(ex.exp_dir(ctx, rec))))
    con.tip("next: expvault enter   进去以后只敲 add / note / scan / backup")
    con.blank()
    return 0


def cmd_current(args, con: Console) -> int:
    from . import session

    ctx = config.load(Path(args.root)) if args.root else config.load()
    rec = session.resolve_current(ctx)
    if not rec:
        con.warn("还没有设定当前实验")
        con.tip("expvault enter（弹出列表）或 expvault use <编号>")
        return 1
    t = con.t
    con.info(f"  {t.bold(rec['title'])}")
    con.info(f"  {t.dim(rec['id'])}  ·  {rec['file_count']} 个文件  ·  "
             f"{human_size(rec['total_bytes'])}")
    con.info(f"  {t.dim(str(ex.exp_dir(ctx, rec)))}")
    return 0


def cmd_put(args, con: Console) -> int:
    import glob

    from . import session

    ctx = config.load(Path(args.root)) if args.root else config.load()
    rec = session.require_current(ctx)
    expanded: list[Path] = []
    for p in args.paths:
        if not Path(p).exists() and any(ch in p for ch in "*?["):
            hits = sorted(glob.glob(p))
            expanded.extend(Path(h) for h in hits)
        else:
            expanded.append(Path(p))
    if not expanded:
        fail("没有匹配到任何文件")
    link = _link_mode(args)
    res = ex.add_files(ctx, rec, expanded, role=args.role, move=args.move, link=link)
    for line in res["copied"]:
        con.info(f"  {con.t.ok('+')} {line}")
    for line in res["skipped"]:
        con.info(f"  {con.t.dim('·')} 跳过 {line}")
    for line in res.get("already", []):
        con.info(f"  {con.t.dim('·')} 已在实验目录（直接 expvault scan）：{line}")
    for line in res["errors"]:
        con.warn(line)
    if link:
        _report_links(con, res["link_stats"])
    if not res["copied"]:
        if res.get("already"):
            con.ok(f"已登记到 {rec['id']}")
            return 0
        if res["errors"]:
            con.warn("没有文件被加入，原因见上面的错误")
        else:
            con.warn("没有文件被加入")
        return 1
    con.ok(f"{len(res['copied'])} 项已加入 {rec['id']}")
    return 0


def cmd_scan(args, con: Console) -> int:
    from . import session

    ctx = config.load(Path(args.root)) if args.root else config.load()
    rec = session.require_current(ctx)
    folder = ex.exp_dir(ctx, rec)
    meta = ex.load_meta(folder) or {}
    st = ex.scan_files(ctx, folder, meta, rehash=args.rehash, detect_external=True)
    con.ok(f"扫描 {rec['id']}：新增 {st['new']}，变更 {st['changed']}，"
           f"丢失 {st['missing']}，共 {st['count']} 个文件")
    if st["new"] == 0 and st["changed"] == 0 and st["missing"] == 0:
        con.info("   （没有变化。文件要放进实验目录才会被登记）")
    return 0


def cmd_rm(args, con: Console) -> int:
    from . import session

    ctx = config.load(Path(args.root)) if args.root else config.load()
    rec = session.require_current(ctx) if args.exp in ("-", ".") else get_rec(ctx, args.exp)
    if args.missing:
        rels = [f["relpath"] for f in ctx.db.files_of(rec["id"]) if f["missing"]]
        if not rels:
            con.ok(f"{rec['id']} 没有标记为丢失的文件")
            return 0
        con.warn(f"{len(rels)} 个文件早已不在磁盘上，将只清掉登记记录")
        if rels[:5]:
            for r in rels[:5]:
                con.detail(con.t.dim(r))
            if len(rels) > 5:
                con.detail(con.t.dim(f"…… 其余 {len(rels) - 5} 个"))
        if not args.yes:
            if not confirm_delete(f"{len(rels)} 条丢失登记（不动磁盘文件）", indent=""):
                con.warn("已取消")
                return 1
        res = ex.remove_files(ctx, rec, rels, delete_disk=False)
        con.ok(f"已清掉 {len(res['removed'])} 条丢失登记")
        return 0

    rels = [p.replace("\\", "/") for p in args.paths]
    if not rels:
        fail("请给出要删除的相对路径，或用 --missing 清掉丢失的登记")

    folder = ex.exp_dir(ctx, rec)
    targets = [t for t in ex.expand_targets(folder, rels, recursive=args.recursive)
               if t.exists() or t.is_symlink()]
    if not targets:
        con.warn("没有匹配到任何文件")
        return 1
    n_files, n_dirs, n_bytes = ex.describe_targets(folder, targets)
    if n_dirs and not args.recursive:
        fail("匹配到目录，删除目录需要加 -r")

    if not args.yes:
        print(f"将从 {rec['id']} 删除 {n_files} 个文件"
              + (f"、{n_dirs} 个目录" if n_dirs else "")
              + f"，共 {human_size(n_bytes)}：")
        for t in targets[:10]:
            rel = t.relative_to(folder) if str(t).startswith(str(folder)) else t
            print(f"  {rel}{'/' if t.is_dir() else ''}")
        if len(targets) > 10:
            print(f"  …… 其余 {len(targets) - 10} 项")
        if not args.keep_disk:
            print("（磁盘上的文件也会被删除，先 backup 更稳妥）")
        brief = f"{n_files} 个文件" + (f"、{n_dirs} 个目录" if n_dirs else "") \
                + f"（{human_size(n_bytes)}）"
        if not confirm_delete(brief, indent=""):
            print("已取消")
            return 1
    res = ex.remove_files(ctx, rec, [str(t) for t in targets],
                          delete_disk=not args.keep_disk, recursive=args.recursive)
    for r in res["removed"][:20]:
        con.info(f"  - {r}")
    if len(res["removed"]) > 20:
        con.info(f"  …… 其余 {len(res['removed']) - 20} 项")
    for f in res["failed"]:
        con.warn(f)
    if res["removed"]:
        con.ok(f"已删除 {res['files']} 个文件（{len(res['removed'])} 项）")
        return 0
    con.warn("没有删除任何文件")
    return 1


def cmd_gui(args, con: Console) -> int:
    try:
        from .gui import launch
    except ModuleNotFoundError as exc:
        if exc.name != "PySide6" and not str(exc.name).startswith("PySide6."):
            raise
        requirements = Path(__file__).resolve().parents[1] / "requirements-gui.txt"
        raise ValueError("缺少可选 GUI 依赖，请使用当前 Python 安装：\n"
                         f'  "{sys.executable}" -m pip install -r "{requirements}"') from None
    return launch(Path(args.root) if args.root else config.find_root(), args.exp)


def cmd_web(args, con: Console) -> int:
    from .web import run

    if not 0 <= args.port <= 65535:
        raise ValueError("端口必须在 0–65535 之间")
    return run(Path(args.root) if args.root else config.find_root(), host=args.host, port=args.port, open_browser=not args.no_open)


def cmd_mcp(args, con: Console) -> int:
    from .mcp_server import serve_stdio

    return serve_stdio(Path(args.root) if args.root else config.find_root())


def cmd_export_conditions(args, con: Console) -> int:
    from . import conditions as proc_conditions

    ctx = config.load(Path(args.root) if args.root else None)
    try:
        rec = ctx.db.find_experiment(args.exp)
        if not rec:
            raise ValueError(f"找不到实验：{args.exp}")
        out = Path(args.output) if args.output else None
        res = proc_conditions.export_table(ctx, rec, args.format, output=out)
        if con.json_mode:
            con.emit({"path": str(res["path"]), "format": res["format"], "count": res["count"], "columns": res["columns"]})
        else:
            con.ok(f"已导出 {res['count']} 组工艺条件（{res['format'].upper()}）：{res['path']}")
        return 0
    finally:
        if ctx._db is not None:
            ctx.db.close()


DISPATCH = {
    "init": cmd_init, "new": cmd_new, "add": cmd_add, "sync": cmd_sync,
    "list": cmd_list, "ls": cmd_list, "show": cmd_show, "edit": cmd_edit,
    "ids": cmd_ids, "id": cmd_ids,
    "note": cmd_note, "search": cmd_search, "verify": cmd_verify,
    "backup": cmd_backup, "snapshots": cmd_snapshots, "restore": cmd_restore,
    "prune": cmd_prune, "targets": cmd_targets, "export": cmd_export,
    "stats": cmd_stats, "doctor": cmd_doctor, "rebuild": cmd_rebuild,
    "adopt": cmd_adopt, "archive": cmd_archive, "open": cmd_open,
    "issue": cmd_issue, "issues": cmd_issues, "doc": cmd_doc, "clean": cmd_clean,
    "enter": cmd_enter, "shell": cmd_enter, "work": cmd_enter,
    "use": cmd_use, "put": cmd_put, "scan": cmd_scan, "current": cmd_current,
    "rm": cmd_rm, "del": cmd_rm, "gui": cmd_gui, "web": cmd_web,
    "mcp": cmd_mcp, "export-conditions": cmd_export_conditions,
}


def main(argv: list[str] | None = None) -> int:
    setup_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)
    # 让 `expvault list --json` 这种把全局开关写在子命令后面的写法也能用
    globals_first = []
    rest = []
    for a in argv:
        if a in ("--json", "--quiet", "-q", "--no-color"):
            globals_first.append(a)
        else:
            rest.append(a)
    argv = globals_first + rest

    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0

    con = Console(
        quiet=getattr(args, "quiet", False),
        json_mode=getattr(args, "json", False),
        color=False if getattr(args, "no_color", False) else None,
    )
    handler = DISPATCH.get(args.cmd)
    if handler is None:
        parser.print_help()
        return 2

    try:
        if args.cmd == "init":
            return handler(args, con)
        # 检查仓库是否初始化
        root = Path(args.root) if args.root else config.find_root()
        if not (root / config.CONFIG_NAME).exists():
            fail(f"{root} 还不是 expvault 仓库。先运行：python expvault.py init")
        return handler(args, con)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\n已取消")
        return 130
    except FileNotFoundError as e:
        fail(str(e))
    except ValueError as e:
        fail(str(e))
    except Exception as e:
        import traceback

        if os.environ.get("EXPVAULT_DEBUG"):
            traceback.print_exc()
        fail(f"{type(e).__name__}: {e}\n（设置环境变量 EXPVAULT_DEBUG=1 可看详细堆栈）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
