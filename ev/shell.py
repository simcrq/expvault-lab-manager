"""交互式工作区：`expvault enter` 之后就在这个实验里打短命令。

设计目标：把「expvault + 实验编号 + 子命令 + 参数」压成「子命令 + 参数」。
切换实验用 use，退出用 exit，工作目录会切换到实验目录，相对路径直接可用。
"""
from __future__ import annotations

import glob
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import backup as bk
from . import experiment as ex
from . import report
from . import session
from . import tui
from .config import Ctx
from .ui import GLYPH_PROMPT, Console
from .util import confirm_delete, human_size, pad, truncate

STATUS_CHOICES = {
    "planned": "planned", "计划": "planned", "计划中": "planned",
    "running": "running", "进行": "running", "进行中": "running",
    "done": "done", "完成": "done", "已完成": "done",
    "aborted": "aborted", "中止": "aborted", "已中止": "aborted",
}

# 工作区命令名（给 Tab 补全用）
COMMANDS = (
    "add", "put", "import", "ls", "list", "files", "scan", "note", "log",
    "rm", "del", "remove", "info", "show", "status", "title", "tag", "tags",
    "param", "open", "doc", "readme", "verify", "sync", "backup", "bk",
    "export", "use", "switch", "ids", "id", "new", "current", "cd", "pwd",
    "cls", "clear", "help", "exit", "quit", "q", "gui", "web",
)


# --------------------------------------------------------------------------
# 分词（Windows 友好：保留反斜杠，保留引号里的空格）
# --------------------------------------------------------------------------
def split_args(line: str) -> list[str]:
    lex = shlex.shlex(line, posix=False)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        tokens = list(lex)
    except ValueError:
        tokens = line.split()
    out = []
    for t in tokens:
        if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
            t = t[1:-1]
        out.append(t)
    return out


HELP = [
    ("web", "打开完整网页实验管理：实验、步骤、日志、文件、校验和备份"),
    ("gui", "打开桌面导入插件；文件 / U 盘 / 手机扫码，支持文件标签"),
    ("add <文件...> [-L]", "导入文件（默认 01_raw；-L 只建链接，不占额外空间）"),
    ("ls", "列出本实验已登记的文件"),
    ("scan", "扫描本实验目录，登记新放进去的文件"),
    ("note <文本>", "追加一条带时间戳的日志"),
    ("rm <路径> [-r]", "删除文件/通配符/目录（-r 删目录，-f 不确认）"),
    ("info", "查看实验详情（标题、参数、时间线）"),
    ("status <状态>", "running / done / planned / aborted"),
    ("title <新标题>", "改标题"),
    ("tag a,b", "替换标签"),
    ("param k=v ...", "设置工艺参数"),
    ("open", "在资源管理器里打开实验目录"),
    ("doc", "重新生成 README.md"),
    ("verify", "校验本实验文件（重算 SHA256）"),
    ("sync", "扫描整个仓库"),
    ("backup [--prune]", "备份到所有已启用的目标"),
    ("export", "导出 Excel 台账 + HTML 看板"),
    ("use [编号]", "切换实验（不带编号会弹出方向键列表）"),
    ("ids [-l]", "列出所有实验编号（-l 带日期和标题）"),
    ("new <标题>", "新建实验并切换过去"),
    ("current", "显示当前实验"),
    ("cd <目录> / pwd", "切换 / 显示工作目录"),
    ("cls", "清屏"),
    ("!<命令>", "执行系统命令"),
    ("exit", "退出工作区（q 也行）"),
]


