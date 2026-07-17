# -*- coding: utf-8 -*-
# ↑ 这一行告诉 Python：本文件用 UTF-8 编码保存，这样中文注释不会乱码。

"""基于「截图 + 中文 OCR + 模拟坐标点击」的微信控制器。

适用场景：微信 4.0（Qt 重写）等不向 UI Automation 暴露聊天内容的版本。
uiautomation/win32 仅用于：定位微信窗口、取窗口矩形（不做置顶/绑定线程等强风控操作）。
其余读/写全部走「截图 OCR + 拟人化鼠标/键盘模拟」：所有点击带移动轨迹与随机延迟，
模拟真实人类操作，规避 OLE 注入、剪贴板强写、强制置前等易触发风控的行为。

坐标说明：所有点击都基于「窗口左上角 + 截图内相对坐标」换算成屏幕绝对坐标。
布局比例可在 config.json 的 wechat.ocr_layout 调整（因分辨率/缩放而异）。
"""
# ↑ 三引号是「模块说明」（docstring）：讲清这个文件是"微信遥控器"——
#   它不直接操控微信控件，而是"截图 → 用 OCR 认字 → 模拟真人鼠标键盘去点/输入/发图"。

import logging       # 导入 logging：写运行日志
import os            # 导入 os：处理路径、判断文件是否存在
import re            # 导入 re：正则表达式，用于文字归一化匹配
import time          # 导入 time：暂停几秒（拟人节奏）、计时
import ctypes        # 导入 ctypes：调用 Windows 底层功能（还原最小化窗口等）

import uiautomation as auto   # 导入 uiautomation（操作 Windows 窗口），起别名 auto
import win32gui               # 导入 win32gui：按类名/标题找窗口、取窗口矩形
import win32ui                # 导入 win32ui：Windows 界面相关底层功能
import win32con               # 导入 win32con：Windows 常量（如还原窗口的 SW_RESTORE）
import pyautogui              # 导入 pyautogui：模拟鼠标移动/点击、键盘输入
import pyperclip              # 导入 pyperclip：操作系统剪贴板（粘贴文字/图片）
from PIL import Image         # 从 Pillow 导入 Image：处理图片（打开/保存/裁剪）
from PIL import ImageGrab     # 导入 ImageGrab：按屏幕区域截图
from PIL import ImageOps      # 导入 ImageOps：图片操作（如反色，帮忙认浅色字）

from ocr_engine import ocr_image       # 从 ocr_engine 引入 OCR 识别函数
from flow_runtime import mark          # AUTO-INSTRUMENTED  从流程追踪引入 mark（记录走到哪步）
from human_input import (              # 从 human_input 引入"拟人化"输入函数
    human_click, human_double_click, human_type,
    # ↑ 拟人点击、双击、打字
    human_send_enter, human_delay, is_foreground, human_activate_titlebar,
    # ↑ 拟人回车、拟人延时、判断是否在前台、点标题栏激活窗口
    bring_to_front,
    # ↑ 把窗口带到最前
)


# 注意：已废弃「强制置前」方案（原 _force_foreground 使用 AttachThreadInput /
# SetWindowPos(TOPMOST) / SetForegroundWindow 等强风控 API，且微信 4.0 会被系统
# 拒绝）。窗口激活改由 _focus() 用「真实鼠标点击标题栏」模拟人类完成，见 human_input。
# ↑ 注释说明历史决策：之前用过"强制置前"的强风控 API，微信 4.0 会拒绝且易封号，
#   现已废弃，改用"点标题栏"这种像真人一样的激活方式。


