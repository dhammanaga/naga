"""点击 IMA 左侧的 "ima" 按钮，切换到 AI 聊天视图，然后截图 + OCR。"""
import time
import ctypes
import win32gui
import win32con
from PIL import Image
import pyautogui

from ocr_engine import ocr_image


def capture_window(hwnd, out_path):
    PW_RENDERFULLCONTENT = 2
    ctypes.windll.user32.ShowWindow(hwnd, win32con.SW_RESTORE)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.8)
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top

    import win32ui
    hwndDC = win32gui.GetWindowDC(hwnd)
    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
    saveDC = mfcDC.CreateCompatibleDC()
    saveBitMap = win32ui.CreateBitmap()
    saveBitMap.CreateCompatibleBitmap(mfcDC, w, h)
    saveDC.SelectObject(saveBitMap)
    ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), PW_RENDERFULLCONTENT)
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
    return img, (left, top, w, h)


def find_ima_hwnd():
    import uiautomation as auto
    for w in auto.GetRootControl().GetChildren():
        try:
            name = w.Name or ""
            cls = getattr(w, "ClassName", "") or ""
        except Exception:
            continue
        if "ima" in name.lower() or "ima" in cls.lower() or name.endswith(" - ima.copilot"):
            return w.NativeWindowHandle
    return None


def main():
    hwnd = find_ima_hwnd()
    if not hwnd:
        print("没找到 IMA 窗口")
        return
    print(f"hwnd={hwnd}")
    # 初始截图
    capture_window(hwnd, "ima_initial.png")
    print("已保存初始视图 ima_initial.png")
    # 定位 "ima" 按钮：左侧栏第一个图标
    img, (left, top, ww, hh) = capture_window(hwnd, "ima_sidebar.png")
    boxes = ocr_image(img)
    ima_btn = None
    for text, x, y in boxes:
        if text.strip().lower() == "ima":
            ima_btn = (left + x, top + y)
            break
    if not ima_btn:
        print("未找到 'ima' 按钮文字坐标")
        return
    print(f"点击 ima 按钮: {ima_btn}")
    pyautogui.click(ima_btn[0], ima_btn[1])
    time.sleep(2.5)
    # 再次截图
    capture_window(hwnd, "ima_chat.png")
    print("已保存聊天视图 ima_chat.png")
    # OCR 聊天视图
    img, (left, top, ww, hh) = capture_window(hwnd, "ima_chat.png")
    boxes = ocr_image(img)
    print("\n=== AI 聊天视图 OCR 文字 ===")
    for text, x, y in boxes:
        print(f"  {text!r:50} @ ({x}, {y})")


if __name__ == "__main__":
    main()
