# -*- coding: utf-8 -*-
"""调试：打印微信窗口矩形，并保存全窗口截图，确认能否抓到内容。"""
import logging
import uiautomation as auto
from PIL import ImageGrab

logging.basicConfig(level=logging.INFO)

w = auto.WindowControl(Name="微信")
if not w.Exists(3):
    w = auto.WindowControl(ClassName="WeChatMainWndForPC")
print("EXISTS:", w.Exists(3))
r = w.BoundingRectangle
print("RECT left=%d top=%d right=%d bottom=%d" % (r.left, r.top, r.right, r.bottom))
print("W=%d H=%d" % (r.width(), r.height()))

# 全窗口截图
img = ImageGrab.grab((r.left, r.top, r.right, r.bottom))
print("FULL IMG size:", img.size)
img.save("debug_window.png")
print("saved debug_window.png")

# 侧边栏子区域
sb = 0.26
img2 = ImageGrab.grab((r.left, int(r.top + r.height()*0.07), int(r.left + r.width()*sb), r.bottom))
print("SIDEBAR IMG size:", img2.size)
img2.save("debug_sidebar.png")
print("saved debug_sidebar.png")