class OCRWeChatController:
    # ↑ 定义一个类 OCRWeChatController（基于 OCR 的微信控制器），封装所有微信操作的类。
    def __init__(self, cfg):
        # ↑ 初始化方法：创建控制器时执行，记住配置。
        self.cfg = cfg["wechat"]
        # ↑ 取出配置里 "wechat" 这一块，存起来。
        self.window = None
        # ↑ 先占个位：微信窗口对象，还没找到，先设为 None。
        self._layout = self.cfg.get("ocr_layout", {}) or {}
        # ↑ 取出"布局比例"配置（决定各区域在窗口中的大致位置）。
        self.sidebar_ratio = float(self._layout.get("sidebar_ratio", 0.26))
        # ↑ 左侧"会话列表"宽度占窗口的比例（默认 0.26 = 26%）。
        self.title_ratio = float(self._layout.get("title_ratio", 0.07))
        # ↑ 顶部"标题栏"高度占窗口的比例（默认 0.07）。
        self.input_ratio = float(self._layout.get("input_ratio", 0.16))
        # ↑ 底部"输入框"高度占窗口的比例（默认 0.16）。
        self.input_x = float(self._layout.get("input_x", 0.55))
        # ↑ 输入框中心横坐标比例（默认 0.55 = 中间偏右）。
        self.input_y = float(self._layout.get("input_y", 0.90))
        # ↑ 输入框中心纵坐标比例（默认 0.90 = 靠近底部）。
        # 固定布局：直接给出窗口物理矩形 [L,T,R,B]（pyautogui 坐标系）。
        # 设置后不再动态探测/移动窗口，最稳。不设置则回退到 win32 物理探测。
        # ↑ 注释：下面支持"固定窗口位置"，配置了就用固定值，最稳。不配就自动探测。
        fr = self.cfg.get("fixed_rect")
        # ↑ 取出 fixed_rect 配置（固定窗口矩形）。
        self.fixed_rect = [int(v) for v in fr] if fr else None
        # ↑ 如果有配置，就转成整数列表 [左,上,右,下]；否则为 None（稍后自动探测）。

    # ---- 窗口 ----
    # ↑ 注释分隔：下面这组方法是"窗口相关"的。

    # 微信 3.x/4.x 常见顶层类名。不要按标题含"微信"兜底，否则 WorkBuddy 等
    # 标题为"微信IMA监控"的窗口会被误命中。
    # ↑ 注释：下面这组类名是微信窗口的"身份证"，用类名找窗口最准，不靠标题（避免误命中）。
    WECHAT_CLASSES = (
        # ↑ 定义一个元组，列出微信各版本的主窗口类名。
        "WeChatMainWndForPC",     # 微信 3.x 桌面版类名
        "WeixinMainWndForPC",     # 微信 4.x(早期) 类名
        "Qt51514QWindowIcon",     # 微信 4.0 某些安装包的类名
    )

    def _restore_control(self, ctrl):
        """仅当窗口被最小化时才还原；绝不对已停靠/正常的窗口调用 ShowWindow
        （SW_RESTORE 会把 docked 窗口还原成旧位置，导致窗口乱跳）。"""
        # ↑ 定义 _restore_control（还原控件）：只有窗口被最小化时才还原它。
        try:
            # ↑ try 保护：取窗口句柄可能失败，包一下。
            hwnd = int(ctrl.NativeWindowHandle)
            # ↑ 取出窗口的"身份证号"（句柄 hwnd）。
            if hwnd and ctypes.windll.user32.IsIconic(hwnd):
                # ↑ 如果有句柄、且窗口正处于"最小化"状态（IsIconic 为真）……
                ctypes.windll.user32.ShowWindow(hwnd, 9)
                # ↑ 调用系统函数把窗口还原（9 对应 SW_RESTORE 还原）。注意：只对最小化才做。
        except Exception:
            # ↑ 失败就忽略。
            pass

    def find_window(self):
        # ↑ 定义 find_window（找窗口）：定位微信主窗口，找到就把对象存进 self.window。
        # 1) win32gui 优先：按类名精确匹配微信主窗口，最稳，绕开 uiautomation
        #    在 Qt/Electron 窗口上偶发的 COM 崩溃（_locate 时 Exists/遍历会崩）。
        # ↑ 注释：优先用 win32gui 按类名找，最稳，能避开 uiautomation 偶尔崩溃。
        try:
            # ↑ try 保护：找窗口整个过程可能出错，包一下。
            target_hwnd = None
            # ↑ 先占个位：最终找到的目标窗口句柄，初始 None。

            def _enum(hwnd, _):
                # ↑ 定义一个内部回调函数 _enum，系统枚举每个窗口时会调用它（hwnd 是当前窗口）。
                nonlocal target_hwnd
                # ↑ 声明 target_hwnd 是外层变量（这样修改能影响外面）。
                if target_hwnd or not hwnd:
                    # ↑ 如果已经找到、或当前句柄为空……
                    return
                    # ↑ 就直接返回，不再处理。
                try:
                    # ↑ try 保护：取类名可能失败。
                    cls = win32gui.GetClassName(hwnd)
                    # ↑ 取当前窗口的类名。
                except Exception:
                    # ↑ 失败就跳过这个窗口。
                    return
                if cls in self.WECHAT_CLASSES:
                    # ↑ 如果这个窗口的类名在微信类名清单里……
                    try:
                        # ↑ try 保护：取窗口尺寸可能失败。
                        r = win32gui.GetWindowRect(hwnd)
                        # ↑ 取窗口矩形 (左,上,右,下)。
                        if (r[2] - r[0]) >= 600 and (r[3] - r[1]) >= 400:
                            # ↑ 如果窗口宽≥600、高≥400（确认是个正常大小的微信窗口，不是小弹窗）……
                            target_hwnd = hwnd
                            # ↑ 记下这个窗口句柄，找到了！
                    except Exception:
                        # ↑ 取尺寸失败就忽略。
                        pass

            win32gui.EnumWindows(_enum, None)
            # ↑ 让系统遍历所有顶层窗口，对每个都调用 _enum。
            if target_hwnd:
                # ↑ 如果找到了目标窗口……
                try:
                    # ↑ try 保护：把句柄转成 uiautomation 控件对象可能失败。
                    ctrl = auto.ControlFromHandle(target_hwnd)
                    # ↑ 把句柄转成 uiautomation 控件对象（方便后续操作）。
                except Exception:
                    # ↑ 失败就当 None。
                    ctrl = None
                if ctrl is not None:
                    # ↑ 如果转成功了……
                    self._restore_control(ctrl)
                    # ↑ 先还原一下（若最小化）。
                    time.sleep(0.3)
                    # ↑ 暂停 0.3 秒，等窗口稳定。
                    logging.info("已定位微信窗口（win32gui）：Class=%r Name=%r",
                                 win32gui.GetClassName(target_hwnd),
                                 getattr(ctrl, "Name", ""))
                    # ↑ 记日志：成功按类名定位到微信窗口。
                    self.window = ctrl
                    # ↑ 把窗口对象存起来。
                    self._restore()
                    # ↑ 再还原一次（兜底）。
                    return ctrl
                    # ↑ 返回窗口对象，结束。
        except Exception as e:
            # ↑ 整个 win32gui 流程出错……
            logging.debug("win32gui 定位微信失败，转 uiautomation: %s", e)
            # ↑ 记调试日志，准备用兜底方案。

        # 2) uiautomation 兜底（整体 try/except 防 COM 崩溃）
        # ↑ 注释：如果上面的 win32gui 没找到，就用 uiautomation 再试一次。
        try:
            # ↑ try 保护：uiautomation 在 Qt 窗口上可能 COM 崩溃，整体包住。
            for cls in self.WECHAT_CLASSES:
                # ↑ 遍历微信各版本类名。
                try:
                    # ↑ try 保护：单个类名查找可能失败。
                    c = auto.WindowControl(ClassName=cls)
                    # ↑ 按类名创建一个窗口控件对象。
                    if c.Exists(3):
                        # ↑ 如果它确实存在（3 秒超时内确认）……
                        self._restore_control(c)
                        # ↑ 还原（若最小化）。
                        time.sleep(0.3)
                        # ↑ 暂停 0.3 秒。
                        if self._valid_wechat_rect(c):
                            # ↑ 检查窗口尺寸是否合理（足够大）……
                            logging.info("已定位微信窗口（按类名）：Class=%r Name=%r",
                                         cls, getattr(c, "Name", ""))
                            # ↑ 记日志。
                            self.window = c
                            # ↑ 存窗口对象。
                            self._restore()
                            # ↑ 还原兜底。
                            return c
                            # ↑ 返回窗口对象。
                        else:
                            # ↑ 尺寸异常……
                            logging.debug("类名 %r 命中但窗口尺寸异常，忽略。", cls)
                            # ↑ 记调试日志，跳过这个候选。
                except Exception:
                    # ↑ 单个类名查找失败就继续下一个。
                    continue

            for w in auto.GetRootControl().GetChildren():
                # ↑ 遍历桌面根下的所有直接子窗口（兜底：按名称找含"微信"的）。
                try:
                    # ↑ try 保护。
                    name = (w.Name or "").lower()
                    # ↑ 取窗口名称并转小写。
                    cls = (getattr(w, "ClassName", "") or "").lower()
                    # ↑ 取窗口类名并转小写。
                    if not (("微信" in name or "wechat" in name) and "ima" not in name and "workbuddy" not in name):
                        # ↑ 如果名称不含"微信/wechat"，或含"ima/workbuddy"（避免误命中别的窗口）……
                        continue
                        # ↑ 跳过这个窗口。
                    self._restore_control(w)
                    # ↑ 还原（若最小化）。
                    time.sleep(0.3)
                    # ↑ 暂停。
                    if not self._valid_wechat_rect(w):
                        # ↑ 如果尺寸不合理……
                        continue
                        # ↑ 跳过。
                    logging.info("已定位微信窗口（兜底）：Name=%r Class=%r",
                                 getattr(w, "Name", ""), getattr(w, "ClassName", ""))
                    # ↑ 记日志。
                    self.window = w
                    # ↑ 存窗口对象。
                    self._restore()
                    # ↑ 还原兜底。
                    return w
                    # ↑ 返回窗口对象。
                except Exception:
                    # ↑ 单个窗口处理失败就继续。
                    continue
        except Exception as e:
            # ↑ uiautomation 整体失败……
            logging.debug("uiautomation 兜底定位失败: %s", e)
            # ↑ 记调试日志。

        logging.error(
            # ↑ 如果到此还没找到，记一条错误日志告诉用户怎么排查。
            "找不到微信窗口。请确认：①微信桌面端已启动并登录；②窗口未最小化；③类名为 %s 之一。"
            "如果微信 4.0 类名不同，请把 config.json 的 wechat.class_name 改为真实类名。",
            self.WECHAT_CLASSES
        )
        return None
        # ↑ 实在找不到，返回 None。

    def _valid_wechat_rect(self, ctrl):
        """微信主窗口应当足够大；排除 WorkBuddy 等小窗口误命中。"""
        # ↑ 定义 _valid_wechat_rect（验证窗口尺寸）：确认这是个够大的微信窗口。
        try:
            # ↑ try 保护：取矩形可能失败。
            r = ctrl.BoundingRectangle
            # ↑ 取窗口边界矩形。
            return r.width() >= 600 and r.height() >= 400
            # ↑ 宽≥600 且 高≥400 才算合格，否则返回 False。
        except Exception:
            # ↑ 取失败就当不合格。
            return False

    def _restore(self):
        """仅当微信窗口被最小化时才还原。

        绝不对已停靠/正常的窗口调用 ShowWindow(SW_RESTORE)——那会把 docked 窗口
        还原成旧位置导致窗口乱跳（之前因此把微信从右下四分之一挪走了）。
        """
        # ↑ 定义 _restore（还原窗口）：只有最小化时才还原，避免窗口乱跳。
        try:
            # ↑ try 保护。
            hwnd = self.window.NativeWindowHandle
            # ↑ 取窗口句柄。
            if hwnd and ctypes.windll.user32.IsIconic(int(hwnd)):
                # ↑ 如果有句柄且处于最小化……
                ctypes.windll.user32.ShowWindow(int(hwnd), 9)
                # ↑ 还原窗口（9=SW_RESTORE）。
        except Exception as e:
            # ↑ 失败就记个警告，不影响大局。
            logging.warning("还原微信窗口失败（可忽略）: %s", e)

    def _rect(self):
        """返回窗口物理矩形 (left,top,right,bottom,w,h)。

        优先用 config 的 fixed_rect（物理像素，pyautogui 坐标系）；否则用 win32
        GetWindowRect（本进程已因导入 pyautogui 而变为 DPI 感知，故返回物理像素，
        与 pyautogui 点击坐标同空间，不会因高 DPI 缩放而打偏）。
        """
        # ↑ 定义 _rect（取窗口矩形）：返回窗口的位置和大小，供点击换算坐标用。
        if self.fixed_rect:
            # ↑ 如果配置了固定矩形……
            L, T, R, B = self.fixed_rect
            # ↑ 拆出 左/上/右/下。
            return L, T, R, B, R - L, B - T
            # ↑ 返回 (左,上,右,下,宽,高)。
        if getattr(self, "window", None) is None:
            # ↑ 如果连窗口都没定位到……
            raise RuntimeError("微信窗口尚未定位（请先 find_window）。")
            # ↑ 抛错：必须先找到窗口。
        hwnd = int(self.window.NativeWindowHandle)
        # ↑ 取窗口句柄。
        L, T, R, B = win32gui.GetWindowRect(hwnd)
        # ↑ 用 win32 取窗口真实矩形。
        w, h = R - L, B - T
        # ↑ 算出宽 w、高 h。
        if w <= 0 or h <= 0:
            # ↑ 如果宽或高 ≤ 0（窗口异常/不可见）……
            raise RuntimeError(
                # ↑ 抛错说明窗口尺寸为 0，提示用户确认微信已登录且可见。
                "微信窗口尺寸为 0（left=%d top=%d right=%d bottom=%d）。"
                "请确认：①微信已登录且未最小化；②微信窗口当前可见。" % (L, T, R, B)
            )
        return L, T, R, B, w, h
        # ↑ 返回 (左,上,右,下,宽,高)。

    def _capture_window_pw(self):
        """截取微信窗口本体（物理像素）。

        用 ImageGrab 按窗口物理矩形抓屏：绕开 PrintWindow 在高 DPI 下的逻辑/物理
        尺寸错配；坐标与 pyautogui 点击同处物理像素空间，避免点击打偏。
        返回 (img, left, top, w, h)。
        """
        # ↑ 定义 _capture_window_pw（截窗口图）：把微信窗口截图返回。
        left, top, right, bottom, w, h = self._rect()
        # ↑ 先取窗口矩形。
        img = ImageGrab.grab((left, top, right, bottom))
        # ↑ 按矩形区域调用系统截图，得到图片 img。
        return img, left, top, w, h
        # ↑ 返回 (图片, 左, 上, 宽, 高)。

    def _screenshot(self):
        # ↑ 定义 _screenshot（截图）：对外简化接口，只返回图片。
        img, _left, _top, _w, _h = self._capture_window_pw()
        # ↑ 调用截窗口函数（下划线变量表示"用不到这些值"）。
        return img
        # ↑ 返回图片。

    def save_debug(self, out_dir="."):
        """截图并保存：整窗 + 侧边栏（带 OCR 框）+ 聊天区（带 OCR 框）。
        用于在你本机微调 config.json 的 ocr_layout 比例。
        """
        # ↑ 定义 save_debug（存调试图）：截图并画出 OCR 认到的字框，方便你校准坐标。
        self._focus()
        # ↑ 先把微信窗口带到前台（确保截图完整）。
        from PIL import ImageDraw
        # ↑ 导入 ImageDraw（画图工具，用来画框）。
        import os
        # ↑ 导入 os（建目录用）。
        os.makedirs(out_dir, exist_ok=True)
        # ↑ 创建输出目录（不存在才建）。

        full, left, top, w, h = self._capture_window_pw()
        # ↑ 截整窗图，并取得窗口位置/尺寸。
        full.save(os.path.join(out_dir, "debug_full.png"))
        # ↑ 保存整窗截图。
        print("[debug] 已保存整窗截图 -> debug_full.png")
        # ↑ 打印提示。
        iw, ih = full.size
        # ↑ 取整窗图片的像素宽 iw、高 ih。
        sx_scale = (w / iw) if iw else 1.0
        # ↑ 计算"图片像素 → 屏幕物理像素"的横向缩放（防止高 DPI 坐标错位）。
        sy_scale = (h / ih) if ih else 1.0
        # ↑ 纵向同理。

        # 侧边栏
        # ↑ 注释：下面处理"左侧会话列表"区域。
        sb = self._ocr_screen(0.0, self.title_ratio, self.sidebar_ratio, 1.0)
        # ↑ 对左侧边栏区域做 OCR，得到 (文字, 屏幕x, 屏幕y) 列表。
        sb_img = full.crop((0, int(ih * self.title_ratio),
                            int(iw * self.sidebar_ratio), ih))
        # ↑ 从整窗图里裁出侧边栏那块小图（用于画框标注）。
        d = ImageDraw.Draw(sb_img)
        # ↑ 拿一支画笔，准备在侧边栏小图上画框。
        for text, absx, absy in sb:
            # ↑ 遍历 OCR 认到的每个字（absx/absy 是屏幕坐标）。
            ax = int((absx - left) / sx_scale)
            # ↑ 把屏幕 x 换算成"小图内的 x"。
            ay = int((absy - (top + h * self.title_ratio)) / sy_scale)
            # ↑ 把屏幕 y 换算成"小图内的 y"。
            d.rectangle([ax - 4, ay - 4, ax + 60, ay + 4], outline="red", width=2)
            # ↑ 在字周围画一个红色矩形框（便于你看到 OCR 认到了哪）。
        sb_img.save(os.path.join(out_dir, "debug_sidebar.png"))
        # ↑ 保存带框的侧边栏截图。
        print("[debug] 侧边栏识别到的文字：")
        # ↑ 打印提示。
        for text, _sx, _sy in sb:
            # ↑ 遍历认到的字……
            print("   ", repr(text))
            # ↑ 打印每个字（repr 保留原始样子，方便核对）。
        print("[debug] 已保存侧边栏带框截图 -> debug_sidebar.png")
        # ↑ 打印保存提示。

        # 聊天区
        # ↑ 注释：下面处理"右侧聊天区"区域。
        ch = self._ocr_screen(self.sidebar_ratio, self.title_ratio, 1.0,
                              1.0 - self.input_ratio)
        # ↑ 对右侧聊天区（排除底部输入框）做 OCR。
        ch_img = full.crop((int(iw * self.sidebar_ratio),
                            int(ih * self.title_ratio), iw,
                            int(ih * (1.0 - self.input_ratio))))
        # ↑ 从整窗裁出聊天区那块小图。
        d2 = ImageDraw.Draw(ch_img)
        # ↑ 拿画笔。
        for text, absx, absy in ch:
            # ↑ 遍历聊天区认到的字……
            ax = int((absx - (left + w * self.sidebar_ratio)) / sx_scale)
            # ↑ 换算成小图内 x。
            ay = int((absy - (top + h * self.title_ratio)) / sy_scale)
            # ↑ 换算成小图内 y。
            d2.rectangle([ax - 4, ay - 4, ax + 60, ay + 4], outline="red", width=2)
            # ↑ 画红框。
        ch_img.save(os.path.join(out_dir, "debug_chat.png"))
        # ↑ 保存带框聊天区截图。
        print("[debug] 聊天区识别到的文字：")
        # ↑ 打印提示。
        for text, _sx, _sy in ch:
            # ↑ 遍历认到的字……
            print("   ", repr(text))
            # ↑ 打印。
        print("[debug] 已保存聊天区带框截图 -> debug_chat.png")
        # ↑ 打印保存提示。

    def _ocr_screen(self, x0=0.0, y0=0.0, x1=1.0, y1=1.0):
        """对窗口的某子区域做 OCR，返回屏幕绝对坐标的 (text, sx, sy)。

        注意：本函数只负责截图+OCR，不在此做窗口置前（避免每帧 bring_to_front
        引发焦点抖动、吃掉点击）。窗口置前由 _focus() 在「点击/读取前」按需调用。
        """
        # ↑ 定义 _ocr_screen（屏幕区域 OCR）：对窗口某个比例区域截图并认字，
        #   返回每个字的"文字 + 屏幕绝对坐标"，供后续点击使用。
        img, left, top, w, h = self._capture_window_pw()
        # ↑ 先截整窗图，并取窗口位置/尺寸。
        iw, ih = img.size
        # ↑ 取图片像素宽高。
        px0, py0, px1, py1 = (
            # ↑ 算出要 OCR 的"子区域"在图片里的像素范围：
            int(iw * x0), int(ih * y0), int(iw * x1), int(ih * y1)
            # ↑ 用比例 × 图片尺寸，得到左上(px0,py0)和右下(px1,py1)像素坐标。
        )
        px0, py0 = max(0, px0), max(0, py0)
        # ↑ 左上角坐标不能小于 0（防止比例算出负数）。
        px1, py1 = min(iw, px1), min(ih, py1)
        # ↑ 右下角坐标不能超过图片边界。
        sub = img.crop((px0, py0, px1, py1))
        # ↑ 从整窗图裁出这个子区域小图。
        scale_x = (w / iw) if iw else 1.0
        # ↑ 横向缩放：图片像素 → 屏幕物理像素。
        scale_y = (h / ih) if ih else 1.0
        # ↑ 纵向缩放。
        boxes = ocr_image(sub)
        # ↑ 调用 OCR 引擎，认出子图里每个字和它在子图中的坐标 (文字, 子图x, 子图y)。
        # 反色兜底：原图抓不到的白字/浅色字（如「已发送」绿底白字气泡、
        # 浅灰提示字）在反色后变深，可被 OCR 识别，提升读消息鲁棒性。
        # 优化：首遍已识别到足够文字（≥4 块）时跳过反色，省约一半耗时
        # （open_group / read_messages 会多次调用本函数，累计省时明显）。
        # ↑ 注释：下面"反色"是兜底——有些浅色字认不到，反色后变深就能认。
        #   但首遍已认到≥4块就不用再反色，省时间（这函数会被反复调用）。
        if len(boxes) < 4:
            # ↑ 如果首遍认到的字少于 4 个（可能漏了浅色字）……
            try:
                # ↑ try 保护：反色处理可能失败。
                inv = ImageOps.invert(sub.convert("RGB"))
                # ↑ 把子图转 RGB 后"反色"（黑变白、白变黑）。
                boxes += ocr_image(inv)
                # ↑ 再对反色图做一次 OCR，把新认到的字追加进结果。
            except Exception:
                # ↑ 失败就忽略。
                pass
        out = []
        # ↑ 准备空列表，收集"文字 + 屏幕绝对坐标"。
        for text, lx, ly in boxes:
            # ↑ 遍历每个认到的字（lx,ly 是子图内坐标）。
            # 子图内像素 -> 整图像素 -> 屏幕绝对坐标
            # ↑ 注释：下面做坐标换算，把"子图内坐标"变成"屏幕真实坐标"。
            ax = int(left + (px0 + lx) * scale_x)
            # ↑ 屏幕 x = 窗口左边 + (子图起点 + 字在子图x) × 横向缩放。
            ay = int(top + (py0 + ly) * scale_y)
            # ↑ 屏幕 y = 窗口上边 + (子图起点 + 字在子图y) × 纵向缩放。
            out.append((text, ax, ay))
            # ↑ 把 (文字, 屏幕x, 屏幕y) 加进结果。
        return out
        # ↑ 返回结果列表。

    # ---- 群列表 ----
    # ↑ 注释分隔：下面这组方法负责"找群、开群"。

    def list_visible_groups(self):
        mark("list_visible_groups", "枚举可见会话")  # AUTO-INSTRUMENTED
        # ↑ 定义 list_visible_groups（列出可见会话）：返回当前左侧能看到的群名列表。
        self._focus()
        # ↑ 先把微信带到前台。
        items = self._ocr_screen(0.0, self.title_ratio, self.sidebar_ratio, 1.0)
        # ↑ 对左侧会话列表区域做 OCR。
        names = []
        # ↑ 准备空列表收集群名。
        for text, _sx, _sy in items:
            # ↑ 遍历认到的每个字/词。
            t = text.strip()
            # ↑ 去掉首尾空格。
            if t and t not in names:
                # ↑ 如果非空、且还没收过这个名字……
                names.append(t)
                # ↑ 加进群名列表（去重）。
        return names
        # ↑ 返回群名列表。

    @staticmethod
    def _norm_group(s):
        # ↑ 定义 _norm_group（规范化群名）：把群名变成"可比对"的干净形式。
        s = (s or "").strip().lower()
        # ↑ 去空格、转小写。
        # 仅保留中文与字母，去除数字/标点/空格，提升 OCR 识别差异时的匹配鲁棒性
        # （例如「自己5人群」被识别成「自己五人群」也能匹配）
        # ↑ 注释：只保留中文和字母，删掉数字标点。这样"自己5人群"和"自己五人群"能匹配上。
        return re.sub(r"[^一-鿿a-z]", "", s)
        # ↑ 用正则删掉"非中文非字母"的所有字符，返回干净群名。

    def _group_match(self, ocr_text, target):
        # ↑ 定义 _group_match（群名匹配）：判断 OCR 认到的文字是不是目标群。
        a = self._norm_group(target)
        # ↑ 把目标群名规范化。
        b = self._norm_group(ocr_text)
        # ↑ 把 OCR 认到的文字也规范化。
        if not a or not b:
            # ↑ 任一为空……
            return False
            # ↑ 返回不匹配。
        if a == b or a in b or b in a:
            # ↑ 完全相等、或互相包含……
            return True
            # ↑ 匹配成功。
        # 容忍开头 1~2 个字的 OCR 误识/漏识（如「自己5人群」被读成「已5人群」：
        # 「自己」两字被压成「已」一字；或首个字被误识）。去掉前 i 字后再做包含匹配。
        # ↑ 注释：OCR 偶尔开头会认错一两个字，下面去掉开头 1~2 字再比，增加容错。
        for i in range(1, 3):
            # ↑ i 取 1、2（去掉开头 1 字或 2 字再试）。
            if a[i:] and a[i:] in b:
                # ↑ 去掉目标开头 i 字后，剩下的能在 b 里找到……
                return True
                # ↑ 算匹配。
            if b[i:] and b[i:] in a:
                # ↑ 反过来，去掉 OCR 文字开头 i 字后在目标里找到……
                return True
                # ↑ 也算匹配。
        return False
        # ↑ 都不行，返回不匹配。

    def open_group(self, name):
        mark("open_group", "打开监控群")  # AUTO-INSTRUMENTED
        """打开指定微信群。优先在可见会话列表中直接匹配（群已置顶时最快）；
        否则通过微信搜索定位并打开（不要求群在侧栏可见）。

        注意：必须以「标题栏 OCR 命中群名」确认聊天真的打开了才返回 True，
        不能仅因「侧栏还显示群名」就放行——否则点击没切到该群时会误判成功，
        后续读到的是别的聊天而漏答。标题栏 OCR 对「自己5人群」这类会误读成
        「已5人群」，故 _group_match 已做开头 1~2 字容错。
        """
        # ↑ docstring 解释：open_group 就是"打开某个群"，而且必须用标题栏确认真的打开了，
        #   不能只看侧栏还显示群名就以为成功（那样可能点错群）。下面用多个策略依次尝试。
        self._focus()
        # ↑ 先把微信带到前台。
        target = name.strip()
        # ↑ 目标群名去空格。
        # 先把左侧会话列表滚到最顶，确保近期会话都进入可见区
        # ↑ 注释：先把会话列表滚到最顶，让近期会话都露出来。
        try:
            # ↑ try 保护：滚动操作可能失败。
            _l, _t, _r, _b, _w, _h = self._rect()
            # ↑ 取窗口矩形。
            _sx = int(_l + _w * self.sidebar_ratio * 0.5)
            # ↑ 算出侧栏中心 x 坐标（滚动要在这个位置滚）。
            _sy = int((_t + _h * self.title_ratio + 10 + _b - 10) / 2)
            # ↑ 算出列表区域中间的 y 坐标。
            for _ in range(10):
                # ↑ 向上滚 10 次（把列表顶到最上）。
                pyautogui.scroll(10, _sx, _sy)
                # ↑ 模拟滚轮向上滚。
                time.sleep(0.15)
                # ↑ 每次滚完停 0.15 秒。
            time.sleep(0.3)
            # ↑ 最后停 0.3 秒。
        except Exception:
            # ↑ 失败就忽略，继续。
            pass
        # 1) 侧栏直接匹配（群在近期列表/置顶时最快）
        # ↑ 注释：策略1：直接在可见会话列表里找并点。
        if self._find_and_click_in_sidebar(target):
            # ↑ 如果侧栏里找到并点开了……
            return True
            # ↑ 成功返回。
        # 2) 向下滚动侧栏继续找
        # ↑ 注释：策略2：列表里没找到，就边滚边找。
        if self._scroll_sidebar_find(target):
            # ↑ 滚动查找成功……
            return True
            # ↑ 成功返回。
        # 3) 搜索框兜底（OCR 定位「搜索」，不写死坐标）
        # ↑ 注释：策略3：用搜索框搜群名。
        if self._search_open_group(target):
            # ↑ 搜索打开成功……
            return True
            # ↑ 成功返回。
        # 4) 通讯录-群聊路线
        # ↑ 注释：策略4：从通讯录→群聊里找。
        if self._via_contacts_open_group(target):
            # ↑ 通讯录路线成功……
            return True
            # ↑ 成功返回。
        logging.warning("所有策略均未能打开群「%s」。", target)
        # ↑ 四种策略都失败，记警告。
        return False
        # ↑ 返回失败。

    def search_and_open_group(self, name):
        """定位并打开微信群。不依赖搜索框（微信4.0搜索框会触发搜一搜）。

        策略：
        1) ESC 退出可能的搜一搜/覆盖层，回到聊天视图；
        2) 在左侧会话列表当前可见区域 OCR 找群名；
        3) 找不到则滚动侧栏（先回顶再向下滚）继续找；
        4) 还找不到则尝试点击侧栏底部「通讯录」→「群聊」找群；
        5) 最后兜底：用搜索框下拉建议（只输入不按 Enter）。
        """
        # ↑ docstring 解释：这是另一个"开群"方法（备用），策略与 open_group 类似但更强调 ESC 先清掉覆盖层。
        self._focus()
        # ↑ 把微信带到前台。
        target = name.strip()
        # ↑ 目标群名去空格。

        # 1) 先 ESC 退出搜一搜/任何覆盖层
        # ↑ 注释：策略1：先按 ESC 关掉可能弹出的搜索/覆盖层，回到聊天视图。
        pyautogui.keyDown('esc')
        # ↑ 按下 ESC 键。
        pyautogui.keyUp('esc')
        # ↑ 松开 ESC 键。
        time.sleep(0.5)
        # ↑ 暂停 0.5 秒等界面恢复。

        # 如果已经在这个群，直接成功
        # ↑ 注释：如果当前已经开着这个群，直接算成功。
        if self._current_chat_title_matches(name):
            # ↑ 检查当前聊天标题是不是就是这个群……
            logging.info("当前已在群「%s」。", name)
            # ↑ 记日志。
            return True
            # ↑ 返回成功。

        # 2) 在当前侧栏可见区域查找
        # ↑ 注释：策略2：在可见会话列表里找。
        if self._find_and_click_in_sidebar(name):
            # ↑ 找到并点开……
            return True
            # ↑ 成功。

        # 3) 滚动侧栏查找：先滚到顶，再向下滚多轮
        # ↑ 注释：策略3：滚动查找。
        logging.info("侧栏当前可见区域未找到「%s」，开始滚动查找。", name)
        # ↑ 记日志。
        if self._scroll_sidebar_find(name):
            # ↑ 滚动找到……
            return True
            # ↑ 成功。

        # 4) 通讯录路线：点击侧栏底部「通讯录」→ 找「群聊」→ 找群名
        # ↑ 注释：策略4：通讯录→群聊。
        logging.info("滚动查找未果，尝试通讯录-群聊路线。")
        # ↑ 记日志。
        if self._via_contacts_open_group(name):
            # ↑ 通讯录路线成功……
            return True
            # ↑ 成功。

        # 5) 最后兜底：搜索框（OCR 定位，输入群名，点实时结果）
        # ↑ 注释：策略5：搜索框兜底。
        logging.info("尝试用搜索框打开群「%s」。", name)
        # ↑ 记日志。
        if self._search_open_group(name):
            # ↑ 搜索打开成功……
            return True
            # ↑ 成功。

        logging.warning("所有策略均未能打开群「%s」。建议：①确认群名无误；②在微信里手动打开一次该群，让它进入最近会话列表。", name)
        # ↑ 全部失败，给详细建议。
        return False
        # ↑ 返回失败。

    def _find_and_click_in_sidebar(self, name, save_debug=True):
        mark("_find_and_click_in_sidebar", "侧栏精确匹配点击")  # AUTO-INSTRUMENTED
        """在左侧会话列表当前可见区域找群名并点击。成功返回 True。

        校验策略：点击后若右侧标题栏 OCR 未命中，则保存右侧标题栏 debug 图；
        同时检查侧栏里目标群是否仍存在（高亮），存在也视为成功，避免 OCR 漏识别标题导致假失败。
        """
        # ↑ docstring 解释：在左侧栏找群名、点击它，并校验是否真的打开。
        items = self._ocr_screen(0.0, self.title_ratio, self.sidebar_ratio, 1.0)
        # ↑ 对左侧栏区域做 OCR，得到所有认到的文字及坐标。
        if save_debug:
            # ↑ 如果需要存调试图……
            self._save_debug_sidebar("sidebar_find", items)
            # ↑ 保存一张带框的侧栏调试图。
        for text, sx, sy in items:
            # ↑ 遍历每个认到的文字（sx,sy 是屏幕坐标）。
            if self._group_match(text, name):
                # ↑ 如果这个文字匹配目标群名……
                try:
                    # ↑ try 保护：点击操作可能失败。
                    human_click(sx, sy)
                    # ↑ 拟人化点击这个位置（像真人一样移动鼠标去点）。
                    time.sleep(1.2)
                    # ↑ 暂停 1.2 秒，等聊天切换。
                    if self._current_chat_title_matches(name, save_debug=save_debug):
                        # ↑ 校验右侧标题栏是否显示该群名……
                        logging.info("已在侧栏打开群「%s」。", name)
                        # ↑ 记日志：成功打开。
                        return True
                        # ↑ 返回成功。
                    # 标题栏 OCR 没命中，但聊天区已显示该群（微信4.0群名在聊天区顶部）-> 已打开
                    # ↑ 注释：有时标题栏 OCR 认不到，但聊天区顶部显示了群名，也算打开。
                    if self._chat_shows_group(name):
                        # ↑ 检查聊天区是否显示该群名……
                        logging.info("标题栏 OCR 未命中，但聊天区已显示「%s」，视为已打开。", name)
                        # ↑ 记日志。
                        return True
                        # ↑ 返回成功。
                    # 再重试一次点击
                    # ↑ 注释：标题栏没命中，可能第一次点偏了，再点一次试试。
                    human_click(sx, sy)
                    # ↑ 再拟人点击一次。
                    time.sleep(1.0)
                    # ↑ 暂停 1 秒。
                    if self._current_chat_title_matches(name, save_debug=save_debug):
                        # ↑ 再次校验标题栏……
                        logging.info("重试后在侧栏打开群「%s」。", name)
                        # ↑ 记日志。
                        return True
                        # ↑ 返回成功。
                except Exception as e:
                    # ↑ 点击过程中出错……
                    logging.error("点击群名失败: %s", e)
                    # ↑ 记错误日志。
        return False
        # ↑ 遍历完都没成功，返回失败。

    def _scroll_sidebar_find(self, name, max_down_rounds=8):
        mark("_scroll_sidebar_find", "滚动侧栏查找")  # AUTO-INSTRUMENTED
        """滚动左侧会话列表查找群名。先滚到顶，再向下滚多轮。"""
        # ↑ docstring 解释：边滚侧栏边找群名，先顶后下。
        left, top, right, bottom, w, h = self._rect()
        # ↑ 取窗口矩形和尺寸。
        # 侧栏中心 x，列表区域 y（标题栏下方到底部）
        # ↑ 注释：下面算滚动要用的坐标。
        scroll_x = int(left + w * self.sidebar_ratio * 0.5)
        # ↑ 侧栏中心 x（滚动位置）。
        list_top = int(top + h * self.title_ratio + 10)
        # ↑ 列表区域顶部 y（标题栏下方一点）。
        list_bottom = int(bottom - 10)
        # ↑ 列表区域底部 y（窗口底部上方一点）。
        scroll_y = int((list_top + list_bottom) / 2)
        # ↑ 滚动 y = 列表区域正中。

        # 先滚到顶部（多次向上滚）
        # ↑ 注释：先把列表滚到顶。
        for _ in range(6):
            # ↑ 向上滚 6 次。
            pyautogui.scroll(8, scroll_x, scroll_y)
            # ↑ 向上滚。
            time.sleep(0.25)
            # ↑ 每次停 0.25 秒。
        # 再向下滚动查找
        # ↑ 注释：然后一边向下滚、一边尝试在可见区找群。
        for i in range(max_down_rounds):
            # ↑ 最多向下滚 max_down_rounds 轮（默认 8）。
            if self._find_and_click_in_sidebar(name, save_debug=(i == 0)):
                # ↑ 每滚一轮就尝试在可见区找并点群（第一轮存调试图）……
                return True
                # ↑ 找到就成功。
            # 向下滚动一段
            # ↑ 注释：这轮没找到，向下滚一段再试。
            pyautogui.scroll(-6, scroll_x, scroll_y)
            # ↑ 向下滚（负数=向下）。
            time.sleep(0.5)
            # ↑ 停 0.5 秒。
        return False
        # ↑ 滚完都没找到，返回失败。

    def _via_contacts_open_group(self, name):
        mark("_via_contacts_open_group", "通讯录-群聊路线")  # AUTO-INSTRUMENTED
        """通过通讯录→群聊打开群。点击侧栏底部约 85%-95% 高度的「通讯录」入口。"""
        # ↑ docstring 解释：从通讯录里找群聊，再找目标群。
        left, top, right, bottom, w, h = self._rect()
        # ↑ 取窗口矩形。
        # 左侧底部常见通讯录图标/文字区域
        # ↑ 注释：下面在侧栏底部几个候选高度找"通讯录"。
        candidates_y = [0.88, 0.92, 0.96]
        # ↑ 通讯录入口大概在侧栏 88%、92%、96% 高度处，列几个候选。
        cx = int(left + w * self.sidebar_ratio * 0.5)
        # ↑ 侧栏中心 x。
        for y_ratio in candidates_y:
            # ↑ 逐个尝试候选高度……
            cy = int(top + h * y_ratio)
            # ↑ 算出候选 y 坐标。
            try:
                # ↑ try 保护：点击/查找可能失败。
                human_click(cx, cy)
                # ↑ 拟人点击这个候选位置（可能是"通讯录"入口）。
                time.sleep(1.0)
                # ↑ 暂停 1 秒。
                # 找「群聊」
                # ↑ 注释：进入通讯录后找"群聊"分类。
                items = self._ocr_screen(0.0, 0.0, 1.0, 1.0)
                # ↑ 全窗 OCR，找"群聊"二字。
                for text, sx, sy in items:
                    # ↑ 遍历认到的文字……
                    if self._group_match(text, "群聊") or "群聊" in (text or ""):
                        # ↑ 如果认到"群聊"……
                        human_click(sx, sy)
                        # ↑ 点击"群聊"。
                        time.sleep(1.0)
                        # ↑ 暂停 1 秒。
                        # 在群聊列表里找目标群
                        # ↑ 注释：进入群聊列表后找目标群。
                        if self._find_and_click_anywhere(name):
                            # ↑ 在整窗范围内找并点目标群……
                            return True
                            # ↑ 成功返回。
            except Exception as e:
                # ↑ 出错就记调试日志，继续下一个候选。
                logging.debug("通讯录路线尝试失败: %s", e)
        return False
        # ↑ 都失败，返回 False。

    def _find_and_click_anywhere(self, name):
        mark("_find_and_click_anywhere", "全窗模糊点击")  # AUTO-INSTRUMENTED
        """在全屏范围内找群名并点击，用于通讯录/群聊列表。"""
        # ↑ docstring 解释：在整个窗口里找群名并点击（用在通讯录/群聊列表场景）。
        items = self._ocr_screen(0.0, 0.0, 1.0, 1.0)
        # ↑ 全窗 OCR。
        for text, sx, sy in items:
            # ↑ 遍历认到的文字……
            if self._group_match(text, name):
                # ↑ 如果匹配目标群名……
                try:
                    # ↑ try 保护。
                    human_click(sx, sy)
                    # ↑ 拟人点击。
                    time.sleep(1.2)
                    # ↑ 暂停 1.2 秒。
                    if self._current_chat_title_matches(name):
                        # ↑ 校验标题栏……
                        logging.info("通过通讯录打开群「%s」。", name)
                        # ↑ 记日志。
                        return True
                        # ↑ 成功。
                except Exception:
                    # ↑ 出错就忽略，继续。
                    pass
        return False
        # ↑ 没找到，返回失败。

    def _search_open_group(self, name):
        mark("_search_open_group", "搜索框打开群")  # AUTO-INSTRUMENTED
        """用左侧搜索框：OCR 定位「搜索」文字并点击（不再写死坐标），输入群名，
        在实时下拉结果里点匹配项（不按 Enter，避免进搜一搜）。不依赖群在近期列表。
        """
        # ↑ docstring 解释：用搜索框搜群名打开，关键是不按回车（避免进搜一搜网页）。
        self._focus()
        # ↑ 把微信带到前台。
        target = name.strip()
        # ↑ 目标群名去空格。
        # 1) 定位搜索框：OCR 找「搜索」文字；找不到再退回固定比例
        # ↑ 注释：步骤1：先 OCR 找"搜索"二字定位搜索框。
        items = self._ocr_screen(0.0, 0.0, self.sidebar_ratio, 0.5)
        # ↑ 在左侧上半区 OCR，找"搜索"。
        sx = sy = None
        # ↑ 先占个位：搜索框坐标，初始 None。
        for t, x, y in items:
            # ↑ 遍历认到的文字……
            if "搜索" in (t or ""):
                # ↑ 如果认到"搜索"……
                sx, sy = x, y
                # ↑ 记下它的坐标。
                break
                # ↑ 找到就停。
        if sx is None:
            # ↑ 如果没找到"搜索"文字（OCR 没认出来）……
            left, top, right, bottom, w, h = self._rect()
            # ↑ 回退：用固定比例算搜索框位置。
            sx, sy = int(left + w * self.sidebar_ratio * 0.5), int(top + h * 0.05)
            # ↑ 侧栏上半区中心附近。
        try:
            # ↑ try 保护：输入可能失败。
            human_click(sx, sy)
            # ↑ 拟人点击搜索框（获取焦点）。
            time.sleep(0.5)
            # ↑ 暂停 0.5 秒。
            pyautogui.hotkey("ctrl", "a")
            # ↑ 按 Ctrl+A 全选原有内容。
            time.sleep(0.1)
            # ↑ 暂停 0.1 秒。
            human_type(target)
            # ↑ 拟人化输入目标群名（像真人逐字打）。
            time.sleep(1.5)
            # ↑ 暂停 1.5 秒等下拉结果。
        except Exception as e:
            # ↑ 失败就记调试日志。
            logging.debug("搜索框输入失败: %s", e)
        # 2) 在左半屏实时结果里找群名
        # ↑ 注释：步骤2：在下拉结果里找群名并点。
        items = self._ocr_screen(0.0, 0.0, self.sidebar_ratio, 0.8)
        # ↑ 在左侧 0~80% 高度区域 OCR（下拉结果区）。
        for t, x, y in items:
            # ↑ 遍历认到的文字……
            if self._group_match(t, target):
                # ↑ 如果匹配目标群名……
                try:
                    # ↑ try 保护。
                    human_click(x, y)
                    # ↑ 拟人点击这个结果。
                    time.sleep(1.5)
                    # ↑ 暂停 1.5 秒。
                    if self._current_chat_title_matches(target) or self._chat_shows_group(target):
                        # ↑ 校验标题栏或聊天区是否显示该群……
                        logging.info("通过搜索打开群「%s」。", target)
                        # ↑ 记日志。
                        return True
                        # ↑ 成功。
                except Exception:
                    # ↑ 出错忽略。
                    pass
        return False
        # ↑ 没成功，返回失败。

    def _save_debug_sidebar(self, prefix, items):
        """保存当前侧栏截图并在图上标注 OCR 结果，便于排查。"""
        # ↑ 定义 _save_debug_sidebar（存侧栏调试图）：截图并画框，方便排查开群失败。
        try:
            # ↑ try 保护：存图可能失败。
            from PIL import ImageDraw
            # ↑ 导入画图工具。
            import os
            # ↑ 导入 os。
            os.makedirs("debug", exist_ok=True)
            # ↑ 建 debug 目录。
            img, left, top, w, h = self._capture_window_pw()
            # ↑ 截整窗图。
            iw, ih = img.size
            # ↑ 取图片尺寸。
            sx_scale = (w / iw) if iw else 1.0
            # ↑ 横向缩放。
            sy_scale = (h / ih) if ih else 1.0
            # ↑ 纵向缩放。
            crop = img.crop((0, int(ih * self.title_ratio),
                             int(iw * self.sidebar_ratio), ih))
            # ↑ 裁出左侧栏小图。
            d = ImageDraw.Draw(crop)
            # ↑ 拿画笔。
            for text, absx, absy in items:
                # ↑ 遍历 OCR 结果……
                ax = int((absx - left) / sx_scale)
                # ↑ 换算成小图内 x。
                ay = int((absy - (top + h * self.title_ratio)) / sy_scale)
                # ↑ 换算成小图内 y。
                d.rectangle([ax - 4, ay - 4, ax + 80, ay + 4], outline="red", width=2)
                # ↑ 画红框。
            path = os.path.join("debug", f"{prefix}_{int(time.time())}.png")
            # ↑ 拼出调试图路径（带时间戳，避免重名覆盖）。
            crop.save(path)
            # ↑ 保存调试图。
        except Exception:
            # ↑ 失败就忽略（调试功能不应影响主流程）。
            pass

    def _save_debug_right(self, prefix, items):
        """保存右侧标题栏/聊天区截图并标注 OCR 结果，便于排查标题校验失败。"""
        # ↑ 定义 _save_debug_right（存右侧调试图）：和上面类似，但针对标题栏/聊天区。
        try:
            # ↑ try 保护。
            from PIL import ImageDraw
            # ↑ 导入画图工具。
            import os
            # ↑ 导入 os。
            os.makedirs("debug", exist_ok=True)
            # ↑ 建 debug 目录。
            img, left, top, w, h = self._capture_window_pw()
            # ↑ 截整窗图。
            iw, ih = img.size
            # ↑ 取图片尺寸。
            sx_scale = (w / iw) if iw else 1.0
            # ↑ 横向缩放。
            sy_scale = (h / ih) if ih else 1.0
            # ↑ 纵向缩放。
            crop = img.crop((int(iw * self.sidebar_ratio), 0, iw,
                             int(ih * (self.title_ratio + 0.15))))
            # ↑ 裁出右侧标题栏那块小图。
            d = ImageDraw.Draw(crop)
            # ↑ 拿画笔。
            for text, absx, absy in items:
                # ↑ 遍历 OCR 结果……
                ax = int((absx - (left + w * self.sidebar_ratio)) / sx_scale)
                # ↑ 换算 x。
                ay = int((absy - top) / sy_scale)
                # ↑ 换算 y。
                d.rectangle([ax - 4, ay - 4, ax + 80, ay + 4], outline="red", width=2)
                # ↑ 画红框。
            path = os.path.join("debug", f"{prefix}_{int(time.time())}.png")
            # ↑ 拼调试图路径（带时间戳）。
            crop.save(path)
            # ↑ 保存。
        except Exception:
            # ↑ 失败忽略。
            pass

    def _current_chat_title_matches(self, name, save_debug=True):
        mark("_current_chat_title_matches", "校验当前聊天标题")  # AUTO-INSTRUMENTED
        """检查右侧聊天标题栏/聊天区顶部是否显示目标群名。

        微信 4.0 标题栏可能较矮或文字被头像遮挡，OCR 容易漏。这里同时扫描：
        ① 右侧顶部 title_ratio+0.12 区域；② 聊天区顶部一小条。
        找不到时保存 debug 图便于后续微调。
        """
        # ↑ docstring 解释：校验"现在打开的聊天标题是不是目标群"。因为 OCR 可能漏，
        #   所以扫两个区域，找不到还存调试图。
        # 区域 1：标题栏（更宽一些）
        # ↑ 注释：区域1：标题栏。
        items = self._ocr_screen(self.sidebar_ratio, 0.0, 1.0, self.title_ratio + 0.12)
        # ↑ 对右侧顶部（含标题栏）做 OCR。
        if save_debug:
            # ↑ 需要存调试图……
            self._save_debug_right("right_title", items)
            # ↑ 存右侧标题调试图。
        for text, _sx, _sy in items:
            # ↑ 遍历认到的文字……
            if self._group_match(text, name):
                # ↑ 如果匹配目标群名……
                return True
                # ↑ 校验通过。
        # 区域 2：聊天区顶部再扫一下（某些布局群名在消息区域顶部）
        # ↑ 注释：区域2：聊天区顶部（有些布局群名在消息区顶部）。
        items2 = self._ocr_screen(
            self.sidebar_ratio + 0.02, self.title_ratio,
            min(1.0, self.sidebar_ratio + 0.40), self.title_ratio + 0.10
        )
        # ↑ 对聊天区顶部一小条做 OCR。
        for text, _sx, _sy in items2:
            # ↑ 遍历认到的文字……
            if self._group_match(text, name):
                # ↑ 匹配目标群名……
                return True
                # ↑ 校验通过。
        return False
        # ↑ 两个区域都没找到，返回失败。

    def _sidebar_still_shows(self, name):
        mark("_sidebar_still_shows", "校验侧栏仍显示群")  # AUTO-INSTRUMENTED
        """点击后检查侧栏里目标群名是否仍然存在。用于标题栏 OCR 漏识别时的辅助判断。"""
        # ↑ docstring 解释：点击后看侧栏里是否还显示该群名（辅助判断，证明群在列表里）。
        items = self._ocr_screen(0.0, self.title_ratio, self.sidebar_ratio, 1.0)
        # ↑ 对左侧栏做 OCR。
        for text, _sx, _sy in items:
            # ↑ 遍历认到的文字……
            if self._group_match(text, name):
                # ↑ 匹配目标群名……
                return True
                # ↑ 仍在侧栏显示。
        return False
        # ↑ 没找到，返回 False。

    def _chat_shows_group(self, name):
        mark("_chat_shows_group", "校验聊天区显示群名")  # AUTO-INSTRUMENTED
        """微信 4.0 群名常显示在聊天区顶部（带未读角标，如「自己5人群(6)」），
        而非标题栏。扫描右侧聊天区（排除侧栏）确认当前打开的正是目标群——
        这是比「侧栏仍显示群名」更强的成功判定（侧栏里群名一直都在，不能证明已打开）。
        """
        # ↑ docstring 解释：微信 4.0 群名常在聊天区顶部（带未读角标），扫描这里确认已打开。
        #   这比"侧栏还显示群名"更可靠（侧栏一直显示所有群，不能证明当前打开了它）。
        needle = self._norm_group(name)
        # ↑ 把目标群名规范化（去标点，便于和带角标的群名比对）。
        items = self._ocr_screen(self.sidebar_ratio, 0.0, 1.0, 0.6)
        # ↑ 对右侧聊天区上半部（0~60% 高度）做 OCR。
        for text, _sx, _sy in items:
            # ↑ 遍历认到的文字……
            if self._group_match(text, name):
                # ↑ 直接匹配目标群名……
                return True
                # ↑ 校验通过。
            # 群名带未读角标时归一化比对（"自己5人群(6)" -> "自己人群"）
            # ↑ 注释：群名带未读角标如"(6)"，归一化后比对其核心名。
            if needle and needle in self._norm_group(text):
                # ↑ 如果目标群名核心出现在归一化后的文字里……
                return True
                # ↑ 校验通过。
        return False
        # ↑ 没找到，返回 False。

    # ---- 读消息 ----
    # ↑ 注释分隔：下面这组方法负责"读群里消息"。

    def read_messages(self):
        mark("read_messages", "读取群消息")  # AUTO-INSTRUMENTED
        # ↑ 定义 read_messages（读消息）：读取当前群聊天区的文字，返回 (文字, 屏幕y)。
        self._focus()
        # ↑ 把微信带到前台。
        # 聊天区域：右侧，排除顶部标题栏；读到底部「接近输入框上缘」，
        # 否则最新一条消息（紧贴输入框）会被裁掉而漏读。返回 (文本, 屏幕y) 以便
        # 上层按 y 取「最底部=最新」的提问。
        # ↑ 注释：读消息要覆盖到"紧贴输入框"的最新一条，否则最新消息会被裁掉；
        #   返回屏幕 y 是为了上层按"最靠下=最新"来定位提问。
        read_bottom = 1.0 - max(0.04, self.input_ratio * 0.35)
        # ↑ 计算读取区域的下边界（稍微超过输入框上缘一点，确保最新消息不被裁）。
        items = self._ocr_screen(
            self.sidebar_ratio, self.title_ratio, 1.0, read_bottom
        )
        # ↑ 对右侧聊天区（排除标题栏、到 read_bottom 为止）做 OCR。
        return [(t.strip(), sy) for t, _sx, sy in items if t.strip()]
        # ↑ 把结果整理成 (去空格文字, 屏幕y) 列表，过滤掉空文字，返回。

    # ---- 发消息 ----
    # ↑ 注释分隔：下面这组方法负责"发消息"。

    def _focus(self):
        """把微信带到前台。

        优先用 bring_to_front（SetWindowPos 置顶 + SetForegroundWindow）——重叠布局下
        单纯点标题栏会被别的窗口挡住、点不中。仅当该方式失败时才兜底点标题栏。
        不使用 AttachThreadInput / SetWindowPos(TOPMOST) 等强风控 API。
        """
        # ↑ docstring 解释：_focus 把微信窗口弄到最前台，好让后续点击打在它身上。
        self._restore()
        # ↑ 先还原（若最小化）。
        try:
            # ↑ try 保护：取句柄可能失败。
            hwnd = int(self.window.NativeWindowHandle)
            # ↑ 取窗口句柄。
        except Exception:
            # ↑ 失败就当 0。
            hwnd = 0
        if not hwnd:
            # ↑ 没有句柄就直接返回（没法操作）。
            return
        if is_foreground(hwnd):
            # ↑ 如果窗口已经在前台……
            time.sleep(0.3)
            # ↑ 暂停 0.3 秒。
            return
            # ↑ 已在前台，不用再操作。
        if bring_to_front(hwnd):
            # ↑ 尝试用"置顶+置前"方式把它带到前台……
            time.sleep(0.3)
            # ↑ 暂停 0.3 秒。
            return
            # ↑ 成功，返回。
        # 兜底：点标题栏中部（避开左上角图标 / 右侧按钮）
        # ↑ 注释：置顶方式失败，就兜底"点标题栏中部"激活（避开图标和按钮）。
        try:
            # ↑ try 保护。
            left, top, right, bottom, w, h = self._rect()
            # ↑ 取窗口矩形。
        except Exception:
            # ↑ 失败就返回。
            return
        tx = int(left + w * 0.5)
        # ↑ 标题栏中部 x（窗口水平中心）。
        ty = int(top + max(8, int(h * 0.025)))
        # ↑ 标题栏中部 y（上边往下一点，避开图标）。
        human_click(tx, ty)
        # ↑ 拟人点击标题栏中部。
        time.sleep(0.3)
        # ↑ 暂停 0.3 秒。

    def send_text(self, text):
        # ↑ 定义 send_text（发文字）：把一段文字作为消息发到当前群。
        left, top, right, bottom, w, h = self._rect()
        # ↑ 取窗口矩形。
        cx = int(left + w * self.input_x)
        # ↑ 算出输入框中心 x。
        cy = int(top + h * self.input_y)
        # ↑ 算出输入框中心 y。
        try:
            # ↑ try 保护：整个发送过程可能失败。
            self._focus()
            # ↑ 先把微信带到前台。
            human_click(cx, cy)
            # ↑ 拟人点击输入框（获取焦点）。
            time.sleep(0.4)
            # ↑ 暂停 0.4 秒。
            # 先清空可能残留内容；中文/ASCII 一律走剪贴板 Ctrl+V，
            # 绕过中文输入法（IME）吞掉 SendInput 键入的字符。
            # ↑ 注释：下面先清空输入框。中文一律用"剪贴板 Ctrl+V"输入，绕开输入法吞字问题。
            pyautogui.hotkey("ctrl", "a")
            # ↑ Ctrl+A 全选输入框现有内容。
            time.sleep(0.1)
            # ↑ 暂停 0.1 秒。
            pyautogui.press("delete")
            # ↑ 按删除键清空。
            time.sleep(0.2)
            # ↑ 暂停 0.2 秒。
            pyperclip.copy(text)
            # ↑ 把要发的文字复制到系统剪贴板。
            time.sleep(0.1)
            # ↑ 暂停 0.1 秒。
            pyautogui.hotkey("ctrl", "v")
            # ↑ Ctrl+V 粘贴文字到输入框（中文不会丢）。
            time.sleep(0.3)
            # ↑ 暂停 0.3 秒。
            human_send_enter()
            # ↑ 拟人化按回车发送。
            return True
            # ↑ 返回发送成功。
        except Exception as e:
            # ↑ 发送过程出错……
            logging.error("发送失败: %s", e)
            # ↑ 记错误日志。
            return False
            # ↑ 返回失败。

    # ---- 发图片 ----
    # ↑ 注释分隔：下面这组方法负责"发图片"（把答案图发到群）。

    def send_image(self, image_path, verify=True, retries=2):
        mark("send_image", "回发回答截图")  # AUTO-INSTRUMENTED
        """把一张图片作为消息发送到当前打开的微信群。

        做法：把图片写入系统剪贴板（CF_DIB），点击微信输入框后 Ctrl+V 粘贴，
        微信会把它变成图片预览，再回车发送。无需依赖微信内部控件。

        默认开启校验：对比发送前后聊天区的 OCR 文本，若出现了新文字（发出的
        图片本身带文字，OCR 可读到），即认为发送成功；否则重试，避免「日志显示
        已发送、微信里却没收到」的静默失败。
        """
        # ↑ docstring 解释：send_image 把答案图片发到群。做法是"图片入剪贴板 → 点输入框 →
        #   Ctrl+V 粘贴 → 回车"。默认会校验：对比发送前后聊天区，若出现新文字说明发送成功。
        if not os.path.exists(image_path):
            # ↑ 如果图片文件不存在……
            logging.error("图片不存在: %s", image_path)
            # ↑ 记错误日志。
            return False
            # ↑ 返回失败。

        def _chat_blob():
            # ↑ 定义内部函数 _chat_blob（取聊天区文字）：用来对比"发送前后"聊天区内容。
            try:
                # ↑ try 保护：OCR 可能失败。
                # 下探到贴近输入框（新发的图片消息恰在输入框正上方），
                # 否则校验区会在 1.0-input_ratio 处把刚发出的图裁掉，造成误判未发送。
                # ↑ 注释：读取区域要探到贴近输入框，否则刚发的图会被裁掉、误判没发出。
                bottom = 1.0 - max(0.04, self.input_ratio * 0.35)
                # ↑ 算读取下边界（贴近输入框）。
                items = self._ocr_screen(self.sidebar_ratio, self.title_ratio,
                                         1.0, bottom)
                # ↑ 对聊天区做 OCR。
                return " ".join(t.strip() for t, _x, _y in items if t.strip())
                # ↑ 把所有认到的文字拼成一个长字符串返回。
            except Exception:
                # ↑ 失败就返回空。
                return ""

        def _one_shot():
            # ↑ 定义内部函数 _one_shot（发一次）：执行一次"粘贴图片+回车"动作。
            left, top, right, bottom, w, h = self._rect()
            # ↑ 取窗口矩形。
            cx = int(left + w * self.input_x)
            # ↑ 输入框中心 x。
            cy = int(top + h * self.input_y)
            # ↑ 输入框中心 y。
            self._focus()
            # ↑ 把微信带到前台。
            human_click(cx, cy)
            # ↑ 拟人点击输入框。
            time.sleep(0.4)
            # ↑ 暂停 0.4 秒。
            # 把图片写入系统剪贴板（CF_DIB），再 Ctrl+V 粘贴进微信输入框，
            # 由微信渲染成图片预览后回车发送。这是用户确认的「保留方案」。
            # ↑ 注释：下面把图片写进剪贴板，再粘贴，微信会渲染成预览、回车发送。
            self._copy_image_to_clipboard(image_path)
            # ↑ 把图片以 CF_DIB 形式写入系统剪贴板。
            time.sleep(0.4)
            # ↑ 暂停 0.4 秒。
            pyautogui.hotkey("ctrl", "v")
            # ↑ Ctrl+V 粘贴图片到输入框（变成预览）。
            time.sleep(2.0)  # 等微信把图片渲染成预览
            # ↑ 暂停 2 秒，等微信把图片渲染成预览图。
            human_send_enter()
            # ↑ 拟人回车发送。
            time.sleep(1.0)
            # ↑ 暂停 1 秒。

        before = _chat_blob() if verify else None
        # ↑ 如果开启校验，先记录"发送前"聊天区文字。
        for attempt in range(1, retries + 1):
            # ↑ 最多重试 retries 次（默认 2）。
            try:
                # ↑ try 保护：发送可能失败。
                _one_shot()
                # ↑ 执行一次发送。
            except Exception as e:
                # ↑ 发送出错……
                logging.error("发送图片失败(第%d次): %s", attempt, e)
                # ↑ 记错误日志。
                continue
                # ↑ 重试下一次。
            if not verify:
                # ↑ 如果没开校验……
                return True
                # ↑ 直接认为成功。
            time.sleep(1.5)
            # ↑ 暂停 1.5 秒等发送完成。
            after = _chat_blob()
            # ↑ 记录"发送后"聊天区文字。
            # 发出的图片本身含文字，OCR 应能读到「before 里没有的新 token」
            # ↑ 注释：发的图若含文字，OCR 会读到新内容，据此判断是否发送成功。
            new_tokens = [t for t in after.split() if len(t) >= 4 and t not in before]
            # ↑ 找出"发送后新增的、长度≥4 的文字块"（说明图发出去了）。
            if new_tokens:
                # ↑ 如果有新增文字……
                logging.info("图片发送成功（聊天区已更新，新增 %d 段文字）。", len(new_tokens))
                # ↑ 记日志。
                return True
                # ↑ 返回成功。
            logging.warning("第 %d 次发送后聊天区未变化，重试。", attempt)
            # ↑ 没变化，记警告，准备重试。
        logging.error("图片发送失败：多次尝试后聊天区仍无变化。")
        # ↑ 重试多次仍失败，记错误日志。
        return False
        # ↑ 返回失败。

    @staticmethod
    def _copy_image_to_clipboard(path):
        mark("_copy_image_to_clipboard", "图片写入剪贴板")  # AUTO-INSTRUMENTED
        """把图片以 CF_DIB 形式放到系统剪贴板（微信可识别）。

        用 ctypes 以 GMEM_MOVEABLE 全局内存方式写入 —— 这是微信能稳定识别图片
        粘贴的最可靠做法。pywin32 直接传 bytes 给 SetClipboardData 在 4.0 上常
        写入无效数据（返回成功但粘贴不出图），故这里不再优先用它。
        """
        # ↑ docstring 解释：_copy_image_to_clipboard 把图片以 CF_DIB 格式写进剪贴板。
        #   微信只认这种格式，且必须用"可移动全局内存"方式写才稳。
        import io
        # ↑ 导入 io（内存字节流）。
        import ctypes
        # ↑ 导入 ctypes（Windows 底层）。
        from PIL import Image
        # ↑ 从 Pillow 导入 Image（读图）。

        img = Image.open(path).convert("RGB")
        # ↑ 打开图片并转成 RGB 模式。
        out = io.BytesIO()
        # ↑ 创建一个内存字节流（临时存放图片数据）。
        img.save(out, "BMP")
        # ↑ 把图片以 BMP 格式写入内存流。
        data = out.getvalue()[14:]  # 去掉 14 字节 BMP 文件头，保留 DIB
        # ↑ 取出字节数据，并去掉前面 14 字节的 BMP 文件头（只留 DIB 数据部分，微信要的是这个）。

        cf_dib = 8  # CF_DIB
        # ↑ CF_DIB 这个剪贴板格式代号是 8。
        GMEM_MOVEABLE = 0x0002
        # ↑ 全局内存标志：可移动。
        GMEM_ZEROINIT = 0x0040
        # ↑ 全局内存标志：初始化为零。
        user32 = ctypes.windll.user32
        # ↑ 取 user32 系统库（管窗口/剪贴板）。
        kernel32 = ctypes.windll.kernel32
        # ↑ 取 kernel32 系统库（管内存）。
        wt = ctypes.wintypes
        # ↑ 取 Windows 类型定义。
        kernel32.GlobalAlloc.argtypes = [wt.DWORD, ctypes.c_size_t]
        # ↑ 声明 GlobalAlloc（分配全局内存）的参数类型。
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        # ↑ 声明它的返回类型（内存句柄）。
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        # ↑ 声明 GlobalLock（锁定内存）的参数类型。
        kernel32.GlobalLock.restype = ctypes.c_void_p
        # ↑ 声明它的返回类型（内存指针）。
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        # ↑ 声明 GlobalUnlock（解锁）的参数类型。
        kernel32.GlobalUnlock.restype = wt.BOOL
        # ↑ 声明它的返回类型（布尔）。
        user32.OpenClipboard.argtypes = [wt.HWND]
        # ↑ 声明 OpenClipboard（打开剪贴板）的参数类型。
        user32.OpenClipboard.restype = wt.BOOL
        # ↑ 声明它的返回类型。
        user32.EmptyClipboard.argtypes = []
        # ↑ 声明 EmptyClipboard（清空剪贴板）的参数类型（无参数）。
        user32.EmptyClipboard.restype = wt.BOOL
        # ↑ 声明它的返回类型。
        user32.SetClipboardData.argtypes = [wt.UINT, ctypes.c_void_p]
        # ↑ 声明 SetClipboardData（写入剪贴板数据）的参数类型。
        user32.SetClipboardData.restype = ctypes.c_void_p
        # ↑ 声明它的返回类型。
        user32.CloseClipboard.argtypes = []
        # ↑ 声明 CloseClipboard（关闭剪贴板）的参数类型（无参数）。
        user32.CloseClipboard.restype = wt.BOOL
        # ↑ 声明它的返回类型。

        hmem = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, len(data))
        # ↑ 分配一块"可移动+清零"的全局内存，大小等于图片数据长度。
        if not hmem:
            # ↑ 如果分配失败（返回空）……
            raise ctypes.WinError()
            # ↑ 抛出 Windows 错误。
        try:
            # ↑ try 保护：写入过程可能失败。
            ptr = kernel32.GlobalLock(hmem)
            # ↑ 锁定这块内存，拿到可写指针。
            if not ptr:
                # ↑ 如果锁定失败……
                raise ctypes.WinError()
                # ↑ 抛错。
            ctypes.memmove(ptr, data, len(data))
            # ↑ 把图片数据从 data 拷贝到这块内存里。
            kernel32.GlobalUnlock(hmem)
            # ↑ 解锁内存（写完就可以解）。
            if not user32.OpenClipboard(0):
                # ↑ 打开剪贴板（参数 0 表示当前线程）……
                raise ctypes.WinError()
                # ↑ 打不开就抛错。
            try:
                # ↑ 内层 try：写入后要保证关闭剪贴板。
                user32.EmptyClipboard()
                # ↑ 先清空剪贴板原有内容。
                if not user32.SetClipboardData(cf_dib, hmem):
                    # ↑ 把图片数据（DIB）写入剪贴板；如果失败……
                    raise ctypes.WinError()
                    # ↑ 抛错。
            finally:
                # ↑ finally：无论成败都执行……
                user32.CloseClipboard()
                # ↑ 关闭剪贴板（释放占用）。
        except Exception:
            # ↑ 整个过程出错……
            # 失败时释放内存，避免泄漏
            # ↑ 注释：下面释放内存，避免内存泄漏。
            try:
                # ↑ try 保护：释放可能失败。
                kernel32.GlobalFree(hmem)
                # ↑ 释放这块全局内存。
            except Exception:
                # ↑ 失败忽略。
                pass
            raise
            # ↑ 重新抛出原错误，让上层知道失败了。

    # ---- 校准 ----
    # ↑ 注释分隔：下面这个方法用于"校准坐标"。

    def calibrate(self):
        # ↑ 定义 calibrate（校准）：截图并打印微信窗口里所有 OCR 认到的文字及坐标。
        left, top, right, bottom, w, h = self._rect()
        # ↑ 取窗口矩形。
        print("================ 微信窗口截图 OCR ================")
        # ↑ 打印分隔标题。
        print("窗口矩形 left=%d top=%d right=%d bottom=%d w=%d h=%d" % (
            left, top, right, bottom, w, h))
        # ↑ 打印窗口位置尺寸。
        items = self._ocr_screen(0.0, 0.0, 1.0, 1.0)
        # ↑ 全窗 OCR。
        for text, sx, sy in items:
            # ↑ 遍历认到的文字……
            print("  text=%-30s screen=(%d,%d)" % (text, sx, sy))
            # ↑ 打印"文字 + 屏幕坐标"，方便你对照调整 config 里的比例。
        print("=================================================")
        # ↑ 打印分隔结尾。
