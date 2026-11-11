"""终端交互组件：方向键选择列表。

Windows 用 msvcrt.getwch 直接读键（不需要回车），Unix 用 termios cbreak + ANSI 转义序列。
不是终端（管道/重定向）时自动降级为「输入编号」。
"""
from __future__ import annotations

import os
import shutil
import sys

from .util import disp_width, pad, truncate

# Windows 特殊键的第二字节
_WIN_KEYS = {
    "H": "up", "P": "down", "K": "left", "M": "right",
    "G": "home", "O": "end", "I": "pgup", "Q": "pgdn",
    "S": "delete", "R": "insert",
}


def enable_ansi() -> bool:
    """打开 Windows 控制台的 ANSI 转义支持。"""
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


def _read_key_win() -> str:
    import msvcrt

    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        second = msvcrt.getwch()
        return _WIN_KEYS.get(second, "")
    if ch == "\r":
        return "enter"
    if ch == "\x1b":
        return "esc"
    if ch == "\x03":
        raise KeyboardInterrupt
    if ch == "\x08":
        return "backspace"
    return ch


def _read_key_unix() -> str:
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = os.read(fd, 1)
        if ch == b"\x1b":
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                return "esc"
            seq = os.read(fd, 2).decode("latin-1", "ignore")
            return {"[A": "up", "[B": "down", "[C": "right", "[D": "left",
                    "[H": "home", "[F": "end", "[5": "pgup", "[6": "pgdn"}.get(seq, "esc")
        if ch in (b"\r", b"\n"):
            return "enter"
        if ch == b"\x7f":
            return "backspace"
        if ch == b"\x03":
            raise KeyboardInterrupt
        return ch.decode("utf-8", "ignore")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_key() -> str:
    return _read_key_win() if os.name == "nt" else _read_key_unix()


