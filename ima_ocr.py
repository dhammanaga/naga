# -*- coding: utf-8 -*-
"""基于「截图 OCR + 模拟鼠标/键盘」的 IMA 桌面端控制器（纯视觉、人自然操作方式）。

设计原则（应对无法现场调试的现实）：
1. 每一步动作后都用 OCR 自检「是否真的发生了」，不靠猜坐标就假设成功。
2. 失败自动换策略重试（OCR 文字点击 -> 比例兜底点击 -> 键盘焦点）。
3. 全程把关键步骤截图存到 debug/ 目录，附 manifest，便于一次性诊断。
4. 成功坐标/标签写入 state.json，后续运行直接复用，越跑越稳。

IMA 桌面端是 Chromium/Electron（Class=Chrome_WidgetWin_1），画面可经
PrintWindow 截取自身；文字用 RapidOCR 识别；鼠标键盘用 pyautogui 模拟。
"""
import os
import re
import time
import json
import logging
import ctypes
import subprocess

import pyautogui
import pyperclip
import uiautomation as auto
import win32gui
import win32con
import win32ui
from PIL import Image, ImageDraw

from ocr_engine import ocr_image
from flow_runtime import mark  # AUTO-INSTRUMENTED


def _force_foreground(hwnd):
    """可靠地把窗口抢到前台，绕过 Windows 前台锁。

    IMA 是 Electron 窗口，直接用 SetForegroundWindow 常因调用线程不是前台线程
    而静默失败，导致截图/点击打到别的窗口。这里先恢复最小化窗口，再短暂
    tap 一次 ALT 清除前台锁，然后用 AttachThreadInput 把本线程与前台线程输入
    临时绑定后置前，并用 SetWindowPos 把窗口提到最前，确保后续点击落在本窗口。
    """
    if not hwnd:
        return False
    try:
        import win32process
        hwnd = int(hwnd)
        ctypes.windll.user32.ShowWindow(hwnd, win32con.SW_RESTORE)
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


PW_RENDERFULLCONTENT = 2
pyautogui.FAILSAFE = True

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ima_state.json")


