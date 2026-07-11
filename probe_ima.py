"""探测本机 IMA 桌面端窗口：强制置前 + 截窗口本身（非屏幕）+ OCR。"""
import sys
import logging
import ctypes
import time
from PIL import ImageGrab, Image

import win32gui
import win32ui
import win32con

import uiautomation as auto
from ocr_engine import ocr_image

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
PW_RENDERFULLCONTENT = 2


def enum_windows():
    root = auto.GetRootControl()
    found = []
    for w in root.GetChildren():
        try:
            name = w.Name or ""
            cls = getattr(w, "ClassName", "") or ""
            hwnd = w.NativeWindowHandle
        except Exception:
            continue
        found.append((name, cls, hwnd))
    return found


def capture_window(hwnd, out_path):
    """用 PrintWindow 截窗口自身画面，即使被其他窗口覆盖也尽量有效。"""
    if not hwnd:
        return None
    # 还原并强制前置
    ctypes.windll.user32.ShowWindow(hwnd, win32con.SW_RESTORE)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.8)

    rect = win32gui.GetWindowRect(hwnd)
    left, top, right, bottom = rect
    w, h = right - left, bottom - top

    hwndDC = win32gui.GetWindowDC(hwnd)
    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
    saveDC = mfcDC.CreateCompatibleDC()
    saveBitMap = win32ui.CreateBitmap()
    saveBitMap.CreateCompatibleBitmap(mfcDC, w, h)
    saveDC.SelectObject(saveBitMap)
    result = ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), PW_RENDERFULLCONTENT)

    bmpinfo = saveBitMap.GetInfo()
    bmpstr = saveBitMap.GetBitmapBits(True)
    img = Image.frombuffer(
        "RGB",
        (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
        bmpstr,
        "raw",
        "BGRX",
        0,
        1,
    )

    win32gui.DeleteObject(saveBitMap.GetHandle())
    saveDC.DeleteDC()
    mfcDC.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwndDC)

    img.save(out_path)
    print(f"已保存窗口截图 {out_path} ({img.size}), PrintWindow 结果={result}")
    return img, (left, top, w, h)


def main():
    print("=== 枚举顶层窗口（含 IMA/Tencent/腾讯）===")
    wins = enum_windows()
    ima_like = []
    for name, cls, hwnd in wins:
        if not name and not cls:
            continue
        low = (name + cls).lower()
        if any(k in low for k in ["ima", "tencent", "腾讯", "qq", "微信", "weixin", "wechat"]):
            ima_like.append((name, cls, hwnd))
    print(f"匹配到 {len(ima_like)} 个候选窗口：")
    for name, cls, hwnd in ima_like:
        print(f"  Name={name!r:50} Class={cls!r:30} hwnd={hwnd}")

    # 直接定位 IMA 窗口
    candidates = []
    for w in auto.GetRootControl().GetChildren():
        try:
            name = w.Name or ""
            cls = getattr(w, "ClassName", "") or ""
            hwnd = w.NativeWindowHandle
        except Exception:
            continue
        if "ima" in name.lower() or "ima" in cls.lower() or name.endswith(" - ima.copilot"):
            candidates.append((w, hwnd))

    if not candidates:
        print("\n[WARN] 没找到 IMA 窗口。请先打开 IMA 桌面端并保持可见，再运行本探测。")
        img = ImageGrab.grab()
        img.save("ima_probe_screen.png")
        print(f"已保存整屏截图到 ima_probe_screen.png ({img.size})")
        return

    w, hwnd = candidates[0]
    print(f"\n[OK] 定位到窗口：Name={w.Name!r} Class={getattr(w,'ClassName','')!r} hwnd={hwnd}")

    img, (left, top, ww, hh) = capture_window(hwnd, "ima_probe_window.png")
    if img is None:
        return

    print("\n=== OCR 识别到的文字（文本, 相对窗口 x, 相对窗口 y）===")
    boxes = ocr_image(img)
    for text, x, y in boxes:
        print(f"  {text!r:60} @ ({x}, {y})")
    print(f"\n共识别 {len(boxes)} 个文本块。")
    print(f"若文字明显是其他窗口，说明 PrintWindow 未捕获到 IMA 自身，请确保 IMA 已完全渲染。")


if __name__ == "__main__":
    main()
