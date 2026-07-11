# -*- coding: utf-8 -*-
"""WeChat -> IMA 自动应答 启动器（Python 版，不依赖 .bat 关联）。

直接用 venv 里的 python 运行 wechat_ima_monitor.py，并提供菜单。
双击指向本文件的桌面快捷方式即可使用。
"""
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(SCRIPT_DIR, "venv", "Scripts", "python.exe")
MON = os.path.join(SCRIPT_DIR, "wechat_ima_monitor.py")


def run(args):
    if not os.path.exists(PY):
        print("[错误] 找不到 venv\\Scripts\\python.exe，请先运行 install_windows.bat 安装环境。")
        return
    subprocess.run([PY, MON] + args, cwd=SCRIPT_DIR)


def main():
    while True:
        print("=" * 52)
        print("     WeChat -> IMA 自动问答（自然语言回答） 启动器")
        print("=" * 52)
        print("  前提：微信/IMA 桌面端已安装并登录过（未运行会自动启动，群会自动搜索打开）。")
        print()
        print("    1) 列出当前微信会话名")
        print("    2) 验证模式（只读不回，生成 IMA 回答截图但不发）")
        print("    3) 正式运行（真正回消息，Ctrl+C 停止）")
        print("    4) 打印微信控件树（读不准时用来排查）")
        print("    5) 截图存盘+画OCR框（微信坐标微调）")
        print("    6) IMA 坐标校准（截图 IMA 窗口并标注文字位置）")
        print("    7) 单测 IMA 回答（问一个问题，保存截图）")
        print("    0) 退出")
        print()
        try:
            c = input("请选择 (0-7): ").strip()
        except EOFError:
            break
        if c == "1":
            run(["--list-groups"])
        elif c == "2":
            run(["--once"])
        elif c == "3":
            run(["--live"])
        elif c == "4":
            run(["--calibrate"])
        elif c == "5":
            run(["--screenshot"])
        elif c == "6":
            run(["--ima-calibrate"])
        elif c == "7":
            q = input("请输入要测试的问题: ").strip()
            if q:
                run(["--ask-ima", q])
        elif c == "0":
            break
        else:
            print("无效选择。")
        try:
            input("按回车返回菜单...")
        except EOFError:
            break


if __name__ == "__main__":
    main()
