# -*- coding: utf-8 -*-
"""中文 OCR 引擎封装（RapidOCR / onnxruntime，CPU 友好、国内模型可下载）。

返回格式：list of (text:str, cx:int, cy:int)
  - cx, cy 是文字框中心在「截图坐标系」下的像素坐标（截图左上角为原点）。
调用方需自行叠加窗口左上角偏移，得到屏幕绝对坐标。
"""
import logging

import numpy as np

_ENGINE = None


def _load():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        logging.info("加载 RapidOCR 模型（首次运行会下载 PP-OCR 模型）...")
        _ENGINE = RapidOCR()
    return _ENGINE


def ocr_image(pil_img):
    """pil_img: PIL.Image。返回 list[(text, cx, cy)]（截图坐标系）。"""
    engine = _load()
    arr = np.array(pil_img.convert("RGB"))
    result, _elapse = engine(arr)
    out = []
    if not result:
        return out
    for item in result:
        try:
            bbox, text, _score = item
        except Exception:
            continue
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        cx = int(sum(xs) / len(xs))
        cy = int(sum(ys) / len(ys))
        text = (text or "").strip()
        if not text:
            continue
        out.append((text, cx, cy))
    # 从上到下、从左到右排序，便于按视觉顺序处理
    out.sort(key=lambda t: (t[2], t[1]))
    return out
