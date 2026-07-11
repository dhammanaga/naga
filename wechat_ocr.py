# -*- coding: utf-8 -*-
"""基于「截图 + 中文 OCR + 模拟坐标点击」的微信控制器。

适用场景：微信 4.0（Qt 重写）等不向 UI Automation 暴露聊天内容的版本。
uiautomation 仅用于：定位微信窗口、取窗口矩形、置前窗口。
其余读/写全部走截图 OCR 与 pyautogui 模拟。

坐标说明：所有点击都基于「窗口左上角 + 截图内相对坐标」换算成屏幕绝对坐标。
布局比例可在 config.json 的 wechat.ocr_layout 调整（因分辨率/缩放而异）。
"""
import logging
import os
import re
import time
import ctypes

import uiautomation as auto
import win32gui
import win32ui
import win32con
import pyautogui
import pyperclip
from PIL import Image
from PIL import ImageGrab

from ocr_engine import ocr_image
from flow_runtime import mark  # AUTO-INSTRUMENTED


def _force_foreground(hwnd):
    """可靠地把窗口抢到前台，绕过 Windows 前台锁。

    仅用 SetForegroundWindow 常因「调用线程不是前台线程」而静默失败（尤其有其它
    程序/终端抢焦点时），导致截图/点击打到别的窗口。这里：先恢复最小化窗口，
    再短暂按下/松开 ALT 清除前台锁，然后用 AttachThreadInput 把本线程与前台线程
    输入临时绑定后置前，并用 SetWindowPos 把窗口提到最前，确保后续点击落在本窗口。
    """
    if not hwnd:
        return False
    try:
        import win32process
        hwnd = int(hwnd)
        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE，防止最小化
        # 清除前台锁（部分 Windows 版本要求先 tap 一次 ALT）
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
        fg = ctypes.windll.user32.GetForegroundWindow()
        if fg == hwnd:
            return True
        fg_tid, _ = win32process.GetWindowThreadProcessId(fg)
        my_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
        attached = False
        if fg_tid and my_tid and fg_tid != my_tid:
            try:
                ctypes.windll.user32.AttachThreadInput(fg_tid, my_tid, True)
                attached = True
            except Exception:
                attached = False
        try:
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2
            u = ctypes.windll.user32
            u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
            u.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        finally:
            if attached:
                try:
                    ctypes.windll.user32.AttachThreadInput(fg_tid, my_tid, False)
                except Exception:
                    pass
        return True
    except Exception as e:
        logging.debug("force_foreground 失败: %s", e)
        return False