class IMAController:
    def __init__(self, cfg, debug=True, debug_dir="debug"):
        self.cfg = cfg.get("ima", {})
        self.hwnd = None
        self._last_window_rect = None
        # 布局参数（窗口内比例）
        self._ly = self.cfg.get("layout", {})
        self.sidebar_ratio = float(self._ly.get("sidebar_ratio", 0.08))
        self.top_bar_ratio = float(self._ly.get("top_bar_ratio", 0.15))
        self.input_ratio = float(self._ly.get("input_ratio", 0.18))
        self.input_center_x = float(self._ly.get("input_center_x", 0.55))
        self.input_center_y = float(self._ly.get("input_center_y", 0.92))
        self.answer_top = float(self._ly.get("answer_top", 0.12))
        self.answer_bottom = float(self._ly.get("answer_bottom", 0.82))
        self.answer_left = float(self._ly.get("answer_left", 0.20))
        self.answer_right = float(self._ly.get("answer_right", 1.0))
        # UI 文本标签（可配置，适配不同版本）
        self.labels = self.cfg.get("labels", {})
        self.input_hint_label = self.labels.get("input_hint", "有问题尽管问ima")
        self.kb_chat_hint = self.labels.get("kb_chat_hint", "基于文件夹问答")
        self.kb_chat_entries = self.cfg.get(
            "kb_chat_entries",
            ["基于文件夹问答", "向知识库提问", "问知识库", "在知识库中提问",
             "问答", "AI问答", "开始问答", "问问知识库", "智能问答"],
        )
        self.input_hints = self.labels.get("input_hints", [])
        if not self.input_hints:
            self.input_hints = [self.input_hint_label, self.kb_chat_hint,
                                "输入问题", "问ima", "请输入", "问知识库"]
        # 行为参数
        self.answer_wait = float(self.cfg.get("answer_wait", 25))
        self.post_send_wait = float(self.cfg.get("post_send_wait", 2.0))
        self.chat_switch_wait = float(self.cfg.get("chat_switch_wait", 2.0))
        self.max_answer_wait = float(self.cfg.get("max_answer_wait", 100))
        self.retry_attempts = int(self.cfg.get("retry_attempts", 3))
        # 自动启动 / 知识库导航
        self.kb_name = self.cfg.get("kb_name", "")
        self.auto_launch = bool(self.cfg.get("auto_launch", True))
        self.launch_timeout = float(self.cfg.get("launch_timeout", 20))
        self.navigate_kb = bool(self.cfg.get("navigate_kb", True))
        self.exe_path = self.cfg.get("exe_path", "") or ""
        self._launched_once = False
        self._last_question = ""
        # 调试
        self.debug = debug
        self.debug_dir = debug_dir
        self._dbg_index = 0
        os.makedirs(self.debug_dir, exist_ok=True)
        self._manifest = []
        # 自校准状态
        self.state = self._load_state()

    # ------------------------------------------------------------------
    # 状态 / 调试
    def _load_state(self):
        try:
            if os.path.exists(STATE_PATH):
                with open(STATE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_state(self):
        try:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.warning("保存 state.json 失败: %s", e)

    def _dbg(self, name):
        """截整窗存盘并记 manifest。返回路径或 None。"""
        if not self.debug:
            return None
        try:
            img, rect = self.capture()
            self._dbg_index += 1
            fn = "ima_%02d_%s.png" % (self._dbg_index, name)
            path = os.path.join(self.debug_dir, fn)
            img.save(path)
            # 标注 OCR 文字
            annotated = img.copy()
            d = ImageDraw.Draw(annotated)
            for text, rx, ry in ocr_image(img):
                d.rectangle([rx - 3, ry - 3, rx + 80, ry + 3], outline=(255, 0, 0), width=1)
                d.text((rx, ry - 14), text[:20], fill=(255, 0, 0))
            ann_path = os.path.join(self.debug_dir, "ann_" + fn)
            annotated.save(ann_path)
            self._manifest.append("%02d %s -> %s" % (self._dbg_index, name, fn))
            return path
        except Exception as e:
            logging.warning("debug 截图失败(%s): %s", name, e)
            return None

    def _manifest_flush(self):
        try:
            with open(os.path.join(self.debug_dir, "manifest.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(self._manifest))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 窗口
    def find_window(self):
        if self._locate_window():
            return self.hwnd
        if self.auto_launch and not self._launched_once:
            self._launched_once = True
            if self.launch():
                return self.hwnd
        if not self.hwnd:
            logging.error("未找到 IMA 窗口，且自动启动失败。请确认 IMA 已安装并登录过。")
        return self.hwnd

    def _locate_window(self):
        for name in (self.cfg.get("window_title", ""), "ima", "IMA", "腾讯IMA", "ima.copilot"):
            if not name:
                continue
            c = auto.WindowControl(Name=name)
            if c.Exists(3):
                self.hwnd = c.NativeWindowHandle
                if self.hwnd:
                    logging.info("已定位 IMA 窗口：Name=%r hwnd=%s", c.Name, self.hwnd)
                    return self.hwnd
        for w in auto.GetRootControl().GetChildren():
            try:
                n = (w.Name or "").lower()
                cls = (getattr(w, "ClassName", "") or "").lower()
                is_electron = ("chrome" in cls or "widgetwin" in cls or "electron" in cls)
                title_ok = (n == "ima" or n == "ima.copilot" or "ima.copilot" in n)
                if (is_electron and ("ima" in n or "copilot" in n)) or title_ok:
                    self.hwnd = w.NativeWindowHandle
                    logging.info("已定位 IMA 窗口（兜底）：Name=%r Class=%r hwnd=%s",
                                 w.Name, cls, self.hwnd)
                    return self.hwnd
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # 自动启动
    def _find_ima_exe(self):
        if self.exe_path and os.path.exists(self.exe_path):
            return self.exe_path
        local = os.path.expandvars("%LOCALAPPDATA%")
        bases = [
            local,
            os.path.expandvars("%APPDATA%"),
            os.environ.get("ProgramFiles", "C:/Program Files"),
            os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"),
        ]
        candidates = []
        for base in bases:
            if not base:
                continue
            for sub in ("Programs/ima-copilot", "Programs/ima", "ima-copilot", "ima"):
                candidates.append(os.path.join(base, sub, "ima.exe"))
        for c in candidates:
            if os.path.exists(c):
                logging.info("找到 IMA 可执行文件：%s", c)
                return c
        for base in (os.path.join(local, "Programs"), local):
            if not os.path.isdir(base):
                continue
            try:
                for entry in os.scandir(base):
                    if entry.is_dir():
                        for nm in ("ima.exe", "ima-copilot.exe"):
                            p = os.path.join(entry.path, nm)
                            if os.path.exists(p):
                                logging.info("找到 IMA 可执行文件：%s", p)
                                return p
            except Exception:
                continue
        return ""

    def launch(self):
        if self.hwnd:
            return True
        exe = self._find_ima_exe()
        if exe:
            try:
                logging.info("尝试启动 IMA：%s", exe)
                subprocess.Popen([exe])
            except Exception as e:
                logging.error("启动 IMA 失败: %s", e)
        else:
            for proto in ("ima://", "tencentima://", "imacopilot://"):
                try:
                    os.startfile(proto)
                    logging.info("已尝试通过协议启动 IMA：%s", proto)
                    break
                except Exception:
                    continue
        deadline = time.time() + self.launch_timeout
        while time.time() < deadline:
            if self._locate_window():
                logging.info("IMA 已启动。")
                return True
            time.sleep(1.0)
        logging.error("等待 IMA 窗口超时（%.0fs）。", self.launch_timeout)
        return False

    def _window_title(self):
        if not self.hwnd:
            return ""
        try:
            return win32gui.GetWindowText(self.hwnd) or ""
        except Exception:
            return ""

    def _title_has_kb(self):
        if not self.kb_name:
            return True
        return self.kb_name in self._window_title()

    def _restore(self):
        if not self.hwnd:
            return
        ctypes.windll.user32.ShowWindow(self.hwnd, win32con.SW_RESTORE)
        _force_foreground(self.hwnd)
        time.sleep(0.5)

    def _rect(self):
        self._restore()
        left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
        return left, top, right, bottom, right - left, bottom - top

    def _pw_full(self):
        left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
        w, h = right - left, bottom - top
        hwndDC = win32gui.GetWindowDC(self.hwnd)
        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()
        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, w, h)
        saveDC.SelectObject(saveBitMap)
        ctypes.windll.user32.PrintWindow(self.hwnd, saveDC.GetSafeHdc(), PW_RENDERFULLCONTENT)
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
        win32gui.ReleaseDC(self.hwnd, hwndDC)
        return img, (w, h)

    def capture(self, path=None, region=None):
        self._restore()
        img, (w, h) = self._pw_full()
        if region:
            x0, y0, x1, y1 = region
            img = img.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1)))
        if path:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            img.save(path)
        left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
        # 注意：截图像素尺寸(w,h) 在高 DPI 下可能是窗口逻辑尺寸的若干倍，
        # 因此单独保存逻辑矩形与图像尺寸，坐标换算时按比例缩放（见 _find_text）。
        self._last_window_rect = (left, top, right - left, bottom - top)
        self._last_img_size = (w, h)
        return img, (left, top, w, h)

    # ------------------------------------------------------------------
    # OCR 与点击辅助
    def _ocr_region_text(self, x0, y0, x1, y1):
        img, _ = self.capture(region=(x0, y0, x1, y1))
        return "\n".join(t for t, _x, _y in ocr_image(img))

    def _find_text(self, img, target, case_sensitive=False, strip=True):
        if not self._last_window_rect or not getattr(self, "_last_img_size", None):
            return None
        left, top, lw, lh = self._last_window_rect
        iw, ih = self._last_img_size
        # 图像像素 -> 屏幕逻辑坐标 的缩放（修复高 DPI 下点击打偏/打飞的问题）
        sx = (lw / iw) if iw else 1.0
        sy = (lh / ih) if ih else 1.0
        target = target.strip() if strip else target
        for text, rx, ry in ocr_image(img):
            t = text.strip() if strip else text
            cmp = (t if case_sensitive else t.lower())
            tgt = (target if case_sensitive else target.lower())
            if cmp == tgt or tgt in cmp:
                return text, rx, ry, left + int(rx * sx), top + int(ry * sy)
        return None

    def _click_text(self, img, target, fallback=None, case_sensitive=False):
        found = self._find_text(img, target, case_sensitive=case_sensitive)
        if found:
            _, _, _, sx, sy = found
            logging.info("  点击文字 '%s' @ (%d, %d)", target, sx, sy)
            pyautogui.click(sx, sy)
            return True
        if fallback:
            logging.info("  未找到 '%s'，使用兜底坐标 %s", target, fallback)
            pyautogui.click(fallback[0], fallback[1])
            return True
        return False

    def _click_ratio(self, x_ratio, y_ratio):
        rect = getattr(self, "_last_window_rect", None)
        if not rect:
            return None
        left, top, lw, lh = rect
        sx = left + int(lw * x_ratio)
        sy = top + int(lh * y_ratio)
        logging.info("  点击比例坐标 (%.2f,%.2f) -> (%d, %d)", x_ratio, y_ratio, sx, sy)
        pyautogui.click(sx, sy)
        return sx, sy

    @staticmethod
    def _norm(s):
        s = (s or "").strip().lower()
        return re.sub(r"[^一-鿿a-z0-9]", "", s)

    def _text_in_chat(self, img, target):
        """在聊天区 OCR 结果里模糊找目标文字（归一化，容忍 OCR 误差）。"""
        tn = self._norm(target)
        if not tn:
            return False
        for text, _x, _y in ocr_image(img):
            if tn in self._norm(text) or self._norm(text) in tn:
                return True
        return False

    def _in_chat_view(self, img):
        for hint in self.input_hints:
            if self._find_text(img, hint) is not None:
                return True
        return False

    def _in_kb_chat_view(self, img):
        mark("_in_kb_chat_view", "判定是否在知识库问答")  # AUTO-INSTRUMENTED
        """是否在『目标知识库』专属问答视图。

        关键：必须同时满足
          ① 当前在具体知识库里（知识库名在标题或页面内可见）；
          ② 存在问答输入框提示。
        仅有全局「问问ima」的输入框（_in_chat_view 为真）不算数，
        否则问题会被发到全局对话而不是目标知识库里搜索。
        """
        if not self.kb_name:
            return self._in_chat_view(img)
        kb_visible = self._title_has_kb() or (self._find_text(img, self.kb_name) is not None)
        if not kb_visible:
            return False
        return self._in_chat_view(img)

    # ------------------------------------------------------------------
    # 进入问答视图 + 聚焦输入（多策略 + 自检）
    def ensure_window_and_kb(self):
        mark("ensure_window_and_kb", "定位窗口+确保知识库")  # AUTO-INSTRUMENTED
        if not self.hwnd:
            if not self.find_window():
                return False
        self._restore()
        self.ensure_kb()
        return True

    def ensure_kb(self):
        mark("ensure_kb", "确保目标知识库")  # AUTO-INSTRUMENTED
        """确保在『目标知识库』的问答视图（不是泛用的全局问问ima）。"""
        if not self.navigate_kb or not self.kb_name:
            return True
        img, _ = self.capture()
        if self._in_kb_chat_view(img):
            logging.info("IMA 已在目标知识库「%s」问答视图", self.kb_name)
            return True
        logging.info("IMA 尚未进入知识库「%s」，开始导航...", self.kb_name)
        return self._navigate_to_kb_chat()

    def _navigate_to_kb_chat(self):
        mark("_navigate_to_kb_chat", "导航到知识库问答")  # AUTO-INSTRUMENTED
        """主动导航到目标知识库的问答视图。"""
        # 1) 进入「知识库」列表
        img, _ = self.capture()
        for label in ("知识库", "我的知识库", "全部知识库", "知识库问答"):
            if self._click_text(img, label):
                time.sleep(self.chat_switch_wait)
                img, _ = self.capture()
                break
        # 2) 点击具体的知识库名
        if not self._click_text(img, self.kb_name):
            logging.warning("未找到知识库名『%s』，尝试用归一化匹配...", self.kb_name)
            if not self._click_kb_by_norm(img):
                logging.warning("仍未定位到知识库『%s』。", self.kb_name)
        else:
            time.sleep(self.chat_switch_wait)
            img, _ = self.capture()
        # 3) 若还在文件列表（没进入问答），点击问答入口
        if not self._in_kb_chat_view(img):
            logging.info("在知识库页面，尝试进入问答视图...")
            if self._enter_kb_chat(img):
                time.sleep(self.chat_switch_wait)
                img, _ = self.capture()
        ok = self._in_kb_chat_view(img)
        if not ok:
            logging.warning("仍未进入目标知识库问答视图。当前标题=%r。", self._window_title())
        return ok

    def _click_kb_by_norm(self, img):
        mark("_click_kb_by_norm", "模糊匹配知识库名")  # AUTO-INSTRUMENTED
        """OCR 可能因长中文名出错，退一步用归一化（去标点/空格）模糊匹配知识库名。"""
        tn = self._norm(self.kb_name)
        if not tn:
            return False
        best = None
        for text, _x, _y in ocr_image(img):
            cn = self._norm(text)
            if not cn:
                continue
            if tn in cn or cn in tn or (len(tn) > 4 and cn[:4] == tn[:4]):
                best = text
                break
        if best is None:
            return False
        found = self._find_text(img, best)
        if found:
            _, _, _, sx, sy = found
            logging.info("  归一化匹配并点击知识库 '%s' @ (%d, %d)", best, sx, sy)
            pyautogui.click(sx, sy)
            return True
        return False

    def _enter_kb_chat(self, img):
        mark("_enter_kb_chat", "进入问答视图")  # AUTO-INSTRUMENTED
        entries = list(self.kb_chat_entries)
        # 优先使用上一次成功的入口标签
        last = self.state.get("ima", {}).get("kb_chat_entry")
        if last and last in entries:
            entries.remove(last)
            entries.insert(0, last)
        for label in entries:
            if self._click_text(img, label):
                self.state.setdefault("ima", {})["kb_chat_entry"] = label
                self._save_state()
                return True
        return False

    def _focus_input_box(self, img):
        mark("_focus_input_box", "聚焦输入框")  # AUTO-INSTRUMENTED
        """聚焦输入框：优先 OCR 找输入提示，否则用（自校准/默认）比例兜底。"""
        for hint in self.input_hints:
            found = self._find_text(img, hint)
            if found:
                _, _, _, sx, sy = found
                logging.info("  点击输入框提示 '%s' @ (%d, %d)", hint, sx, sy)
                pyautogui.click(sx, sy)
                return True
        # 用最近一次成功的输入坐标
        saved = self.state.get("ima", {}).get("input_ratio")
        if saved:
            self._click_ratio(saved[0], saved[1])
            return True
        self._click_ratio(self.input_center_x, self.input_center_y)
        return False

    def _verify_question_sent(self, question):
        mark("_verify_question_sent", "校验问题已发送")  # AUTO-INSTRUMENTED
        """OCR 聊天区，确认刚才的问题确实出现在对话里。"""
        needle = question[: max(5, len(question) // 3)]
        text = self._ocr_region_text(self.answer_left, self.top_bar_ratio,
                                     self.answer_right, self.answer_bottom)
        tn = self._norm(question)
        if tn and (self._norm(needle) in self._norm(text) or tn[:6] in self._norm(text)):
            return True
        return False

    def _wait_real_answer(self, pre_text, question):
        mark("_wait_real_answer", "轮询等待回答")  # AUTO-INSTRUMENTED
        """等待「新内容出现并稳定」，避免把旧答案当新答案。返回最终回答文本。"""
        start = time.time()
        last = pre_text
        stable = 0
        while time.time() - start < self.max_answer_wait:
            time.sleep(2.5)
            elapsed = time.time() - start
            post = self._ocr_region_text(self.answer_left, self.top_bar_ratio,
                                         self.answer_right, self.answer_bottom)
            # 新内容出现且足够多
            if len(post) > len(pre_text) + 30 and len(post) >= 60:
                if abs(len(post) - len(last)) <= 10:
                    stable += 1
                else:
                    stable = 0
                last = post
                if stable >= 2:
                    logging.info("[IMA] 回答内容已稳定（%.1fs）", elapsed)
                    # 防旧答案：与上次保存的答案高度相似且问题不同 -> 视为陈旧
                    if self._is_stale_answer(post, question):
                        logging.warning("[IMA] 检测为陈旧答案（与上轮相同），将重试。")
                        return None
                    return post
            else:
                last = post
                stable = 0
            if elapsed >= self.answer_wait and len(post) > len(pre_text) + 20 and len(post) >= 60:
                logging.info("[IMA] 已达最小等待 %.0fs，停止等待。", self.answer_wait)
                if self._is_stale_answer(post, question):
                    return None
                return post
        logging.warning("[IMA] 等待回答超时。")
        return None

    def _is_stale_answer(self, post, question):
        mark("_is_stale_answer", "过滤旧回答")  # AUTO-INSTRUMENTED
        prev = self.state.get("ima", {}).get("last_answer_text", "")
        if not prev:
            return False
        pn = self._norm(prev)
        qn = self._norm(question)
        cur = self._norm(post)
        # 问题不同、但本次回答与上轮一字不差 -> 典型的"旧答案被当成新答案"卡顿
        if pn and cur and pn == cur and qn[:6] not in pn:
            return True
        return False

    # ------------------------------------------------------------------
    # 核心：提问并取完整回答截图
    def ask(self, question, out_path=None):
        mark("ask", "IMA 问答入口")  # AUTO-INSTRUMENTED
        if not self.ensure_window_and_kb():
            return None
        out_path = out_path or self.cfg.get("default_out_path", "cards/ima_answer.png")
        out_path = os.path.abspath(out_path)
        logging.info("[IMA] 准备提问（知识库=%s）：%s", self.kb_name, question[:80])
        self._last_question = question

        last_err = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                return self._ask_once(question, out_path)
            except Exception as e:
                last_err = e
                logging.warning("[IMA] 第 %d 次尝试失败: %s", attempt, e)
                self._dbg("attempt_%d_fail" % attempt)
                time.sleep(1.0)
        logging.error("[IMA] 多次尝试均失败: %s", last_err)
        self._manifest_flush()
        return None

    def _ask_once(self, question, out_path):
        mark("_ask_once", "单次提问流程")  # AUTO-INSTRUMENTED
        # 1) 确保在『目标知识库』的问答视图（不是全局问问ima）
        img, _ = self.capture()
        if not self._in_kb_chat_view(img):
            logging.info("[IMA] 不在目标知识库问答视图，尝试进入...")
            self.ensure_kb()
            img, _ = self.capture()
            if not self._in_kb_chat_view(img):
                # 再试一次进入知识库问答
                self._enter_kb_chat(img)
                time.sleep(self.chat_switch_wait)
                img, _ = self.capture()
            if not self._in_kb_chat_view(img):
                self._dbg("not_in_kb_chat_view")
                raise RuntimeError("无法进入目标知识库「%s」问答视图" % self.kb_name)
        self._dbg("in_kb_chat_view")

        # 2) 聚焦输入框并清空
        self._focus_input_box(img)
        time.sleep(0.5)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("delete")
        time.sleep(0.2)
        self._dbg("input_focused")

        # 3) 输入问题并发送
        pyperclip.copy(question)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.6)
        pyautogui.press("enter")
        logging.info("[IMA] 已发送问题，等待回答...")
        time.sleep(self.post_send_wait)

        # 4) 自检：问题确实出现在对话里
        if not self._verify_question_sent(question):
            # 可能输入框没对上，记录并重试
            self._dbg("question_not_detected")
            raise RuntimeError("问题未被 IMA 接收（输入框可能未命中）")
        self._dbg("question_sent")

        # 5) 记录发送前/后的聊天文本，等待真实新回答
        # pre 已是发送后带问题的文本；直接等更长的新内容
        pre = self._ocr_region_text(self.answer_left, self.top_bar_ratio,
                                    self.answer_right, self.answer_bottom)
        answer_text = self._wait_real_answer(pre, question)
        if answer_text is None:
            raise RuntimeError("未生成新的有效回答（可能为陈旧答案或超时）")

        # 记忆答案指纹，防下次陈旧
        self.state.setdefault("ima", {})["last_answer_text"] = answer_text
        self._save_state()

        # 截完整回答（滚动拼接）
        full = self._capture_full_answer()
        if full is None:
            region = (self.answer_left, self.answer_top, self.answer_right, self.answer_bottom)
            full, _ = self.capture(region=region)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        full.save(out_path)
        logging.info("[IMA] 答案已保存：%s", out_path)
        self._dbg("answer_saved")
        self._manifest_flush()
        return out_path

    # ------------------------------------------------------------------
    # 完整回答截图（滚动拼接）
    def _capture_window_pixels(self, cx0, cy0, cx1, cy1):
        img, _ = self._pw_full()
        return img.crop((cx0, cy0, cx1, cy1))

    def _capture_full_answer(self):
        mark("_capture_full_answer", "截取回答区域")  # AUTO-INSTRUMENTED
        if not self._last_window_rect:
            return None
        self._restore()
        left, top, w, h = self._last_window_rect
        cx0 = int(w * self.answer_left)
        cx1 = int(w * self.answer_right)
        cy0 = int(h * self.answer_top)
        cy1 = int(h * (1.0 - self.input_ratio))
        sx = left + (cx0 + cx1) // 2
        sy = top + (cy0 + cy1) // 2
        for _ in range(12):
            pyautogui.scroll(-12, x=sx, y=sy)
            time.sleep(0.2)
        shots = []
        qsig = (self._last_question or "")[:8]
        prev_sig = None
        for _ in range(30):
            img = self._capture_window_pixels(cx0, cy0, cx1, cy1)
            sig = self._top_signature(img)
            top_text = "\n".join(
                t for t, _x, _y in ocr_image(img.crop((0, 0, img.width, min(90, img.height))))
            )
            hit = bool(qsig) and (qsig in top_text)
            if shots and sig == prev_sig:
                break
            shots.append(img)
            prev_sig = sig
            if hit:
                break
            pyautogui.scroll(12, x=sx, y=sy)
            time.sleep(0.45)
        if not shots:
            return None
        shots.reverse()
        if len(shots) == 1:
            return shots[0]
        return self._stitch(shots)

    @staticmethod
    def _top_signature(img):
        w, h = img.size
        crop = img.crop((0, 0, w, min(24, h)))
        return hash(crop.resize((40, 12), Image.NEAREST).tobytes())

    @staticmethod
    def _find_overlap(upper, lower, max_off=120):
        uw, uh = upper.size
        lw, lh = lower.size
        if uw != lw:
            lower = lower.resize((uw, int(lh * uw / lw)))
            lh = lower.size[1]
        up = upper.load()
        lp = lower.load()

        def rows_match(k):
            for dy in range(k):
                uy = uh - k + dy
                ly = dy
                for sx in range(0, uw, 30):
                    pu = up[sx, uy]
                    pl = lp[sx, ly]
                    if abs(pu[0] - pl[0]) + abs(pu[1] - pl[1]) + abs(pu[2] - pl[2]) > 70:
                        return False
            return True

        for k in range(min(uh, lh, max_off), 0, -1):
            if rows_match(k):
                return k
        return 0

    @staticmethod
    def _stitch(shots):
        mark("_stitch", "拼接长回答截图")  # AUTO-INSTRUMENTED
        result = shots[0]
        for nxt in shots[1:]:
            k = IMAController._find_overlap(result, nxt)
            w = result.width
            cropped = result.crop((0, 0, w, result.height - k))
            out = Image.new("RGB", (w, cropped.height + nxt.height))
            out.paste(cropped, (0, 0))
            out.paste(nxt, (0, cropped.height))
            result = out
        return result

    # ------------------------------------------------------------------
    # 校准
    def calibrate(self, out_dir="."):
        if not self.hwnd:
            logging.error("IMA 窗口未找到")
            return
        out_dir = out_dir or "."
        os.makedirs(out_dir, exist_ok=True)
        img, rect = self.capture()
        left, top, w, h = rect
        draw = ImageDraw.Draw(img)
        boxes = ocr_image(img)
        for text, rx, ry in boxes:
            draw.rectangle([rx - 2, ry - 10, rx + 120, ry + 10], outline="red", width=2)
            draw.text((rx, ry - 20), text, fill="red")
        out = os.path.join(out_dir, "ima_calibrate.png")
        img.save(out)
        logging.info("校准图已保存：%s", out)
        print(f"\n=== IMA 窗口文字 ({len(boxes)} 块) ===")
        for text, rx, ry in boxes:
            print(f"  {text!r:50} @ ({rx}, {ry})")
        return out
