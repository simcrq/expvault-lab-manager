@echo off
setlocal
chcp 65001 >nul
set "task_dir=%~dp0"
set "task_python=%~dp0.runtime\Scripts\python.exe"
if exist "%task_python%" goto check_deps
where py >nul 2>nul
if errorlevel 1 goto use_python
for %%V in (3.13 3.12 3.11 3.10) do (
  py -%%V -c "import sys; raise SystemExit(0)" >nul 2>nul
  if not errorlevel 1 (
    py -%%V -m venv "%task_dir%.runtime"
    goto check_venv
  )
)
:use_python
python -c "import sys; raise SystemExit(not ((3,10) <= sys.version_info[:2] <= (3,13)))" >nul 2>nul
if errorlevel 1 goto need_python
python -m venv "%task_dir%.runtime"
:check_venv
if errorlevel 1 goto failed
:check_deps
"%task_python%" "%task_dir%lab_manager.py" --check
if not errorlevel 1 goto launch
echo 正在本电脑安装运行依赖，需要可访问 Python 包源的网络…
"%task_python%" -m pip install -r "%task_dir%requirements-release.txt"
if errorlevel 1 goto failed
"%task_python%" "%task_dir%lab_manager.py" --check
if errorlevel 1 goto failed
:launch
"%task_python%" "%task_dir%lab_manager.py" %*
if errorlevel 1 goto failed
exit /b 0
:need_python
echo 请先安装标准 CPython 3.10–3.13，并勾选 Add Python to PATH，然后重新启动。
:failed
echo 实验管理系统未能启动，请检查上方错误信息。
pause
exit /b 1
