@echo off
chcp 65001 >nul
call venv\Scripts\activate.bat
start "" python wechat_ima_monitor.py
echo 已在后台启动持久监控循环（每 10 分钟一轮，dry_run 由 config.json 控制）。
echo 关闭请结束对应 python 进程。
pause
