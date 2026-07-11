# -*- coding: utf-8 -*-
"""探测 RapidOCR 用法与返回结构，并验证能从合成图中识别中文。"""
import os
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from rapidocr_onnxruntime import RapidOCR

font_path = "C:/Windows/Fonts/msyh.ttc"
try:
    font = ImageFont.truetype(font_path, 32)
except Exception:
    font = ImageFont.load_default()

img = Image.new("RGB", (520, 80), (255, 255, 255))
d = ImageDraw.Draw(img)
d.text((10, 20), "测试 ABC 123 微信群 你好", fill=(0, 0, 0), font=font)

engine = RapidOCR()
result, elapse = engine(np.array(img))
print("RESULT_TYPE:", type(result))
print("RESULT:", result)
print("ELAPSE:", elapse)