class OCRWeChatController:
    def __init__(self, cfg):
        self.cfg = cfg["wechat"]
        self.window = None
        self._layout = self.cfg.get("ocr_layout", {}) or {}
        self.sidebar_ratio = float(self._layout.get("sidebar_ratio", 0.26))
        self.title_ratio = float(self._layout.get("title_ratio", 0.07))
        self.input_ratio = float(self._layout.get("input_ratio", 0.16))
        self.input_x = float(self._layout.get("input_x", 0.55))
        self.input_y = float(self._layout.get("input_y", 0.90))

    # ---- 窗口 ----
    # 微信 3.x/4.x 常见顶层类名。不要按标题含"微信"兜底，否则 WorkBuddy 等
    # 标题为"微信IMA监控"的窗口会被误命中。
    WECHAT_CLASSES = (
        "WeChatMainWndForPC",
        "WeixinMainWndForPC",
        "Qt51514QWindowIcon",  # 微信 4.0 某些安装包顶层类名
    )

    def _restore_control(self, ctrl):
        """还原一个可能最小化的窗口（ShowWindow SW_RESTORE）。"""
        try:
            hwnd = int(ctrl.NativeWindowHandle)
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 9)
        except Exception:
            pass

    def find_window(self):
        # 1) win32gui 优先：按类名精确匹配微信主窗口，最稳，绕开 uiautomation
        #    在 Qt/Electron 窗口上偶发的 COM 崩溃（_locate 时 Exists/遍历会崩）。
        try:
            target_hwnd = None

            def _enum(hwnd, _):
                nonlocal target_hwnd
                if target_hwnd or not hwnd:
                    return
                try:
                    cls = win32gui.GetClassName(hwnd)
                except Exception:
                    return
                if cls in self.WECHAT_CLASSES:
                    try:
                        r = win32gui.GetWindowRect(hwnd)
                        if (r[2] - r[0]) >= 600 and (r[3] - r[1]) >= 400:
                            target_hwnd = hwnd
                    except Exception:
                        pass

            win32gui.EnumWindows(_enum, None)
            if target_hwnd:
                try:
                    ctrl = auto.ControlFromHandle(target_hwnd)
                except Exception:
                    ctrl = None
                if ctrl is not None:
                    self._restore_control(ctrl)
                    time.sleep(0.3)
                    logging.info("已定位微信窗口（win32gui）：Class=%r Name=%r",
                                 win32gui.GetClassName(target_hwnd),
                                 getattr(ctrl, "Name", ""))
                    self.window = ctrl
                    self._restore()
                    return ctrl
        except Exception as e:
            logging.debug("win32gui 定位微信失败，转 uiautomation: %s", e)

        # 2) uiautomation 兜底（整体 try/except 防 COM 崩溃）
        try:
            for cls in self.WECHAT_CLASSES:
                try:
                    c = auto.WindowControl(ClassName=cls)
                    if c.Exists(3):
                        self._restore_control(c)
                        time.sleep(0.3)
                        if self._valid_wechat_rect(c):
                            logging.info("已定位微信窗口（按类名）：Class=%r Name=%r",
                                         cls, getattr(c, "Name", ""))
                            self.window = c
                            self._restore()
                            return c
                        else:
                            logging.debug("类名 %r 命中但窗口尺寸异常，忽略。", cls)
                except Exception:
                    continue

            for w in auto.GetRootControl().GetChildren():
                try:
                    name = (w.Name or "").lower()
                    cls = (getattr(w, "ClassName", "") or "").lower()
                    if not (("微信" in name or "wechat" in name) and "ima" not in name and "workbuddy" not in name):
                        continue
                    self._restore_control(w)
                    time.sleep(0.3)
                    if not self._valid_wechat_rect(w):
                        continue
                    logging.info("已定位微信窗口（兜底）：Name=%r Class=%r",
                                 getattr(w, "Name", ""), getattr(w, "ClassName", ""))
                    self.window = w
                    self._restore()
                    return w
                except Exception:
                    continue
        except Exception as e:
            logging.debug("uiautomation 兜底定位失败: %s", e)

        logging.error(
            "找不到微信窗口。请确认：①微信桌面端已启动并登录；②窗口未最小化；③类名为 %s 之一。"
            "如果微信 4.0 类名不同，请把 config.json 的 wechat.class_name 改为真实类名。",
            self.WECHAT_CLASSES
        )
        return None

    def _valid_wechat_rect(self, ctrl):
        """微信主窗口应当足够大；排除 WorkBuddy 等小窗口误命中。"""
        try:
            r = ctrl.BoundingRectangle
            return r.width() >= 600 and r.height() >= 400
        except Exception:
            return False

    def _restore(self):
        """若微信窗口被最小化，用 Win32 API 还原，否则截图会得到 0 尺寸。"""
        try:
            hwnd = self.window.NativeWindowHandle
            if hwnd:
                # SW_RESTORE = 9
                ctypes.windll.user32.ShowWindow(int(hwnd), 9)
        except Exception as e:
            logging.warning("还原微信窗口失败（可忽略）: %s", e)

    def _rect(self):
        r = self.window.BoundingRectangle
        left, top, right, bottom = r.left, r.top, r.right, r.bottom
        w, h = r.width(), r.height()
        if w <= 0 or h <= 0:
            raise RuntimeError(
                "微信窗口尺寸为 0（left=%d top=%d right=%d bottom=%d）。"
                "请确认：①微信已登录且未最小化；②微信窗口当前可见。"
                "若仍异常，可能是微信多开/窗口被隐藏。" % (left, top, right, bottom)
            )
        return left, top, right, bottom, w, h

    def _capture_window_pw(self):
        """用 PrintWindow 截取微信窗口本体（无视与其它窗口的重叠/遮挡）。

        ImageGrab 按屏幕像素抓图，若微信与 IMA 等窗口在屏幕上重叠且对方置顶，
        左侧侧栏区域会抓到别的窗口内容，导致 OCR 不出群名。改用 PrintWindow
        直接渲染微信窗口本体，无论视觉上是否被遮挡都能正确截取。
        返回 (img, left, top, w, h)：img 为窗口整图，left/top/w/h 为窗口逻辑矩形。
        """
        left, top, right, bottom, w, h = self._rect()
        hwnd = int(self.window.NativeWindowHandle)
        hwndDC = win32gui.GetWindowDC(hwnd)
        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()
        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, w, h)
        saveDC.SelectObject(saveBitMap)
        # PW_RENDERFULLCONTENT=2：渲染整个窗口（含非客户区），对 Qt/Electron 更稳
        ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), 2)
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
        return img, left, top, w, h

    def _screenshot(self):
        img, _left, _top, _w, _h = self._capture_window_pw()
        return img

    def save_debug(self, out_dir="."):
        """截图并保存：整窗 + 侧边栏（带 OCR 框）+ 聊天区（带 OCR 框）。
        用于在你本机微调 config.json 的 ocr_layout 比例。
        """
        self._focus()
        from PIL import ImageDraw
        import os
        os.makedirs(out_dir, exist_ok=True)

        full, left, top, w, h = self._capture_window_pw()
        full.save(os.path.join(out_dir, "debug_full.png"))
        print("[debug] 已保存整窗截图 -> debug_full.png")
        iw, ih = full.size
        sx_scale = (w / iw) if iw else 1.0
        sy_scale = (h / ih) if ih else 1.0

        # 侧边栏
        sb = self._ocr_screen(0.0, self.title_ratio, self.sidebar_ratio, 1.0)
        sb_img = full.crop((0, int(ih * self.title_ratio),
                            int(iw * self.sidebar_ratio), ih))
        d = ImageDraw.Draw(sb_img)
        for text, absx, absy in sb:
            ax = int((absx - left) / sx_scale)
            ay = int((absy - (top + h * self.title_ratio)) / sy_scale)
            d.rectangle([ax - 4, ay - 4, ax + 60, ay + 4], outline="red", width=2)
        sb_img.save(os.path.join(out_dir, "debug_sidebar.png"))
        print("[debug] 侧边栏识别到的文字：")
        for text, _sx, _sy in sb:
            print("   ", repr(text))
        print("[debug] 已保存侧边栏带框截图 -> debug_sidebar.png")

        # 聊天区
        ch = self._ocr_screen(self.sidebar_ratio, self.title_ratio, 1.0,
                              1.0 - self.input_ratio)
        ch_img = full.crop((int(iw * self.sidebar_ratio),
                            int(ih * self.title_ratio), iw,
                            int(ih * (1.0 - self.input_ratio))))
        d2 = ImageDraw.Draw(ch_img)
        for text, absx, absy in ch:
            ax = int((absx - (left + w * self.sidebar_ratio)) / sx_scale)
            ay = int((absy - (top + h * self.title_ratio)) / sy_scale)
            d2.rectangle([ax - 4, ay - 4, ax + 60, ay + 4], outline="red", width=2)
        ch_img.save(os.path.join(out_dir, "debug_chat.png"))
        print("[debug] 聊天区识别到的文字：")
        for text, _sx, _sy in ch:
            print("   ", repr(text))
        print("[debug] 已保存聊天区带框截图 -> debug_chat.png")

    def _ocr_screen(self, x0=0.0, y0=0.0, x1=1.0, y1=1.0):
        """对窗口的某子区域做 OCR，返回屏幕绝对坐标的 (text, sx, sy)。

        改用 PrintWindow 截取窗口本体（见 _capture_window_pw），无视与其它窗口
        的重叠遮挡。坐标换算：先把子区域在截图像素空间裁剪出来做 OCR，再把
        子图内像素坐标映射回窗口逻辑矩形对应的屏幕绝对坐标（按 DPI 缩放修正）。
        """
        img, left, top, w, h = self._capture_window_pw()
        iw, ih = img.size
        px0, py0, px1, py1 = (
            int(iw * x0), int(ih * y0), int(iw * x1), int(ih * y1)
        )
        px0, py0 = max(0, px0), max(0, py0)
        px1, py1 = min(iw, px1), min(ih, py1)
        sub = img.crop((px0, py0, px1, py1))
        scale_x = (w / iw) if iw else 1.0
        scale_y = (h / ih) if ih else 1.0
        boxes = ocr_image(sub)
        out = []
        for text, lx, ly in boxes:
            # 子图内像素 -> 整图像素 -> 屏幕绝对坐标
            ax = int(left + (px0 + lx) * scale_x)
            ay = int(top + (py0 + ly) * scale_y)
            out.append((text, ax, ay))
        return out

    # ---- 群列表 ----
    def list_visible_groups(self):
        mark("list_visible_groups", "枚举可见会话")  # AUTO-INSTRUMENTED
        self._focus()
        items = self._ocr_screen(0.0, self.title_ratio, self.sidebar_ratio, 1.0)
        names = []
        for text, _sx, _sy in items:
            t = text.strip()
            if t and t not in names:
                names.append(t)
        return names

    @staticmethod
    def _norm_group(s):
        s = (s or "").strip().lower()
        # 仅保留中文与字母，去除数字/标点/空格，提升 OCR 识别差异时的匹配鲁棒性
        # （例如「自己5人群」被识别成「自己五人群」也能匹配）
        return re.sub(r"[^一-鿿a-z]", "", s)

    def _group_match(self, ocr_text, target):
        a = self._norm_group(target)
        b = self._norm_group(ocr_text)
        if not a or not b:
            return False
        return a == b or a in b or b in a

    def open_group(self, name):
        mark("open_group", "打开监控群")  # AUTO-INSTRUMENTED
        """打开指定微信群。优先在可见会话列表中直接匹配（群已置顶时最快）；
        否则通过微信搜索定位并打开（不要求群在侧栏可见）。"""
        self._focus()
        # 1) 当前可见会话列表直接匹配
        items = self._ocr_screen(0.0, self.title_ratio, self.sidebar_ratio, 1.0)
        target = name.strip()
        for text, sx, sy in items:
            if self._group_match(text, name):
                try:
                    # 点文字稍右下一点的位置，命中群条目行
                    pyautogui.click(sx + 12, sy + 6)
                    time.sleep(1.2)
                    if self._current_chat_title_matches(name):
                        logging.info("已在侧栏直接打开群「%s」。", name)
                        return True
                    # 标题栏 OCR 容易漏识别，但群名仍在侧栏 -> 视为已打开
                    if self._sidebar_still_shows(name):
                        logging.info("标题栏 OCR 未命中，但侧栏仍显示「%s」，视为已打开。", name)
                        return True
                except Exception as e:
                    logging.error("点击群 %s 失败: %s", name, e)
        # 2) 兜底：通过微信搜索打开（不依赖群在侧栏可见）
        logging.info("侧栏未直接匹配「%s」，改用微信搜索打开。", name)
        return self.search_and_open_group(name)

    def search_and_open_group(self, name):
        """定位并打开微信群。不依赖搜索框（微信4.0搜索框会触发搜一搜）。

        策略：
        1) ESC 退出可能的搜一搜/覆盖层，回到聊天视图；
        2) 在左侧会话列表当前可见区域 OCR 找群名；
        3) 找不到则滚动侧栏（先回顶再向下滚）继续找；
        4) 还找不到则尝试点击侧栏底部「通讯录」→「群聊」找群；
        5) 最后兜底：用搜索框下拉建议（只输入不按 Enter）。
        """
        self._focus()
        target = name.strip()

        # 1) 先 ESC 退出搜一搜/任何覆盖层
        pyautogui.keyDown('esc')
        pyautogui.keyUp('esc')
        time.sleep(0.5)

        # 如果已经在这个群，直接成功
        if self._current_chat_title_matches(name):
            logging.info("当前已在群「%s」。", name)
            return True

        # 2) 在当前侧栏可见区域查找
        if self._find_and_click_in_sidebar(name):
            return True

        # 3) 滚动侧栏查找：先滚到顶，再向下滚多轮
        logging.info("侧栏当前可见区域未找到「%s」，开始滚动查找。", name)
        if self._scroll_sidebar_find(name):
            return True

        # 4) 通讯录路线：点击侧栏底部「通讯录」→ 找「群聊」→ 找群名
        logging.info("滚动查找未果，尝试通讯录-群聊路线。")
        if self._via_contacts_open_group(name):
            return True

        # 5) 最后兜底：搜索框下拉建议（输入但不按 Enter，避免进搜一搜）
        logging.info("尝试搜索框下拉建议（不按 Enter）。")
        if self._via_search_suggestion(name):
            return True

        logging.warning("所有策略均未能打开群「%s」。建议：①确认群名无误；②在微信里手动打开一次该群，让它进入最近会话列表。", name)
        return False

    def _find_and_click_in_sidebar(self, name, save_debug=True):
        mark("_find_and_click_in_sidebar", "侧栏精确匹配点击")  # AUTO-INSTRUMENTED
        """在左侧会话列表当前可见区域找群名并点击。成功返回 True。

        校验策略：点击后若右侧标题栏 OCR 未命中，则保存右侧标题栏 debug 图；
        同时检查侧栏里目标群是否仍存在（高亮），存在也视为成功，避免 OCR 漏识别标题导致假失败。
        """
        items = self._ocr_screen(0.0, self.title_ratio, self.sidebar_ratio, 1.0)
        if save_debug:
            self._save_debug_sidebar("sidebar_find", items)
        for text, sx, sy in items:
            if self._group_match(text, name):
                try:
                    pyautogui.click(sx, sy)
                    time.sleep(1.2)
                    if self._current_chat_title_matches(name, save_debug=save_debug):
                        logging.info("已在侧栏打开群「%s」。", name)
                        return True
                    # 标题栏 OCR 没命中，但侧栏里仍有该群 -> 大概率已经打开
                    if self._sidebar_still_shows(name):
                        logging.info("标题栏 OCR 未命中，但侧栏仍显示「%s」，视为已打开。", name)
                        return True
                    # 再重试一次点击
                    pyautogui.click(sx, sy)
                    time.sleep(1.0)
                    if self._current_chat_title_matches(name, save_debug=save_debug):
                        logging.info("重试后在侧栏打开群「%s」。", name)
                        return True
                except Exception as e:
                    logging.error("点击群名失败: %s", e)
        return False

    def _scroll_sidebar_find(self, name, max_down_rounds=8):
        mark("_scroll_sidebar_find", "滚动侧栏查找")  # AUTO-INSTRUMENTED
        """滚动左侧会话列表查找群名。先滚到顶，再向下滚多轮。"""
        left, top, right, bottom, w, h = self._rect()
        # 侧栏中心 x，列表区域 y（标题栏下方到底部）
        scroll_x = int(left + w * self.sidebar_ratio * 0.5)
        list_top = int(top + h * self.title_ratio + 10)
        list_bottom = int(bottom - 10)
        scroll_y = int((list_top + list_bottom) / 2)

        # 先滚到顶部（多次向上滚）
        for _ in range(6):
            pyautogui.scroll(8, scroll_x, scroll_y)
            time.sleep(0.25)

        # 再向下滚动查找
        for i in range(max_down_rounds):
            if self._find_and_click_in_sidebar(name, save_debug=(i == 0)):
                return True
            # 向下滚动一段
            pyautogui.scroll(-6, scroll_x, scroll_y)
            time.sleep(0.5)
        return False

    def _via_contacts_open_group(self, name):
        mark("_via_contacts_open_group", "通讯录-群聊路线")  # AUTO-INSTRUMENTED
        """通过通讯录→群聊打开群。点击侧栏底部约 85%-95% 高度的「通讯录」入口。"""
        left, top, right, bottom, w, h = self._rect()
        # 左侧底部常见通讯录图标/文字区域
        candidates_y = [0.88, 0.92, 0.96]
        cx = int(left + w * self.sidebar_ratio * 0.5)
        for y_ratio in candidates_y:
            cy = int(top + h * y_ratio)
            try:
                pyautogui.click(cx, cy)
                time.sleep(1.0)
                # 找「群聊」
                items = self._ocr_screen(0.0, 0.0, 1.0, 1.0)
                for text, sx, sy in items:
                    if self._group_match(text, "群聊") or "群聊" in (text or ""):
                        pyautogui.click(sx, sy)
                        time.sleep(1.0)
                        # 在群聊列表里找目标群
                        if self._find_and_click_anywhere(name):
                            return True
            except Exception as e:
                logging.debug("通讯录路线尝试失败: %s", e)
        return False

    def _find_and_click_anywhere(self, name):
        mark("_find_and_click_anywhere", "全窗模糊点击")  # AUTO-INSTRUMENTED
        """在全屏范围内找群名并点击，用于通讯录/群聊列表。"""
        items = self._ocr_screen(0.0, 0.0, 1.0, 1.0)
        for text, sx, sy in items:
            if self._group_match(text, name):
                try:
                    pyautogui.click(sx, sy)
                    time.sleep(1.2)
                    if self._current_chat_title_matches(name):
                        logging.info("通过通讯录打开群「%s」。", name)
                        return True
                except Exception:
                    pass
        return False

    def _via_search_suggestion(self, name):
        mark("_via_search_suggestion", "搜索建议打开")  # AUTO-INSTRUMENTED
        """最后兜底：点击侧栏顶部搜索框，输入群名，等下拉建议，点匹配项（不按Enter）。"""
        self._focus()
        target = name.strip()
        # 点击顶部搜索框（侧栏上半部分中央）
        left, top, right, bottom, w, h = self._rect()
        cx = int(left + w * self.sidebar_ratio * 0.5)
        cy = int(top + h * self.title_ratio * 0.6)
        try:
            pyautogui.click(cx, cy)
            time.sleep(0.5)
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.1)
            pyperclip.copy(target)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(1.5)
            # 在左半屏找下拉建议里的群名
            items = self._ocr_screen(0.0, self.title_ratio, self.sidebar_ratio, 0.8)
            for text, sx, sy in items:
                if self._group_match(text, target):
                    pyautogui.click(sx, sy)
                    time.sleep(1.5)
                    if self._current_chat_title_matches(name):
                        logging.info("通过搜索建议打开群「%s」。", name)
                        return True
        except Exception as e:
            logging.debug("搜索建议兜底失败: %s", e)
        return False

    def _save_debug_sidebar(self, prefix, items):
        """保存当前侧栏截图并在图上标注 OCR 结果，便于排查。"""
        try:
            from PIL import ImageDraw
            import os
            os.makedirs("debug", exist_ok=True)
            img, left, top, w, h = self._capture_window_pw()
            iw, ih = img.size
            sx_scale = (w / iw) if iw else 1.0
            sy_scale = (h / ih) if ih else 1.0
            crop = img.crop((0, int(ih * self.title_ratio),
                             int(iw * self.sidebar_ratio), ih))
            d = ImageDraw.Draw(crop)
            for text, absx, absy in items:
                ax = int((absx - left) / sx_scale)
                ay = int((absy - (top + h * self.title_ratio)) / sy_scale)
                d.rectangle([ax - 4, ay - 4, ax + 80, ay + 4], outline="red", width=2)
            path = os.path.join("debug", f"{prefix}_{int(time.time())}.png")
            crop.save(path)
        except Exception:
            pass

    def _save_debug_right(self, prefix, items):
        """保存右侧标题栏/聊天区截图并标注 OCR 结果，便于排查标题校验失败。"""
        try:
            from PIL import ImageDraw
            import os
            os.makedirs("debug", exist_ok=True)
            img, left, top, w, h = self._capture_window_pw()
            iw, ih = img.size
            sx_scale = (w / iw) if iw else 1.0
            sy_scale = (h / ih) if ih else 1.0
            crop = img.crop((int(iw * self.sidebar_ratio), 0, iw,
                             int(ih * (self.title_ratio + 0.15))))
            d = ImageDraw.Draw(crop)
            for text, absx, absy in items:
                ax = int((absx - (left + w * self.sidebar_ratio)) / sx_scale)
                ay = int((absy - top) / sy_scale)
                d.rectangle([ax - 4, ay - 4, ax + 80, ay + 4], outline="red", width=2)
            path = os.path.join("debug", f"{prefix}_{int(time.time())}.png")
            crop.save(path)
        except Exception:
            pass

    def _current_chat_title_matches(self, name, save_debug=True):
        mark("_current_chat_title_matches", "校验当前聊天标题")  # AUTO-INSTRUMENTED
        """检查右侧聊天标题栏/聊天区顶部是否显示目标群名。

        微信 4.0 标题栏可能较矮或文字被头像遮挡，OCR 容易漏。这里同时扫描：
        ① 右侧顶部 title_ratio+0.12 区域；② 聊天区顶部一小条。
        找不到时保存 debug 图便于后续微调。
        """
        # 区域 1：标题栏（更宽一些）
        items = self._ocr_screen(self.sidebar_ratio, 0.0, 1.0, self.title_ratio + 0.12)
        if save_debug:
            self._save_debug_right("right_title", items)
        for text, _sx, _sy in items:
            if self._group_match(text, name):
                return True
        # 区域 2：聊天区顶部再扫一下（某些布局群名在消息区域顶部）
        items2 = self._ocr_screen(
            self.sidebar_ratio + 0.02, self.title_ratio,
            min(1.0, self.sidebar_ratio + 0.40), self.title_ratio + 0.10
        )
        for text, _sx, _sy in items2:
            if self._group_match(text, name):
                return True
        return False

    def _sidebar_still_shows(self, name):
        mark("_sidebar_still_shows", "校验侧栏仍显示群")  # AUTO-INSTRUMENTED
        """点击后检查侧栏里目标群名是否仍然存在。用于标题栏 OCR 漏识别时的辅助判断。"""
        items = self._ocr_screen(0.0, self.title_ratio, self.sidebar_ratio, 1.0)
        for text, _sx, _sy in items:
            if self._group_match(text, name):
                return True
        return False

    # ---- 读消息 ----
    def read_messages(self):
        mark("read_messages", "读取群消息")  # AUTO-INSTRUMENTED
        self._focus()
        # 聊天区域：右侧，排除顶部标题栏与底部输入区
        items = self._ocr_screen(
            self.sidebar_ratio, self.title_ratio, 1.0, 1.0 - self.input_ratio
        )
        texts = [t.strip() for t, _sx, _sy in items if t.strip()]
        return texts

    # ---- 发消息 ----
    def _focus(self):
        self._restore()
        try:
            hwnd = int(self.window.NativeWindowHandle)
            if hwnd:
                _force_foreground(hwnd)
        except Exception:
            try:
                self.window.SetActive()
            except Exception:
                pass
        time.sleep(0.6)

    def send_text(self, text):
        left, top, right, bottom, w, h = self._rect()
        cx = int(left + w * self.input_x)
        cy = int(top + h * self.input_y)
        try:
            self._focus()
            pyautogui.click(cx, cy)
            time.sleep(0.4)
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.4)
            pyautogui.press("enter")
            return True
        except Exception as e:
            logging.error("发送失败: %s", e)
            return False

    # ---- 发图片 ----
    def send_image(self, image_path):
        mark("send_image", "回发回答截图")  # AUTO-INSTRUMENTED
        """把一张图片作为消息发送到当前打开的微信群。

        做法：把图片写入系统剪贴板（CF_DIB），点击微信输入框后 Ctrl+V 粘贴，
        微信会把它变成图片预览，再回车发送。无需依赖微信内部控件。
        """
        if not os.path.exists(image_path):
            logging.error("图片不存在: %s", image_path)
            return False
        left, top, right, bottom, w, h = self._rect()
        cx = int(left + w * self.input_x)
        cy = int(top + h * self.input_y)
        try:
            self._focus()
            pyautogui.click(cx, cy)
            time.sleep(0.4)
            self._copy_image_to_clipboard(image_path)
            time.sleep(0.4)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(1.5)  # 等微信把图片渲染成预览
            pyautogui.press("enter")
            return True
        except Exception as e:
            logging.error("发送图片失败: %s", e)
            return False

    @staticmethod
    def _copy_image_to_clipboard(path):
        mark("_copy_image_to_clipboard", "图片写入剪贴板")  # AUTO-INSTRUMENTED
        """把图片以 CF_DIB 形式放到系统剪贴板（微信可识别）。优先 pywin32。"""
        import io
        from PIL import Image

        img = Image.open(path).convert("RGB")
        out = io.BytesIO()
        img.save(out, "BMP")
        data = out.getvalue()[14:]  # 去掉 14 字节 BMP 文件头，保留 DIB

        try:
            import win32clipboard
            import win32con
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
            win32clipboard.CloseClipboard()
            return
        except Exception as e:
            logging.warning("pywin32 剪贴板失败，尝试 ctypes: %s", e)

        # ctypes 兜底
        import ctypes
        cf_dib = 8  # CF_DIB
        GMEM_MOVEABLE = 0x0002
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hmem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not hmem:
            raise ctypes.WinError()
        ptr = kernel32.GlobalLock(hmem)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(hmem)
        user32.OpenClipboard(0)
        user32.EmptyClipboard()
        user32.SetClipboardData(cf_dib, hmem)
        user32.CloseClipboard()

    # ---- 校准 ----
    def calibrate(self):
        left, top, right, bottom, w, h = self._rect()
        print("================ 微信窗口截图 OCR ================")
        print("窗口矩形 left=%d top=%d right=%d bottom=%d w=%d h=%d" % (
            left, top, right, bottom, w, h))
        items = self._ocr_screen(0.0, 0.0, 1.0, 1.0)
        for text, sx, sy in items:
            print("  text=%-30s screen=(%d,%d)" % (text, sx, sy))
        print("=================================================")
