# -*- coding: utf-8 -*-
# ↑ 这一行告诉 Python：本文件用 UTF-8 编码保存，这样中文注释不会乱码。

"""基于「截图 OCR + 模拟鼠标/键盘」的 IMA 桌面端控制器（纯视觉、人自然操作方式）。

设计原则（应对无法现场调试的现实）：
1. 每一步动作后都用 OCR 自检「是否真的发生了」，不靠猜坐标就假设成功。
2. 失败自动换策略重试（OCR 文字点击 -> 比例兜底点击 -> 键盘焦点）。
3. 全程把关键步骤截图存到 debug/ 目录，附 manifest，便于一次性诊断。
4. 成功坐标/标签写入 state.json，后续运行直接复用，越跑越稳。

IMA 桌面端是 Chromium/Electron（Class=Chrome_WidgetWin_1），画面可经
PrintWindow 截取自身；文字用 RapidOCR 识别；鼠标键盘用 pyautogui 模拟。
"""
# ↑ 三引号是「模块说明」（docstring）：讲清这个文件是"IMA 遥控器"——
#   它用"截图→OCR认字→模拟真人点击/打字"的方式操作 IMA 桌面软件，让它回答问题并截图。
import os            # 导入 os：处理路径、建目录、判断文件
import re            # 导入 re：正则，用于文字归一化比对
import time          # 导入 time：暂停几秒、计时
import json          # 导入 json：读写 state.json（记住成功坐标）
import logging       # 导入 logging：写运行日志
import ctypes        # 导入 ctypes：调用 Windows 底层（判断管理员、还原窗口）
import subprocess    # 导入 subprocess：用来启动外部程序（如启动 IMA）

import pyautogui     # 导入 pyautogui：模拟鼠标键盘
import pyperclip     # 导入 pyperclip：系统剪贴板（粘贴中文问题）
import uiautomation as auto    # 导入 uiautomation（操作窗口），起别名 auto
# 方法11/18：限制 UIA 单次查找超时，避免 Electron 窗口上无限阻塞（网上共识）
# ↑ 注释：下面把 uiautomation 的查找超时设短，避免卡在 Electron 窗口上无限等。
try:
    # ↑ try 保护：设置超时可能失败，包一下。
    auto.SetGlobalSearchTimeout(10)
    # ↑ 设全局查找超时为 10 秒（超时就放弃，不卡死）。
except Exception:
    # ↑ 失败就忽略。
    pass
import win32gui      # 导入 win32gui：按标题/类名找窗口
import win32con      # 导入 win32con：Windows 常量（如 SW_RESTORE）
import win32ui       # 导入 win32ui：Windows 界面底层
from PIL import Image, ImageDraw, ImageGrab
# ↑ 从 Pillow 导入 Image（处理图）、ImageDraw（画图标注）、ImageGrab（截图）

from ocr_engine import ocr_image
# ↑ 从 ocr_engine 引入 OCR 识别函数
from flow_runtime import mark  # AUTO-INSTRUMENTED
# ↑ 从流程追踪引入 mark（记录走到哪步，供可视化高亮）
from human_input import human_click, is_foreground, human_type, human_send_enter, bring_to_front
# ↑ 从 human_input 引入"拟人化"输入函数
from ima_watchdog import hard_timeout, IMATimeout  # 硬性超时守护：杜绝永久卡死
# ↑ 从 ima_watchdog 引入"超时保险丝"，防止程序永久卡死


# 注意：已废弃「强制置前」方案（原 _force_foreground 使用 AttachThreadInput /
# SetWindowPos(TOPMOST) / SetForegroundWindow 等强风控 API）。窗口激活改由 _restore()
# 用「真实鼠标点击标题栏」模拟人类完成，见 human_input。
# ↑ 注释说明历史决策：之前用强风控 API 强制置前，现已废弃，改用拟人化方式。


PW_RENDERFULLCONTENT = 2
# ↑ 一个常量（截图为完整内容的标志），保留备用。
pyautogui.FAILSAFE = True
# ↑ 开启 pyautogui 的安全保护：把鼠标甩到屏幕左上角可紧急中断程序，防止失控。


def _warn_if_not_admin():
    """网上共识：UIA/COM 在非管理员下易受限而表现为调用挂起，故给一次提示。"""
    # ↑ 定义 _warn_if_not_admin（非管理员警告）：检查是否以管理员运行，不是就提示。
    try:
        # ↑ try 保护：取管理员状态可能失败。
        if ctypes.windll.shell32.IsUserAnAdmin() == 0:
            # ↑ 如果返回值 0，表示"不是管理员"……
            logging.warning("[IMA] 非管理员运行：UIA/COM 可能受限导致卡死，"
                            "建议以管理员身份运行本脚本。")
            # ↑ 记警告日志，建议用管理员身份运行（否则可能卡死）。
    except Exception:
        # ↑ 取状态失败就忽略。
        pass

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ima_state.json")
# ↑ IMA 专用的状态文件路径（记录成功坐标/标签），和主程序的 state.json 分开。


