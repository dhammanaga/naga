# -*- coding: utf-8 -*-
"""用 comtypes + 动态包装生成桌面 .lnk 快捷方式。"""
import os
import comtypes.client
from comtypes.client import dynamic

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
PROJ = r"D:\workbuddy\2026-07-11-04-40-57\wechat-ima-monitor"
TARGET = os.path.join(PROJ, "venv", "Scripts", "python.exe")
ARGS = "wechat_ima_monitor.py --auto"
LINK = os.path.join(DESKTOP, "微信IMA监控.lnk")

ws = comtypes.client.CreateObject("WScript.Shell")
sc = ws.CreateShortcut(LINK)
sc = dynamic.Dispatch(sc)
sc.TargetPath = TARGET
sc.Arguments = ARGS
sc.WorkingDirectory = PROJ
sc.Description = "WeChat -> IMA 自动应答启动器"
sc.WindowStyle = 1
sc.Save()
print("CREATED:", LINK)
print("EXISTS:", os.path.exists(LINK))
