# -*- coding: utf-8 -*-
"""
WeChat -> IMA 知识库 自动监控应答程序
==========================================

目标：
  每间隔一段时间（默认 10 分钟）自动检查微信里指定的群，
  若群内有人提问，则在 IMA 知识库「古源尊者开示及上座部佛教资料」
  中检索，并把找到的答案自动发回该群。

工作原理（不依赖微信官方接口，规避原生限制）：
  1. 微信 4.0（Qt 重写）不向 UI Automation 暴露聊天内容，因此改用
     「截图 + 中文 OCR + 模拟坐标点击」方案驱动已经登录好的微信桌面端：
     uiautomation 仅用于定位/置前微信窗口，读取群名与消息、点击群、
     在输入框粘贴并回车发送，全部走截图 OCR + pyautogui。
  2. 提问识别用启发式（问号 / 疑问词）+ 状态去重（同一句话只答一次）。
  3. 答案来自 IMA OpenAPI 的 search_knowledge 接口（HTTP，纯标准库实现）。

重要前提（务必先看）：
  * 本机必须已安装并登录微信桌面端（Windows）。脚本不存、也不使用你的
    微信密码，它操作的是「已经登录好」的窗口。
  * 微信个人号自动化属于协议灰色地带，有被限制/封号风险，请自行评估。
  * 默认 dry_run=true：只记录「会回答什么」，不真正发送。先验证读群准确，
    再把 dry_run 改为 false。
  * IMA 桌面端若未运行，程序会自动启动并切换到目标知识库；目标微信群会通过搜索自动定位打开，无需手动置顶/保持可见。
  * 仅在 Windows 上运行（依赖 uiautomation）。

用法：
  python wechat_ima_monitor.py                 # 持久循环（按 interval 轮询，默认 10 分钟）
  python wechat_ima_monitor.py --once         # 只跑一轮（配合 Windows 任务计划程序）
  python wechat_ima_monitor.py --live         # 关闭 dry_run，真正发送（请先验证）
  python wechat_ima_monitor.py --auto         # 全自动：先自检（开群+IMA出图）通过则自动循环
  python wechat_ima_monitor.py --list-groups  # 打印当前可见的会话名，方便填 config
  python wechat_ima_monitor.py --ima-calibrate  # 校准 IMA 坐标并截图
  python wechat_ima_monitor.py --ask-ima "什么是四圣谛"  # 单测 IMA 回答截图

全自动说明（--auto）：
  程序启动后自动做一轮自检：① 打开监控群并读到消息；② 让 IMA 桌面端回答一个
  测试问题并验证生成了有效回答截图。自检通过即进入每 interval 秒一轮的监控循环，
  期间发现群内提问就自动用 IMA 回答并回图。任何一步失败都会把 debug/ 截图存盘，
  届时把 debug/ 文件夹发回即可定位修复，无需你守在电脑前逐步描述。
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import logging
import urllib.request
import urllib.error

if sys.platform != "win32":
    print("错误：本程序仅支持 Windows（需要 uiautomation）。", file=sys.stderr)
    sys.exit(2)

import uiautomation as auto

from wechat_ocr import OCRWeChatController
from ima_ocr import IMAController
from flow_runtime import mark  # AUTO-INSTRUMENTED

# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config(path):
    mark("load_config", "读取配置")  # AUTO-INSTRUMENTED
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def expand(p):
    if p.startswith("~"):
        p = os.path.expanduser(p)
    return p


# ----------------------------------------------------------------------------
# 日志
# ----------------------------------------------------------------------------
def setup_logging(cfg):
    mark("setup_logging", "初始化日志")  # AUTO-INSTRUMENTED
    log_dir = os.path.join(SCRIPT_DIR, cfg["logging"].get("dir", "logs"))
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "monitor.log")
    level = getattr(logging, cfg["logging"].get("level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_path


# ----------------------------------------------------------------------------
# 状态（去重，避免同一条消息反复作答 / 自己发的消息被当成问题）
# ----------------------------------------------------------------------------
STATE_PATH = os.path.join(SCRIPT_DIR, "state.json")


def load_state():
    mark("load_state", "读取去重状态")  # AUTO-INSTRUMENTED
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"answered": {}, "sent": []}


def save_state(state):
    mark("save_state", "保存去重状态")  # AUTO-INSTRUMENTED
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def sig(text):
    return hashlib.md5(text.strip().encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------------------
# IMA 知识库检索
# ----------------------------------------------------------------------------
class IMAClient:
    def __init__(self, cfg):
        self.cfg = cfg["ima"]
        self.kb_id = self.cfg["kb_id"]
        self.timeout = int(self.cfg.get("request_timeout", 30))

    def _read_creds(self):
        cid = self._read_file(self.cfg.get("client_id_path"))
        key = self._read_file(self.cfg.get("api_key_path"))
        return cid, key

    @staticmethod
    def _read_file(p):
        if not p:
            return ""
        p = expand(p)
        try:
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""

    def search(self, query):
        """返回拼接好的答案文本（空字符串表示没找到）。"""
        data = self._fetch(query)
        return self._format(data) if data else ""

    def search_passages(self, query, top_k=None):
        """返回结构化结果列表：[{'title':..., 'content':...}]，空列表表示没找到。"""
        data = self._fetch(query)
        if not data:
            return []
        return self._to_passages(data, top_k or int(self.cfg.get("top_k", 3)))

    def _fetch_python(self, query):
        cid, key = self._read_creds()
        if not cid or not key:
            logging.error("未找到 IMA 凭证（client_id / api_key），请检查 config 中的路径。")
            return None
        url = "https://ima.qq.com/openapi/wiki/v1/search_knowledge"
        body = json.dumps(
            {"query": query, "knowledge_base_id": self.kb_id, "cursor": ""}
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "ima-openapi-clientid": cid,
                "ima-openapi-apikey": key,
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            logging.error("IMA 检索 HTTP 错误 %s: %s", e.code, e.reason)
            return None
        except Exception as e:
            logging.error("IMA 检索失败: %s", e)
            return None

        if data.get("code", -1) != 0:
            logging.error("IMA 业务错误: %s", data.get("msg", "未知"))
            return None
        return data.get("data", {})

    def _fetch_node(self, query):
        import subprocess

        node_script = expand(self.cfg.get("node_script", ""))
        if not os.path.exists(node_script):
            logging.error("未找到 node 检索脚本: %s", node_script)
            return None
        cid, key = self._read_creds()
        if not cid or not key:
            logging.error("未找到 IMA 凭证。")
            return None
        body = json.dumps(
            {"query": query, "knowledge_base_id": self.kb_id, "cursor": ""}
        )
        opts = json.dumps({"clientId": cid, "apiKey": key})
        try:
            out = subprocess.run(
                ["node", node_script, "openapi/wiki/v1/search_knowledge", body, opts],
                capture_output=True,
                text=True,
                timeout=self.timeout + 10,
            )
        except Exception as e:
            logging.error("调用 node 检索失败: %s", e)
            return None
        if out.returncode != 0:
            logging.error("node 检索返回错误: %s", out.stderr.strip())
            return None
        try:
            data = json.loads(out.stdout)
        except Exception:
            logging.error("node 检索输出非 JSON")
            return None
        if data.get("code", -1) != 0:
            return None
        return data.get("data", {})

    def _fetch(self, query):
        method = self.cfg.get("method", "python")
        if method == "node":
            return self._fetch_node(query)
        return self._fetch_python(query)

    @staticmethod
    def _to_passages(data, top_k):
        items = data.get("info_list", []) or []
        if not items:
            # 某些版本返回结构不同，兜底取任意含文本的字段
            items = data.get("list", []) or []
        parts = []
        for it in items[:top_k]:
            title = (it.get("title") or "").strip()
            text = (
                it.get("content")
                or it.get("abstract")
                or it.get("content_preview")
                or ""
            )
            text = (text or "").strip()
            if not text and not title:
                continue
            parts.append({"title": title, "content": text})
        return parts

    def _format(self, data):
        if not data:
            return ""
        return "\n\n".join(
            (("【%s】\n" % p["title"] if p["title"] else "") + p["content"]).strip()
            for p in self._to_passages(data, int(self.cfg.get("top_k", 3)))
        )


# ----------------------------------------------------------------------------
# 微信操控
# ----------------------------------------------------------------------------
CHROME_DENY = {
    "微信", "通讯录", "收藏", "聊天信息", "发送(S)", "更多", "表情", "发送",
    "文件", "截图", "聊天", "微信", "添加", "设置", "最小化", "关闭",
    "语音聊天", "视频聊天", "语音通话", "视频通话",
}


class WeChatController:
    def __init__(self, cfg):
        self.cfg = cfg["wechat"]
        self.window = None

    def find_window(self):
        title = self.cfg.get("window_title", "微信")
        cls = self.cfg.get("class_name", "WeChatMainWndForPC")
        w = auto.WindowControl(Name=title, ClassName=cls)
        if not w.Exists(3):
            # 退而求其次：只按标题
            w = auto.WindowControl(Name=title)
        if not w.Exists(3):
            logging.error("找不到微信窗口，请确认微信桌面端已启动并登录。")
            return None
        self.window = w
        return w

    def list_visible_groups(self):
        """返回当前会话列表里可见的会话名（用于 --list-groups）。"""
        names = []
        try:
            for item in self._session_items():
                nm = item.Name
                if nm:
                    # 会话项 Name 形如 "群名\n最后一条消息\n时间"
                    names.append(nm.split("\n")[0].strip())
        except Exception as e:
            logging.error("列举会话失败: %s", e)
        return names

    def _session_items(self):
        items = []
        try:
            lst = self.window.ListControl()
            if lst.Exists(2):
                items = lst.GetChildren()
        except Exception:
            items = []
        if not items:
            # 兜底：抓所有 ListItemControl
            try:
                items = self.window.ListItemControl().GetChildren()
            except Exception:
                items = []
        return items

    def open_group(self, name):
        for item in self._session_items():
            nm = (item.Name or "").split("\n")[0].strip()
            if nm == name or nm.startswith(name):
                try:
                    item.Click()
                    time.sleep(1.2)
                    return True
                except Exception as e:
                    logging.error("点击群 %s 失败: %s", name, e)
                    return False
        logging.warning("会话列表里没找到群「%s」（建议置顶/可见）。", name)
        return False

    def read_messages(self):
        """返回消息文本列表（从新到旧或旧到新均可，调用方取末尾）。"""
        texts = []
        try:
            for region in self.window.GetChildren():
                for ctrl in region.GetChildren(recursionDepth=10):
                    try:
                        if ctrl.ControlType == auto.ControlType.TextControl:
                            nm = (ctrl.Name or "").strip()
                            if nm and nm not in CHROME_DENY and len(nm) >= 1:
                                texts.append(nm)
                    except Exception:
                        continue
        except Exception as e:
            logging.error("读取消息失败: %s", e)

        # OCR 兜底
        if not texts and self.cfg.get("_ocr_enabled"):
            texts = self._read_via_ocr()
        return texts

    def _read_via_ocr(self):
        mark("_read_via_ocr", "OCR 截取消息区")  # AUTO-INSTRUMENTED
        try:
            from ocr_helper import ocr_window  # 可选模块
            return ocr_window(self.window)
        except Exception as e:
            logging.error("OCR 读取失败: %s", e)
            return []

    def send_text(self, text):
        # 找到输入框（EditControl），粘贴并回车
        edit = None
        for cand in (self.window.EditControl(), self.window.TextControl(AutomationId="input")):
            try:
                if cand.Exists(2):
                    edit = cand
                    break
            except Exception:
                continue
        if edit is None:
            logging.error("找不到微信输入框，无法发送。")
            return False
        try:
            edit.SetFocus()
            time.sleep(0.3)
            auto.SetClipboardText(text)
            auto.SendKeys("{Ctrl}v")
            time.sleep(0.4)
            auto.SendKeys("{Enter}")
            return True
        except Exception as e:
            logging.error("发送失败: %s", e)
            return False

    def calibrate(self):
        """打印微信窗口控件树，便于排查。"""
        try:
            import sys

            def walk(ctrl, depth=0, maxd=6):
                if depth > maxd:
                    return
                try:
                    name = (ctrl.Name or "")
                    ct = str(ctrl.ControlType)
                    cls = ctrl.ClassName or ""
                    aid = ctrl.AutomationId or ""
                except Exception:
                    return
                if name or cls or depth <= 2:
                    indent = "  " * depth
                    label = name if len(name) <= 40 else name[:40] + "..."
                    print(f"{indent}[{ct}] name={label!r} cls={cls!r} id={aid!r}")
                try:
                    for c in ctrl.GetChildren():
                        walk(c, depth + 1, maxd)
                except Exception:
                    pass

            print("================ 微信窗口控件树 ================")
            walk(self.window)
            print("================================================")
            sys.stdout.flush()
        except Exception as e:
            logging.error("打印控件树失败: %s", e)


# ----------------------------------------------------------------------------
# 提问识别
# ----------------------------------------------------------------------------
def is_question(text, det):
    t = (text or "").strip()
    if len(t) < int(det.get("min_len", 4)):
        return False
    for m in det.get("question_marks", []):
        if t.endswith(m):
            return True
    for kw in det.get("question_keywords", []):
        if kw in t:
            return True
    return False


# 搜索查询清洗：去掉口语化/功能词，保留更容易命中知识库的核心关键词
_SEARCH_STOPWORDS = sorted({
    # 代词
    "我", "你", "他", "她", "它", "我们", "你们", "他们", "她们", "它们",
    "大家", "各位", "别人",
    # 疑问/语气助词
    "吗", "呢", "吧", "啊", "哦", "嗯", "呀", "哈", "哇", "呐", "嘛", "么",
    "怎么", "如何", "为什么", "什么", "是否", "哪里", "哪儿", "谁", "几",
    "多少", "是不是", "能不能", "可不可以", "可以", "能否", "请问", "请",
    "告诉", "回答", "讲", "说", "问", "一下", "给", "看", "知道", "想", "要",
    "能", "会", "应该", "能够", "可以", "在", "有", "是", "的", "了", "地",
    "得", "着", "过", "也", "都", "就", "和", "与", "及", "对", "为", "被",
    "把", "让", "从", "到", "向", "往", "比", "跟", "同", "而", "但", "如果",
    "因为", "所以", "虽然", "但是", "或者", "还是", "不过", "只是", "只要",
    "只有", "即使", "无论", "不管", "不仅", "而且", "并", "却", "上", "下",
    "中", "里", "外", "这", "那", "这个", "那个", "这些", "那些", "个",
    "万", "亿",
    # 标点（会与 re.sub 一起处理）
}, key=len, reverse=True)


def clean_search_query(text):
    """从口语化提问中提取核心关键词，提高 IMA 知识库检索命中率。"""
    t = (text or "").strip()
    if not t:
        return t
    # 移除中文/英文常见标点，统一为空格
    t = re.sub(r"[。，、；：！？.”“\"'‘’（）《》【】\s]+", " ", t)
    for w in _SEARCH_STOPWORDS:
        if w in t:
            t = t.replace(w, " ")
    t = re.sub(r"\s+", "", t).strip()
    return t if t else text


def extract_latest_question(texts, det):
    mark("extract_latest_question", "提取最新提问")  # AUTO-INSTRUMENTED
    """从 OCR 读出的若干行消息里，找最后一条像「提问」的文本。

    OCR 常把一条长消息拆成多行，所以不简单取 msgs[-1]，而是扫描所有行，
    返回最后一条带问号/疑问词的文本。
    """
    last_q = ""
    for t in texts:
        t = (t or "").strip()
        if not t:
            continue
        if is_question(t, det) or "？" in t or "?" in t:
            last_q = t
    return last_q


# ----------------------------------------------------------------------------
# 主循环
# ----------------------------------------------------------------------------
def run_cycle(cfg, ima, wechat, state, dry_run):
    mark("run_cycle", "一轮监控开始")  # AUTO-INSTRUMENTED
    logging.info("===== 开始一轮监控 (dry_run=%s) =====", dry_run)
    groups = cfg["wechat"].get("monitored_groups", []) or []
    if cfg["wechat"].get("monitor_all_visible"):
        groups = wechat.list_visible_groups()

    answered = state.setdefault("answered", {})
    sent = state.setdefault("sent", [])
    now = time.time()
    replies = 0
    max_replies = int(cfg["behavior"].get("max_replies_per_cycle", 5))

    for g in groups:
        if replies >= max_replies:
            break
        logging.info(">> 处理群: %s", g)
        if not wechat.open_group(g):
            continue
        msgs = wechat.read_messages()
        if not msgs:
            logging.info("   未读取到消息文本。")
            continue
        # 从可见消息里找最后一条"像提问"的内容（OCR 可能把一条消息拆成多行）
        latest = extract_latest_question(msgs, cfg["detection"])
        if not latest:
            logging.info("   未识别到提问，跳过。")
            continue
        logging.info("   最新提问: %s", latest[:80])

        key = sig(g + "|" + latest)
        if answered.get(g) == key:
            logging.info("   已回答过该消息，跳过。")
            continue
        if cfg["detection"].get("only_newer_than_state", True) and key in answered.values():
            pass  # 同内容已在别处答过，避免重复

        # 自己在短时间内发过的，不答（防回声）
        if any(s.get("text") == latest and now - s.get("ts", 0) < int(
                cfg["behavior"].get("avoid_duplicate_seconds", 3600)) for s in sent):
            logging.info("   疑似自己刚发的消息，跳过。")
            continue

        # 用 IMA 桌面端的 AI 问答生成自然语言回答（并截图）
        logging.info("   准备让 IMA 桌面端回答...")
        answer_img = ima.ask(latest)
        if not answer_img:
            logging.info("   IMA 未生成回答，跳过。")
            continue

        if dry_run:
            logging.info("   [DRY-RUN] 已生成 IMA 回答截图(未发送): %s", answer_img)
            answered[g] = key
            continue

        lo, hi = cfg["behavior"].get("human_like_delay", [1.5, 3.0])
        time.sleep(max(lo, 0))
        ok = wechat.send_image(answer_img)
        if ok:
            logging.info("   已发送 IMA 回答截图到群「%s」: %s", g, answer_img)
            answered[g] = key
            sent.append({"group": g, "text": latest, "ts": now,
                         "image": os.path.basename(answer_img)})
            # 只保留最近 50 条发送记录
            state["sent"] = sent[-50:]
            replies += 1
            time.sleep(min(max(hi, 0), 5))
        else:
            logging.error("   发送失败，跳过该群。")

    save_state(state)
    logging.info("===== 本轮结束 =====\n")


def run_selftest(cfg, ima, wechat, state):
    """全自动模式的首跑自检：验证微信开群+读消息、IMA 出图均可用。

    返回 True 表示可以放心进入循环；False 表示已把 debug/ 存盘，需人工/我介入。
    """
    logging.info("===== 首次自检 =====")
    # 1) 微信：打开监控群并读到消息
    ok_wx = False
    for g in cfg["wechat"].get("monitored_groups", []) or []:
        if wechat.open_group(g):
            msgs = wechat.read_messages()
            logging.info("微信群「%s」已打开，读取到 %d 行文本。样例: %s",
                         g, len(msgs), (msgs[-3:] if msgs else []))
            ok_wx = True
            break
    if not ok_wx:
        logging.error("自检失败：无法打开微信监控群。请确认微信已登录且群存在/可见。debug/ 已存盘。")
        return False

    # 2) IMA：问一个测试问题，验证能拿到真实回答截图
    test_q = cfg.get("selftest_question", "什么是四圣谛？")
    logging.info("自检：让 IMA 回答测试问题 -> %s", test_q)
    ans = ima.ask(test_q)
    if not ans or not os.path.exists(ans) or os.path.getsize(ans) < 5000:
        logging.error("自检失败：IMA 未能生成有效回答截图（%s）。debug/ 已存盘，请发回该目录。", ans)
        return False
    logging.info("自检通过：IMA 回答截图已生成 %s", ans)
    return True


def main():
    mark("main", "程序启动/参数解析")  # AUTO-INSTRUMENTED
    ap = argparse.ArgumentParser(description="WeChat -> IMA 自动监控应答")
    ap.add_argument("--config", default=os.path.join(SCRIPT_DIR, "config.json"))
    ap.add_argument("--once", action="store_true", help="只跑一轮")
    ap.add_argument("--auto", action="store_true", help="全自动：先自检，通过则自动循环")
    ap.add_argument("--live", action="store_true", help="真正发送（覆盖 dry_run）")
    ap.add_argument("--list-groups", action="store_true", help="打印可见会话名后退出")
    ap.add_argument("--calibrate", action="store_true", help="打印微信窗口控件树，便于排查选择器")
    ap.add_argument("--screenshot", action="store_true", help="截图存盘+画OCR框，便于微调微信坐标")
    ap.add_argument("--ima-calibrate", action="store_true", help="截图 IMA 窗口并标注 OCR，便于校准坐标")
    ap.add_argument("--ask-ima", metavar="QUESTION", default=None, help="单测：让 IMA 回答一个问题并保存截图")
    args = ap.parse_args()

    cfg = load_config(args.config)
    log_path = setup_logging(cfg)
    logging.info("日志文件: %s", log_path)

    # OCR 开关（可选，默认关闭，需额外安装 pytesseract + tesseract）
    ocr_cfg = cfg.get("ocr", {}) or {}
    cfg["wechat"]["_ocr_enabled"] = bool(ocr_cfg.get("enabled")) and _ocr_available(ocr_cfg)

    # IMA 校准 / 单测（不需要微信窗口）
    if args.ima_calibrate:
        ima = IMAController(cfg)
        if not ima.find_window():
            sys.exit(1)
        ima.calibrate()
        sys.exit(0)
    if args.ask_ima:
        ima = IMAController(cfg)
        if not ima.find_window():
            sys.exit(1)
        out = ima.ask(args.ask_ima)
        print(out or "IMA 未生成回答")
        sys.exit(0 if out else 1)

    wechat = OCRWeChatController(cfg) if cfg["wechat"].get("method", "ocr") == "ocr" else WeChatController(cfg)
    if not wechat.find_window():
        sys.exit(1)

    if args.calibrate:
        wechat.calibrate()
        sys.exit(0)
    if args.screenshot:
        wechat.save_debug()
        sys.exit(0)
    if args.list_groups:
        for n in wechat.list_visible_groups():
            print(n)
        sys.exit(0)

    ima = IMAController(cfg)
    if not ima.find_window():
        sys.exit(1)
    state = load_state()
    dry_run = not args.live and bool(cfg["behavior"].get("dry_run", True))

    if args.auto:
        if not run_selftest(cfg, ima, wechat, state):
            sys.exit(1)
        logging.info("自检通过，进入自动监控循环（dry_run=%s）。Ctrl+C 退出。", dry_run)
        interval = int(cfg["behavior"].get("interval_seconds", 600))
        try:
            while True:
                try:
                    run_cycle(cfg, ima, wechat, state, dry_run)
                except Exception as e:
                    logging.error("本轮异常: %s", e)
                time.sleep(interval)
        except KeyboardInterrupt:
            logging.info("用户中断，退出。")
        return

    if args.once:
        run_cycle(cfg, ima, wechat, state, dry_run)
        return

    interval = int(cfg["behavior"].get("interval_seconds", 600))
    logging.info("进入持久循环，每 %s 秒一轮。Ctrl+C 退出。", interval)
    try:
        while True:
            try:
                run_cycle(cfg, ima, wechat, state, dry_run)
            except Exception as e:
                logging.error("本轮异常: %s", e)
            time.sleep(interval)
    except KeyboardInterrupt:
        logging.info("用户中断，退出。")


def _ocr_available(ocr_cfg):
    try:
        import pytesseract  # noqa
        return True
    except Exception:
        return False


if __name__ == "__main__":
    main()
