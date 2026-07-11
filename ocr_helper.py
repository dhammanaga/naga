# -*- coding: utf-8 -*-
"""可选 OCR 兜底：对微信窗口截图后用 Tesseract 识别中文文本。

启用方式（config.json 的 ocr.enabled=true）前需安装：
    pip install pytesseract pillow
    系统安装 Tesseract-OCR 并勾选中文包 chi_sim
主程序默认关闭 OCR；UI Automation 文本提取优先。OCR 仅在
UI 提取不到消息时作为兜底使用。
"""
import os
import tempfile


def ocr_window(window, lang="chi_sim+eng", tesseract_cmd="tesseract"):
    import pytesseract

    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    tmp = os.path.join(tempfile.gettempdir(), "wechat_capture.bmp")
    try:
        window.CaptureToImage(tmp)
    except Exception as e:
        raise RuntimeError("截图失败: %s" % e)

    try:
        raw = pytesseract.image_to_string(tmp, lang=lang)
    except Exception as e:
        raise RuntimeError("OCR 失败: %s" % e)
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass

    return [t.strip() for t in raw.splitlines() if t.strip()]
