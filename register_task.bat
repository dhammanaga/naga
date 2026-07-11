@echo off
chcp 65001 >nul
set "HERE=%~dp0"
set "PY=%HERE%venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
echo 注册 Windows 任务计划：每 10 分钟运行一次（--once 模式，更抗崩溃、重启后自启）。
schtasks /create /tn "WeChatIMAMonitor" /tr "\"%PY%\" \"%HERE%wechat_ima_monitor.py\" --once" /sc minute /mo 10 /f
if errorlevel 1 (
    echo 注册失败，请以管理员身份运行本文件。
    pause
    exit /b 1
)
echo 已注册成功。可用 「任务计划程序」查看 / 删除该任务。
echo 若想改成常驻循环（更及时但进程需常开），改跑 run_persistent.bat 并加入开机启动。
pause
