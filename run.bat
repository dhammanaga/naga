@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM 若虚拟环境不存在，自动先安装依赖
if not exist "venv\Scripts\python.exe" (
    echo 未检测到虚拟环境，正在自动安装依赖（首次需要联网）...
    call install_windows.bat
    if not exist "venv\Scripts\python.exe" (
        echo 安装失败，请查看上方错误信息。
        pause
        exit /b 1
    )
)

:menu
cls
echo ============================================================
echo    WeChat -^> IMA 自动应答  启动器
echo ============================================================
echo    前提：微信 Windows 桌面端已登录，目标群已可见/置顶。
echo.
echo    1^) 列出当前微信会话名（用来确认要填的群名）
echo    2^) 验证模式（只读不回，看日志确认读得准）
echo    3^) 正式运行（真正回消息，Ctrl+C 停止）
echo    4^) 打印微信控件树（读不准时用它排查）
echo    0^) 退出
echo.
set /p choice="请选择 (0-4): "

if "!choice!"=="1" venv\Scripts\python.exe wechat_ima_monitor.py --list-groups
if "!choice!"=="2" venv\Scripts\python.exe wechat_ima_monitor.py
if "!choice!"=="3" venv\Scripts\python.exe wechat_ima_monitor.py --live
if "!choice!"=="4" venv\Scripts\python.exe wechat_ima_monitor.py --calibrate
if "!choice!"=="0" exit /b 0

echo.
echo 按任意键返回菜单...
pause >nul
goto menu
