"""终端呈现层：配色、面板、状态符号。

风格参考 Claude Code：暗色、克制、靠符号和缩进建立层次，不靠满屏颜色。
所有样式只在真正的终端里启用；管道/重定向时自动退化为纯文本。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import unicodedata

from .util import disp_width

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", str(text))


def _w(text: str) -> int:
    return disp_width(strip_ansi(str(text)))


def _clip(text: str, width: int) -> str:
    """按可见宽度截断（ANSI 序列原样保留）。"""
    s = str(text)
    if _w(s) <= width:
        return s
    out = []
    vis = 0
    i = 0
    n = len(s)
    while i < n and vis < max(1, width - 1):
        if s[i] == "\x1b":
            j = s.find("m", i)
            if j == -1:
                break
            out.append(s[i:j + 1])
            i = j + 1
            continue
        ch = s[i]
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if vis + w > width - 1:
            break
        out.append(ch)
        vis += w
        i += 1
    return "".join(out) + "…\x1b[0m" if "\x1b[" in s else "".join(out) + "…"


def _pad(text: str, width: int) -> str:
    gap = max(0, width - _w(text))
    return str(text) + " " * gap


def enable_ansi() -> bool:
    """Windows 控制台默认不解析 ANSI，这里显式打开。"""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        return True
    except Exception:
        return False


def supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        if not sys.stdout.isatty():
            return False
    except Exception:
        return False
    return enable_ansi() if os.name == "nt" else True


class Theme:
    """一组语义色，尽量少用。"""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def _c(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.enabled else str(text)

    # 语义
    def brand(self, t: str) -> str:      # 品牌色：暖橙
        return self._c("38;5;209", t)

    def accent(self, t: str) -> str:     # 链接/标识：蓝
        return self._c("38;5;111", t)

    def ok(self, t: str) -> str:         # 成功：绿
        return self._c("38;5;114", t)

    def warn(self, t: str) -> str:       # 提醒：黄
        return self._c("38;5;179", t)

    def err(self, t: str) -> str:        # 错误：红
        return self._c("38;5;203", t)

    def dim(self, t: str) -> str:
        return self._c("2", t)

    def bold(self, t: str) -> str:
        return self._c("1", t)

    def invert(self, t: str) -> str:     # 选中行
        return self._c("30;46", t)


# 符号：统一在这里改
GLYPH_OK = "⏺"
GLYPH_DETAIL = "⎿"
GLYPH_TIP = "ⓘ"
GLYPH_WARN = "▲"
GLYPH_ERR = "✖"
GLYPH_PROMPT = "❯"
GLYPH_STEP = "›"


class Console:
    """统一输出入口；支持 --quiet / --json。"""

    def __init__(self, quiet: bool = False, json_mode: bool = False, color: bool | None = None):
        self.quiet = quiet
        self.json_mode = json_mode
        self.json = json_mode
        self.color = supports_color() if color is None else color
        self.t = Theme(self.color)

    # ---------------- 基础 ----------------
    def info(self, *args, **kw) -> None:
        if not self.quiet:
            kw.setdefault("flush", True)
            print(*args, **kw)

    def blank(self) -> None:
        if not self.quiet:
            print()

    # ---------------- 状态行 ----------------
    def ok(self, msg: str) -> None:
        if not self.quiet:
            print(f"{self.t.ok(GLYPH_OK)} {msg}", flush=True)

    def warn(self, msg: str) -> None:
        print(f"{self.t.warn(GLYPH_WARN)} {msg}", file=sys.stderr, flush=True)

    def error(self, msg: str) -> None:
        print(f"{self.t.err(GLYPH_ERR)} {msg}", file=sys.stderr, flush=True)

    def detail(self, msg: str) -> None:
        if not self.quiet:
            print(f"  {self.t.dim(GLYPH_DETAIL)} {msg}", flush=True)

    def tip(self, msg: str) -> None:
        if not self.quiet:
            print(f"  {self.t.dim(GLYPH_TIP)} {self.t.dim(msg)}", flush=True)

    def step(self, msg: str) -> None:
        if not self.quiet:
            print(f"  {self.t.accent(GLYPH_STEP)} {msg}", flush=True)

    def header(self, msg: str, width: int | None = None) -> None:
        """小节标题：上面空一行，下面一条淡淡的横线。"""
        if self.quiet:
            return
        print()
        print(f"{self.t.bold(msg)}")
        w = width or min(64, max(24, _w(msg) + 8))
        print(self.t.dim("─" * w))

    def rule(self, width: int = 64) -> None:
        if not self.quiet:
            print(self.t.dim("─" * width))

    def status_line(self, line: str) -> None:
        """把 doctor 之类预先拼好的 ✅/❌/⚠️ 行转成统一风格。"""
        s = strip_ansi(line)
        if s.startswith("✅"):
            print(f"{self.t.ok(GLYPH_OK)} {s[2:].strip()}")
        elif s.startswith("❌"):
            print(f"{self.t.err(GLYPH_ERR)} {s[2:].strip()}")
        elif s.startswith("⚠️") or s.startswith("⚠"):
            print(f"{self.t.warn(GLYPH_WARN)} {s.lstrip('⚠️ ').strip()}")
        else:
            print(line)

    # ---------------- 面板 ----------------
    def panel(self, lines: list[str], indent: str = "  ", min_width: int = 34) -> None:
        t = self.t
        body = [str(x) for x in lines]
        term = shutil.get_terminal_size((80, 24)).columns
        inner = max([_w(x) for x in body] + [min_width])
        inner = min(inner, max(20, term - len(indent) - 6))
        body = [_clip(x, inner) for x in body]
        inner = max([_w(x) for x in body] + [min_width])

        print(f"{indent}{t.dim('╭' + '─' * (inner + 2) + '╮')}")
        for line in body:
            print(f"{indent}{t.dim('│')} {_pad(line, inner)} {t.dim('│')}")
        print(f"{indent}{t.dim('╰' + '─' * (inner + 2) + '╯')}")

    def banner(self, subtitle: str = "", version: str = "") -> None:
        """启动横幅：名字 + 一句话 + 版本。"""
        t = self.t
        name = "expvault"
        head = f"{t.brand(name)}  {t.dim('·')}  {t.dim(version)}" if version else t.brand(name)
        lines = [f"{head}"]
        if subtitle:
            lines.append(f"{t.dim(subtitle)}")
        print()
        self.panel(lines, min_width=38)
        print()

    # ---------------- 其它 ----------------
    def emit_json(self, obj) -> None:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
