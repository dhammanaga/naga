@echo off
REM 全自动监控启动器：先自检（开群+IMA出图），通过则进入每 10 分钟一轮的循环。
cd /d %~dp0
call venv\Scripts\activate.bat
python wechat_ima_monitor.py --auto
if errorlevel 1 (
  echo.
  echo [错误] 程序退出。请查看 logs\monitor.log 与 debug\ 目录。
  echo 若 debug\ 下已生成带标注的截图，把整个 debug\ 文件夹发回即可定位修复。
  pause
)
