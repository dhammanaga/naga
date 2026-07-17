# -*- coding: utf-8 -*-
# ↑ 这一行告诉 Python：本文件用 UTF-8 编码保存，这样中文注释不会乱码。

"""中文 OCR 引擎封装（RapidOCR / onnxruntime，CPU 友好、国内模型可下载）。

返回格式：list of (text:str, cx:int, cy:int)
  - cx, cy 是文字框中心在「截图坐标系」下的像素坐标（截图左上角为原点）。
调用方需自行叠加窗口左上角偏移，得到屏幕绝对坐标。
"""
# ↑ 三引号包起来的是「模块说明」（docstring），解释这个文件是干嘛的：
#   它是另一个 OCR 引擎的封装（和 ocr_helper.py 用的是不同技术：这里用 RapidOCR）。
#   与 ocr_helper 的区别：这里不光认出"是什么字"，还能告诉你"这个字在图里的哪个位置"
#   （cx, cy 是文字框中心的横、纵坐标，单位是像素）。
#   为什么需要知道位置？因为程序接下来要"点"那个字，得先知道坐标。

import logging   # 导入 logging：写运行日志用（下面加载模型时会记一条日志）

import numpy as np
# ↑ 导入 numpy 并起个小名叫 np：它是一个处理"数组/矩阵"的数学库。
#   下面要把图片转成 numpy 数组，OCR 引擎才认得。

_ENGINE = None
# ↑ 先占个位：用一个全局变量 _ENGINE 缓存"已加载好的 OCR 引擎"。
#   设为 None 表示"还没加载"。为什么要缓存？因为加载模型很慢，
#   第一次加载后存起来，以后每次识别直接复用，不用反复加载。


def _load():
    # ↑ 定义一个"内部函数" _load（名字前带下划线 _ 表示"这是模块内部用的，不对外"）。
    #   它的唯一职责：确保 OCR 引擎只被加载一次，之后直接返回缓存的那个。
    global _ENGINE
    # ↑ 声明"我要用上面那个全局变量 _ENGINE"（否则函数里改的是局部变量，影响不到外面）。
    if _ENGINE is None:
        # ↑ 只有当引擎还没加载过（是 None）时，才真正去加载；已经加载过就跳过。
        from rapidocr_onnxruntime import RapidOCR
        # ↑ 真正用到时才导入 RapidOCR 库（和 ocr_helper 同理，避免一启动就加载重型依赖）。
        logging.info("加载 RapidOCR 模型（首次运行会下载 PP-OCR 模型）...")
        # ↑ 记一条日志，告诉使用者："我正在加载模型，第一次会比较慢，因为要下载模型文件"。
        _ENGINE = RapidOCR()
        # ↑ 创建并加载真正的 OCR 引擎对象，存进全局变量 _ENGINE（以后直接用这个）。
    return _ENGINE
    # ↑ 把"已加载好的引擎"交出去，供 ocr_image 函数使用。


def ocr_image(pil_img):
    """pil_img: PIL.Image。返回 list[(text, cx, cy)]（截图坐标系）。"""
    # ↑ 定义主函数 ocr_image（识别一张图片里的字和位置）。
    #   参数 pil_img 是一张 PIL 库格式的图片（PIL 是 Python 里常用的图像处理库）。
    #   返回：一串 (文字, x坐标, y坐标) 的列表。
    engine = _load()
    # ↑ 拿到 OCR 引擎（第一次会触发加载，之后直接取缓存）。
    arr = np.array(pil_img.convert("RGB"))
    # ↑ 把 PIL 图片先转成 RGB 三通道格式（去掉透明通道等），再转成 numpy 数组 arr。
    #   OCR 引擎只吃"数组"这种格式，所以必须这一步转换。
    result, _elapse = engine(arr)
    # ↑ 把图片数组喂给引擎做识别。
    #   返回两个值：result = 识别结果（文字+位置），_elapse = 耗时（我们用不到，用 _ 丢弃）。
    out = []
    # ↑ 准备一个空列表 out，用来收集"整理好的 (文字, x, y)"，最后返回它。
    if not result:
        # ↑ 如果识别结果是空的（图里没认出任何字），就直接返回空列表，省得后面白跑。
        return out
    for item in result:
        # ↑ 遍历识别结果里的每一条：每条代表图里一个被框出来的文字块。
        try:
            # ↑ 这一条数据的结构应该是 (边框坐标, 文字内容, 置信度)，但结构可能不稳，
            #   所以用 try 保护，万一格式对不上就跳过这条，别让整段崩溃。
            bbox, text, _score = item
            # ↑ 把一条数据拆成三部分：bbox = 文字框的四个角坐标；text = 认出的文字；
            #   _score = 置信度（有多确定认对了，这里用不到，用 _ 丢弃）。
        except Exception:
            # ↑ 如果拆包失败（数据格式不对），就跳过这一条，继续下一条。
            continue
        xs = [p[0] for p in bbox]
        # ↑ 从文字框的四个角坐标里，把所有点的横坐标(x)挑出来，组成一个列表 xs。
        ys = [p[1] for p in bbox]
        # ↑ 同理，把所有点的纵坐标(y)挑出来，组成列表 ys。
        cx = int(sum(xs) / len(xs))
        # ↑ 计算文字框"中心点"的横坐标 cx：把所有 x 加起来求平均，再转成整数。
        cy = int(sum(ys) / len(ys))
        # ↑ 同理计算中心点纵坐标 cy：所有 y 求平均，转整数。
        text = (text or "").strip()
        # ↑ 把认出的文字整理干净：如果 text 是空(None)就当作空字符串，再去掉首尾空格。
        if not text:
            # ↑ 如果整理完发现是空白（什么字都没认到），就跳过这条，不往结果里放。
            continue
        out.append((text, cx, cy))
        # ↑ 把"文字 + 中心点坐标"作为一个小元组，塞进结果列表 out 里。

    # 从上到下、从左到右排序，便于按视觉顺序处理
    # ↑ 下面这行是给结果排序：让列表里的文字按"先上后下、同行再左后右"的顺序排，
    #   这样主程序按列表顺序处理时，读出来的文字顺序才符合人眼看图的顺序。
    out.sort(key=lambda t: (t[2], t[1]))
    # ↑ 排序规则：lambda t: (t[2], t[1]) 意思是"先按纵坐标 t[2] 排，再按横坐标 t[1] 排"。
    #   t[2] 是 y（上下），t[1] 是 x（左右），正好对应"从上到下、从左到右"。
    return out
    # ↑ 把整理好、排好序的 (文字, x, y) 列表交回去。