def input_box(
    title: str,
    hint: str = "",
    glyph: str = "❯",
    indent: str = "  ",
    width: int | None = None,
    theme=None,
    completer=None,
    history: list[str] | None = None,
) -> str:
    """带边框的输入行（支持 Tab 补全与上下键历史）。

    边框在按回车后补上，所以视觉上就是一个「装着你刚敲的命令」的盒子；
    宽度在进入工作区时算好并固定，不会因为终端刷新而左右跳动。
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return input(f"{title} {glyph} ")

    term = shutil.get_terminal_size((80, 24)).columns
    if width is None:
        width = min(max(40, term - len(indent)), 80)
    width = max(24, width)

    if len(title) > width - 24:
        title = title[: max(4, width - 24)] + "…"
    left = f"╭─ {title} "
    right = f" {hint} ─╮" if hint else "─╮"
    if disp_width(left) + disp_width(right) + 6 > width:
        right = "─╮"
    fill = max(0, width - disp_width(left) - disp_width(right))
    top = left + "─" * fill + right
    bottom = "╰" + "─" * (width - 2) + "╯"
    if theme is not None:
        top, bottom = theme.dim(top), theme.dim(bottom)
    print(f"{indent}{top}")
    try:
        text = line_input(f"{indent}│ {glyph} ", completer=completer, history=history)
    finally:
        print(f"{indent}{bottom}")
    return text


def _print_candidates(cands: list[str]) -> None:
    """把候选按列排开打印。"""
    width = shutil.get_terminal_size((80, 24)).columns
    longest = max((disp_width(c) for c in cands), default=1)
    col_w = min(longest + 2, width - 1)
    per_row = max(1, width // max(col_w + 1, 1))
    for i in range(0, len(cands), per_row):
        row = cands[i:i + per_row]
        print("  " + "".join(pad(c, col_w + 1) for c in row).rstrip())


def line_input(prompt: str, completer=None, history: list[str] | None = None) -> str:
    """自己实现的行输入：支持 Tab 补全、左右移动、上下翻历史。"""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return input(prompt)

    buf: list[str] = []
    pos = 0
    hist = history if history is not None else []
    hist_idx = len(hist)
    stash = ""
    prev_rows = [1]

    def _rows(text: str) -> int:
        term = shutil.get_terminal_size((80, 24)).columns or 80
        return max(1, disp_width(prompt + text) // term + 1)

    def redraw(to_end: bool = False) -> None:
        nonlocal pos
        if to_end:
            pos = len(buf)
        text = "".join(buf)
        parts = []
        if prev_rows[0] > 1:
            parts.append(f"\x1b[{prev_rows[0] - 1}A")
        parts.append("\r\x1b[J")          # 清掉上一帧（包括换行后的残影）
        parts.append(prompt + text)
        back = disp_width(text[pos:])
        if back:
            parts.append(f"\x1b[{back}D")
        sys.stdout.write("".join(parts))
        sys.stdout.flush()
        prev_rows[0] = _rows(text)

    redraw()

    while True:
        key = read_key()
        show_cands: list[str] = []
        if key == "enter":
            redraw(to_end=True)
            sys.stdout.write("\n")
            sys.stdout.flush()
            return "".join(buf)
        if key == "backspace":
            if pos > 0:
                del buf[pos - 1]
                pos -= 1
        elif key == "delete":
            if pos < len(buf):
                del buf[pos]
        elif key == "left":
            pos = max(0, pos - 1)
        elif key == "right":
            pos = min(len(buf), pos + 1)
        elif key == "home":
            pos = 0
        elif key == "end":
            pos = len(buf)
        elif key == "up":
            if hist:
                if hist_idx == len(hist):
                    stash = "".join(buf)
                if hist_idx > 0:
                    hist_idx -= 1
                    buf = list(hist[hist_idx])
                    pos = len(buf)
        elif key == "down":
            if hist and hist_idx < len(hist):
                hist_idx += 1
                buf = list(stash if hist_idx >= len(hist) else hist[hist_idx])
                pos = len(buf)
        elif key == "\t":
            buf, pos, show_cands = _complete(buf, pos, completer)
        elif key == "esc":
            pos = len(buf)
        elif len(key) == 1:
            buf.insert(pos, key)
            pos += 1
        if show_cands:
            sys.stdout.write("\n")
            _print_candidates(show_cands)
        redraw()


def _complete(buf: list[str], pos: int, completer):
    """调用补全器，返回 (新缓冲, 新光标, 需要展示的候选)。"""
    if completer is None:
        return buf, pos, []
    text = "".join(buf)
    try:
        start, cands = completer(text, pos)
    except Exception:
        return buf, pos, []
    if not cands:
        return buf, pos, []
    if len(cands) == 1:
        new = text[:start] + cands[0] + text[pos:]
        return list(new), start + len(cands[0]), []
    common = os.path.commonprefix(cands)
    if len(common) > pos - start:
        new = text[:start] + common + text[pos:]
        return list(new), start + len(common), []
    return buf, pos, cands


def _plain_select(lines: list[str], header: str, default: int) -> int | None:
    """非终端环境：退化成逐行编号 + input。"""
    if header:
        print(header)
    for i, line in enumerate(lines, 1):
        print(f"  {i}. {line}")
    try:
        ans = input(f"选择编号（回车选 {default + 1}）：").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not ans:
        return default
    if ans.isdigit() and 1 <= int(ans) <= len(lines):
        return int(ans) - 1
    return None


def select(
    lines: list[str],
    header: str = "",
    default: int = 0,
    height: int = 14,
    hint: str = "↑↓ 选择   Enter 确认   q 取消",
) -> int | None:
    """方向键选一项；返回下标，取消返回 None。"""
    if not lines:
        return None
    if not (sys.stdin.isatty() and sys.stdout.isatty()) or not enable_ansi():
        return _plain_select(lines, header, default)

    n = len(lines)
    cursor = max(0, min(default, n - 1))
    top = 0
    view = min(height, n)
    drawn = 0
    width = shutil.get_terminal_size((80, 24)).columns

    def window() -> int:
        nonlocal top
        if cursor < top:
            top = cursor
        elif cursor >= top + view:
            top = cursor - view + 1
        return top

    def build() -> list[str]:
        out = []
        start = window()
        end = min(start + view, n)
        for i in range(start, end):
            mark = "❯ " if i == cursor else "  "
            body = truncate(lines[i], max(10, width - 6))
            if i == cursor:
                body = f"\x1b[30;46m{body}\x1b[0m"
            out.append(f"  {mark}{body}")
        if n > view:
            out.append(f"  \x1b[2m{start + 1}-{end}/{n}\x1b[0m")
        out.append(f"  \x1b[2m{hint}\x1b[0m")
        return out

    def render(first: bool) -> None:
        nonlocal drawn
        chunks = []
        if first and header:
            chunks.append(header + "\n")
        if not first and drawn:
            chunks.append(f"\x1b[{drawn}A")
        out = build()
        for line in out:
            chunks.append("\r\x1b[2K" + line + "\n")
        sys.stdout.write("".join(chunks))
        sys.stdout.flush()
        drawn = len(out)

    try:
        render(True)
        while True:
            key = read_key()
            if key in ("up", "k"):
                cursor = (cursor - 1) % n
            elif key in ("down", "j"):
                cursor = (cursor + 1) % n
            elif key == "pgup":
                cursor = max(0, cursor - view)
            elif key == "pgdn":
                cursor = min(n - 1, cursor + view)
            elif key == "home":
                cursor = 0
            elif key == "end":
                cursor = n - 1
            elif key == "enter":
                return cursor
            elif key in ("esc", "q", "Q"):
                return None
            elif len(key) == 1 and key.isdigit():
                idx = int(key) - 1
                if 0 <= idx < n:
                    cursor = idx
            render(False)
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        return None
