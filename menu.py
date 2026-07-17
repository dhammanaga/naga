# -*- coding: utf-8 -*-
# ↑ 这一行告诉 Python：本文件用 UTF-8 编码保存，这样中文注释不会乱码。

"""WeChat -> IMA 自动应答 启动器（Python 版，不依赖 .bat 关联）。

直接用 venv 里的 python 运行 wechat_ima_monitor.py，并提供菜单。
双击指向本文件的桌面快捷方式即可使用。
"""
# ↑ 三引号包起来的是「模块说明」（docstring），解释这个文件是干嘛的：
#   它是整个程序的"总开关 / 菜单界面"。你双击运行它，就会看到一排数字选项，
#   选一个数字，它就替你启动真正干活的那个程序（wechat_ima_monitor.py）。
#   这样做的好处：不懂命令行的用户，也能靠"按数字键"来使用全部功能。

import os         # 导入 os：处理文件路径、判断文件是否存在
import subprocess # 导入 subprocess：用来"在 Python 里再启动另一个程序"
import sys        # 导入 sys：提供系统相关信息（本文件暂未直接用，保留备用）


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# ↑ 算出"本文件所在文件夹"的绝对路径，存进 SCRIPT_DIR。
#   为什么要算这个？因为下面要找 venv 和主程序，用"相对本文件夹"的路径最稳妥，
#   无论你把这个项目放电脑哪个盘，都能正确找到，不会因为路径写死而出错。
PY = os.path.join(SCRIPT_DIR, "venv", "Scripts", "python.exe")
# ↑ 拼出"虚拟环境里的 python 解释器"完整路径：
#   venv 是项目专属的 Python 运行环境，Scripts\python.exe 是里面的执行程序。
MON = os.path.join(SCRIPT_DIR, "wechat_ima_monitor.py")
# ↑ 拼出"真正干活的主程序"完整路径：wechat_ima_monitor.py。


def run(args):
    # ↑ 定义一个函数 run（运行），参数 args 是"要传给主程序的额外指令"（比如 --live）。
    if not os.path.exists(PY):
        # ↑ 先检查：虚拟环境的 python.exe 在不在？不存在说明环境没装好。
        print("[错误] 找不到 venv\\Scripts\\python.exe，请先运行 install_windows.bat 安装环境。")
        # ↑ 打印一句人话提示：请先安装环境。install_windows.bat 是安装脚本。
        return
        # ↑ 既然环境都没了，就没法继续，直接返回（结束这个函数）。
    subprocess.run([PY, MON] + args, cwd=SCRIPT_DIR)
    # ↑ 真正启动主程序：
    #   [PY, MON] + args = 一条命令 = "用 venv 的 python 去运行 主程序，并带上用户选的参数"
    #   cwd=SCRIPT_DIR = 把"工作目录"设为本文件夹，保证主程序能找到它的配置文件。


