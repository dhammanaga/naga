# -*- coding: utf-8 -*-
# ↑ 这一行告诉 Python：本文件用 UTF-8 编码保存，这样中文注释不会乱码。

"""硬性超时守护：保证任何 live UI 驱动操作都不会永久卡死（方法11/18 的工程落地）。

网上调研共识（uiautomation 官方文档 / CSDN / 51CTO）：
  * 用 pyautogui 按坐标驱动网页内核类桌面应用极易卡死；
  * 根治手段 = 给每一步都设超时、用 Exists() 轮询、捕获 COMError、管理员运行。
本模块是「绝不让脚本永久挂起」的最后一道保险：即使某个 C 层调用（截图 BitBlt 等）
意外阻塞，到点也向主线程注入 IMATimeout 强制中断。
"""
# ↑ 三引号包起来的是「模块说明」（docstring），解释这个文件是干嘛的：
#   它是一个「保险丝」，专门防止程序在操作界面时永久卡死。
#   原理：给一段操作规定「最多允许花多少秒」，超过就强制打断它。

import ctypes      # 导入 ctypes：让 Python 能调用 Windows 系统底层功能（这里用于向线程"注入"一个中断）
import threading   # 导入 threading：多线程工具。我们用它开一个"计时闹钟"在后台数秒
import logging     # 导入 logging：记录日志用（本文件目前未直接用到，保留以备扩展）


class IMATimeout(Exception):
    """硬性超时异常：用于打断永久卡死的 UI 驱动。"""
    # ↑ 定义一个「自定义异常」类型，名字叫 IMATimeout（IMA 超时）。
    #   「异常」就是程序里的"报错信号"。当操作超时，我们就抛出这个信号，
    #   外层代码看到这个特定信号，就知道"哦，是超时了"，而不是别的错误。
    pass  # pass 表示"这里没有额外内容"，这个异常类只需要一个名字就够用了


class hard_timeout:
    """上下文管理器：进入后启动一个 daemon 计时线程，超过 seconds 就向主线程
    注入 IMATimeout。无论主线程卡在 time.sleep 还是 C 层调用，都会在回到 Python
    时中断。正常完成时计时器被取消，不影响性能。

    用法：
        try:
            with hard_timeout(180, "ask('...')"):
                do_risky_ui_stuff()
        except IMATimeout:
            return None  # 明确知道是超时中断，而非普通异常
    """
    # ↑ 这个类是本文件的主角，叫「上下文管理器」，配合 Python 的 with 语句使用。
    #   通俗理解：把一段"可能卡住"的代码放进 with hard_timeout(180): 里面，
    #   它就会自动帮你计时；超过 180 秒还没做完，就强制打断那段代码。

    def __init__(self, seconds, label="操作"):
        # ↑ __init__ 是「初始化方法」：创建这个计时器时最先执行，用来记住参数。
        #   seconds = 允许的最长秒数；label = 给这次操作起的名字（方便日志辨认）。
        self.seconds = float(seconds) if seconds else 0.0
        # ↑ 把传入的秒数转成小数存起来。如果没传（seconds 为空/0），就存 0.0，
        #   0.0 代表"不设超时"（后面会据此跳过计时）。
        self.label = label      # 记住这次操作的名字
        self._timer = None      # 先占个位：稍后这里会放"计时闹钟"对象，现在还没有，先设为空
        self._tid = None        # 先占个位：稍后这里会放"主线程的身份证号"，现在还没有，先设为空

    def _fire(self):
        # ↑ _fire 是"闹钟响了要做的事"：时间一到，这个方法会被自动调用。
        #   它的任务是：向主线程扔出 IMATimeout 信号，把卡住的操作打断。
        if self._tid is None:
            return
        # ↑ 如果没记下主线程身份证号（说明没真正启动计时），就什么都不做，直接返回。
        try:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                self._tid, ctypes.py_object(IMATimeout))
            # ↑ 这是核心一招：调用 Python 底层接口，向"身份证号为 self._tid 的线程"
            #   （也就是主线程）强行塞入一个 IMATimeout 异常。
            #   效果：即使主线程正卡在某个操作里，等它回到 Python 层就会立刻"报超时"。
        except Exception:
            pass
        # ↑ 万一上面这步本身出错，也不让它连累程序崩溃——静默忽略（pass）。

    def __enter__(self):
        # ↑ __enter__ 是「进入 with 代码块时」自动执行的方法：在这里启动计时。
        if self.seconds > 0:
            # ↑ 只有当规定了大于 0 的秒数时，才真正启动计时（0 表示不限时）。
            self._tid = threading.current_thread().ident
            # ↑ 记下"当前线程（主线程）的身份证号"，供闹钟响时精确打断它。
            self._timer = threading.Timer(self.seconds, self._fire)
            # ↑ 创建一个"计时闹钟"：等待 self.seconds 秒后，自动执行 self._fire（打断操作）。
            self._timer.daemon = True
            # ↑ 把闹钟设为"守护线程"：意思是主程序退出时，这个后台闹钟会跟着一起结束，
            #   不会赖着不走、拖住程序关闭。
            self._timer.start()
            # ↑ 启动闹钟，开始倒计时。
        return self
        # ↑ 返回自己，让 with ... as x 语法里的 x 能拿到这个对象（本例用不到，但这是规范写法）。

    def __exit__(self, exc_type, exc, tb):
        # ↑ __exit__ 是「离开 with 代码块时」自动执行的方法：无论正常做完还是中途报错，都会执行。
        #   三个参数记录了"离开时是否发生了异常"（发生了什么类型的异常等），这里我们只需判断要不要撤销计时。
        if self._timer is not None:
            self._timer.cancel()
        # ↑ 如果之前启动过闹钟，就把它取消——因为操作已经结束（正常做完或已报错），
        #   不再需要"到点打断"了。这样正常完成时就不会白白触发超时。
        # 不吞异常：IMATimeout 由外层 except 处理；普通异常照常上抛
        return False
        # ↑ 返回 False 表示"我不处理这里冒出来的异常，原样向外传递"。
        #   这样超时信号(IMATimeout)会被 with 外层的 except 抓到并妥善处理，
        #   其它普通错误也照常往外报，不会被这里悄悄藏起来。