class IMAController:
    # ↑ 定义一个类 IMAController（IMA 控制器），封装所有操作 IMA 的逻辑。
    def __init__(self, cfg, debug=True, debug_dir="debug"):
        # ↑ 初始化方法：创建控制器时执行。cfg=总配置；debug=是否存调试图；debug_dir=调试目录。
        self.cfg = cfg.get("ima", {})
        # ↑ 取出配置里 "ima" 这一块。
        self.hwnd = None
        # ↑ 先占个位：IMA 窗口句柄，初始 None。
        self._last_window_rect = None
        # ↑ 占个位：上次截图的窗口矩形，初始 None。
        # 布局参数（窗口内比例）
        # ↑ 注释：下面读入一堆"布局比例"参数（各区域在窗口中的大致位置）。
        self._ly = self.cfg.get("layout", {})
        # ↑ 取出 layout 配置块。
        # 固定布局：直接给出窗口物理矩形 [L,T,R,B]（pyautogui 坐标系）。
        # ↑ 注释：支持"固定窗口矩形"，配了就用，最稳。
        self.fixed_rect = self.cfg.get("fixed_rect")
        # ↑ 取出固定矩形配置。
        self.sidebar_ratio = float(self._ly.get("sidebar_ratio", 0.08))
        # ↑ 左侧栏宽度比例（默认 0.08）。
        self.top_bar_ratio = float(self._ly.get("top_bar_ratio", 0.15))
        # ↑ 顶部栏高度比例（默认 0.15）。
        self.input_ratio = float(self._ly.get("input_ratio", 0.18))
        # ↑ 底部输入框高度比例（默认 0.18）。
        self.input_center_x = float(self._ly.get("input_center_x", 0.55))
        # ↑ 输入框中心横坐标比例（默认 0.55）。
        self.input_center_y = float(self._ly.get("input_center_y", 0.92))
        # ↑ 输入框中心纵坐标比例（默认 0.92）。
        self.answer_top = float(self._ly.get("answer_top", 0.12))
        # ↑ 回答区顶部比例。
        self.answer_bottom = float(self._ly.get("answer_bottom", 0.82))
        # ↑ 回答区底部比例。
        self.answer_left = float(self._ly.get("answer_left", 0.20))
        # ↑ 回答区左边比例（排除左侧栏）。
        self.answer_right = float(self._ly.get("answer_right", 1.0))
        # ↑ 回答区右边比例。
        # UI 文本标签（可配置，适配不同版本）
        # ↑ 注释：下面读入"界面文字标签"，不同版本 IMA 文案可能不同，配了就按配置的认。
        self.labels = self.cfg.get("labels", {})
        # ↑ 取出 labels 配置块。
        self.input_hint_label = self.labels.get("input_hint", "有问题尽管问ima")
        # ↑ 输入框提示文字（默认"有问题尽管问ima"）。
        self.kb_chat_hint = self.labels.get("kb_chat_hint", "基于知识库提问")
        # ↑ 知识库问答提示文字。
        self.kb_chat_entries = self.cfg.get(
            # ↑ 知识库问答的"入口标签"候选列表（点其中一个就能进入问答）。
            "kb_chat_entries",
            ["基于知识库提问", "基于文件夹问答", "向知识库提问", "问知识库",
             "在知识库中提问", "问答", "AI问答", "开始问答", "问问知识库",
             "智能问答", "有问题尽管问ima", "问问ima"],
        )
        self.input_hints = self.labels.get("input_hints", [])
        # ↑ 取出"输入框提示"候选列表。
        if not self.input_hints:
            # ↑ 如果没配置，就用默认的一堆常见提示文字。
            self.input_hints = [self.input_hint_label, self.kb_chat_hint,
                                "基于知识库提问", "基于文件夹问答",
                                "问知识库", "向知识库提问",
                                "输入问题", "问ima", "请输入", "有问题尽管问ima"]
        # 行为参数
        # ↑ 注释：下面读入"行为参数"（等待时间、重试次数、超时等）。
        self.answer_wait = float(self.cfg.get("answer_wait", 25))
        # ↑ 最小等待回答的秒数（默认 25）。
        self.post_send_wait = float(self.cfg.get("post_send_wait", 2.0))
        # ↑ 发完问题后等待的秒数（默认 2）。
        self.chat_switch_wait = float(self.cfg.get("chat_switch_wait", 2.0))
        # ↑ 切换聊天/知识库后等待的秒数（默认 2）。
        self.max_answer_wait = float(self.cfg.get("max_answer_wait", 100))
        # ↑ 最多等多久等回答（默认 100 秒）。
        self.retry_attempts = int(self.cfg.get("retry_attempts", 3))
        # ↑ 单次提问最多重试几次（默认 3）。
        # 硬性总时限：整个 ask 流程超过此秒数一律中断返回 None（防永久卡死）
        # ↑ 注释：下面这个"硬性超时"是兜底保险，整个问答流程超过它就强制中断。
        self.ask_timeout = float(self.cfg.get("ask_timeout", 180))
        # ↑ 问答流程总超时秒数（默认 180）。
        # 自动启动 / 知识库导航
        # ↑ 注释：下面读入"自动启动"和"知识库导航"相关配置。
        self.kb_name = self.cfg.get("kb_name", "")
        # ↑ 目标知识库的名字（要进哪个库问答）。
        self.auto_launch = bool(self.cfg.get("auto_launch", True))
        # ↑ 是否允许自动启动 IMA（默认允许）。
        self.launch_timeout = float(self.cfg.get("launch_timeout", 20))
        # ↑ 等 IMA 启动的超时秒数（默认 20）。
        self.navigate_kb = bool(self.cfg.get("navigate_kb", True))
        # ↑ 是否要导航到目标知识库（默认要）。
        self.exe_path = self.cfg.get("exe_path", "") or ""
        # ↑ IMA 可执行文件路径（若已知可配，省去自动查找）。
        self._launched_once = False
        # ↑ 标记"是否已经尝试启动过"，避免重复启动。
        self._last_question = ""
        # ↑ 记下最后一次问的问题。
        # 调试
        # ↑ 注释：下面准备调试相关。
        self.debug = debug
        # ↑ 是否开启调试存图。
        self.debug_dir = debug_dir
        # ↑ 调试目录。
        self._dbg_index = 0
        # ↑ 调试图序号计数器。
        os.makedirs(self.debug_dir, exist_ok=True)
        # ↑ 创建调试目录（不存在才建）。
        self._manifest = []
        # ↑ 准备一个清单列表，记录每步调试图，方便一次性诊断。
        # 自校准状态
        # ↑ 注释：下面读取"自校准状态"（之前成功过的坐标/标签，直接复用）。
        self.state = self._load_state()
        # ↑ 读取 IMA 专用状态文件。

    # ------------------------------------------------------------------
    # 状态 / 调试
    # ↑ 注释分隔：下面是"状态读写"和"调试截图"相关方法。
    def _load_state(self):
        # ↑ 定义 _load_state（读取 IMA 状态）：从 ima_state.json 读出上次成功的配置。
        try:
            # ↑ try 保护：读文件可能失败。
            if os.path.exists(STATE_PATH):
                # ↑ 如果状态文件存在……
                with open(STATE_PATH, "r", encoding="utf-8") as f:
                    # ↑ 以只读、UTF-8 打开……
                    return json.load(f)
                    # ↑ 读成字典返回。
        except Exception:
            # ↑ 读失败忽略。
            pass
        return {}
        # ↑ 没文件或读失败，返回空字典。

    def _save_state(self):
        # ↑ 定义 _save_state（保存 IMA 状态）：把成功配置写回文件。
        try:
            # ↑ try 保护：写文件可能失败。
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                # ↑ 以写入、UTF-8 打开……
                json.dump(self.state, f, ensure_ascii=False, indent=2)
                # ↑ 把状态字典写回（中文正常、缩进整齐）。
        except Exception as e:
            # ↑ 写失败就记警告。
            logging.warning("保存 state.json 失败: %s", e)

    def _dbg(self, name):
        """截整窗存盘并记 manifest。返回路径或 None。"""
        # ↑ 定义 _dbg（存调试图）：截一张 IMA 整窗图存盘，并记录到清单。
        if not self.debug:
            # ↑ 如果没开调试，直接返回。
            return None
        try:
            # ↑ try 保护：截图/画图可能失败。
            img, rect = self.capture()
            # ↑ 截整窗图（capture 内部会还原窗口+截图）。
            self._dbg_index += 1
            # ↑ 调试图序号 +1。
            fn = "ima_%02d_%s.png" % (self._dbg_index, name)
            # ↑ 拼出调试图文件名（带序号和步骤名）。
            path = os.path.join(self.debug_dir, fn)
            # ↑ 拼出完整路径。
            img.save(path)
            # ↑ 保存截图。
            # 标注 OCR 文字
            # ↑ 注释：下面在图上把 OCR 认到的字框出来，方便排查。
            annotated = img.copy()
            # ↑ 复制一张图用来画标注（不破坏原图）。
            d = ImageDraw.Draw(annotated)
            # ↑ 拿画笔。
            for text, rx, ry in ocr_image(img):
                # ↑ 对原图做 OCR，遍历每个认到的字（rx,ry 是图内坐标）……
                d.rectangle([rx - 3, ry - 3, rx + 80, ry + 3], outline=(255, 0, 0), width=1)
                # ↑ 画红色小框。
                d.text((rx, ry - 14), text[:20], fill=(255, 0, 0))
                # ↑ 在字上方写前 20 个字（红字）。
            ann_path = os.path.join(self.debug_dir, "ann_" + fn)
            # ↑ 拼出标注图的文件名。
            annotated.save(ann_path)
            # ↑ 保存标注图。
            self._manifest.append("%02d %s -> %s" % (self._dbg_index, name, fn))
            # ↑ 把这一步记录加进清单。
            return path
            # ↑ 返回调试图路径。
        except Exception as e:
            # ↑ 截图失败就记警告。
            logging.warning("debug 截图失败(%s): %s", name, e)
            return None
            # ↑ 返回 None。

    def _manifest_flush(self):
        # ↑ 定义 _manifest_flush（写清单）：把调试清单写到 manifest.txt。
        try:
            # ↑ try 保护：写文件可能失败。
            with open(os.path.join(self.debug_dir, "manifest.txt"), "w", encoding="utf-8") as f:
                # ↑ 以写入、UTF-8 打开 manifest.txt……
                f.write("\n".join(self._manifest))
                # ↑ 把所有清单条目用换行连起来写进去。
        except Exception:
            # ↑ 失败忽略。
            pass

    # ------------------------------------------------------------------
    # 窗口
    # ↑ 注释分隔：下面这组方法负责"找 IMA 窗口、启动 IMA、还原窗口"。
    def find_window(self):
        # ↑ 定义 find_window（找窗口）：定位 IMA 窗口，找不到就尝试自动启动。
        if self._locate_window():
            # ↑ 先尝试定位已有窗口；找到了……
            return self.hwnd
            # ↑ 返回窗口句柄。
        if self.auto_launch and not self._launched_once:
            # ↑ 如果允许自动启动、且还没启动过……
            self._launched_once = True
            # ↑ 标记"已尝试启动"。
            if self.launch():
                # ↑ 尝试启动 IMA；成功……
                return self.hwnd
                # ↑ 返回窗口句柄。
        if not self.hwnd:
            # ↑ 如果还是没找到窗口……
            logging.error("未找到 IMA 窗口，且自动启动失败。请确认 IMA 已安装并登录过。")
            # ↑ 记错误日志，提示用户确认 IMA 已安装登录。
        return self.hwnd
        # ↑ 返回（可能是 None 表示失败）。

    def _locate_window(self):
        """定位 IMA 窗口。优先用 win32gui 按标题匹配（避开 uiautomation 在
        Electron 窗口上偶发的 COM 崩溃），失败再用 uiautomation 兜底。
        """
        # ↑ docstring 解释：_locate_window 用"按标题找"的方式定位 IMA 窗口，
        #   优先 win32gui（更稳），失败再用 uiautomation 兜底。
        # 1) win32gui：枚举所有顶层窗口，按标题关键字匹配
        # ↑ 注释：步骤1：用 win32gui 遍历所有窗口，按标题关键词匹配 IMA。
        try:
            # ↑ try 保护：找窗口可能失败。
            candidates = []
            # ↑ 准备候选窗口列表。
            title_keys = [t for t in (self.cfg.get("window_title", ""), "ima.copilot", "ima")
                          if t]
            # ↑ 取出配置的窗口标题关键词 + 内置默认关键词（ima.copilot、ima）。
            if self.kb_name:
                # ↑ 如果配置了知识库名……
                title_keys.append(self.kb_name)
                # ↑ 也把知识库名当关键词（标题里常含库名）。

            def _enum(hwnd, _):
                # ↑ 内部回调：系统枚举每个窗口时调用。
                if not hwnd:
                    # ↑ 句柄为空就跳过。
                    return
                try:
                    # ↑ try 保护：取标题可能失败。
                    txt = win32gui.GetWindowText(hwnd) or ""
                    # ↑ 取窗口标题文字。
                except Exception:
                    # ↑ 失败就跳过这个窗口。
                    return
                tl = txt.lower()
                # ↑ 标题转小写，便于不区分大小写匹配。
                for k in title_keys:
                    # ↑ 遍历每个关键词……
                    if k and k.lower() in tl:
                        # ↑ 如果关键词出现在标题里……
                        candidates.append((hwnd, txt))
                        # ↑ 加入候选列表。
                        return
                        # ↑ 找到一个就停（不再匹配其它关键词）。
            win32gui.EnumWindows(_enum, None)
            # ↑ 让系统遍历所有窗口，对每个调用 _enum。
            # 优先精确匹配 ima.copilot / 知识库标题
            # ↑ 注释：下面优先挑"精确匹配 ima.copilot 或含知识库名"的窗口。
            for hwnd, txt in candidates:
                # ↑ 遍历候选……
                if "ima.copilot" in txt.lower() or (self.kb_name and self.kb_name in txt):
                    # ↑ 如果标题含 ima.copilot 或知识库名（最精确）……
                    self.hwnd = hwnd
                    # ↑ 记下句柄。
                    logging.info("已定位 IMA 窗口（win32gui）：Name=%r hwnd=%s", txt, self.hwnd)
                    # ↑ 记日志。
                    return self.hwnd
                    # ↑ 返回。
            if candidates:
                # ↑ 如果候选非空（但没有精确匹配）……
                hwnd, txt = candidates[0]
                # ↑ 取第一个候选。
                self.hwnd = hwnd
                # ↑ 记下句柄。
                logging.info("已定位 IMA 窗口（win32gui 兜底）：Name=%r hwnd=%s", txt, self.hwnd)
                # ↑ 记日志。
                return self.hwnd
                # ↑ 返回。
        except Exception as e:
            # ↑ win32gui 流程失败……
            logging.debug("win32gui 定位失败，转 uiautomation: %s", e)
            # ↑ 记调试日志。

        # 2) uiautomation 兜底（加 try/except 防 COM 崩溃）
        # ↑ 注释：步骤2：用 uiautomation 再试一次（整体包 try 防崩溃）。
        try:
            # ↑ try 保护。
            for name in (self.cfg.get("window_title", ""), "ima", "IMA", "腾讯IMA", "ima.copilot"):
                # ↑ 遍历几个可能的窗口名……
                if not name:
                    # ↑ 空名跳过。
                    continue
                c = auto.WindowControl(Name=name)
                # ↑ 按名字创建窗口控件。
                try:
                    # ↑ try 保护：单个查找可能失败。
                    if c.Exists(3):
                        # ↑ 如果存在（3 秒确认）……
                        self.hwnd = c.NativeWindowHandle
                        # ↑ 取句柄。
                        if self.hwnd:
                            # ↑ 句柄有效……
                            logging.info("已定位 IMA 窗口：Name=%r hwnd=%s", c.Name, self.hwnd)
                            # ↑ 记日志。
                            return self.hwnd
                            # ↑ 返回。
                except Exception:
                    # ↑ 查找失败就继续下一个名字。
                    continue
            for w in auto.GetRootControl().GetChildren():
                # ↑ 兜底：遍历桌面根下所有子窗口，按"Electron类 + 含ima"判断。
                try:
                    # ↑ try 保护。
                    n = (w.Name or "").lower()
                    # ↑ 取窗口名转小写。
                    cls = (getattr(w, "ClassName", "") or "").lower()
                    # ↑ 取类名转小写。
                    is_electron = ("chrome" in cls or "widgetwin" in cls or "electron" in cls)
                    # ↑ 判断是不是 Electron 类窗口（IMA 是 Electron 写的）。
                    title_ok = (n == "ima" or n == "ima.copilot" or "ima.copilot" in n)
                    # ↑ 判断标题是否像 IMA。
                    if (is_electron and ("ima" in n or "copilot" in n)) or title_ok:
                        # ↑ 如果是 Electron 且标题含 ima/copilot，或标题精确匹配……
                        self.hwnd = w.NativeWindowHandle
                        # ↑ 取句柄。
                        logging.info("已定位 IMA 窗口（兜底）：Name=%r Class=%r hwnd=%s",
                                     w.Name, cls, self.hwnd)
                        # ↑ 记日志。
                        return self.hwnd
                        # ↑ 返回。
                except Exception:
                    # ↑ 单个失败就继续。
                    continue
        except Exception as e:
            # ↑ uiautomation 整体失败……
            logging.debug("uiautomation 兜底定位失败: %s", e)
            # ↑ 记调试日志。
        return None
        # ↑ 实在找不到，返回 None。

    # ------------------------------------------------------------------
    # 自动启动
    # ↑ 注释分隔：下面这组方法负责"自动启动 IMA"。
    def _find_ima_exe(self):
        # ↑ 定义 _find_ima_exe（找 IMA 程序）：在常见安装位置寻找 ima.exe。
        if self.exe_path and os.path.exists(self.exe_path):
            # ↑ 如果配置了 exe 路径且文件存在……
            return self.exe_path
            # ↑ 直接返回这个路径。
        local = os.path.expandvars("%LOCALAPPDATA%")
        # ↑ 取用户本地应用数据目录（环境变量展开）。
        bases = [
            # ↑ 列出几个可能的"根目录"去搜。
            local,
            os.path.expandvars("%APPDATA%"),
            os.environ.get("ProgramFiles", "C:/Program Files"),
            os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"),
        ]
        candidates = []
        # ↑ 准备候选 exe 路径列表。
        for base in bases:
            # ↑ 遍历每个根目录……
            if not base:
                # ↑ 空的跳过。
                continue
            for sub in ("Programs/ima-copilot", "Programs/ima", "ima-copilot", "ima"):
                # ↑ 几个常见的子路径……
                candidates.append(os.path.join(base, sub, "ima.exe"))
                # ↑ 拼出 ima.exe 的完整候选路径。
        for c in candidates:
            # ↑ 遍历候选路径……
            if os.path.exists(c):
                # ↑ 如果存在……
                logging.info("找到 IMA 可执行文件：%s", c)
                # ↑ 记日志。
                return c
                # ↑ 返回路径。
        for base in (os.path.join(local, "Programs"), local):
            # ↑ 再深入一层：遍历 Programs 下各文件夹……
            if not os.path.isdir(base):
                # ↑ 不是目录就跳过。
                continue
            try:
                # ↑ try 保护：遍历目录可能失败。
                for entry in os.scandir(base):
                    # ↑ 遍历该目录下的每一项……
                    if entry.is_dir():
                        # ↑ 只关心子文件夹……
                        for nm in ("ima.exe", "ima-copilot.exe"):
                            # ↑ 两种可能的 exe 名……
                            p = os.path.join(entry.path, nm)
                            # ↑ 拼出完整路径。
                            if os.path.exists(p):
                                # ↑ 如果存在……
                                logging.info("找到 IMA 可执行文件：%s", p)
                                # ↑ 记日志。
                                return p
                                # ↑ 返回路径。
            except Exception:
                # ↑ 遍历失败就继续下一个根。
                continue
        return ""
        # ↑ 都没找到，返回空字符串。

    def launch(self):
        # ↑ 定义 launch（启动 IMA）：找到 exe 并启动，然后等窗口出现。
        if self.hwnd:
            # ↑ 如果窗口已经找到了……
            return True
            # ↑ 不用启动，直接返回成功。
        exe = self._find_ima_exe()
        # ↑ 先找 IMA 的 exe 路径。
        if exe:
            # ↑ 如果找到了 exe……
            try:
                # ↑ try 保护：启动可能失败。
                logging.info("尝试启动 IMA：%s", exe)
                # ↑ 记日志。
                subprocess.Popen([exe])
                # ↑ 启动这个程序（Popen 不阻塞，启动后立刻返回）。
            except Exception as e:
                # ↑ 启动失败就记错误日志。
                logging.error("启动 IMA 失败: %s", e)
        else:
            # ↑ 如果没找到 exe……
            for proto in ("ima://", "tencentima://", "imacopilot://"):
                # ↑ 尝试用"协议"方式启动（类似网页的 mailto:）……
                try:
                    # ↑ try 保护：协议启动可能失败。
                    os.startfile(proto)
                    # ↑ 用系统默认方式打开这个协议（会唤起 IMA）。
                    logging.info("已尝试通过协议启动 IMA：%s", proto)
                    # ↑ 记日志。
                    break
                    # ↑ 试一个就行，跳出。
                except Exception:
                    # ↑ 失败就试下一个协议。
                    continue
        deadline = time.time() + self.launch_timeout
        # ↑ 算出"最晚等到什么时候"（当前时间 + 启动超时）。
        while time.time() < deadline:
            # ↑ 在超时前循环等待……
            if self._locate_window():
                # ↑ 每次检查窗口是否出现了……
                logging.info("IMA 已启动。")
                # ↑ 记日志。
                return True
                # ↑ 出现就返回成功。
            time.sleep(1.0)
            # ↑ 没出现就等 1 秒再查。
        logging.error("等待 IMA 窗口超时（%.0fs）。", self.launch_timeout)
        # ↑ 超时仍没窗口，记错误日志。
        return False
        # ↑ 返回失败。

    def _window_title(self):
        # ↑ 定义 _window_title（取窗口标题）：返回 IMA 窗口标题文字。
        if not self.hwnd:
            # ↑ 没窗口句柄……
            return ""
            # ↑ 返回空。
        try:
            # ↑ try 保护：取标题可能失败。
            return win32gui.GetWindowText(self.hwnd) or ""
            # ↑ 取窗口标题文字（取不到就空）。
        except Exception:
            # ↑ 失败返回空。
            return ""

    def _title_has_kb(self):
        # ↑ 定义 _title_has_kb（标题含知识库名吗）：判断窗口标题是否已显示目标库名。
        if not self.kb_name:
            # ↑ 如果没配置知识库名（不限定库）……
            return True
            # ↑ 直接认为"已在该库"（无需导航）。
        return self.kb_name in self._window_title()
        # ↑ 返回：知识库名是否出现在窗口标题里。

    def _restore(self):
        """仅当 IMA 窗口被最小化时才还原；绝不对已停靠/正常的窗口调用 ShowWindow
        （SW_RESTORE 会把 docked 窗口还原成旧位置，导致窗口乱跳）。
        若不在前台，用 bring_to_front（SetWindowPos 置顶 + SetForegroundWindow）把它
        可靠带到最前——重叠布局下点标题栏会被别的窗口挡住，置顶法更稳；标题栏点击
        不再作为主路径。不使用 TOPMOST / AttachThreadInput 等强风控 API。
        """
        # ↑ docstring 解释：_restore 还原最小化窗口；若不在前台，用"置顶+置前"带上来
        #   （比点标题栏更稳），但不使用强风控 API。
        if not self.hwnd:
            # ↑ 没窗口句柄就返回。
            return
        if ctypes.windll.user32.IsIconic(self.hwnd):
            # ↑ 如果窗口是最小化状态……
            ctypes.windll.user32.ShowWindow(self.hwnd, win32con.SW_RESTORE)
            # ↑ 还原窗口（SW_RESTORE）。
        if not is_foreground(self.hwnd):
            # ↑ 如果不在前台……
            if bring_to_front(self.hwnd):
                # ↑ 用置顶+置前把它带上来；成功……
                time.sleep(0.4)
                # ↑ 暂停 0.4 秒。
                return
                # ↑ 返回。
            # 兜底：点标题栏中部
            # ↑ 注释：置顶失败，兜底"点标题栏中部"。
            try:
                # ↑ try 保护。
                left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
                # ↑ 取窗口矩形。
                w, h = right - left, bottom - top
                # ↑ 算宽高。
                human_click(int(left + w * 0.5), int(top + max(8, int(h * 0.02))))
                # ↑ 拟人点击标题栏中部。
            except Exception:
                # ↑ 失败忽略。
                pass
        time.sleep(0.4)
        # ↑ 最后暂停 0.4 秒。

    def _rect(self):
        # ↑ 定义 _rect（取窗口矩形）：返回 IMA 窗口的位置和大小。
        if self.fixed_rect:
            # ↑ 如果配置了固定矩形……
            L, T, R, B = [int(v) for v in self.fixed_rect]
            # ↑ 拆出 左/上/右/下 并转整数。
            return L, T, R, B, R - L, B - T
            # ↑ 返回 (左,上,右,下,宽,高)。
        if not self.hwnd:
            # ↑ 没窗口句柄……
            raise RuntimeError("IMA 窗口未定位")
            # ↑ 抛错：得先找到窗口。
        L, T, R, B = win32gui.GetWindowRect(self.hwnd)
        # ↑ 用 win32 取窗口矩形。
        return L, T, R, B, R - L, B - T
        # ↑ 返回 (左,上,右,下,宽,高)。

    def _grab(self):
        """截取 IMA 窗口本体（物理像素）。用 ImageGrab 按窗口物理矩形抓屏，
        绕开 PrintWindow 在高 DPI 下逻辑/物理尺寸错配；坐标与 pyautogui 点击
        同处物理像素空间。返回 (img, left, top, w, h)。
        """
        # ↑ docstring 解释：_grab 按窗口物理矩形截图，返回图片和窗口位置尺寸。
        left, top, right, bottom, w, h = self._rect()
        # ↑ 先取窗口矩形。
        img = ImageGrab.grab((left, top, right, bottom))
        # ↑ 按矩形截图。
        return img, left, top, w, h
        # ↑ 返回 (图片, 左, 上, 宽, 高)。

    def capture(self, path=None, region=None):
        # ↑ 定义 capture（截图）：对外统一截图接口，可存盘、可只取某区域。
        self._restore()
        # ↑ 先还原+置前窗口，确保截到的是 IMA。
        img, left, top, w, h = self._grab()
        # ↑ 截整窗图。
        if region:
            # ↑ 如果指定了"区域"（四个比例值）……
            x0, y0, x1, y1 = region
            # ↑ 拆出区域的四个比例。
            img = img.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1)))
            # ↑ 按区域比例裁剪出子图。
        if path:
            # ↑ 如果指定了存盘路径……
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            # ↑ 创建路径所在的目录。
            img.save(path)
            # ↑ 保存图片。
        # 物理矩形与物理图像尺寸一致，坐标换算无需缩放（见 _find_text）。
        # ↑ 注释：下面把窗口矩形和图片尺寸记下来，供后面坐标换算（物理像素=图片像素，不用缩放）。
        self._last_window_rect = (left, top, w, h)
        # ↑ 记下窗口矩形。
        self._last_img_size = (w, h)
        # ↑ 记下图片尺寸。
        return img, (left, top, w, h)
        # ↑ 返回 (图片, (左,上,宽,高))。

    # ------------------------------------------------------------------
    # OCR 与点击辅助
    # ↑ 注释分隔：下面这组方法是"OCR 认字 + 点击"的辅助工具。
    def _ocr_region_text(self, x0, y0, x1, y1):
        # ↑ 定义 _ocr_region_text（区域文字）：对窗口某区域截图并返回所有认到的文字（拼成一段）。
        img, _ = self.capture(region=(x0, y0, x1, y1))
        # ↑ 截指定比例区域的图（_ 表示忽略窗口矩形返回值）。
        return "\n".join(t for t, _x, _y in ocr_image(img))
        # ↑ 对子图做 OCR，把每个认到的文字用换行拼成一段长文本返回。

    def _find_text(self, img, target, case_sensitive=False, strip=True):
        # ↑ 定义 _find_text（找文字并返回坐标）：在图片里找目标文字，返回它的屏幕坐标。
        if not self._last_window_rect or not getattr(self, "_last_img_size", None):
            # ↑ 如果窗口矩形或图片尺寸没记录（截图没成功）……
            return None
            # ↑ 返回 None（没法换算坐标）。
        left, top, lw, lh = self._last_window_rect
        # ↑ 取出窗口矩形（左,上,宽,高）。
        iw, ih = self._last_img_size
        # ↑ 取出图片尺寸（宽,高）。
        # 图像像素 -> 屏幕逻辑坐标 的缩放（修复高 DPI 下点击打偏/打飞的问题）
        # ↑ 注释：下面算"图片像素 → 屏幕坐标"的缩放，修复高 DPI 下点击打偏。
        sx = (lw / iw) if iw else 1.0
        # ↑ 横向缩放。
        sy = (lh / ih) if ih else 1.0
        # ↑ 纵向缩放。
        target = target.strip() if strip else target
        # ↑ 目标文字去空格（除非要求保留）。
        tgt = (target if case_sensitive else target.lower())
        # ↑ 目标文字：不区分大小写就转小写。
        # 两遍匹配：
        #  第 1 遍优先「整词精确匹配」，避免短词（如「问答」）误命中统计文字
        #    「801浏览和问答」等长文本而被点错；
        #  第 2 遍再退回到子串包含匹配（用于长标签 / 长知识库名）。
        # ↑ 注释：先精确整词匹配，避免短词误中点错；再退回到"包含"匹配。
        for exact in (True, False):
            # ↑ 先跑一遍精确匹配(exact=True)，再跑一遍包含匹配(exact=False)。
            for text, rx, ry in ocr_image(img):
                # ↑ 遍历图片里每个认到的字（rx,ry 是图内坐标）……
                t = text.strip() if strip else text
                # ↑ 当前文字去空格。
                cmp = (t if case_sensitive else t.lower())
                # ↑ 当前文字：不区分大小写就转小写。
                if exact:
                    # ↑ 第一遍：精确整词匹配……
                    if cmp == tgt:
                        # ↑ 如果完全相等……
                        return text, rx, ry, left + int(rx * sx), top + int(ry * sy)
                        # ↑ 返回 (文字, 图内x, 图内y, 屏幕x, 屏幕y)。
                else:
                    # ↑ 第二遍：包含匹配……
                    if tgt in cmp and cmp != tgt:
                        # ↑ 如果目标嵌在当前文字里、且不完全相等（避免重复命中）……
                        # 子串匹配：若目标是一个很短的词（<=2 个汉字），且该词
                        # 嵌在更长的中文 token 中（前后还有其它中文字），视为误
                        # 命中（如「问答」∈「浏览和问答」），跳过。
                        # ↑ 注释：短词（≤2字）嵌在更长中文里可能是误命中，下面额外检查。
                        if len(tgt) <= 2 and re.search(r"[一-鿿]", tgt):
                            # ↑ 如果目标是 ≤2 字的中文短词……
                            # 检查目标在 token 中是否被其它汉字紧邻包围
                            # ↑ 注释：检查目标前后是否被其它汉字紧邻（说明它是长词的一部分）。
                            idx = cmp.find(tgt)
                            # ↑ 找目标在文字里的位置。
                            before = cmp[idx - 1] if idx > 0 else ""
                            # ↑ 目标前一个字（如果有）。
                            after = cmp[idx + len(tgt)] if idx + len(tgt) < len(cmp) else ""
                            # ↑ 目标后一个字（如果有）。
                            if re.search(r"[一-鿿]", before) or re.search(r"[一-鿿]", after):
                                # ↑ 如果前后都是汉字（说明目标夹在长词里）……
                                continue
                                # ↑ 跳过这次，不当匹配。
                        return text, rx, ry, left + int(rx * sx), top + int(ry * sy)
                        # ↑ 返回坐标。
        return None
        # ↑ 两遍都没找到，返回 None。

    def _click_text(self, img, target, fallback=None, case_sensitive=False):
        # ↑ 定义 _click_text（点文字）：在图片里找目标文字并点击它；找不到就用兜底坐标。
        found = self._find_text(img, target, case_sensitive=case_sensitive)
        # ↑ 先找目标文字的坐标。
        if found:
            # ↑ 如果找到了……
            _, _, _, sx, sy = found
            # ↑ 取出屏幕坐标（前面三个 _ 是忽略的值）。
            logging.info("  点击文字 '%s' @ (%d, %d)", target, sx, sy)
            # ↑ 记日志：点哪个字、点哪。
            human_click(sx, sy)
            # ↑ 拟人点击该坐标。
            return True
            # ↑ 返回成功。
        if fallback:
            # ↑ 如果没找到、但提供了兜底坐标……
            logging.info("  未找到 '%s'，使用兜底坐标 %s", target, fallback)
            # ↑ 记日志：用兜底坐标。
            human_click(fallback[0], fallback[1])
            # ↑ 点击兜底坐标。
            return True
            # ↑ 返回成功。
        return False
        # ↑ 都没点到，返回失败。

    def _click_ratio(self, x_ratio, y_ratio):
        # ↑ 定义 _click_ratio（按比例点击）：按"窗口内比例"换算成坐标并点击。
        rect = getattr(self, "_last_window_rect", None)
        # ↑ 取上次窗口矩形。
        if not rect:
            # ↑ 没有矩形记录（没截过图）……
            return None
            # ↑ 返回 None（没法算）。
        left, top, lw, lh = rect
        # ↑ 取出 (左,上,宽,高)。
        sx = left + int(lw * x_ratio)
        # ↑ 屏幕 x = 左边 + 宽 × 横向比例。
        sy = top + int(lh * y_ratio)
        # ↑ 屏幕 y = 上边 + 高 × 纵向比例。
        logging.info("  点击比例坐标 (%.2f,%.2f) -> (%d, %d)", x_ratio, y_ratio, sx, sy)
        # ↑ 记日志：比例 → 屏幕坐标。
        human_click(sx, sy)
        # ↑ 拟人点击。
        return sx, sy
        # ↑ 返回点击的屏幕坐标。

    @staticmethod
    def _norm(s):
        # ↑ 定义 _norm（规范化）：把文字变成"可比对"的干净形式（去标点、小写）。
        s = (s or "").strip().lower()
        # ↑ 去空格、转小写。
        return re.sub(r"[^一-鿿a-z0-9]", "", s)
        # ↑ 只保留中文、字母、数字，删掉其它字符。

    def _text_in_chat(self, img, target):
        """在聊天区 OCR 结果里模糊找目标文字（归一化，容忍 OCR 误差）。"""
        # ↑ 定义 _text_in_chat（聊天区含文字吗）：模糊判断图片里有没有目标文字。
        tn = self._norm(target)
        # ↑ 把目标文字规范化。
        if not tn:
            # ↑ 空就返回 False。
            return False
        for text, _x, _y in ocr_image(img):
            # ↑ 遍历图片里认到的文字……
            if tn in self._norm(text) or self._norm(text) in tn:
                # ↑ 如果规范化后互相包含……
                return True
                # ↑ 认为找到了。
        return False
        # ↑ 没找到，返回 False。

    def _in_chat_view(self, img):
        # ↑ 定义 _in_chat_view（在问答视图吗）：用"输入提示"判断是否在问答界面。
        for hint in self.input_hints:
            # ↑ 遍历所有输入提示候选……
            if self._find_text(img, hint) is not None:
                # ↑ 如果图片里能找到这个提示文字……
                return True
                # ↑ 说明在问答视图。
        return False
        # ↑ 都没找到，返回 False。

    def _has_bottom_input(self, img):
        """窗口下半屏(图像 y 比例>0.5)是否存在输入框提示（"基于知识库提问"等）。

        用 y 位置区分：底部的是『问答输入框』，顶部的是『文档列表搜索框』，二者
        文案可能相同但位置不同，必须点底部的才能真正发问到知识库。
        """
        # ↑ docstring 解释：_has_bottom_input 判断"输入框提示"是否在下半屏，
        #   因为顶部也有同款提示（那是文档搜索框），只有底部的才是真正发问的框。
        iw, ih = getattr(self, "_last_img_size", (1, 1))
        # ↑ 取图片尺寸（没有就当 1x1）。
        for hint in self.input_hints:
            # ↑ 遍历输入提示候选……
            for text, rx, ry in ocr_image(img):
                # ↑ 遍历图片里认到的文字（rx,ry 图内坐标）……
                t = (text or "").strip()
                # ↑ 当前文字去空格。
                if t and (t == hint or hint in t) and ih and (ry / ih) > 0.5:
                    # ↑ 如果匹配提示、且位于图片下半屏（y比例>0.5）……
                    return True
                    # ↑ 说明底部有输入框。
        return False
        # ↑ 没找到，返回 False。

    def _in_kb_chat_view(self, img):
        mark("_in_kb_chat_view", "判定是否在知识库问答")  # AUTO-INSTRUMENTED
        """是否在『目标知识库』专属问答视图。

        关键：标题已含知识库名 且 窗口底部存在输入框（区别于文档列表页的顶部搜索框）
        则说明已在正确的问答视图；否则退而用输入提示出现位置判断。
        """
        # ↑ docstring 解释：判断当前是不是在"目标知识库"的问答视图。
        if not self.kb_name:
            # ↑ 如果没指定知识库（不限定）……
            return self._in_chat_view(img)
            # ↑ 只要在问答视图就行。
        if self._title_has_kb() and self._has_bottom_input(img):
            # ↑ 如果标题含库名、且底部有输入框（双条件都满足）……
            return True
            # ↑ 确定在正确的问答视图。
        return self._in_chat_view(img)
        # ↑ 否则退回用输入提示判断。

    # ------------------------------------------------------------------
    # 进入问答视图 + 聚焦输入（多策略 + 自检）
    # ↑ 注释分隔：下面这组方法负责"进入问答视图、聚焦输入框"。
    def ensure_window_and_kb(self):
        mark("ensure_window_and_kb", "定位窗口+确保知识库")  # AUTO-INSTRUMENTED
        # ↑ 定义 ensure_window_and_kb（确保窗口+知识库）：先找窗口，再确保进对知识库。
        if not self.hwnd:
            # ↑ 如果还没定位到窗口……
            if not self.find_window():
                # ↑ 尝试找窗口；找不到……
                return False
                # ↑ 返回失败。
        self._restore()
        # ↑ 还原+置前窗口。
        self.ensure_kb()
        # ↑ 确保进入目标知识库问答视图。
        return True
        # ↑ 返回成功。

    def ensure_kb(self):
        mark("ensure_kb", "确保目标知识库")  # AUTO-INSTRUMENTED
        """确保在『目标知识库』的问答视图（不是泛用的全局问问ima）。"""
        # ↑ docstring 解释：ensure_kb 确保当前在"目标知识库"的问答视图（而非通用问问ima）。
        if not self.navigate_kb or not self.kb_name:
            # ↑ 如果配置不导航、或没指定库名……
            return True
            # ↑ 直接算成功（无需导航）。
        img, _ = self.capture()
        # ↑ 截一张图。
        if self._in_kb_chat_view(img):
            # ↑ 如果已经在正确的知识库问答视图……
            logging.info("IMA 已在目标知识库「%s」问答视图", self.kb_name)
            # ↑ 记日志。
            return True
            # ↑ 返回成功。
        logging.info("IMA 尚未进入知识库「%s」，开始导航...", self.kb_name)
        # ↑ 记日志：还没进，开始导航。
        return self._navigate_to_kb_chat()
        # ↑ 调用导航函数进入知识库。

    def _navigate_to_kb_chat(self):
        mark("_navigate_to_kb_chat", "导航到知识库问答")  # AUTO-INSTRUMENTED
        """主动导航到目标知识库的问答视图。

        正确入口：直接点『左侧栏里的知识库名』即可进入该库的问答视图。
        注意：千万不要点左侧栏的「知识库」菜单——那会进入知识库管理/文档列表页
        （其顶部也有一个「基于知识库提问」搜索框，点到它会变成搜索而非问答）。
        只有在左侧栏没直接列出时才展开「知识库」菜单去找。
        """
        # ↑ docstring 解释：_navigate_to_kb_chat 导航进目标知识库问答视图。
        #   关键：直接点左侧栏的"库名"；别点"知识库"菜单（那是管理页不是问答页）。
        img, _ = self.capture()
        # ↑ 截一张图。
        # 1) 直接点左侧栏里的知识库名（进入该库问答视图）
        # ↑ 注释：策略1：直接点左侧栏里的知识库名。
        if self._click_text(img, self.kb_name):
            # ↑ 如果找到并点击了知识库名……
            time.sleep(self.chat_switch_wait)
            # ↑ 暂停（等切换）。
            img, _ = self.capture()
            # ↑ 重新截图。
            if self._in_kb_chat_view(img):
                # ↑ 如果已进入正确视图……
                return True
                # ↑ 返回成功。
        if not self._click_kb_by_norm(img):
            # ↑ 策略2：用模糊匹配点知识库名（OCR 可能认错长名）……
            # 2) 左侧栏可能折叠在「知识库」菜单下，先展开再点知识库名
            # ↑ 注释：如果没直接列出，就展开"知识库"菜单再找。
            for label in ("知识库", "我的知识库", "个人知识库", "全部知识库"):
                # ↑ 遍历可能的菜单名……
                if self._click_text(img, label):
                    # ↑ 点开这个菜单……
                    time.sleep(self.chat_switch_wait)
                    # ↑ 暂停。
                    img, _ = self.capture()
                    # ↑ 重新截图。
                    if self._click_text(img, self.kb_name) or self._click_kb_by_norm(img):
                        # ↑ 再点知识库名（精确或模糊）……
                        time.sleep(self.chat_switch_wait)
                        # ↑ 暂停。
                        img, _ = self.capture()
                        # ↑ 重新截图。
                        if self._in_kb_chat_view(img):
                            # ↑ 如果已进入正确视图……
                            return True
                            # ↑ 返回成功。
                    break
                    # ↑ 菜单只要展开一次，跳出。
        # 3) 兜底：若当前已在含底部输入框的视图，也算成功
        # ↑ 注释：策略3：兜底，当前已在含底部输入框的问答视图也算成功。
        return self._in_kb_chat_view(img)
        # ↑ 返回当前是否已在正确视图。

    def _click_kb_by_norm(self, img):
        mark("_click_kb_by_norm", "模糊匹配知识库名")  # AUTO-INSTRUMENTED
        """OCR 可能因长中文名出错，退一步用归一化（去标点/空格）模糊匹配知识库名。"""
        # ↑ docstring 解释：_click_kb_by_norm 用"去标点归一化"模糊匹配知识库名
        #   （因为长中文名 OCR 容易认错，直接比对会失败）。
        tn = self._norm(self.kb_name)
        # ↑ 把知识库名规范化。
        if not tn:
            # ↑ 空就返回失败。
            return False
        best = None
        # ↑ 先占个位：最佳匹配的文字。
        for text, _x, _y in ocr_image(img):
            # ↑ 遍历图片里认到的文字……
            cn = self._norm(text)
            # ↑ 当前文字规范化。
            if not cn:
                # ↑ 空跳过。
                continue
            if tn in cn or cn in tn or (len(tn) > 4 and cn[:4] == tn[:4]):
                # ↑ 如果互相包含、或前 4 字相同（长名容错）……
                best = text
                # ↑ 记下这个文字。
                break
                # ↑ 找到就停。
        if best is None:
            # ↑ 如果没找到候选……
            return False
            # ↑ 返回失败。
        found = self._find_text(img, best)
        # ↑ 用找到的文字精确取它的坐标。
        if found:
            # ↑ 如果取到坐标……
            _, _, _, sx, sy = found
            # ↑ 取出屏幕坐标。
            logging.info("  归一化匹配并点击知识库 '%s' @ (%d, %d)", best, sx, sy)
            # ↑ 记日志。
            human_click(sx, sy)
            # ↑ 拟人点击。
            return True
            # ↑ 返回成功。
        return False
        # ↑ 没取到坐标，返回失败。

    def _enter_kb_chat(self, img):
        mark("_enter_kb_chat", "进入问答视图")  # AUTO-INSTRUMENTED
        # ↑ 定义 _enter_kb_chat（进入问答）：点击"问答入口"标签进入问答。
        entries = list(self.kb_chat_entries)
        # ↑ 复制一份问答入口标签候选列表。
        # 优先使用上一次成功的入口标签
        # ↑ 注释：下面把"上次成功的入口"排到最前面，优先用（自校准，越跑越稳）。
        last = self.state.get("ima", {}).get("kb_chat_entry")
        # ↑ 从状态里取上次成功的入口标签。
        if last and last in entries:
            # ↑ 如果上次成功过、且仍在候选里……
            entries.remove(last)
            # ↑ 先从列表移除。
            entries.insert(0, last)
            # ↑ 再插到最前面（优先尝试）。
        for label in entries:
            # ↑ 遍历入口标签候选……
            if self._click_text(img, label):
                # ↑ 如果找到并点击了这个入口……
                self.state.setdefault("ima", {})["kb_chat_entry"] = label
                # ↑ 把这次成功的标签存进状态。
                self._save_state()
                # ↑ 保存状态（下次优先用）。
                return True
                # ↑ 返回成功。
        return False
        # ↑ 都没点成，返回失败。

    def _focus_input_box(self, img):
        mark("_focus_input_box", "聚焦输入框")  # AUTO-INSTRUMENTED
        """聚焦输入框：优先在窗口下半屏 OCR 找输入提示（确保点的是底部『问答输入框』，
        而非文档列表页顶部的搜索框），否则用（自校准/默认）比例兜底点击底部输入区。
        """
        # ↑ docstring 解释：_focus_input_box 聚焦输入框，关键要点在"底部"的问答框，
        #   而不是顶部的文档搜索框；找不到就用上次成功/默认比例兜底。
        iw, ih = getattr(self, "_last_img_size", (1, 1))
        # ↑ 取图片尺寸。
        left, top, lw, lh = self._last_window_rect
        # ↑ 取窗口矩形。
        best = None
        # ↑ 占个位：最佳输入框坐标。
        for hint in self.input_hints:
            # ↑ 遍历输入提示候选……
            for text, rx, ry in ocr_image(img):
                # ↑ 遍历图片里认到的文字……
                t = (text or "").strip()
                # ↑ 当前文字去空格。
                if t and (t == hint or hint in t) and ih and (ry / ih) > 0.5:
                    # ↑ 如果匹配提示、且位于下半屏（>0.5）……
                    best = (rx, ry)
                    # ↑ 记下它的图内坐标。
                    break
                    # ↑ 找到一个就停。
            if best:
                # ↑ 如果找到了……
                break
                # ↑ 跳出外层循环。
        if best:
            # ↑ 如果找到输入框提示……
            rx, ry = best
            # ↑ 取出图内坐标。
            sx = left + int(rx * (lw / iw)) if iw else left + rx
            # ↑ 换算成屏幕 x。
            sy = top + int(ry * (lh / ih)) if ih else top + ry
            # ↑ 换算成屏幕 y。
            logging.info("  点击底部输入框提示 @ (%d, %d)", sx, sy)
            # ↑ 记日志。
            human_click(sx, sy)
            # ↑ 拟人点击。
            return True
            # ↑ 返回成功。
        # 用最近一次成功的输入坐标
        # ↑ 注释：没找到提示，就用上次成功的输入坐标兜底。
        saved = self.state.get("ima", {}).get("input_ratio")
        # ↑ 从状态取上次成功的输入坐标。
        if saved:
            # ↑ 如果有记录……
            self._click_ratio(saved[0], saved[1])
            # ↑ 按比例点击那个位置。
            return True
            # ↑ 返回成功。
        self._click_ratio(self.input_center_x, self.input_center_y)
        # ↑ 否则用默认的输入中心比例点击。
        return False
        # ↑ 返回结果。

    def _verify_question_sent(self, question):
        mark("_verify_question_sent", "校验问题已发送")  # AUTO-INSTRUMENTED
        """OCR 聊天区，确认刚才的问题确实出现在对话里。"""
        # ↑ docstring 解释：_verify_question_sent 校验"刚才的问题确实发到了对话里"。
        needle = question[: max(5, len(question) // 3)]
        # ↑ 取问题的前一小段（至少5字或1/3长度）作为"指纹"去比对。
        text = self._ocr_region_text(self.answer_left, self.top_bar_ratio,
                                     self.answer_right, self.answer_bottom)
        # ↑ 读取回答区（聊天区）的文字。
        tn = self._norm(question)
        # ↑ 把整个问题规范化。
        if tn and (self._norm(needle) in self._norm(text) or tn[:6] in self._norm(text)):
            # ↑ 如果问题指纹或前6字出现在聊天区文字里……
            return True
            # ↑ 说明问题已发送，校验通过。
        return False
        # ↑ 没找到，返回 False。

    def _wait_real_answer(self, pre_text, question):
        mark("_wait_real_answer", "轮询等待回答")  # AUTO-INSTRUMENTED
        """等待回答生成完毕。判定策略：区域 OCR 文本已达「足够长」且连续两次
        稳定（或已达最小等待时间），即认为回答完成。不再强求比旧内容增长
        固定字数，避免旧答案被滚动移出区域 / 同问题重问长度相近导致的误判超时。
        """
        # ↑ docstring 解释：_wait_real_answer 轮询等待 IMA 把回答生成完。
        #   判定：文字够长(≥60)且连续两次稳定（或已达最小等待），就认为完成。
        start = time.time()
        # ↑ 记下开始时间。
        last = pre_text
        # ↑ 上一次读到的文字（用于判断"稳定"）。
        stable = 0
        # ↑ 稳定计数（连续几次没大变化）。
        while time.time() - start < self.max_answer_wait:
            # ↑ 在最大等待时间内循环……
            time.sleep(2.5)
            # ↑ 每 2.5 秒查一次。
            elapsed = time.time() - start
            # ↑ 已过去多少秒。
            try:
                # ↑ try 保护：OCR 读取可能失败。
                post = self._ocr_region_text(self.answer_left, self.top_bar_ratio,
                                             self.answer_right, self.answer_bottom)
                # ↑ 读取当前回答区文字。
            except Exception as e:
                # ↑ 读取失败……
                logging.debug("[IMA] 等待期间 OCR 读取失败，继续等待: %s", e)
                # ↑ 记调试日志。
                stable = 0
                # ↑ 稳定计数清零。
                continue
                # ↑ 继续下一轮。
            if len(post) >= 60:
                # ↑ 如果当前文字够长（≥60 字，说明有实质内容）……
                if abs(len(post) - len(last)) <= 15:
                    # ↑ 且和上次长度差 ≤15（基本不变，说明生成完了）……
                    stable += 1
                    # ↑ 稳定计数 +1。
                else:
                    # ↑ 否则（还在变化）……
                    stable = 0
                    # ↑ 稳定计数清零。
                last = post
                # ↑ 更新"上次文字"。
                if stable >= 2:
                    # ↑ 如果连续 2 次稳定……
                    logging.info("[IMA] 回答内容已稳定（%.1fs）", elapsed)
                    # ↑ 记日志。
                    if self._is_stale_answer(post, question):
                        # ↑ 检查是不是"陈旧旧答案"（和上轮问题不同但答案一样）……
                        logging.warning("[IMA] 检测为陈旧答案（与上轮不同问题相同），将重试。")
                        # ↑ 记警告。
                        return None
                        # ↑ 返回 None（让上层重试）。
                    return post
                    # ↑ 返回当前回答文字。
            else:
                # ↑ 如果文字还不够长……
                last = post
                # ↑ 更新上次文字。
                stable = 0
                # ↑ 稳定计数清零。
            if elapsed >= self.answer_wait and len(post) >= 60:
                # ↑ 如果已达最小等待、且文字够长（不必等稳定）……
                logging.info("[IMA] 已达最小等待 %.0fs，停止等待。", self.answer_wait)
                # ↑ 记日志。
                if self._is_stale_answer(post, question):
                    # ↑ 检查陈旧……
                    return None
                    # ↑ 陈旧就返回 None。
                return post
                # ↑ 返回回答文字。
        logging.warning("[IMA] 等待回答超时。")
        # ↑ 超过最大等待仍没完成，记警告。
        return None
        # ↑ 返回 None（超时）。

    def _is_stale_answer(self, post, question):
        mark("_is_stale_answer", "过滤旧回答")  # AUTO-INSTRUMENTED
        # ↑ 定义 _is_stale_answer（过滤陈旧答案）：判断这次答案是否和上轮完全相同却问的不同。
        ima_state = self.state.setdefault("ima", {})
        # ↑ 取出状态里的 ima 块（没有就建）。
        prev = ima_state.get("last_answer_text", "")
        # ↑ 取上次存的答案文字。
        prev_q = ima_state.get("last_answer_question", "")
        # ↑ 取上次对应的问题。
        if not prev:
            # ↑ 如果没有上次答案（首次）……
            return False
            # ↑ 不算陈旧。
        pn = self._norm(prev)
        # ↑ 上次答案规范化。
        qn = self._norm(question)
        # ↑ 本次问题规范化。
        cur = self._norm(post)
        # ↑ 本次答案规范化。
        # 仅当「回答与上轮一字不差」且「本次提问与上次不同」时才判定为陈旧，
        # 避免同一问题重问时被误判陈旧而无限重试。
        # ↑ 注释：只有"答案完全一样 且 问题不同"才判定陈旧，避免同问题重问误判。
        if pn and cur and pn == cur and qn != prev_q:
            # ↑ 如果答案一字不差、但问题不同……
            return True
            # ↑ 判定为陈旧答案。
        return False
        # ↑ 否则不算陈旧。

    # ------------------------------------------------------------------
    # 核心：提问并取完整回答截图
    # ↑ 注释分隔：下面这组方法是"核心流程"——提问并取回答截图。
    def ask(self, question, out_path=None):
        mark("ask", "IMA 问答入口")  # AUTO-INSTRUMENTED
        # ↑ 定义 ask（问答入口）：对外主接口，问一个问题、返回答案截图路径。
        _warn_if_not_admin()
        # ↑ 先检查是否管理员（非管理员可能卡死，给个提示）。
        out_path = out_path or self.cfg.get("default_out_path", "cards/ima_answer.png")
        # ↑ 确定答案截图存哪（没传就用配置默认路径）。
        out_path = os.path.abspath(out_path)
        # ↑ 转成绝对路径。
        logging.info("[IMA] 准备提问（知识库=%s）：%s", self.kb_name, question[:80])
        # ↑ 记日志：准备问什么（只显示前 80 字）。
        self._last_question = question
        # ↑ 记下这次的问题。

        last_err = None
        # ↑ 占个位：记录最后一次错误。
        try:
            # ↑ try 保护整个 ask 流程。
            # 硬性超时守护：整个 ask 流程（含窗口就绪 + 知识库导航 + 等待回答 +
            # 截图拼接）一旦超过 ask_timeout 秒，就注入 IMATimeout 强制中断，
            # 绝不再永久卡死（方法11/18 + 网上共识）。
            # ↑ 注释：下面用 hard_timeout 包住整个流程——超过秒数就强制中断，杜绝卡死。
            with hard_timeout(self.ask_timeout, "ask('%s')" % question[:30]):
                # ↑ 进入"超时保险丝"：超过 ask_timeout 秒就打断。
                if not self.ensure_window_and_kb():
                    # ↑ 先确保窗口找到、且进入正确知识库；失败……
                    return None
                    # ↑ 返回 None。
                for attempt in range(1, self.retry_attempts + 1):
                    # ↑ 最多重试 retry_attempts 次……
                    try:
                        # ↑ 内层 try：单次尝试可能失败。
                        return self._ask_once(question, out_path)
                        # ↑ 跑一次完整提问流程，成功就直接返回结果。
                    except IMATimeout:
                        # ↑ 如果遇到超时异常（说明卡死了）……
                        raise  # 超时异常不重试，直接上抛到外层
                        # ↑ 超时不能重试，直接往外层抛。
                    except Exception as e:
                        # ↑ 其它错误（普通失败）……
                        last_err = e
                        # ↑ 记下错误。
                        logging.warning("[IMA] 第 %d 次尝试失败: %s", attempt, e)
                        # ↑ 记警告。
                        self._dbg("attempt_%d_fail" % attempt)
                        # ↑ 存一张失败调试图。
                        time.sleep(1.0)
                        # ↑ 暂停 1 秒再试。
        except IMATimeout:
            # ↑ 超时异常冒泡到这里……
            logging.error("[IMA] ask 超过硬性时限 %.0fs，已中断（避免永久卡死）。"
                          "请确认 IMA 处于正确的知识库问答视图后重试。", self.ask_timeout)
            # ↑ 记错误日志，提示检查 IMA 视图。
            self._manifest_flush()
            # ↑ 把调试清单写盘（方便排查）。
            return None
            # ↑ 返回 None。
        logging.error("[IMA] 多次尝试均失败: %s", last_err)
        # ↑ 重试多次仍失败，记错误日志。
        self._manifest_flush()
        # ↑ 写调试清单。
        return None
        # ↑ 返回 None。

    def _ask_once(self, question, out_path):
        mark("_ask_once", "单次提问流程")  # AUTO-INSTRUMENTED
        # ↑ 定义 _ask_once（单次提问）：完整跑一遍"进库→聚焦→输入→发送→等回答→截图"。
        # 1) 确保在『目标知识库』的问答视图（不是全局问问ima）
        #    注意：标题已含知识库名时说明已在正确视图，绝不要再点「知识库」菜单
        #    （会误入文档列表页），只有标题不含知识库名时才导航进入。
        # ↑ 注释：步骤1：确保进对知识库视图（标题已含库名就别再点多事的菜单）。
        img, _ = self.capture()
        # ↑ 截一张图。
        if not self._last_window_rect:
            # ↑ 如果截完图窗口矩形还是空（说明 IMA 异常）……
            # 窗口区域为空 = IMA 处于异常状态（之前永久卡死的根因之一）。
            # 明确抛出，交给外层超时/重试，而不是带着 None 继续走成黑盒。
            # ↑ 注释：窗口区域为空说明 IMA 没就绪，明确抛错交给重试，不黑盒继续。
            raise RuntimeError("capture 后窗口区域仍为空（_last_window_rect=None），"
                               "IMA 可能未就绪或处于异常状态")
        if not self._title_has_kb():
            # ↑ 如果标题不含目标知识库名（还没进对库）……
            logging.info("[IMA] 标题不含目标知识库，尝试进入...")
            # ↑ 记日志。
            self.ensure_kb()
            # ↑ 导航进入目标知识库。
            img, _ = self.capture()
            # ↑ 重新截图。
            if not self._in_kb_chat_view(img):
                # ↑ 如果还是没进对视图……
                self._dbg("not_in_kb_chat_view")
                # ↑ 存调试图。
                raise RuntimeError("无法进入目标知识库「%s」问答视图" % self.kb_name)
                # ↑ 抛错：进不去。
        self._dbg("in_kb_chat_view")
        # ↑ 存一张"已进入视图"的调试图。

        # 2) 聚焦输入框并清空
        # ↑ 注释：步骤2：聚焦输入框并清空旧内容。
        self._focus_input_box(img)
        # ↑ 聚焦输入框。
        time.sleep(0.5)
        # ↑ 暂停 0.5 秒。
        pyautogui.hotkey("ctrl", "a")
        # ↑ Ctrl+A 全选输入框内容。
        pyautogui.press("delete")
        # ↑ 删除键清空。
        time.sleep(0.2)
        # ↑ 暂停 0.2 秒。
        self._dbg("input_focused")
        # ↑ 存"已聚焦"调试图。

        # 3) 输入问题并发送（中文走剪贴板 Ctrl+V，回车发送）
        # ↑ 注释：步骤3：输入问题并发送（中文用剪贴板粘贴，绕开输入法）。
        human_type(question)
        # ↑ 拟人化逐字输入问题（实际 human_type 内部走剪贴板粘贴更稳）。
        time.sleep(0.6)
        # ↑ 暂停 0.6 秒。
        human_send_enter()
        # ↑ 拟人回车发送。
        logging.info("[IMA] 已发送问题，等待回答...")
        # ↑ 记日志。
        time.sleep(self.post_send_wait)
        # ↑ 暂停（等 IMA 开始处理）。

        # 4) 自检：问题确实出现在对话里
        # ↑ 注释：步骤4：校验问题真的发到了对话里。
        if not self._verify_question_sent(question):
            # ↑ 如果校验没通过（可能没点对输入框）……
            # 可能输入框没对上，记录并重试
            # ↑ 注释：下面记录调试图并重试。
            self._dbg("question_not_detected")
            # ↑ 存调试图。
            raise RuntimeError("问题未被 IMA 接收（输入框可能未命中）")
            # ↑ 抛错：让上层重试。
        self._dbg("question_sent")
        # ↑ 存"已发送"调试图。

        # 5) 记录发送前/后的聊天文本，等待真实新回答
        # pre 已是发送后带问题的文本；直接等更长的新内容
        # ↑ 注释：步骤5：等 IMA 生成新回答（不是问句本身，而是它的回答）。
        pre = self._ocr_region_text(self.answer_left, self.top_bar_ratio,
                                    self.answer_right, self.answer_bottom)
        # ↑ 读取当前聊天区文字（含刚发的问题）。
        answer_text = self._wait_real_answer(pre, question)
        # ↑ 等待并取出"真正的回答文字"。
        if answer_text is None:
            # ↑ 如果没等到有效回答（超时或陈旧）……
            raise RuntimeError("未生成新的有效回答（可能为陈旧答案或超时）")
            # ↑ 抛错：让上层重试。

        # 记忆答案指纹，防下次陈旧（同时记录对应问题）
        # ↑ 注释：把这次的答案和问题记进状态，供下次判断"是不是陈旧旧答案"。
        ima_state = self.state.setdefault("ima", {})
        # ↑ 取出状态里的 ima 块。
        ima_state["last_answer_text"] = answer_text
        # ↑ 存这次答案文字。
        ima_state["last_answer_question"] = question
        # ↑ 存这次对应的问题。
        self._save_state()
        # ↑ 保存状态。

        # 截完整回答（滚动拼接）
        # ↑ 注释：把回答区截图（可能很长，需要滚动拼接）。
        full = self._capture_full_answer()
        # ↑ 调用滚动拼接截图，得到完整回答图。
        if full is None:
            # ↑ 如果拼接失败（返回 None）……
            region = (self.answer_left, self.answer_top, self.answer_right, self.answer_bottom)
            # ↑ 退而求其次：直接截回答区这一屏。
            full, _ = self.capture(region=region)
            # ↑ 截这一屏作为答案图。
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        # ↑ 创建答案图所在目录。
        full.save(out_path)
        # ↑ 保存答案截图。
        logging.info("[IMA] 答案已保存：%s", out_path)
        # ↑ 记日志。
        self._dbg("answer_saved")
        # ↑ 存"已保存"调试图。
        self._manifest_flush()
        # ↑ 写调试清单。
        return out_path
        # ↑ 返回答案图片路径。

    # ------------------------------------------------------------------
    # 完整回答截图（滚动拼接）
    # ↑ 注释分隔：下面这组方法负责"把很长的回答滚动截成一整张长图"。
    def _capture_window_pixels(self, cx0, cy0, cx1, cy1):
        # ↑ 定义 _capture_window_pixels（截窗口像素块）：按像素坐标裁剪一小块图。
        img, _l, _t, w, h = self._grab()
        # ↑ 截整窗图（_l/_t/_ 是忽略的返回值）。
        return img.crop((cx0, cy0, cx1, cy1))
        # ↑ 裁剪出指定像素区域的小图并返回。

    def _capture_full_answer(self):
        mark("_capture_full_answer", "截取回答区域")  # AUTO-INSTRUMENTED
        # ↑ 定义 _capture_full_answer（截完整回答）：滚动截取并拼接成一整张长回答图。
        if not self._last_window_rect:
            # ↑ 如果没有窗口矩形（没截过图）……
            return None
            # ↑ 返回 None。
        self._restore()
        # ↑ 还原+置前窗口。
        left, top, w, h = self._last_window_rect
        # ↑ 取窗口矩形（左,上,宽,高）。
        cx0 = int(w * self.answer_left)
        # ↑ 回答区左边 x（像素）。
        cx1 = int(w * self.answer_right)
        # ↑ 回答区右边 x（像素）。
        cy0 = int(h * self.answer_top)
        # ↑ 回答区顶部 y（像素）。
        cy1 = int(h * (1.0 - self.input_ratio))
        # ↑ 回答区底部 y（贴近输入框上缘）。
        sx = left + (cx0 + cx1) // 2
        # ↑ 回答区中心 x（滚动要在这个位置滚）。
        sy = top + (cy0 + cy1) // 2
        # ↑ 回答区中心 y。
        # 关键：IMA 回答区是嵌在窗口里的 webview，必须先点击获得焦点，
        # 否则滚轮/键盘事件全被吞，永远只截到同一屏（之前回答被截断的根因）。
        # ↑ 注释：回答区是个内嵌网页，必须先点一下获得焦点，否则滚轮无效、永远截同一屏。
        try:
            # ↑ try 保护：点击可能失败。
            human_click(sx, sy)
            # ↑ 拟人点击回答区中心（获取焦点）。
            time.sleep(0.4)
            # ↑ 暂停 0.4 秒。
        except Exception:
            # ↑ 失败忽略。
            pass

        def grab():
            # ↑ 定义内部函数 grab（抓一帧）：截取当前回答区这一屏。
            return self._capture_window_pixels(cx0, cy0, cx1, cy1)
            # ↑ 返回裁剪好的回答区小图。

        # 1) 向上猛滚足够多次，确保回到回答区最顶部。
        #    注意：IMA 回答区顶部有固定栏（不随滚动移动），不能用「顶部签名」
        #    判断进度，否则签名恒定会导致回顶/到底判定全部失效。这里用充足
        #    次数直接滚到顶（scroll(30) ≈ 每步 1470px，50 次远超任何答案长度）。
        # ↑ 注释：先向上猛滚 50 次，确保回到回答最顶部（用次数保证到位，不用"顶部签名"判断）。
        for _ in range(50):
            # ↑ 循环 50 次……
            pyautogui.scroll(30, x=sx, y=sy)
            # ↑ 向上猛滚（每步约 1470px）。
            time.sleep(0.05)
            # ↑ 暂停 0.05 秒。
        time.sleep(0.3)
        # ↑ 滚完停 0.3 秒。

        # 2) 从顶部开始向下小步滚动，逐帧拼接。
        #    关键改进（方案 A）：不再依赖「growing result 底部 vs 新帧」的像素重叠
        #    匹配（该方法在滚动量不稳定时会返回 0，导致整屏堆叠成 9 万 px 重复图），
        #    而是对【相邻两帧原始截图】用整图灰度相关算出本步真实位移 d，只把新帧
        #    底部 d 行追加进去。这样无论滚动量是否稳定，都只追加真正的新内容，
        #    不会重复、不会跳段；滚到底时 d→0 自然停止。
        # ↑ 注释：向下小步滚、逐帧拼接。用"灰度相关"算真实位移 d，只追加新内容，
        #   不重复不跳段；到底时位移趋零自然停止。
        prev = grab()
        # ↑ 先抓第一帧（顶部）。
        frames = [prev]
        # ↑ 帧列表，初始放第一帧。
        no_advance = 0
        # ↑ "无进展"计数（判断是否到底）。
        last_bottom_sig = None
        # ↑ 上一帧底部内容签名（用于"内容不变=到底"判断）。
        stable = 0
        # ↑ 底部稳定计数。
        for _ in range(200):
            # ↑ 最多向下滚 200 次（足够覆盖任何长答案）……
            pyautogui.scroll(-8, x=sx, y=sy)
            # ↑ 向下滚一小步（负数=向下）。
            time.sleep(0.25)
            # ↑ 暂停 0.25 秒等滚动稳定。
            cur = grab()
            # ↑ 抓当前这一帧。
            d = IMAController._vshift(prev, cur)
            # ↑ 用灰度相关算法算出"这一帧相对上一帧向上移动了多少像素 d"。
            if d <= 6:
                # ↑ 如果位移很小（≤6 像素，说明基本没动）……
                no_advance += 1
                # ↑ 无进展计数 +1。
                if no_advance >= 2:  # 连续两次无进展 = 已到底
                    # ↑ 连续 2 次没动，判定已滚到底……
                    break
                    # ↑ 停止滚动。
                prev = cur
                # ↑ 更新上一帧。
                continue
                # ↑ 继续下一轮。
            no_advance = 0
            # ↑ 有进展，无进展计数清零。
            # cur 是 prev 上移 d 行后的画面，只追加 cur 底部 d 行新内容
            # ↑ 注释：cur 比 prev 上移了 d 行，所以只把 cur 底部 d 行的新内容接上去。
            ww = cur.width
            # ↑ 当前帧宽度。
            frames.append(cur.crop((0, cur.height - d, ww, cur.height)))
            # ↑ 把 cur 底部 d 行裁下来，作为新内容追加进帧列表。
            # 底部内容签名连续不变也视为到底（防止微小位移死循环）
            # ↑ 注释：如果底部内容签名连续不变，也判定到底（防微小位移导致死循环）。
            sig = hash(cur.crop((0, cur.height - 40, ww, cur.height)).tobytes())
            # ↑ 取当前帧底部 40 像素的内容签名（哈希）。
            if sig == last_bottom_sig:
                # ↑ 如果和上一帧底部签名一样（内容没变）……
                stable += 1
                # ↑ 稳定计数 +1。
                if stable >= 3:
                    # ↑ 连续 3 次不变……
                    break
                    # ↑ 判定到底，停止。
            else:
                # ↑ 内容有变化……
                stable = 0
                # ↑ 稳定计数清零。
                last_bottom_sig = sig
                # ↑ 更新底部签名。
            prev = cur
            # ↑ 更新上一帧。
        # 纵向拼合所有帧
        # ↑ 注释：把所有帧上下拼成一张长图。
        W = frames[0].width
        # ↑ 拼图宽度（取第一帧宽）。
        H = sum(f.height for f in frames)
        # ↑ 拼图总高度 = 所有帧高度之和。
        out = Image.new("RGB", (W, H))
        # ↑ 创建一张空白长图（宽 W、高 H）。
        y = 0
        # ↑ 当前粘贴的 y 坐标，从 0 开始。
        for f in frames:
            # ↑ 遍历每一帧……
            out.paste(f, (0, y))
            # ↑ 把这一帧贴到长图的当前 y 位置。
            y += f.height
            # ↑ y 下移这一帧的高度。
        logging.info("[IMA] 拼接完成：%d 帧，高 %d px", len(frames), H)
        # ↑ 记日志：拼了多少帧、多高。
        return out
        # ↑ 返回拼好的长图。

    @staticmethod
    def _top_signature(img):
        # ↑ 定义 _top_signature（顶部签名）：取图片顶部一小块的内容哈希，用于判断"是否同一屏"。
        w, h = img.size
        # ↑ 取图片宽高。
        crop = img.crop((0, 0, w, min(24, h)))
        # ↑ 裁剪顶部最多 24 像素高的小条。
        return hash(crop.resize((40, 12), Image.NEAREST).tobytes())
        # ↑ 缩成 40x12 小图，转字节后取哈希作为"签名"。

    @staticmethod
    def _vshift(A, B):
        """求 B 相对 A 向下滚动的像素位移 d（B = A 上移 d 行，底部 d 行为新内容）。

        用整图灰度相关求【精确】最小差位移（逐行、列子采样、step=1），
        对 IMA 纯文本块稳定，且不像量化搜索那样因取整产生重复/丢行。
        返回 d ∈ [0, A.height)；若两帧几乎相同（已到底）返回 ~0。
        """
        # ↑ docstring 解释：_vshift 算 B 相对 A "向上移动了多少行 d"（即 B 比 A 多了 d 行新内容在底部）。
        #   用灰度相关逐行比对，求出最精确的位移 d。
        import numpy as np
        # ↑ 导入 numpy（数值计算，用于矩阵差值）。
        ga = np.asarray(A.convert("L"), dtype=np.int32)
        # ↑ 把图 A 转灰度、再转成整数矩阵。
        gb = np.asarray(B.convert("L"), dtype=np.int32)
        # ↑ 把图 B 同样转灰度整数矩阵。
        hh = ga.shape[0]
        # ↑ 图的高度（行数）。
        cols = slice(0, ga.shape[1], 40)  # 每 40 列取一列，足够区分文本行
        # ↑ 列采样：每 40 列取一列即可区分文本，省计算。
        best = None
        # ↑ 占个位：最小差值。
        bestd = 0
        # ↑ 占个位：最佳位移 d。
        for d in range(0, hh):
            # ↑ 遍历所有可能的位移 d（从 0 到高度）……
            if hh - d < 20:
                # ↑ 如果剩余行数不足 20（没法比了）……
                break
                # ↑ 停止。
            sub_a = ga[d:hh, cols]
            # ↑ 取 A 从 d 行到末尾的子矩阵（对应"上移 d 行后的 A"）。
            sub_b = gb[0:hh - d, cols]
            # ↑ 取 B 从开头到 (hh-d) 行的子矩阵。
            if sub_a.shape[0] < 20:
                # ↑ 如果子矩阵行数不足 20……
                break
                # ↑ 停止。
            diff = np.abs(sub_a - sub_b).mean()
            # ↑ 算两个子矩阵对应元素差的绝对值的平均（平均差异度）。
            if best is None or diff < best:
                # ↑ 如果这是目前最小的差异……
                best = diff
                # ↑ 记下最小差异值。
                bestd = d
                # ↑ 记下对应的位移 d。
        return bestd
        # ↑ 返回最佳位移 d（B 相对 A 上移的行数）。

    @staticmethod
    def _find_overlap(upper, lower, max_off=None):
        # ↑ 定义 _find_overlap（找重叠）：找上下两张图重叠的行数 k（旧拼接法备用）。
        uw, uh = upper.size
        # ↑ 上图宽高。
        lw, lh = lower.size
        # ↑ 下图宽高。
        if uw != lw:
            # ↑ 如果两张图宽度不同（需要缩放对齐）……
            lower = lower.resize((uw, int(lh * uw / lw)))
            # ↑ 把下图缩放到和上图同宽。
            lh = lower.size[1]
            # ↑ 更新下图高度。
        # 全范围搜索重叠（max_off 默认取到较短边），避免重叠被 120px 上限截断而匹配失败
        # ↑ 注释：全范围搜索重叠，避免被固定上限截断导致匹配失败。
        max_off = max_off or min(uh, lh)
        # ↑ 最大偏移 = 两张图较短的边。
        up = upper.load()
        # ↑ 取上图像素访问器。
        lp = lower.load()
        # ↑ 取下图像素访问器。

        def rows_match(k):
            # ↑ 内部函数：判断"上图底部 k 行"和"下图顶部 k 行"是否逐像素匹配。
            for dy in range(k):
                # ↑ 遍历重叠的每一行偏移……
                uy = uh - k + dy
                # ↑ 上图对应的行。
                ly = dy
                # ↑ 下图对应的行。
                for sx in range(0, uw, 20):
                    # ↑ 每隔 20 列采样一个点（省计算）……
                    pu = up[sx, uy]
                    # ↑ 上图该点颜色。
                    pl = lp[sx, ly]
                    # ↑ 下图对应点颜色。
                    if abs(pu[0] - pl[0]) + abs(pu[1] - pl[1]) + abs(pu[2] - pl[2]) > 90:
                        # ↑ 如果 RGB 三通道差值之和超过 90（差别太大）……
                        return False
                        # ↑ 这一行不匹配，返回 False。
            return True
            # ↑ 所有采样点都匹配，返回 True。

        for k in range(min(uh, lh, max_off), 0, -1):
            # ↑ 从最大可能重叠往下试（找最大的 k）……
            if rows_match(k):
                # ↑ 如果重叠 k 行匹配……
                return k
                # ↑ 返回这个重叠行数。
        return 0
        # ↑ 没找到重叠，返回 0。

    @staticmethod
    def _stitch(shots):
        mark("_stitch", "拼接长回答截图")  # AUTO-INSTRUMENTED
        # ↑ 定义 _stitch（拼接）：把多张截图上下拼成一张长图（旧拼接法，备用）。
        result = shots[0]
        # ↑ 从第一张开始作为基准。
        for nxt in shots[1:]:
            # ↑ 遍历后续每张图……
            k = IMAController._find_overlap(result, nxt)
            # ↑ 找当前结果与下一张的重叠行数 k。
            w = result.width
            # ↑ 当前结果宽度。
            cropped = result.crop((0, 0, w, result.height - k))
            # ↑ 把当前结果底部重叠的 k 行裁掉（避免重复）。
            out = Image.new("RGB", (w, cropped.height + nxt.height))
            # ↑ 创建新长图（宽不变，高 = 裁后高 + 下一张高）。
            out.paste(cropped, (0, 0))
            # ↑ 贴上裁后的部分。
            out.paste(nxt, (0, cropped.height))
            # ↑ 贴上下一张（接在后面）。
            result = out
            # ↑ 更新结果，进入下一轮。
        return result
        # ↑ 返回拼好的长图。

    # ------------------------------------------------------------------
    # 校准
    # ↑ 注释分隔：下面这个方法用于"校准坐标"。
    def calibrate(self, out_dir="."):
        # ↑ 定义 calibrate（校准）：截图并标注 IMA 窗口里所有 OCR 认到的文字及坐标。
        if not self.hwnd:
            # ↑ 如果没找到 IMA 窗口……
            logging.error("IMA 窗口未找到")
            # ↑ 记错误日志。
            return
            # ↑ 返回。
        out_dir = out_dir or "."
        # ↑ 输出目录（空就用当前目录）。
        os.makedirs(out_dir, exist_ok=True)
        # ↑ 创建输出目录。
        img, rect = self.capture()
        # ↑ 截一张 IMA 整窗图。
        left, top, w, h = rect
        # ↑ 取窗口矩形。
        draw = ImageDraw.Draw(img)
        # ↑ 拿画笔。
        boxes = ocr_image(img)
        # ↑ 对整窗做 OCR，得到文字和坐标。
        for text, rx, ry in boxes:
            # ↑ 遍历认到的文字……
            draw.rectangle([rx - 2, ry - 10, rx + 120, ry + 10], outline="red", width=2)
            # ↑ 在字周围画红色矩形框。
            draw.text((rx, ry - 20), text, fill="red")
            # ↑ 在字上方写文字（红字）。
        out = os.path.join(out_dir, "ima_calibrate.png")
        # ↑ 拼出校准图路径。
        img.save(out)
        # ↑ 保存校准图。
        logging.info("校准图已保存：%s", out)
        # ↑ 记日志。
        print(f"\n=== IMA 窗口文字 ({len(boxes)} 块) ===")
        # ↑ 打印分隔标题（带认到的块数）。
        for text, rx, ry in boxes:
            # ↑ 遍历认到的文字……
            print(f"  {text!r:50} @ ({rx}, {ry})")
            # ↑ 打印"文字 + 图内坐标"，方便你对照调整 config 比例。
        return out
        # ↑ 返回校准图路径。
