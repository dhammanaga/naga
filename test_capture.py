# -*- coding: utf-8 -*-
"""只读测试：截微信窗口并 OCR，打印识别到的文字与屏幕坐标。

用于校准前验证「截图 + OCR」能否从你的微信里读出会话名/消息。
不会点击、不会发送。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wechat_ima_monitor import load_config, setup_logging
from wechat_ocr import OCRWeChatController


def main():
    cfg = load_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
    setup_logging(cfg)
    c = OCRWeChatController(cfg)
    if not c.find_window():
        print("NO_WECHAT_WINDOW")
        return
    print("\n===== 左侧会话列表（OCR）=====")
    for name in c.list_visible_groups():
        print("  -", name)
    print("\n===== 当前聊天区消息（OCR，从上到下）=====")
    for msg in c.read_messages():
        print("  |", msg)
    print("\n（这是只读测试，未做任何点击/发送）")


if __name__ == "__main__":
    main()
