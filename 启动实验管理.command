#!/bin/bash
set -e
task_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
task_python="$task_dir/.runtime/bin/python"
if [ ! -x "$task_python" ]; then
  task_base=""
  for task_candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$task_candidate" >/dev/null 2>&1 && "$task_candidate" -c 'import sys; raise SystemExit(not ((3,10) <= sys.version_info[:2] <= (3,13)))' 2>/dev/null; then
      task_base="$task_candidate"
      break
    fi
  done
  if [ -z "$task_base" ]; then
    echo '请先安装标准 CPython 3.10–3.13，然后重新双击启动。'
    read -r -p '按回车关闭…'
    exit 1
  fi
  "$task_base" -m venv "$task_dir/.runtime"
fi
if ! "$task_python" "$task_dir/lab_manager.py" --check; then
  echo '正在本电脑安装运行依赖，需要可访问 Python 包源的网络…'
  "$task_python" -m pip install -r "$task_dir/requirements-release.txt"
  "$task_python" "$task_dir/lab_manager.py" --check
fi
exec "$task_python" "$task_dir/lab_manager.py" "$@"
