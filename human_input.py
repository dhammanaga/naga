# -*- coding: utf-8 -*-
"""拟人化输入封装：所有窗口交互通过「真实鼠标 / 键盘模拟」完成，避免程序化注入
（OLE DoDragDrop / 剪贴板直写 / AttachThreadInput + SetWindowPos(TOPMOST) 强制置前
等）被微信 / IMA 风控识别。

设计原则：
  * 鼠标点击带「移动轨迹 + 微抖动 + 随机停顿」，模拟人类手不稳与反应延迟；
  * 文本输入：中文 / 含非 ASCII 字符走剪贴板(Ctrl+V)（中文输入法下唯一可靠），
    纯 ASCII 逐字 typewrite 更像真人敲键盘；
  * 窗口激活用「真实鼠标点击标题栏」，让 Windows 自然把窗口提到前台，
    不使用任何强制置前 / 线程绑定的强风控 API。
"""
import time
import random
import ctypes

import pyautogui
import pyperclip

pyautogui.FAILSAFE = True


def human_delay(lo=0.3, hi=1.2):
    """随机停顿，模拟人类思考 / 反应延迟。"""
    time.sleep(random.uniform(lo, hi))


def human_click(x, y, button="left", clicks=1):
    """带移动轨迹与微抖动的真实鼠标点击，模拟人类点击。"""
    x, y = int(x), int(y)
    # 先移动过去（带随机轨迹时长与微小偏移），再点
    pyautogui.moveTo(
        x + random.randint(-2, 2),
        y + random.randint(-2, 2),
        duration=random.uniform(0.15, 0.45),
        tween=pyautogui.easeOutQuad,
    )
    time.sleep(random.uniform(0.05, 0.2))
    pyautogui.click(x, y, button=button, clicks=clicks)
    time.sleep(random.uniform(0.1, 0.3))


def human_double_click(x, y):
    human_click(x, y, clicks=2)


def human_type(text, chinese=True):
    """输入文本。中文 / 含非 ASCII 时走剪贴板(Ctrl+V)，否则逐字 typewrite 像真人。"""
    if not text:
        return
    if chinese and any(ord(ch) > 127 for ch in text):
        pyperclip.copy(text)
        time.sleep(random.uniform(0.05, 0.15))
        pyautogui.hotkey("ctrl", "v")
    else:
        pyautogui.typewrite(text, interval=random.uniform(0.04, 0.12))
    time.sleep(random.uniform(0.1, 0.3))


def human_send_enter():
    pyautogui.press("enter")
    time.sleep(random.uniform(0.2, 0.5))


def is_foreground(hwnd):
    """当前窗口是否已是前台窗口。"""
    try:
        return int(ctypes.windll.user32.GetForegroundWindow()) == int(hwnd)
    except Exception:
        return False


def bring_to_front(hwnd):
    """把目标窗口可靠地提到最前（用于重叠布局：单纯点标题栏会被别的窗口挡住）。

    关键：SetWindowPos(hwnd, HWND_TOP) 一次性置顶（z 序提到最上，但不设 TOPMOST，
    不会永远置顶、不绑定线程）。这步始终有效，保证重叠时该窗口绘制在最上层——
    截图与点击都落在它身上，不会被 IMA/其它窗口压住。
    SetForegroundWindow 仅尽力拿到键盘焦点（被系统前台锁限制时可能失败，但 z 序已
    置顶，不影响后续「点输入框」获得焦点与截图正确性）。对微信 / IMA 风控无害。
    不再回退到「点标题栏」——重叠布局下标题栏坐标可能被别的窗口覆盖，点到错窗口。
    """
    if not hwnd:
        return False
    hwnd = int(hwnd)
    try:
        if ctypes.windll.user32.IsIconic(hwnd):
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # 先还原最小化
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)
        try:
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
    except Exception:
        return False
    return True


def human_activate_titlebar(hwnd, rect):
    """模拟人类点击窗口标题栏中部，把窗口自然带到前台。

    rect 为 (left, top, right, bottom, w, h)。仅当窗口尚未处于前台时才点击，
    避免重复激活（人类不会对已聚焦窗口再点一次）。
    不使用 SetForegroundWindow / AttachThreadInput / SetWindowPos(TOPMOST)。
    """
    if not hwnd:
        return
    hwnd = int(hwnd)
    try:
        # 还原最小化（普通 API，不抢前台锁）
        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    except Exception:
        pass
    if is_foreground(hwnd):
        return
    try:
        left, top, right, bottom, w, h = rect
    except Exception:
        return
    tx = int(left + w * 0.5)
    ty = int(top + max(8, int(h * 0.025)))
    human_click(tx, ty)
