"""通用工具：哈希、路径、时间、JSON、终端排版、文件锁。

所有写操作都走「临时文件 + os.replace」的原子写，避免断电/崩溃时留下半截文件。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import subprocess
import time
import unicodedata
from datetime import datetime, date
from pathlib import Path

CHUNK = 4 * 1024 * 1024          # 4 MB，兼顾大文件吞吐与小内存
LOCK_STALE_SECONDS = 3600        # 锁文件超过 1 小时视为残留


# --------------------------------------------------------------------------
# 时间
# --------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today() -> str:
    return date.today().isoformat()


def parse_iso(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.now().astimezone()


def human_time(iso: str) -> str:
    """2026-10-18T13:51:20+08:00 -> 2026-10-18 13:51"""
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso


def human_age(iso: str) -> str:
    if not iso:
        return "从未"
    delta = datetime.now().astimezone() - parse_iso(iso)
    sec = delta.total_seconds()
    if sec < 0:
        return "刚刚"
    for unit, size in (("天", 86400), ("小时", 3600), ("分钟", 60)):
        if sec >= size:
            return f"{int(sec // size)} {unit}前"
    return "刚刚"


# --------------------------------------------------------------------------
# 哈希与大小
# --------------------------------------------------------------------------
def sha256_file(path: Path, chunk: int = CHUNK) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def human_size(n: int | float) -> str:
    n = float(n or 0)
    if n < 1024:
        return f"{int(n)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
    return f"{n:.1f} TB"


def short_hash(h: str, n: int = 10) -> str:
    return (h or "")[:n]


# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------
def posix(p: str | Path) -> str:
    return str(p).replace("\\", "/")


def rel_posix(path: Path, base: Path) -> str:
    return posix(os.path.relpath(str(path), str(base)))


def is_ascii(s: str) -> bool:
    try:
        s.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def non_ascii_chars(s: str) -> str:
    return "".join(sorted({c for c in s if not is_ascii(c)}))


def slugify(text: str, fallback: str = "exp") -> str:
    """把标题转成安全的 ASCII 目录片段；中文标题返回 fallback。"""
    s = unicodedata.normalize("NFKD", text or "")
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return (s[:40].strip("-") or fallback)


def open_path(path: Path) -> Path:
    """通过操作系统的默认应用打开本地文件或文件夹，不经 shell 拼接。"""
    target = path.expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"路径不存在：{target}")
    try:
        if sys.platform == "win32":
            os.startfile(str(target))
        else:
            launcher = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.run([launcher, str(target)], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise ValueError(f"默认应用打开失败：{target}\n{detail}") from exc
    except OSError as exc:
        raise ValueError(f"无法调用默认应用打开 {target}：{exc}") from exc
    return target


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_filename(name: str, replacement: str = "_") -> str:
    bad = '<>:"/\\|?*'
    for c in bad:
        name = name.replace(c, replacement)
    return name.strip(" .") or "untitled"


# --------------------------------------------------------------------------
# JSON / JSONL（原子写）
# --------------------------------------------------------------------------
def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        return default


def write_json(path: Path, obj, *, indent: int = 2) -> None:
    ensure_dir(path.parent)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def append_jsonl(path: Path, obj) -> None:
    ensure_dir(path.parent)
    line = json.dumps(obj, ensure_ascii=False)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")


def read_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


# --------------------------------------------------------------------------
# 终端排版（中文字符按 2 列宽）
# --------------------------------------------------------------------------
def _w(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def disp_width(s: str) -> int:
    return sum(_w(c) for c in str(s))


def pad(s: str, width: int, align: str = "left") -> str:
    s = "" if s is None else str(s)
    gap = max(0, width - disp_width(s))
    if align == "right":
        return " " * gap + s
    if align == "center":
        left = gap // 2
        return " " * left + s + " " * (gap - left)
    return s + " " * gap


def truncate(s: str, width: int) -> str:
    s = "" if s is None else str(s)
    if disp_width(s) <= width:
        return s
    out, w = "", 0
    for ch in s:
        w += _w(ch)
        if w > width - 1:
            break
        out += ch
    return out + "…"


def table(rows: list[list[str]], headers: list[str], aligns: list[str] | None = None) -> str:
    """渲染等宽表格；中文字符按 2 列计算，避免错位。"""
    if not rows:
        return "（无记录）"
    n = len(headers)
    aligns = aligns or ["left"] * n
    widths = [disp_width(h) for h in headers]
    for r in rows:
        for i in range(n):
            widths[i] = max(widths[i], disp_width(r[i] if i < len(r) else ""))
    line = "  ".join(
        pad(h, widths[i], "center" if aligns[i] == "right" else aligns[i])
        for i, h in enumerate(headers)
    )
    sep = "  ".join("─" * widths[i] for i in range(n))
    body = [
        "  ".join(pad(r[i] if i < len(r) else "", widths[i], aligns[i]) for i in range(n))
        for r in rows
    ]
    return "\n".join([line, sep] + body)


def hr(char: str = "─", width: int = 68) -> str:
    return char * width


def confirm_delete(summary: str, *, indent: str = "  ", word: str = "DELETE") -> bool:
    """删除前的两遍确认。

    第一遍是随手就能按的 ``[y/N]``；第二遍要手打 ``DELETE``（或“删除”），
    逼着人先看一眼摘要，避免回车连打把数据删了。
    两遍都通过才返回 True；Ctrl+C、EOF、答错都算取消。
    """

    def ask(prompt: str) -> str | None:
        try:
            return input(indent + prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

    first = ask("确认删除？[y/N] ")
    if first is None or first.lower() not in ("y", "yes", "是"):
        return False
    second = ask(f"再次确认：将删除 {summary}，输入 {word} 继续：")
    if second is None:
        return False
    return second.upper() == word.upper() or second == "删除"


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------
def setup_stdout() -> None:
    """Windows 控制台默认 cp936，强制 UTF-8 以免中文乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# --------------------------------------------------------------------------
# 进程锁（为将来多人共用同一个备份盘预留）
# --------------------------------------------------------------------------
class FileLock:
    """基于 O_EXCL 的跨进程锁；超时自动接管陈旧锁。"""

    def __init__(self, path: Path, timeout: float = 0.0):
        self.path = Path(path)
        self.timeout = timeout
        self.acquired = False

    def __enter__(self):
        ensure_dir(self.path.parent)
        deadline = time.time() + self.timeout
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps({"pid": os.getpid(), "at": now_iso()}).encode())
                os.close(fd)
                self.acquired = True
                return self
            except FileExistsError:
                if self._stale():
                    try:
                        os.unlink(self.path)
                    except OSError:
                        pass
                    continue
                if time.time() >= deadline:
                    raise RuntimeError(
                        f"另一个 expvault 进程正在使用 {self.path.parent}（锁：{self.path}）。"
                        "若确认没有其它进程在跑，删除该锁文件后重试。"
                    )
                time.sleep(0.3)

    def _stale(self) -> bool:
        try:
            age = time.time() - self.path.stat().st_mtime
            if age > LOCK_STALE_SECONDS:
                return True
            info = read_json(self.path, {}) or {}
            pid = info.get("pid")
            if pid and os.name == "nt":
                # Windows: 进程不存在时 os.kill(pid, 0) 会抛错
                try:
                    os.kill(int(pid), 0)
                except OSError:
                    return True
        except Exception:
            return False
        return False

    def __exit__(self, *exc):
        if self.acquired:
            try:
                os.unlink(self.path)
            except OSError:
                pass
        return False
