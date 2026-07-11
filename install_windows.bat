@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM =========================================================================
REM  WeChat -> IMA 监控程序  ·  环境安装（不依赖你本机是否装了 Python）
REM  优先使用 WorkBuddy 自带的 Python，找不到再退而求其次用系统 py / python
REM =========================================================================

set "PYEXE="
set "PYARG="

if exist "C:\Users\27659\.workbuddy\binaries\python\versions\3.13.12\python.exe" (
    set "PYEXE=C:\Users\27659\.workbuddy\binaries\python\versions\3.13.12\python.exe"
    echo [OK] 使用 WorkBuddy 自带 Python：!PYEXE!
) else (
    where py >nul 2>nul
    if not errorlevel 1 (
        set "PYEXE=py"
        set "PYARG=-3"
        echo [OK] 使用系统 py 启动器
    ) else (
        where python >nul 2>nul
        if not errorlevel 1 (
            set "PYEXE=python"
            echo [OK] 使用系统 python
        )
    )
)

if not defined PYEXE (
    echo [错误] 找不到任何 Python。二选一：
    echo   方案A：安装 Python 3.10+ 并勾选 "Add to PATH"  https://www.python.org/downloads/
    echo   方案B：确认 WorkBuddy 已安装（自带 Python 在 C:\Users\27659\.workbuddy\...）
    pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo [1/3] 创建虚拟环境...
    "!PYEXE!" !PYARG! -m venv venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败，请检查磁盘空间或权限。
        pause
        exit /b 1
    )
) else (
    echo [1/3] 虚拟环境已存在，跳过。
)

echo [2/3] 安装依赖（uiautomation / pillow / pyperclip）...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip -q
pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] 安装依赖失败，请检查网络后重试。
    pause
    exit /b 1
)

echo [3/3] 完成！
echo.
echo 下一步：
echo   1^) 登录微信 Windows 桌面端（程序不存密码，需你已登录）。
echo   2^) 列出群名：双击 run.bat 选 1，或命令行 venv\Scripts\python wechat_ima_monitor.py --list-groups
echo   3^) 把群名填进 config.json 的 monitored_groups。
echo   4^) 先验证（默认不发消息）：run.bat 选 2
echo   5^) 确认无误后正式运行：run.bat 选 3
echo.
pause