def main():
    # ↑ 定义主函数 main：这就是菜单的"主体循环"，负责不断显示菜单、等待你选。
    while True:
        # ↑ 一个无限循环：每转一圈就显示一次菜单，等你选完再回来显示下一次，
        #   直到你选"0 退出"才用 break 跳出。while True 就是"一直循环"的意思。
        print("=" * 52)
        # ↑ 打印 52 个等号，当作菜单顶部的装饰线，让界面更好看。
        print("     WeChat -> IMA 自动问答（自然语言回答） 启动器")
        # ↑ 打印菜单标题。
        print("=" * 52)
        # ↑ 再打印一条装饰线，和顶部呼应，框住标题。
        print("  前提：微信/IMA 桌面端已安装并登录过（未运行会自动启动，群会自动搜索打开）。")
        # ↑ 打印一句前提说明：用之前得先装好并登录微信和 IMA。
        print()
        # ↑ 打印一个空行，和下面的选项之间留点空隙。
        print("    1) 列出当前微信会话名")
        print("    2) 验证模式（只读不回，生成 IMA 回答截图但不发）")
        print("    3) 正式运行（真正回消息，Ctrl+C 停止）")
        print("    4) 打印微信控件树（读不准时用来排查）")
        print("    5) 截图存盘+画OCR框（微信坐标微调）")
        print("    6) IMA 坐标校准（截图 IMA 窗口并标注文字位置）")
        print("    7) 单测 IMA 回答（问一个问题，保存截图）")
        print("    0) 退出")
        # ↑ 上面这 8 行就是菜单选项，列出你能做的事，以及对应的数字。
        print()
        # ↑ 又是一个空行，隔开选项和下面的输入提示。

        try:
            # ↑ try 保护：下面要"等用户在键盘上输入"，如果用户直接关闭窗口(Ctrl+Z/D 之类)，
            #   会触发 EOFError（输入结束错误），我们用 except 接住它，优雅退出。
            c = input("请选择 (0-7): ").strip()
            # ↑ 显示提示"请选择 (0-7): "，等用户输入数字，.strip() 去掉首尾可能多打的空格。
        except EOFError:
            # ↑ 如果用户没有输入就结束了（EOF 错误），就跳出循环，结束程序。
            break
        if c == "1":
            # ↑ 如果用户输入的是 "1"……
            run(["--list-groups"])
            # ↑ 调用 run，传给主程序参数 --list-groups（意思是：列出当前微信有哪些会话）。
        elif c == "2":
            # ↑ 否则如果输入 "2"（验证模式：只跑流程不真发消息）……
            run(["--once"])
            # ↑ 传 --once 参数（跑一次看看效果，生成截图但不真正回复）。
        elif c == "3":
            # ↑ 否则如果输入 "3"（正式运行：真的去回复群消息）……
            run(["--live"])
            # ↑ 传 --live 参数（正式上线运行，Ctrl+C 可随时停止）。
        elif c == "4":
            # ↑ 否则如果输入 "4"（排查用：打印控件树）……
            run(["--calibrate"])
            # ↑ 传 --calibrate 参数（把微信界面里各按钮的"名字和位置"打印出来，方便调试）。
        elif c == "5":
            # ↑ 否则如果输入 "5"（微信截图 + 画 OCR 识别框，用来微调坐标）……
            run(["--screenshot"])
            # ↑ 传 --screenshot 参数（截图并存盘，顺便把认出的字框出来）。
        elif c == "6":
            # ↑ 否则如果输入 "6"（IMA 窗口坐标校准）……
            run(["--ima-calibrate"])
            # ↑ 传 --ima-calibrate 参数（截图 IMA 窗口并标注文字位置，帮助校准点击坐标）。
        elif c == "7":
            # ↑ 否则如果输入 "7"（单独问 IMA 一个问题做测试）……
            q = input("请输入要测试的问题: ").strip()
            # ↑ 再问一次：你想问什么？把问题读进来、去掉首尾空格，存进 q。
            if q:
                # ↑ 只有当你确实输入了内容（q 不是空），才继续。
                run(["--ask-ima", q])
                # ↑ 传 --ask-ima 参数 + 你的问题 q，让主程序去问 IMA 并把回答截图保存。
        elif c == "0":
            # ↑ 否则如果输入 "0"（退出）……
            break
            # ↑ 跳出 while True 循环，菜单结束，程序退出。
        else:
            # ↑ 如果输入的不是 0-7 里的任何一个数字……
            print("无效选择。")
            # ↑ 提示"无效选择"，然后循环会再转一圈重新显示菜单。

        try:
            # ↑ 再套一层 try：等你按回车返回菜单。
            input("按回车返回菜单...")
            # ↑ 显示"按回车返回菜单..."，你按一下回车，程序继续、重新显示菜单。
        except EOFError:
            # ↑ 同样，如果这里输入被中断（EOF），就跳出循环结束。
            break


if __name__ == "__main__":
    # ↑ 这是 Python 的固定写法：当"直接双击运行本文件"时，下面这行才执行 main()；
    #   如果是被别的文件 import 引入，就不会自动跑。保证入口清晰、不会乱执行。
    main()
    # ↑ 启动菜单主函数，开始接待用户选择。
