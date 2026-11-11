#!/usr/bin/env python3
"""Portable web application entry; creates a fresh vault beside the program."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent


def verify_package(folder=APP_DIR):
    manifest = folder / "release-manifest.json"
    if not manifest.is_file():
        if (folder / "VERSION").is_file():
            raise ValueError("发布清单缺失，请重新解压原始发布包")
        return "development"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    for relative, expected in value["files"].items():
        path = folder / relative
        if not path.resolve().is_relative_to(folder.resolve()) or not path.is_file() or path.is_symlink():
            raise ValueError(f"发布文件缺失或路径无效：{relative}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"发布文件校验失败：{relative}，请重新解压原始发布包")
    return value["version"]


def main(argv=None):
    parser = argparse.ArgumentParser(description="独立实验管理系统：首次启动创建自己的空实验库")
    version_path = APP_DIR / "VERSION"
    parser.add_argument("--version", action="version", version="实验管理系统 " + (version_path.read_text(encoding="utf-8").strip() if version_path.is_file() else "development"))
    parser.add_argument("--root", type=Path, default=APP_DIR / "实验库", help="实验库目录，默认程序旁的 实验库/")
    parser.add_argument("--host", default="127.0.0.1", help="默认仅本机；局域网访问须指定私有 IPv4 地址")
    parser.add_argument("--port", type=int, default=0, help="默认自动分配空闲端口")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--check", action="store_true", help="检查程序完整性与依赖，不创建实验库")
    args = parser.parse_args(argv)
    if not (3, 10) <= sys.version_info[:2] <= (3, 13):
        print("此发布版需要标准 CPython 3.10–3.13。", file=sys.stderr)
        return 2
    try:
        version = verify_package()
        from ev import config
        from ev.web import run
        from ev.util import setup_stdout
        from filelock import FileLock
        import openpyxl  # release requirements include native Excel export
        import cryptography  # ensure the destination installed the TLS pairing dependency

        setup_stdout()
        if args.check:
            print(f"实验管理系统 {version}：程序完整性与依赖检查通过；未创建实验库。")
            return 0
        root = args.root.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        state = root / ".expvault"
        if state.is_symlink():
            raise ValueError("实验库状态目录不能使用符号链接")
        state.mkdir(exist_ok=True)
        with FileLock(str(state / "bootstrap.lock"), timeout=30):
            if not (root / config.CONFIG_NAME).exists():
                config.init_repo(root, project="默认实验库")
                print("已创建空实验库：" + str(root))
            else:
                config.load(root)
        print(f"实验管理系统 {version}；数据保存在：{root}")
        return run(root, host=args.host, port=args.port, open_browser=not args.no_open)
    except ModuleNotFoundError as exc:
        print(f"缺少依赖 {exc.name}。请在目标电脑运行：\n"
              f'  "{sys.executable}" -m pip install -r "{APP_DIR / "requirements-release.txt"}"', file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
