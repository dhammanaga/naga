# -*- coding: utf-8 -*-
# ↑ 这一行告诉 Python：本文件用 UTF-8 编码保存，这样中文注释不会乱码。

"""可选 OCR 兜底：对微信窗口截图后用 Tesseract 识别中文文本。

启用方式（config.json 的 ocr.enabled=true）前需安装：
    pip install pytesseract pillow
    系统安装 Tesseract-OCR 并勾选中文包 chi_sim
主程序默认关闭 OCR；UI Automation 文本提取优先。OCR 仅在
UI 提取不到消息时作为兜底使用。
"""
# ↑ 三引号包起来的是「模块说明」（docstring），解释这个文件是干嘛的：
#   它是「备胎」方案——平时程序用 UI Automation（界面自动化）直接读取微信里的文字；
#   万一哪天读不到（比如界面变了），就用"截图 + 文字识别(OCR)"把图里的字认出来。
#   默认不开启，因为 OCR 识别速度慢、还可能认错字，能不用就不用。

import os       # 导入 os：提供操作系统相关功能（这里用于拼路径、删临时文件）
import tempfile # 导入 tempfile：提供"临时文件夹"功能，截图可以先放这里，用完即删


def ocr_window(window, lang="chi_sim+eng", tesseract_cmd="tesseract"):
    """把微信窗口截图下来，交给 Tesseract 认出里面的中文文字。"""
    # ↑ 定义一个函数（功能块），名字叫 ocr_window（识别窗口文字）。
    #   参数说明：
    #     window       = 要识别的微信窗口对象
    #     lang         = 识别语言，"chi_sim+eng" 表示"简体中文 + 英文"都认
    #     tesseract_cmd = Tesseract 程序本身的名字/路径，默认就是 "tesseract"
    import pytesseract
    # ↑ 在函数内部才导入 pytesseract（OCR 识别库）。
    #   为什么放在函数里而不是文件顶部？因为平时不用 OCR，没必要一启动就加载它，
    #   等真正用到这一刻再加载，能省一点启动时间、也避免没装这个库时整程序报错。

    if tesseract_cmd:
        # ↑ 如果调用者指定了 tesseract 程序的路径，就执行下面这行设置它。
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        # ↑ 告诉 pytesseract 库：Tesseract 主程序在哪里。
        #   不设置的话，库会去默认位置找；如果装在了非默认位置，就得靠这里指定。

    tmp = os.path.join(tempfile.gettempdir(), "wechat_capture.bmp")
    # ↑ 拼出一个临时图片文件的完整路径：
    #   tempfile.gettempdir() = 系统临时文件夹（如 C:\Users\你\AppData\Local\Temp）
    #   "wechat_capture.bmp" = 我们给截图起的临时文件名
    #   合起来就是"临时文件夹里的 wechat_capture.bmp"，专门用来临时存截图。

    try:
        # ↑ try 表示"下面这段代码可能出错，我要试着做，出错了就跳到 except 处理"。
        window.CaptureToImage(tmp)
        # ↑ 让微信窗口把自己当前画面"拍成照片"，保存到上面那个临时文件 tmp 里。
    except Exception as e:
        # ↑ 如果截图这步失败（比如窗口不见了），就捕获错误，变量 e 里是错误信息。
        raise RuntimeError("截图失败: %s" % e)
        # ↑ 主动抛出一个更清晰的错误："截图失败: 具体原因"，让调用者一眼知道错在哪。

    try:
        # ↑ 再次进入 try：这一步是把截图交给 OCR 引擎去认字，同样可能出错。
        raw = pytesseract.image_to_string(tmp, lang=lang)
        # ↑ 核心一步：把临时截图 tmp 喂给 Tesseract，让它返回识别出的原始文字字符串。
        #   lang=lang 告诉它按"中文+英文"来认。识别结果是一大段带换行的文本，存进 raw。
    except Exception as e:
        # ↑ 如果 OCR 识别失败（比如没装 Tesseract 中文包），捕获错误。
        raise RuntimeError("OCR 失败: %s" % e)
        # ↑ 抛出清晰错误："OCR 失败: 具体原因"。
    finally:
        # ↑ finally 表示"不管上面成功还是失败，最后都要执行这一段"（清理工作）。
        try:
            # ↑ 再套一层 try：删除临时文件这步也可能出错（比如文件正被占用），所以也保护一下。
            os.remove(tmp)
            # ↑ 删除刚才那张临时截图，避免它在临时文件夹里越堆越多。
        except Exception:
            # ↑ 如果删除失败（比如文件被占用），就忽略，不要为了这点小事让程序报错。
            pass
            # ↑ pass 表示"什么都不做"，静静地放过这个无关紧要的小错误。

    return [t.strip() for t in raw.splitlines() if t.strip()]
    # ↑ 把识别结果整理干净再返回：
    #   raw.splitlines() = 把一大段文本按"换行"切成一行一行
    #   t.strip()        = 去掉每行首尾的空格和空行（strip = 修剪）
    #   if t.strip()     = 只保留"不是空白"的那些行（空行直接丢掉）
    #   最终返回一个"去掉空白行、每行已修剪"的文字列表，方便主程序一行行处理。