# --------------------------------------------------------------------------
# 工作区
# --------------------------------------------------------------------------
class Workspace:
    def __init__(self, ctx: Ctx, con: Console, rec: dict, change_dir: bool = True):
        self.ctx = ctx
        self.con = con
        self.rec = rec
        self.change_dir = change_dir
        self.style = con.t
        self.origin_cwd = Path.cwd()
        # 输入框宽度在进入时定死，避免刷新/缩放时左右乱跳
        self.box_width = min(max(40, shutil.get_terminal_size((80, 24)).columns - 2), 80)
        self.history: list[str] = []
        self.completer = self._make_completer()

    # ---------------- Tab 补全 ----------------
    def _make_completer(self):
        """返回 completer(text, pos) -> (替换起点, 候选列表)。"""
        import re

        def complete(text: str, pos: int):
            head = text[:pos]
            m = re.search(r"(?:^|\s)(\S*)$", head)
            if not m:
                return pos, []
            start = m.start(1)
            token = head[start:]
            if start == 0:
                # 第一个词：补全命令
                return start, [c for c in COMMANDS if c.startswith(token)]
            if token.startswith("-"):
                return start, []
            base, _, prefix = token.rpartition("/")
            search = base or "."
            try:
                entries = os.listdir(search)
            except OSError:
                return start, []
            low = prefix.lower()
            out = []
            for name in entries:
                if name.startswith(".") and not prefix.startswith("."):
                    continue
                if not name.lower().startswith(low):
                    continue
                p = f"{base}/{name}" if base else name
                if os.path.isdir(os.path.join(search, name)):
                    p += "/"
                out.append(p.replace("\\", "/"))
            return start, sorted(out)

        return complete

    # ---------------- 输出 ----------------
    def ok(self, msg: str) -> None:
        print(f"{self.style.ok('⏺')} {msg}")

    def warn(self, msg: str) -> None:
        print(f"{self.style.warn('▲')} {msg}", file=sys.stderr)

    def err(self, msg: str) -> None:
        print(f"{self.style.err('✖')} {msg}", file=sys.stderr)

    def tip(self, msg: str) -> None:
        self.con.tip(msg)

    def detail(self, msg: str) -> None:
        self.con.detail(msg)

    # ---------------- 状态 ----------------
    def refresh(self) -> None:
        self.rec = self.ctx.db.get_experiment(self.rec["id"]) or self.rec

    def cmd_gui(self, args: list[str]) -> None:
        import sys

        if args:
            raise ValueError("用法：gui（打开当前实验的桌面导入窗口）")
        entry = Path(__file__).resolve().parents[1] / "expvault.py"
        subprocess.Popen([sys.executable, str(entry), "--root", str(self.ctx.root), "gui", self.rec["id"]])
        self.ok("已启动桌面导入窗口")

    def cmd_web(self, args: list[str]) -> None:
        import sys

        if args:
            raise ValueError("用法：web（本机网页管理；自定义地址和端口请使用 expvault web --help）")
        entry = Path(__file__).resolve().parents[1] / "expvault.py"
        subprocess.Popen([sys.executable, str(entry), "--root", str(self.ctx.root), "web", "--port", "0"])
        self.ok("已启动网页实验管理；可在启动终端中查看访问链接并停止服务")

    def folder(self) -> Path:
        return ex.exp_dir(self.ctx, self.rec)

    def switch(self, key: str) -> None:
        rec = self.ctx.db.find_experiment(key)
        if not rec:
            self.err(f"找不到实验：{key}")
            return
        self.rec = rec
        session.set_current(self.ctx, rec["id"])
        self.enter_dir()
        self.ok(f"已切换到 {rec['id']}  {truncate(rec['title'], 40)}")

    def enter_dir(self) -> None:
        if not self.change_dir:
            return
        folder = self.folder()
        if folder.exists():
            try:
                os.chdir(folder)
            except OSError:
                pass

    def prompt(self) -> str:
        s = self.style
        title = truncate(self.rec.get("title") or self.rec["id"], 34)
        return f"{s.bold(title)} {s.brand(GLYPH_PROMPT)} "

    # ---------------- 主循环 ----------------
    def run(self) -> None:
        s = self.style
        info = self.rec["id"]
        if self.rec.get("file_count"):
            info += f"  ·  {self.rec['file_count']} 个文件  ·  {human_size(self.rec['total_bytes'])}"
        print()
        self.con.panel([
            f"{s.brand('expvault')} {s.dim('· 实验工作区')}",
            "",
            f"{s.bold(self.rec['title'])}",
            s.dim(info),
            s.dim(str(self.folder())),
        ])
        print()
        self.con.tip("add / ls / note / scan / backup 直接敲；help 看全部，exit 退出")
        print()

        self.enter_dir()
        title = truncate(self.rec.get("title") or self.rec["id"], 34)
        try:
            while True:
                try:
                    line = tui.input_box(
                        title,
                        hint="tab 补全 · help · exit",
                        glyph=GLYPH_PROMPT,
                        theme=self.style,
                        width=self.box_width,
                        completer=self.completer,
                        history=self.history,
                    )
                except EOFError:
                    print()
                    break
                except KeyboardInterrupt:
                    print()
                    continue
                line = (line or "").strip()
                if not line:
                    continue
                if line in ("exit", "quit", "q", ":q", "退出"):
                    break
                if not self.history or self.history[-1] != line:
                    self.history.append(line)
                self.dispatch(line)
                if self.rec.get("title") != title:
                    title = truncate(self.rec.get("title") or self.rec["id"], 34)
        finally:
            try:
                os.chdir(self.origin_cwd)
            except OSError:
                pass
            print(self.style.dim("  ⏏ 已离开工作区"))

    def dispatch(self, line: str) -> None:
        if line.startswith("!"):
            try:
                subprocess.run(line[1:], shell=True, cwd=os.getcwd())
            except Exception as e:
                self.err(str(e))
            return
        tokens = split_args(line)
        if not tokens:
            return
        cmd, args = tokens[0].lower().lstrip("/"), tokens[1:]
        handler = {
            "help": self.cmd_help, "?": self.cmd_help, "h": self.cmd_help,
            "add": self.cmd_add, "put": self.cmd_add, "import": self.cmd_add,
            "ls": self.cmd_ls, "list": self.cmd_ls, "files": self.cmd_ls,
            "scan": self.cmd_scan,
            "note": self.cmd_note, "log": self.cmd_note,
            "rm": self.cmd_rm, "del": self.cmd_rm, "remove": self.cmd_rm,
            "info": self.cmd_info, "show": self.cmd_info,
            "status": self.cmd_status,
            "title": self.cmd_title,
            "tag": self.cmd_tag, "tags": self.cmd_tag,
            "param": self.cmd_param,
            "open": self.cmd_open,
            "gui": self.cmd_gui,
            "web": self.cmd_web,
            "doc": self.cmd_doc, "readme": self.cmd_doc,
            "verify": self.cmd_verify,
            "sync": self.cmd_sync,
            "backup": self.cmd_backup, "bk": self.cmd_backup,
            "export": self.cmd_export,
            "use": self.cmd_use, "switch": self.cmd_use,
            "ids": self.cmd_ids, "id": self.cmd_ids,
            "new": self.cmd_new,
            "current": self.cmd_current,
            "cd": self.cmd_cd, "pwd": self.cmd_pwd,
            "cls": self.cmd_cls, "clear": self.cmd_cls,
        }.get(cmd)
        if handler is None:
            self.err(f"不认识的命令：{cmd}（敲 help 看能用的）")
            return
        try:
            handler(args)
        except SystemExit:
            raise
        except Exception as e:  # 单条命令出错不应该踢出工作区
            self.err(f"{type(e).__name__}: {e}")
        self.refresh()

    # ---------------- 命令 ----------------
    def cmd_help(self, args: list[str]) -> None:
        if args:
            name = args[0].lower()
            for usage, desc in HELP:
                if usage.split()[0] == name:
                    print(f"  {usage:<26} {desc}")
                    return
            print(f"  没有 {name} 的单独说明，看完整列表吧")
        print()
        for usage, desc in HELP:
            # 先补齐再上色，否则 ANSI 转义字符会把宽度算进去
            print(f"  {self.style.accent(pad(usage, 24))} {desc}")
        print()

    def cmd_ls(self, args: list[str]) -> None:
        files = self.ctx.db.files_of(self.rec["id"])
        if not files:
            self.warn("还没有登记任何文件。把文件放进目录后敲 scan")
            return
        rows = []
        for f in files:
            rows.append([
                truncate(f["relpath"], 52),
                human_size(f["size"]),
                f["role"],
                "丢失" if f["missing"] else "",
            ])
        from .util import table

        print(table(rows, ["文件", "大小", "类型", ""], ["left", "right", "left", "left"]))
        print(self.style.dim(
            f"  共 {len(files)} 个，{human_size(self.rec['total_bytes'])}"))

        known = {f["relpath"] for f in files}
        extra = 0
        folder = self.folder()
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                rel = os.path.relpath(os.path.join(dirpath, fn), folder).replace("\\", "/")
                if rel not in known and not fn.startswith("~$"):
                    extra += 1
        if extra:
            self.warn(f"目录里还有 {extra} 个文件没登记，敲 scan 补上")

    def cmd_add(self, args: list[str]) -> None:
        role = ""
        move = False
        link = ""
        paths: list[str] = []
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("-r", "--role") and i + 1 < len(args):
                role = args[i + 1]
                i += 2
                continue
            if a == "--move":
                move = True
                i += 1
                continue
            if a in ("-L", "--link"):
                link = "auto"
                i += 1
                continue
            if a == "--link-hard":
                link = "hard"
                i += 1
                continue
            if a == "--link-soft":
                link = "soft"
                i += 1
                continue
            paths.append(a)
            i += 1
        if not paths:
            self.warn("用法：add <文件或文件夹> [-r raw|processed|figure] [--move] [-L 只建链接]")
            return
        expanded: list[Path] = []
        for p in paths:
            if not Path(p).exists() and any(ch in p for ch in "*?["):
                hits = sorted(glob.glob(p))
                expanded.extend(Path(h) for h in hits)
            else:
                expanded.append(Path(p))
        if not expanded:
            self.err("没有匹配到任何文件")
            return
        res = ex.add_files(self.ctx, self.rec, expanded, role=role, move=move, link=link)
        for line in res["copied"]:
            print(f"  {self.style.ok('+')} {line}")
        for line in res["skipped"]:
            print(f"  {self.style.dim('·')} 跳过 {line}")
        for line in res["already"]:
            print(f"  {self.style.dim('·')} 已在实验目录（直接 scan 就行）：{line}")
        for line in res["errors"]:
            self.err(line)
        if link:
            st = res["link_stats"]
            bits = []
            if st.get("hard"):
                bits.append(f"硬链接 {st['hard']}")
            if st.get("soft"):
                bits.append(f"符号链接 {st['soft']}")
            if st.get("copy"):
                bits.append(f"退回复制 {st['copy']}")
            if bits:
                self.tip("链接模式：" + "，".join(bits))
        self.refresh()
        if res["copied"]:
            self.ok(f"已导入 {len(res['copied'])} 项，现有 {self.rec['file_count']} 个文件")
        elif res["already"]:
            self.ok(f"已登记，现有 {self.rec['file_count']} 个文件")

    def cmd_scan(self, args: list[str]) -> None:
        folder = self.folder()
        meta = ex.load_meta(folder) or {}
        st = ex.scan_files(self.ctx, folder, meta)
        self.ok(f"扫描完成：新增 {st['new']}，变更 {st['changed']}，"
                f"丢失 {st['missing']}，共 {st['count']} 个文件")

    def cmd_note(self, args: list[str]) -> None:
        text = " ".join(args).strip()
        if not text:
            self.warn('用法：note 今天观察到……')
            return
        path = ex.append_note(self.ctx, self.rec, text)
        self.ok(f"已记到 {path.name}")

    def cmd_info(self, args: list[str]) -> None:
        print(ex.format_show(self.ctx, self.rec))

    def cmd_rm(self, args: list[str]) -> None:
        force = False
        recursive = False
        raw: list[str] = []
        for a in args:
            if a in ("-f", "--force", "-y"):
                force = True
            elif a in ("-r", "-R", "--recursive"):
                recursive = True
            else:
                raw.append(a)
        if not raw:
            self.warn("用法：rm <文件或通配符> [-r 删目录] [-f 跳过确认]")
            self.tip('例如：rm "01_raw/4-1/*"   rm 01_raw/4-1 -r')
            return

        folder = self.folder()
        # 先按当前目录展开；展不开的再交给实验目录去解析
        patterns: list[str] = []
        for pat in raw:
            if any(ch in pat for ch in "*?["):
                hits = glob.glob(pat, recursive=True)
                if hits:
                    patterns.extend(os.path.abspath(h) for h in hits)
                    continue
            elif os.path.exists(pat):
                patterns.append(os.path.abspath(pat))
                continue
            patterns.append(pat)

        targets = ex.expand_targets(folder, patterns, recursive=recursive)
        targets = [t for t in targets if t.exists() or t.is_symlink()]
        if not targets:
            self.warn("没有匹配到任何文件")
            return

        n_files, n_dirs, n_bytes = ex.describe_targets(folder, targets)
        has_dir = any(t.is_dir() and not t.is_symlink() for t in targets)
        if has_dir and not recursive:
            self.err("匹配到目录，删除目录需要加 -r")
            for t in targets[:8]:
                if t.is_dir():
                    print(f"    {t.relative_to(folder) if str(t).startswith(str(folder)) else t}/")
            return

        print(f"  将删除 {n_files} 个文件"
              + (f"、{n_dirs} 个目录" if n_dirs else "")
              + f"，共 {human_size(n_bytes)}")
        for t in targets[:10]:
            rel = t.relative_to(folder) if str(t).startswith(str(folder)) else t
            mark = "/" if t.is_dir() and not t.is_symlink() else ""
            print(f"    {rel}{mark}")
        if len(targets) > 10:
            print(f"    …… 其余 {len(targets) - 10} 项")

        if not force:
            brief = f"{n_files} 个文件" + (f"、{n_dirs} 个目录" if n_dirs else "") \
                    + f"（{human_size(n_bytes)}），且无法撤销"
            if not confirm_delete(brief):
                print("  已取消，什么都没动")
                return

        res = ex.remove_files(self.ctx, self.rec,
                              [str(t) for t in targets], recursive=recursive)
        for f in res["failed"]:
            self.err(f)
        if res["removed"]:
            self.ok(f"已删除 {res['files']} 个文件"
                    + (f"（{len(res['removed'])} 项）" if len(res["removed"]) > 1 else ""))

    def cmd_status(self, args: list[str]) -> None:
        if not args:
            cur = self.rec.get("status", "")
            print(f"  当前状态：{ex.STATUS_LABEL.get(cur, cur)}")
            print("  可选：running / done / planned / aborted")
            return
        raw = args[0].lower()
        status = STATUS_CHOICES.get(raw, raw)
        if status not in ex.STATUS_LABEL:
            self.err(f"不认识的状态：{args[0]}")
            return
        ex.update(self.ctx, self.rec, {"status": status})
        self.ok(f"状态改为 {ex.STATUS_LABEL[status]}")

    def cmd_title(self, args: list[str]) -> None:
        title = " ".join(args).strip()
        if not title:
            print(f"  当前标题：{self.rec['title']}")
            return
        ex.update(self.ctx, self.rec, {"title": title})
        self.ok(f"标题改为：{title}")

    def cmd_tag(self, args: list[str]) -> None:
        raw = " ".join(args)
        tags = [t.strip() for t in raw.replace("，", ",").split(",") if t.strip()]
        ex.update(self.ctx, self.rec, {"tags": ",".join(tags)})
        self.ok("标签：" + ("、".join(tags) if tags else "（已清空）"))

    def cmd_param(self, args: list[str]) -> None:
        if not args:
            params = self.rec.get("params") or {}
            if params:
                for k, v in params.items():
                    print(f"  {k} = {v}")
            else:
                print("  还没有参数")
            return
        params = dict(self.rec.get("params") or {})
        for item in args:
            if "=" not in item:
                self.warn(f"跳过（应为 k=v）：{item}")
                continue
            k, v = item.split("=", 1)
            params[k.strip()] = v.strip()
        ex.update(self.ctx, self.rec, {"params": params})
        self.ok(f"参数已更新（{len(params)} 项）")

    def cmd_open(self, args: list[str]) -> None:
        folder = self.folder()
        if hasattr(os, "startfile"):
            os.startfile(str(folder))
            self.ok(str(folder))
        else:
            print(folder)

    def cmd_doc(self, args: list[str]) -> None:
        meta = ex.load_meta(self.folder()) or {}
        ex.write_readme(self.ctx, meta, self.ctx.db.files_of(self.rec["id"], False),
                        folder=self.folder())
        self.ok("README.md 已重新生成")

    def cmd_verify(self, args: list[str]) -> None:
        print(self.style.dim("  正在重算哈希…"))
        rep = ex.verify(self.ctx, rehash=True, exp_id=self.rec["id"])
        bad = len(rep["modified"]) + len(rep["missing"]) + len(rep["unregistered"])
        if not bad:
            self.ok(f"{rep['checked']} 个文件全部一致")
            return
        if rep["modified"]:
            self.warn(f"{len(rep['modified'])} 个文件内容被改过：")
            for m in rep["modified"][:10]:
                print(f"    {m['relpath']}  {m['registered']} → {m['actual']}")
        if rep["missing"]:
            self.warn(f"{len(rep['missing'])} 个文件丢失：")
            for m in rep["missing"][:10]:
                print(f"    {m['relpath']}")
        if rep["unregistered"]:
            self.warn(f"{len(rep['unregistered'])} 个新文件未登记（敲 scan）")

    def cmd_sync(self, args: list[str]) -> None:
        st = ex.sync(self.ctx, rehash="--rehash" in args)
        self.ok(f"全库扫描：{st['experiments']} 个实验，新增 {st['new_files']}，"
                f"变更 {st['changed']}，丢失 {st['missing']}")

    def cmd_backup(self, args: list[str]) -> None:
        targets = []
        for t in self.ctx.cfg["backup"]["targets"]:
            if not t.get("enabled"):
                continue
            p = self.ctx.resolve_target_path(t)
            if not bk.path_available(p):
                self.warn(f"目标「{t['name']}」不可用，跳过")
                continue
            targets.append((t["name"], p))
        if not targets:
            self.err("没有可用的备份目标")
            return
        prune = "--prune" in args
        for name, path in targets:
            r = bk.backup(self.ctx, path, name=name, prune=prune)
            st = r["stats"]
            self.ok(f"{name}：快照 {r['snapshot']}，{st['files']} 个文件，"
                    f"新增 {human_size(st['new_bytes'])}")

    def cmd_export(self, args: list[str]) -> None:
        out = self.ctx.report_dir
        x = report.export_xlsx(self.ctx, out / "实验台账.xlsx")
        h = report.export_html(self.ctx, out / "实验看板.html")
        self.ok(str(x))
        self.ok(str(h))

    def cmd_use(self, args: list[str]) -> None:
        if args:
            self.switch(args[0])
            return
        rec = session.choose(self.ctx, current=self.rec)
        if rec is None:
            self.warn("已取消")
            return
        self.switch(rec["id"])

    def cmd_ids(self, args: list[str]) -> None:
        """列出所有实验编号；当前所在的那个会高亮。"""
        long_mode = False
        include_archived = False
        filters = {"project": "", "status": "", "tag": ""}
        keymap = {"-p": "project", "--project": "project",
                  "-s": "status", "--status": "status",
                  "-t": "tag", "--tag": "tag"}
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("-l", "--long"):
                long_mode = True
                i += 1
            elif a == "--all":
                include_archived = True
                i += 1
            elif a in keymap and i + 1 < len(args):
                filters[keymap[a]] = args[i + 1]
                i += 2
            else:
                i += 1

        recs = self.ctx.db.list_experiments(
            project=filters["project"] or None,
            status=filters["status"] or None,
            tag=filters["tag"] or None,
            include_archived=include_archived,
        )
        if not recs:
            self.warn("没有匹配的实验")
            return
        current = self.rec["id"]
        for r in recs:
            if long_mode:
                line = f"{r['id']}\t{r['date']}\t{r['title']}"
            else:
                line = r["id"]
            if r["id"] == current:
                line = self.style.accent(line)
            print(line)
        print(self.style.dim(f"  共 {len(recs)} 个（高亮的是当前）"))

    def cmd_new(self, args: list[str]) -> None:
        title = " ".join(args).strip()
        if not title:
            self.warn('用法：new "实验标题"')
            return
        rec = ex.create(self.ctx, title)
        session.set_current(self.ctx, rec["id"])
        self.rec = rec
        self.enter_dir()
        self.ok(f"已新建并切换：{rec['id']}  {rec['title']}")
        print(self.style.dim(f"  {self.folder()}"))

    def cmd_current(self, args: list[str]) -> None:
        r = self.rec
        print(f"  {r['id']}  {r['title']}")
        print(self.style.dim(f"  {self.folder()}"))

    def cmd_cd(self, args: list[str]) -> None:
        target = args[0] if args else str(Path.home())
        try:
            os.chdir(os.path.expanduser(target))
            print(f"  {os.getcwd()}")
        except OSError as e:
            self.err(str(e))

    def cmd_pwd(self, args: list[str]) -> None:
        print(f"  {os.getcwd()}")

    def cmd_cls(self, args: list[str]) -> None:
        os.system("cls" if os.name == "nt" else "clear")


def run(ctx: Ctx, con: Console, rec: dict, change_dir: bool = True) -> None:
    Workspace(ctx, con, rec, change_dir=change_dir).run()
