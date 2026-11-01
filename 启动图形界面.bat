@echo off
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"

set "PYW="
set "PY="

rem 1) 优先 py 启动器（Python 官方安装器会注册）
where py >nul 2>&1
if %errorlevel%==0 (
    for /f "delims=" %%i in ('py -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do (
        if exist "%%i" set "PYW=%%i"
    )
)

rem 2) PATH 里的 pythonw
if not defined PYW (
    for /f "delims=" %%i in ('where pythonw 2^>nul') do (
        if not defined PYW set "PYW=%%i"
    )
)

rem 3) PATH 里的 python
if not defined PYW (
    for /f "delims=" %%i in ('where python 2^>nul') do (
        if not defined PY set "PY=%%i"
    )
)

if defined PYW (
    start "" "%PYW%" "claudectl_gui.py"
    exit /b 0
)

if defined PY (
    start "" "%PY%" "claudectl_gui.py"
    exit /b 0
)

echo.
echo   [X] 未找到 Python
echo.
echo   请先安装 Python 3.8 或更高版本：
echo       https://www.python.org/downloads/
echo.
echo   安装时务必勾选 "Add Python to PATH"
echo.
pause
exit /b 1
