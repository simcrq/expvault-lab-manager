#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""expvault 命令行入口。

用法示例：
    python expvault.py init
    python expvault.py new "器件A 转移特性" -c IV
    python expvault.py backup
    python expvault.py doctor
"""
import sys
from pathlib import Path

# 允许从任意工作目录调用
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ev.cli import main  # noqa: E402

if __name__ == "__main__":
    from ev.nas_cli import dispatch
    raise SystemExit(dispatch(main, sys.argv[1:]))
