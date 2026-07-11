"""测试：在 IMA 桌面端问一个佛学问题，截图并 OCR 回答。"""
import time
import ctypes
import win32gui
import win32con
import win32ui
from PIL import Image
import pyautogui
import pyperclip

from ocr_engine import ocr_image

PW_RENDERFULLCONTENT = 2
pyautogui.FAILSAFE = True


def capture_window(hwnd, out_path):
    ctypes.windll.user32.ShowWindow(hwnd, win32con.SW_RESTORE)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.8)
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top
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


def ocr_window(img, region=None):
    if region:
        left, top, right, bottom = region
        img = img.crop((left, top, right, bottom))
    return ocr_image(img)


def click_ocr_text(img, win_rect, target_text, case=True):
    """在窗口中查找目标文字并点击其中心。返回是否点击成功。"""
    left, top, ww, hh = win_rect
    for text, x, y in ocr_image(img):
        t = text.strip()
        if (case and t == target_text) or (not case and t.lower() == target_text.lower()):
            sx = left + x
            sy = top + y
            print(f"  点击文字 '{target_text}' @ ({sx}, {sy})")
            pyautogui.click(sx, sy)
            return True
    return False


def main():
    hwnd = find_ima_hwnd()
    if not hwnd:
        print("没找到 IMA 窗口")
        return
    print(f"hwnd={hwnd}")

    # 先切换到 AI 聊天视图（点击左侧 ima）
    img, rect = capture_window(hwnd, "ima_test_0.png")
    left, top, ww, hh = rect
    if not click_ocr_text(img, rect, "ima", case=False):
        print("未找到 'ima' 入口")
        return
    time.sleep(2.0)

    # 再确认进入“问问ima” tab（如果还没在的话）
    img, rect = capture_window(hwnd, "ima_test_1.png")
    if not click_ocr_text(img, rect, "问问ima"):
        print("未找到 '问问ima' tab，可能已在该视图")
    else:
        time.sleep(1.5)

    # 点击输入框占位文字
    img, rect = capture_window(hwnd, "ima_test_2.png")
    if not click_ocr_text(img, rect, "有问题尽管问ima"):
        # 兜底：点击窗口中央偏下
        cx, cy = left + ww // 2, top + int(hh * 0.55)
        print(f"  兜底点击输入框中心 @ ({cx}, {cy})")
        pyautogui.click(cx, cy)
    time.sleep(0.6)

    question = "什么是四圣谛？"
    print(f"  输入问题: {question}")
    pyperclip.copy(question)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.5)
    pyautogui.press("enter")
    print("  已发送，等待回答...")

    # 等待生成（可改为轮询“生成中/思考中”）
    time.sleep(20)

    # 截图整个聊天区域（中间主区域，避开左侧栏和底部输入区）
    img, rect = capture_window(hwnd, "ima_test_answer.png")
    left, top, ww, hh = rect
    # 回答区域：右侧主区域，去掉左侧栏和底部输入框
    chat_region = (
        int(ww * 0.08), 0,
        ww, int(hh * 0.52)
    )
    chat_img = img.crop(chat_region)
    chat_img.save("ima_test_answer_crop.png")
    print(f"  已保存回答截图 ima_test_answer_crop.png")

    print("\n=== 回答区域 OCR（前 30 行）===")
    for text, x, y in ocr_image(chat_img)[:30]:
        print(f"  {text!r}")


if __name__ == "__main__":
    main()
