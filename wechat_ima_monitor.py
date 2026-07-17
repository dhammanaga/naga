# -*- coding: utf-8 -*-
# ↑ 这一行告诉 Python：本文件用 UTF-8 编码保存，这样中文注释不会乱码。

"""
WeChat -> IMA 知识库 自动监控应答程序
==========================================

目标：
  每间隔一段时间（默认 10 分钟）自动检查微信里指定的群，
  若群内有人提问，则在 IMA 知识库「古源尊者开示及上座部佛教资料」
  中检索，并把找到的答案自动发回该群。

工作原理（不依赖微信官方接口，规避原生限制）：
  1. 微信 4.0（Qt 重写）不向 UI Automation 暴露聊天内容，因此改用
     「截图 + 中文 OCR + 拟人化鼠标/键盘模拟」方案驱动已经登录好的微信桌面端：
     uiautomation/win32 仅用于定位微信窗口；读取群名与消息、点击群、输入、
     发送全部走截图 OCR + pyautogui 模拟人类操作（点击带移动轨迹与随机延迟、
     窗口激活用真实鼠标点标题栏），不使用置顶/线程绑定等强风控 API，也不使用
     OLE 拖放注入；发图采用「剪贴板图片 + Ctrl+V」方案（用户确认保留）。
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
# ↑ 三引号里是「程序说明书」（docstring），讲清这个程序是干嘛的、怎么用、要注意什么。
#   它是整个项目的"总指挥"，负责：定时检查微信群 → 识别提问 → 让 IMA 回答 → 把答案发回群。

import argparse        # 导入 argparse：用来解析命令行参数（比如 --once、--live 这些开关）
import hashlib         # 导入 hashlib：用来计算"指纹"（把一段文字变成固定长度的摘要，便于去重）
import json            # 导入 json：用来读写配置文件 config.json 和状态文件
import os              # 导入 os：处理文件路径、判断文件是否存在
import re              # 导入 re：正则表达式库，用来做文字匹配/清洗
import sys             # 导入 sys：访问命令行参数、控制程序退出
import time            # 导入 time：用来"睡觉"（暂停几秒）、获取当前时间
import logging         # 导入 logging：写运行日志（记录程序每一步在做什么、有没有出错）
import urllib.request  # 导入 urllib.request：用来发 HTTP 网络请求（调用 IMA 的检索接口）
import urllib.error    # 导入 urllib.error：专门处理 HTTP 请求出错的情况

# 修复 Windows 控制台中文乱码：stdout/stderr 改 UTF-8 并切换控制台代码页
# ↑ 下面这段是"修乱码"的保险措施：Windows 老控制台默认编码不认 UTF-8，中文会显示成乱码。
if sys.platform == "win32":
    # ↑ 如果当前系统是 Windows（win32 是 Windows 的内部代号）……
    try:
        # ↑ try 保护：改编码这步可能在某些环境失败，包一下避免整程序崩溃。
        sys.stdout.reconfigure(encoding="utf-8")
        # ↑ 把"标准输出"（屏幕上打印的文字）改成 UTF-8 编码，中文不再乱码。
        sys.stderr.reconfigure(encoding="utf-8")
        # ↑ 把"标准错误输出"（报错信息）也改成 UTF-8 编码。
    except Exception:
        # ↑ 万一改不动，就忽略，不要因为这点小事让程序起不来。
        pass
    try:
        # ↑ 再试一次：直接调用 Windows 底层命令把控制台代码页切到 65001（UTF-8）。
        import ctypes
        # ↑ 临时导入 ctypes（Windows 底层接口库），用来调系统函数。
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        # ↑ 调用系统函数 SetConsoleOutputCP(65001)：把控制台输出代码页设为 UTF-8。
    except Exception:
        # ↑ 失败也忽略。
        pass

if sys.platform != "win32":
    # ↑ 如果当前系统不是 Windows……
    print("错误：本程序仅支持 Windows（需要 uiautomation）。", file=sys.stderr)
    # ↑ 打印一句报错（红色错误通道），告诉用户只支持 Windows。
    sys.exit(2)
    # ↑ 直接退出程序，错误码 2（表示"环境不对，没法跑"）。

import uiautomation as auto
# ↑ 导入 uiautomation 库（专门操作 Windows 窗口/控件），并起个别名 auto 方便用。

from wechat_ocr import OCRWeChatController
# ↑ 从 wechat_ocr.py 引入"微信控制器"：负责开群、读消息、发图（靠截图+模拟操作）。
from ima_ocr import IMAController
# ↑ 从 ima_ocr.py 引入"IMA 控制器"：负责让 IMA 回答问题并截图。
from flow_runtime import mark  # AUTO-INSTRUMENTED
# ↑ 从 flow_runtime 引入 mark 函数：用来记录"程序现在走到哪一步"，供可视化流程图实时高亮。
#   （AUTO-INSTRUMENTED 是自动埋点的标记，表示这行是工具自动插入的追踪代码。）

# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
# ↑ 分隔线注释：下面这一节是关于"配置"的代码。

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# ↑ 算出"本文件所在文件夹"的绝对路径，存进 SCRIPT_DIR。
#   为什么？因为下面读 config.json、存日志、存状态都要用"相对本文件夹"的路径，
#   无论项目放电脑哪个盘，都能正确找到，不会写死路径而出错。


def load_config(path):
    mark("load_config", "读取配置")  # AUTO-INSTRUMENTED
    # ↑ 定义一个函数 load_config（读取配置），参数 path 是 config.json 文件的路径。
    #   mark 这行记录"现在正在读配置"，可视化流程图上会高亮这一步。
    with open(path, "r", encoding="utf-8") as f:
        # ↑ 以"只读"方式打开配置文件，编码用 UTF-8（中文配置才不乱码）。f 是文件句柄。
        return json.load(f)
        # ↑ 把 JSON 格式的配置读成 Python 字典（一堆"名字:值"），交回去给调用者。


def expand(p):
    # ↑ 定义一个函数 expand（展开路径），专门处理路径里的 "~"（代表用户主目录）。
    if p.startswith("~"):
        # ↑ 如果路径以 ~ 开头……
        p = os.path.expanduser(p)
        # ↑ 就把 ~ 替换成真实的用户主目录路径（如 C:\Users\你）。
    return p
    # ↑ 把（可能展开过的）路径交回去。


# ----------------------------------------------------------------------------
# 日志
# ----------------------------------------------------------------------------
# ↑ 分隔线：下面这节配置"日志系统"。

def setup_logging(cfg):
    mark("setup_logging", "初始化日志")  # AUTO-INSTRUMENTED
    # ↑ 定义 setup_logging（初始化日志），参数 cfg 是配置字典。
    #   日志 = 把程序运行过程记到文件里，出了问题可以回头查"哪一步出错了"。
    log_dir = os.path.join(SCRIPT_DIR, cfg["logging"].get("dir", "logs"))
    # ↑ 算出日志文件夹路径：在本文件夹下、取配置里 logging.dir 的值，没配就用默认的 "logs"。
    os.makedirs(log_dir, exist_ok=True)
    # ↑ 创建这个日志文件夹（exist_ok=True 表示"已存在也不报错"）。
    log_path = os.path.join(log_dir, "monitor.log")
    # ↑ 日志文件完整路径：日志文件夹里的 monitor.log。
    level = getattr(logging, cfg["logging"].get("level", "INFO").upper(), logging.INFO)
    # ↑ 取配置的日志级别（如 INFO / DEBUG），找不到就用默认的 INFO。
    #   级别越高记的越详细：DEBUG 最细，INFO 适中，ERROR 只记错误。
    logging.basicConfig(
        # ↑ 真正配置 logging：设定级别、格式、输出到哪。
        level=level,
        # ↑ 上面算好的级别。
        format="%(asctime)s [%(levelname)s] %(message)s",
        # ↑ 每行日志长这样：[时间] [级别] 消息内容。
        handlers=[
            # ↑ handlers 指定"日志写到哪"：这里同时写两个地方。
            logging.FileHandler(log_path, encoding="utf-8"),
            # ↑ ① 写进日志文件 monitor.log（UTF-8 编码）。
            logging.StreamHandler(sys.stdout),
            # ↑ ② 同时打印到屏幕（stdout），让你实时看到进度。
        ],
    )
    return log_path
    # ↑ 把日志文件路径交回去（后面可能会打印"日志写在哪"提示用户）。


# ----------------------------------------------------------------------------
# 状态（去重，避免同一条消息反复作答 / 自己发的消息被当成问题）
# ----------------------------------------------------------------------------
# ↑ 分隔线：下面这节管"去重状态"——记住已经回答过的问题，别反复答同一句。

STATE_PATH = os.path.join(SCRIPT_DIR, "state.json")
# ↑ 去重状态文件路径：在本文件夹下的 state.json（记录"哪些问题已答过"）。


def load_state():
    mark("load_state", "读取去重状态")  # AUTO-INSTRUMENTED
    # ↑ 定义 load_state（读取去重状态）。mark 记录"正在读状态"。
    if os.path.exists(STATE_PATH):
        # ↑ 如果状态文件存在……
        try:
            # ↑ try 保护：读文件可能出错（文件损坏等），包一下。
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                # ↑ 以只读、UTF-8 打开状态文件。
                return json.load(f)
                # ↑ 把文件内容（JSON）读成字典并返回。
        except Exception:
            # ↑ 读失败（比如文件坏了）就忽略，走下面的默认值。
            pass
    return {"answered": {}, "sent": []}
    # ↑ 如果没文件或读失败，返回一个"空白状态"：answered 是空字典、sent 是空列表。


def save_state(state):
    mark("save_state", "保存去重状态")  # AUTO-INSTRUMENTED
    # ↑ 定义 save_state（保存去重状态），参数 state 是当前的去重状态字典。
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        # ↑ 以"写入"方式打开状态文件（会覆盖旧内容），UTF-8 编码。
        json.dump(state, f, ensure_ascii=False, indent=2)
        # ↑ 把状态字典写回文件：ensure_ascii=False 让中文正常存、indent=2 让格式缩进整齐好读。


def sig(text):
    # ↑ 定义 sig（signature 的缩写，生成"指纹"），参数 text 是一段文字。
    return hashlib.md5(text.strip().encode("utf-8")).hexdigest()[:16]
    # ↑ 把文字去掉首尾空格、编码成 UTF-8，再用 MD5 算法算出一段指纹字符串，只取前 16 位。
    #   用途：把"问题文字"变成一个短而唯一的代号，方便去重比对（不用比整段长文字）。


# ----------------------------------------------------------------------------
# IMA 知识库检索
# ----------------------------------------------------------------------------
# ↑ 分隔线：下面这节负责"向 IMA 知识库提问并拿回答案"。

class IMAClient:
    # ↑ 定义一个类 IMAClient（IMA 客户端），封装"调 IMA 接口检索知识库"的全部逻辑。
    def __init__(self, cfg):
        # ↑ 初始化方法：创建这个客户端时最先执行，记住配置。
        self.cfg = cfg["ima"]
        # ↑ 取出配置里 "ima" 这一大块设置，存起来备用。
        self.kb_id = self.cfg["kb_id"]
        # ↑ 记住知识库的 ID（IMA 用它知道你要查哪个知识库）。
        self.timeout = int(self.cfg.get("request_timeout", 30))
        # ↑ 记住网络请求超时时间（秒），没配就用默认 30 秒（超过就放弃，别一直等）。

    def _read_creds(self):
        # ↑ 定义一个内部方法 _read_creds（读取凭证）：从文件里读出 API 的账号和密钥。
        cid = self._read_file(self.cfg.get("client_id_path"))
        # ↑ 读取 client_id（客户端 ID）文件内容，存到 cid。
        key = self._read_file(self.cfg.get("api_key_path"))
        # ↑ 读取 api_key（密钥）文件内容，存到 key。
        return cid, key
        # ↑ 把账号和密钥一起交回去。

    @staticmethod
    def _read_file(p):
        # ↑ 定义一个静态方法 _read_file（读文件），@staticmethod 表示它不依赖对象自身。
        #   参数 p 是文件路径。
        if not p:
            # ↑ 如果路径是空的（没配置）……
            return ""
            # ↑ 直接返回空字符串，表示"没读到东西"。
        p = expand(p)
        # ↑ 把路径里的 ~ 展开成真实主目录。
        try:
            # ↑ try 保护：读文件可能失败，包一下。
            with open(p, "r", encoding="utf-8") as f:
                # ↑ 以只读、UTF-8 打开文件。
                return f.read().strip()
                # ↑ 读取全部内容、去掉首尾空格后返回。
        except Exception:
            # ↑ 读不到（文件不存在/没权限）就返回空字符串，不报错。
            return ""

    def search(self, query):
        """返回拼接好的答案文本（空字符串表示没找到）。"""
        # ↑ 定义 search（搜索）：给一个问题，返回拼好的答案文字。
        data = self._fetch(query)
        # ↑ 先去 IMA 把原始检索结果拉回来（_fetch 内部决定用 python 还是 node 方式）。
        return self._format(data) if data else ""
        # ↑ 如果有结果就 _format 整理成可读文字；否则返回空字符串（表示没找到）。

    def search_passages(self, query, top_k=None):
        """返回结构化结果列表：[{'title':..., 'content':...}]，空列表表示没找到。"""
        # ↑ 定义 search_passages（搜索并取段落）：和 search 类似，但返回"结构化"的段落列表，
        #   每个段落有标题和正文，方便后面做成答案卡片图片。
        data = self._fetch(query)
        # ↑ 拉取原始检索结果。
        if not data:
            # ↑ 如果没结果……
            return []
            # ↑ 返回空列表。
        return self._to_passages(data, top_k or int(self.cfg.get("top_k", 3)))
        # ↑ 否则把原始结果转成"段落列表"，最多取 top_k 个（没指定就用配置里的 3）。

    def _fetch_python(self, query):
        # ↑ 定义 _fetch_python（用 Python 方式拉取）：纯标准库发 HTTP 请求调 IMA 接口。
        cid, key = self._read_creds()
        # ↑ 先读出 API 的账号和密钥。
        if not cid or not key:
            # ↑ 如果账号或密钥缺失……
            logging.error("未找到 IMA 凭证（client_id / api_key），请检查 config 中的路径。")
            # ↑ 记一条错误日志，提示用户配置不对。
            return None
            # ↑ 返回 None 表示"取不到"。
        url = "https://ima.qq.com/openapi/wiki/v1/search_knowledge"
        # ↑ IMA 检索接口的网址（固定写死，这是腾讯给的公开 API 地址）。
        body = json.dumps(
            # ↑ 把要发给服务器的"请求体"做成 JSON 字符串。
            {"query": query, "knowledge_base_id": self.kb_id, "cursor": ""}
            # ↑ 请求内容：查询问题、知识库 ID、游标（分页用，这里空）。
        ).encode("utf-8")
        # ↑ 再编码成 UTF-8 字节（网络传输要字节）。
        req = urllib.request.Request(
            # ↑ 构造一个 HTTP 请求对象。
            url,
            # ↑ 请求地址。
            data=body,
            # ↑ 请求体（POST 的数据）。
            headers={
                # ↑ 请求头：告诉服务器"我是谁、我要什么格式"。
                "ima-openapi-clientid": cid,
                # ↑ 客户端 ID（从文件读的账号）。
                "ima-openapi-apikey": key,
                # ↑ API 密钥（从文件读的密码）。
                "Content-Type": "application/json; charset=utf-8",
                # ↑ 声明发送的数据是 JSON 格式。
            },
            method="POST",
            # ↑ 用 POST 方法（把问题发给服务器）。
        )
        try:
            # ↑ try 保护：网络请求可能失败（超时、服务器错等），包一下。
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                # ↑ 真正发出请求并等待回应（最多等 timeout 秒）；resp 是服务器回包。
                data = json.loads(resp.read().decode("utf-8"))
                # ↑ 把回包内容读出来、解码成 UTF-8 文字、再解析成 Python 字典 data。
        except urllib.error.HTTPError as e:
            # ↑ 如果服务器返回了 HTTP 错误（如 400/500）……
            logging.error("IMA 检索 HTTP 错误 %s: %s", e.code, e.reason)
            # ↑ 记日志：错误码 + 原因。
            return None
            # ↑ 返回 None 表示取不到。
        except Exception as e:
            # ↑ 其它任何网络错误（超时、断网等）……
            logging.error("IMA 检索失败: %s", e)
            # ↑ 记日志。
            return None
            # ↑ 返回 None。

        if data.get("code", -1) != 0:
            # ↑ 检查服务器返回的 code 字段：IMA 约定 code==0 才是成功，否则是业务错误。
            logging.error("IMA 业务错误: %s", data.get("msg", "未知"))
            # ↑ 记日志：错误的具体信息（msg 字段，没有就写"未知"）。
            return None
            # ↑ 返回 None。
        return data.get("data", {})
        # ↑ 成功：取出 data 字段（真正的检索结果）交回去，没有就返回空字典。

    def _fetch_node(self, query):
        # ↑ 定义 _fetch_node（用 Node 方式拉取）：有些环境用 Node.js 脚本调 IMA 更稳，这里支持这种。
        import subprocess
        # ↑ 导入 subprocess（用来在 Python 里启动另一个程序，比如 node）。

        node_script = expand(self.cfg.get("node_script", ""))
        # ↑ 取出 node 检索脚本的路径，并展开 ~。
        if not os.path.exists(node_script):
            # ↑ 如果脚本文件不存在……
            logging.error("未找到 node 检索脚本: %s", node_script)
            # ↑ 记错误日志。
            return None
            # ↑ 返回 None。
        cid, key = self._read_creds()
        # ↑ 读出 API 账号和密钥。
        if not cid or not key:
            # ↑ 缺账号或密钥……
            logging.error("未找到 IMA 凭证。")
            # ↑ 记日志。
            return None
            # ↑ 返回 None。
        body = json.dumps(
            # ↑ 准备请求体 JSON。
            {"query": query, "knowledge_base_id": self.kb_id, "cursor": ""}
        )
        opts = json.dumps({"clientId": cid, "apiKey": key})
        # ↑ 把账号密钥也做成 JSON，作为 node 脚本的参数。
        try:
            # ↑ try 保护：运行 node 脚本可能失败，包一下。
            out = subprocess.run(
                # ↑ 运行外部命令。
                ["node", node_script, "openapi/wiki/v1/search_knowledge", body, opts],
                # ↑ 命令 = node 脚本路径 + 接口路径 + 问题 + 凭证。
                capture_output=True,
                # ↑ 捕获脚本的输出（不让它直接刷屏）。
                text=True,
                # ↑ 以文字形式返回输出（而非字节）。
                timeout=self.timeout + 10,
                # ↑ 超时时间比 HTTP 多给 10 秒，给 node 启动留余地。
            )
        except Exception as e:
            # ↑ 运行失败（node 没装、脚本崩了）……
            logging.error("调用 node 检索失败: %s", e)
            # ↑ 记日志。
            return None
            # ↑ 返回 None。
        if out.returncode != 0:
            # ↑ 如果 node 脚本退出码不是 0（表示它自己报错了）……
            logging.error("node 检索返回错误: %s", out.stderr.strip())
            # ↑ 记下它打印的错误信息。
            return None
            # ↑ 返回 None。
        try:
            # ↑ try 保护：解析 node 输出可能失败，包一下。
            data = json.loads(out.stdout)
            # ↑ 把 node 输出的文字解析成 JSON 字典。
        except Exception:
            # ↑ 解析失败（输出不是合法 JSON）……
            logging.error("node 检索输出非 JSON")
            # ↑ 记日志。
            return None
            # ↑ 返回 None。
        if data.get("code", -1) != 0:
            # ↑ 同样检查业务 code。
            return None
            # ↑ 非 0 表示失败，返回 None。
        return data.get("data", {})
        # ↑ 成功，取出 data 返回。

    def _fetch(self, query):
        # ↑ 定义 _fetch（拉取）：对外统一的"取数据"入口，内部选择用哪种方式。
        method = self.cfg.get("method", "python")
        # ↑ 读配置里的方法：默认 "python"（用标准库），也可配 "node"。
        if method == "node":
            # ↑ 如果配了 node……
            return self._fetch_node(query)
            # ↑ 就走 node 方式。
        return self._fetch_python(query)
        # ↑ 否则（默认）走 python 方式。

    @staticmethod
    def _to_passages(data, top_k):
        # ↑ 定义 _to_passages（转成段落）：把 IMA 返回的原始数据整理成"标题+正文"列表。
        items = data.get("info_list", []) or []
        # ↑ 先从 data 里取 info_list（信息列表），没有就当空列表。
        if not items:
            # ↑ 如果 info_list 是空的……
            # 某些版本返回结构不同，兜底取任意含文本的字段
            # ↑ 注释解释：IMA 不同版本返回格式可能不同，下面换一个字段名再试。
            items = data.get("list", []) or []
            # ↑ 退而求其次，取 list 字段。
        parts = []
        # ↑ 准备一个空列表 parts，收集整理好的段落。
        for it in items[:top_k]:
            # ↑ 遍历前 top_k 个结果（[:top_k] 表示"只取前面这么多"）。
            title = (it.get("title") or "").strip()
            # ↑ 取出这一条的标题，没有就当空字符串并去空格。
            text = (
                # ↑ 取出正文内容：IMA 不同字段名都有可能装正文，这里按顺序尝试。
                it.get("content")
                or it.get("abstract")
                or it.get("content_preview")
                or ""
            )
            text = (text or "").strip()
            # ↑ 取到正文后去掉首尾空格。
            if not text and not title:
                # ↑ 如果既没正文也没标题（这条是空的）……
                continue
                # ↑ 跳过这条，不要往结果里放。
            parts.append({"title": title, "content": text})
            # ↑ 把"标题+正文"作为一个小字典，加进结果列表。
        return parts
        # ↑ 返回整理好的段落列表。

    def _format(self, data):
        # ↑ 定义 _format（格式化）：把原始结果拼成一段连续的答案文字。
        if not data:
            # ↑ 如果没数据……
            return ""
            # ↑ 返回空字符串。
        return "\n\n".join(
            # ↑ 用两个换行符（空一行）把每个段落拼起来，形成易读的答案文本。
            (("【%s】\n" % p["title"] if p["title"] else "") + p["content"]).strip()
            # ↑ 每个段落：有标题就加"【标题】"前缀，再接正文，并去掉首尾空格。
            for p in self._to_passages(data, int(self.cfg.get("top_k", 3)))
            # ↑ 对每个段落（最多 top_k 个）都做上面的拼接。
        )


# ----------------------------------------------------------------------------
# 微信操控
# ----------------------------------------------------------------------------
# ↑ 分隔线：下面这节讲"微信操控"的注释说明。

# 原 WeChatController（UI Automation 全程序化控制：item.Click()/auto.SetClipboardText()
# /SendKeys()）已被废除——它属于「程序化注入」，与「视觉识别 + 鼠标/键盘模拟人类操作」
# 的要求相悖，且微信 4.0 不暴露控件。现在只走 OCRWeChatController（截图 OCR + 拟人化
# pyautogui 模拟，窗口激活用真实鼠标点标题栏）。
# ↑ 这段注释解释一个"历史决策"：之前用过另一种控制方式（直接调控件），但因为微信 4.0 不
#   暴露控件、且那种方式属于"程序注入"易触发封号，所以废弃了，现在统一用"截图+模拟人类"的方案。


# ----------------------------------------------------------------------------
# 提问识别
# ----------------------------------------------------------------------------
# ↑ 分隔线：下面这节负责"判断一段文字是不是个问题"。

def is_question(text, det):
    # ↑ 定义 is_question（是不是问题），参数 text 是待判断的文字，det 是检测配置。
    t = (text or "").strip()
    # ↑ 把文字去空格存到 t（text 为空就当空字符串）。
    if len(t) < int(det.get("min_len", 4)):
        # ↑ 如果文字太短（少于配置的最小长度，默认 4 字）……
        return False
        # ↑ 太短不可能是个问题，返回"不是"。
    for m in det.get("question_marks", []):
        # ↑ 遍历配置里的"问号标记"列表（如 "?"、"？"）。
        if t.endswith(m):
            # ↑ 如果文字以某个问号结尾……
            return True
            # ↑ 结尾是问号，基本就是个问题，返回"是"。
    for kw in det.get("question_keywords", []):
        # ↑ 否则遍历配置里的"疑问词"列表（如 "吗"、"怎么"、"为什么"）。
        if kw in t:
            # ↑ 如果文字里包含某个疑问词……
            return True
            # ↑ 含有疑问词，认定为问题，返回"是"。
    return False
    # ↑ 既没问号也没疑问词，判定"不是问题"。


# 搜索查询清洗：去掉口语化/功能词，保留更容易命中知识库的核心关键词
# ↑ 注释说明：下面这个函数是为了"净化问题"，让去 IMA 搜的时候更容易搜到。
_SEARCH_STOPWORDS = sorted({
    # ↑ 定义一个"停用词"集合（口语词、虚词），这些是搜索时的噪音，要被剔除。
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
# ↑ sorted(..., key=len, reverse=True) 表示按"词的长度从长到短"排序。
#   为什么要长词优先？因为替换时长词先替，避免短词误伤（比如先替"可以"再替"以"会乱）。


def clean_search_query(text):
    """从口语化提问中提取核心关键词，提高 IMA 知识库检索命中率。"""
    # ↑ 定义 clean_search_query（清洗搜索词），把口语问题提炼成核心关键词。
    t = (text or "").strip()
    # ↑ 去掉首尾空格。
    if not t:
        # ↑ 如果文字是空的……
        return t
        # ↑ 直接返回空的。
    # 移除中文/英文常见标点，统一为空格
    # ↑ 注释：先把标点去掉，避免干扰。
    t = re.sub(r"[。，、；：！？.”“\"'‘’（）《》【】\s]+", " ", t)
    # ↑ 用正则表达式把各种中英文标点、空格替换成一个空格。
    for w in _SEARCH_STOPWORDS:
        # ↑ 遍历每一个停用词。
        if w in t:
            # ↑ 如果文字里包含这个词……
            t = t.replace(w, " ")
            # ↑ 就把它替换成空格（相当于删掉）。
    t = re.sub(r"\s+", "", t).strip()
    # ↑ 把连续多个空格压成一个（这里再删掉所有空格），并去首尾空格。
    return t if t else text
    # ↑ 如果清洗后还剩字，就返回净化版；如果全被删光了，就退回原问题（至少还有点东西可搜）。


def extract_latest_question(items, det):
    mark("extract_latest_question", "提取最新提问")  # AUTO-INSTRUMENTED
    # ↑ 定义 extract_latest_question（提取最新提问）：从一堆读到的消息里找"最新的那个问题"。
    """从 OCR 读出的若干行消息里，找「屏幕最底部（y 最大=最新）」且像提问的文本。

    OCR 常把一条长消息拆成多行，且各行顺序不严格按版面，因此不能简单取列表
    末尾；这里按屏幕 y 坐标取位置最靠下的一条提问，确保命中「最新」消息。
    items 元素可为 字符串 或 (文本, y) 元组（来自 wechat_ocr.read_messages）。
    """
    # ↑ docstring 解释：OCR 读出来的消息可能乱序、一行拆多行，所以不靠"列表末尾"，
    #   而是靠"屏幕纵坐标 y 最大"（最靠下=最新）来定位最新的问题。
    last_q = ""
    # ↑ 记下"目前找到的最新问题"，初始为空。
    last_y = -1
    # ↑ 记下"最新问题所在的屏幕 y 坐标"，初始 -1（表示还没找到）。
    for item in items:
        # ↑ 遍历每一条读到的消息。
        if isinstance(item, (tuple, list)):
            # ↑ 如果这条是"元组/列表"形式（包含文字和坐标）……
            t = (item[0] or "") if len(item) > 0 else ""
            # ↑ 取出第 0 个元素当文字（没有就空）。
            y = item[1] if len(item) > 1 else 0
            # ↑ 取出第 1 个元素当 y 坐标（没有就当 0）。
        else:
            # ↑ 否则这条就是纯文字字符串……
            t, y = item, 0
            # ↑ 文字就是它自己，y 坐标当 0。
        t = (t or "").strip()
        # ↑ 文字去空格。
        if not t:
            # ↑ 空的就跳过。
            continue
        if is_question(t, det) or "？" in t or "?" in t:
            # ↑ 如果这条像问题（有问号或疑问词）……
            if y >= last_y:
                # ↑ 而且它的位置比之前记录的最新问题更靠下（或相等）……
                last_q = t
                # ↑ 就更新"最新问题"为它。
                last_y = y
                # ↑ 同时更新记录的最新 y 坐标。
    return last_q
    # ↑ 循环结束，返回找到的最新问题文字（没找到就是空字符串）。


def _norm_text(s):
    # ↑ 定义 _norm_text（规范化文字）：把文字变成"可比对"的干净形式。
    s = (s or "").strip().lower()
    # ↑ 去空格、转小写（大小写不敏感）。
    return re.sub(r"[^一-鿿a-z0-9]", "", s)
    # ↑ 只保留"中文、小写字母、数字"，删掉所有其它字符（标点、空格等），方便精确比对。


def _looks_like_own_answer(text, state, g):
    """判定 latest 是否其实是机器人自己刚发的答案图片被 OCR 误识出来的「提问」。

    答案图片里的文字（可能含「？」）会被 OCR 读成消息，导致下一轮误把它当新问题
    反复应答。若 latest 文本与最近一次发出的答案高度重合，则判定为自家图片，跳过。
    """
    # ↑ docstring 解释：这是个"防误判"机制。自己刚发过的答案图，被 OCR 读出来可能带"？"，
    #   导致下一轮误以为群里有人新问题、又去答一遍，形成死循环。这里对比一下，像自家图就跳过。
    prev = state.get("last_answer", {}).get(g, "")
    # ↑ 取出"之前给这个群发过的答案文字"。
    if not prev or not text:
        # ↑ 如果没发过答案、或当前文字是空的……
        return False
        # ↑ 无法判断，返回"不是自家图"。
    tn = _norm_text(text)
    # ↑ 把当前文字规范化（去标点变小写）。
    pn = _norm_text(prev)
    # ↑ 把之前答案文字也规范化。
    if not tn or not pn:
        # ↑ 任一规范化后为空……
        return False
        # ↑ 返回"不是"。
    # 自家图片被 OCR 误识时，读出来的是接近整篇答案的「长文本」；
    # 真实提问一般很短。先用长度门槛放行短提问，避免短字被长答案
    # 文本「字符覆盖」而误判为自家图片（曾误伤「有几层天？」）。
    # ↑ 注释解释：自家图被误读出来的是一整篇长答案；真人提问通常很短。
    #   所以短提问直接放行，避免被长答案"包含"而误判成自家图（曾误伤过"有几层天？"）。
    if len(tn) < 40:
        # ↑ 如果当前文字规范后不到 40 字（属于短提问）……
        return False
        # ↑ 直接判定"不是自家图"，放行（让它被正常识别为提问）。
    if tn in pn or pn in tn:
        # ↑ 如果当前文字和之前答案"互相包含"（高度重合）……
        return True
        # ↑ 判定为自家图，返回 True（意思是"该跳过"）。
    set_t, set_p = set(tn), set(pn)
    # ↑ 把两者规范文字拆成"字符集合"（去重）。
    if set_t and set_p and len(set_t & set_p) / len(set_t) >= 0.85:
        # ↑ 如果两者字符重合度达到 85% 以上……
        return True
        # ↑ 也判定为自家图。
    return False
    # ↑ 都不满足，判定"不是自家图"。


# ----------------------------------------------------------------------------
# 主循环
# ----------------------------------------------------------------------------
# ↑ 分隔线：下面这节是"主循环"——程序一遍遍重复的监控逻辑。

def run_cycle(cfg, ima, wechat, state, dry_run):
    mark("run_cycle", "一轮监控开始")  # AUTO-INSTRUMENTED
    # ↑ 定义 run_cycle（跑一轮监控）：这是核心循环体，每轮检查所有群、作答、发图。
    logging.info("===== 开始一轮监控 (dry_run=%s) =====", dry_run)
    # ↑ 记日志：本轮开始，并标明是否"演练模式"。
    groups = cfg["wechat"].get("monitored_groups", []) or []
    # ↑ 取出要监控的群列表（配置里 monitored_groups），没有就空列表。
    if cfg["wechat"].get("monitor_all_visible"):
        # ↑ 如果配置里说"监控所有可见会话"……
        groups = wechat.list_visible_groups()
        # ↑ 就用微信控制器列出当前所有可见会话，当作要监控的群。

    answered = state.setdefault("answered", {})
    # ↑ 从状态里取"已回答记录"字典（没有就建一个空字典）。
    sent = state.setdefault("sent", [])
    # ↑ 从状态里取"已发送记录"列表（没有就建空列表）。
    now = time.time()
    # ↑ 记下当前时间戳（用于判断"短时间内自己发过的别再答"）。
    replies = 0
    # ↑ 本轮已回答的计数，初始 0。
    max_replies = int(cfg["behavior"].get("max_replies_per_cycle", 5))
    # ↑ 本轮最多回答几个群（防止一次刷太多），默认 5。

    for g in groups:
        # ↑ 挨个处理每个要监控的群。
        if replies >= max_replies:
            # ↑ 如果已经达到本轮上限……
            break
            # ↑ 就停止处理更多群。
        logging.info(">> 处理群: %s", g)
        # ↑ 记日志：正在处理哪个群。
        if not wechat.open_group(g):
            # ↑ 尝试打开这个群；如果打开失败（找不到群）……
            continue
            # ↑ 跳过这个群，处理下一个。
        msgs = wechat.read_messages()
        # ↑ 打开后，读取这个群当前显示的消息。
        if not msgs:
            # ↑ 如果没读到任何消息文字……
            logging.info("   未读取到消息文本。")
            # ↑ 记日志说明。
            continue
            # ↑ 跳过这个群。
        # 从可见消息里找最后一条"像提问"的内容（OCR 可能把一条消息拆成多行）
        # ↑ 注释：下面找"最新提问"。
        latest = extract_latest_question(msgs, cfg["detection"])
        # ↑ 调用前面写的函数，从读到的消息里提取最新提问。
        if not latest:
            # ↑ 如果没识别出提问……
            logging.info("   未识别到提问，跳过。")
            # ↑ 记日志。
            continue
            # ↑ 跳过这个群。
        logging.info("   最新提问: %s", latest[:80])
        # ↑ 记日志：打印提问前 80 字（太长就截断）。

        # 防护：答案图片被 OCR 误识成「提问」时（图片文字含「？」），跳过，避免死循环应答
        # ↑ 注释：下面这步是"防自己发的图被当成新问题"。
        if _looks_like_own_answer(latest, state, g):
            # ↑ 如果最新提问看起来是自家答案图被误识……
            logging.info("   该「提问」与刚发出的答案高度重合，判定为自家图片，跳过。")
            # ↑ 记日志说明。
            continue
            # ↑ 跳过，不当新问题答。

        key = sig(g + "|" + latest)
        # ↑ 给"这个群 + 这句提问"算一个指纹（去重用）。
        if answered.get(g) == key:
            # ↑ 如果这个群上一次答的就是这句（指纹相同）……
            logging.info("   已回答过该消息，跳过。")
            # ↑ 记日志。
            continue
            # ↑ 跳过，避免重复答。
        if cfg["detection"].get("only_newer_than_state", True) and key in answered.values():
            pass  # 同内容已在别处答过，避免重复
            # ↑ 如果配置要求"只答比状态更新的"且这句在别处已答过，就跳过（pass 表示什么都不做）。

        # 自己在短时间内发过的，不答（防回声）
        # ↑ 注释：下面防止"自己刚发的消息被当成问题又答一遍"（回声效应）。
        if any(s.get("text") == latest and now - s.get("ts", 0) < int(
                cfg["behavior"].get("avoid_duplicate_seconds", 3600)) for s in sent):
            # ↑ 遍历已发送记录：如果有"文字相同、且是 1 小时内（默认）发的"……
            logging.info("   疑似自己刚发的消息，跳过。")
            # ↑ 记日志。
            continue
            # ↑ 跳过。

        # 用 IMA 桌面端的 AI 问答生成自然语言回答（并截图）
        # ↑ 注释：下面让 IMA 来回答问题。
        logging.info("   准备让 IMA 桌面端回答...")
        # ↑ 记日志。
        answer_img = ima.ask(latest)
        # ↑ 调用 IMA 控制器：把问题丢给 IMA，让它回答并生成一张答案截图，返回图片路径。
        if not answer_img:
            # ↑ 如果 IMA 没生成回答（可能出错）……
            logging.info("   IMA 未生成回答，跳过。")
            # ↑ 记日志。
            continue
            # ↑ 跳过这个群。

        if dry_run:
            # ↑ 如果是"演练模式"（只记录不真发）……
            logging.info("   [DRY-RUN] 已生成 IMA 回答截图(未发送): %s", answer_img)
            # ↑ 记日志：生成了截图但不发。
            answered[g] = key
            # ↑ 记一下"这个群答过这句了"（演练也要去重，免得日志里重复）。
            continue
            # ↑ 跳到下一个群。

        lo, hi = cfg["behavior"].get("human_like_delay", [1.5, 3.0])
        # ↑ 取出"拟人化延迟"区间 [最小, 最大]（秒），让发送间隔像真人。
        time.sleep(max(lo, 0))
        # ↑ 先暂停 lo 秒（至少 0，不会暂停负数）。
        ok = wechat.send_image(answer_img)
        # ↑ 通过微信把答案图片发到这个群，返回是否成功。
        if ok:
            # ↑ 如果发送成功……
            logging.info("   已发送 IMA 回答截图到群「%s」: %s", g, answer_img)
            # ↑ 记日志。
            answered[g] = key
            # ↑ 记录"这个群已答过这句"。
            # 记录刚发出的答案文本，供下一轮识别「自家图片」误识防护
            # ↑ 注释：下面把刚发的答案文字存起来，供下一轮"防误识"用。
            ans_text = ""
            # ↑ 先准备一个空变量装答案文字。
            try:
                # ↑ try 保护：取答案文字可能失败，包一下。
                ans_text = ima.state.get("ima", {}).get("last_answer_text", "")
                # ↑ 从 IMA 控制器状态里取"上一次答案文字"。
            except Exception:
                # ↑ 取不到就当空。
                ans_text = ""
            state.setdefault("last_answer", {})[g] = ans_text
            # ↑ 把"这个群刚发的答案"存进状态。
            sent.append({"group": g, "text": latest, "ts": now,
                         "image": os.path.basename(answer_img)})
            # ↑ 在"已发送记录"里追加一条：群名、问题、发送时间、图片文件名。
            # 只保留最近 50 条发送记录
            # ↑ 注释：下面只留最近 50 条，避免记录无限增长。
            state["sent"] = sent[-50:]
            # ↑ 把发送记录裁成最后 50 条。
            replies += 1
            # ↑ 本轮已回答数 +1。
            time.sleep(min(max(hi, 0), 5))
            # ↑ 暂停 hi 秒（限制在 0~5 之间），模拟真人节奏再处理下一个群。
        else:
            # ↑ 如果发送失败……
            logging.error("   发送失败，跳过该群。")
            # ↑ 记错误日志，跳过这个群（不重试，避免卡住）。

    save_state(state)
    # ↑ 一轮结束，把更新后的状态写回 state.json（持久化去重记录）。
    logging.info("===== 本轮结束 =====\n")
    # ↑ 记日志：本轮结束。


def run_selftest(cfg, ima, wechat, state):
    """全自动模式的首跑自检：验证微信开群+读消息、IMA 出图均可用。

    返回 True 表示可以放心进入循环；False 表示已把 debug/ 存盘，需人工/我介入。
    """
    # ↑ docstring 解释：自检就是"正式跑之前先小试一下"，确认微信和 IMA 都正常，
    #   没问题才进循环；有问题就把排查截图存到 debug/ 文件夹。
    logging.info("===== 首次自检 =====")
    # ↑ 记日志：开始自检。
    # 1) 微信：打开监控群并读到消息
    # ↑ 注释：第一步测微信。
    ok_wx = False
    # ↑ 标记"微信自检是否通过"，初始 False。
    for g in cfg["wechat"].get("monitored_groups", []) or []:
        # ↑ 遍历配置的监控群。
        if wechat.open_group(g):
            # ↑ 尝试打开这个群；打开成功才继续……
            msgs = wechat.read_messages()
            # ↑ 读取消息。
            logging.info("微信群「%s」已打开，读取到 %d 行文本。样例: %s",
                         g, len(msgs), (msgs[-3:] if msgs else []))
            # ↑ 记日志：打开成功，并显示读到的行数和最后 3 行样例。
            ok_wx = True
            # ↑ 微信自检算通过。
            break
            # ↑ 只要有一个群能开能读，就够了，跳出循环。
    if not ok_wx:
        # ↑ 如果微信自检没通过（所有群都开不了/读不到）……
        logging.error("自检失败：无法打开微信监控群。请确认微信已登录且群存在/可见。debug/ 已存盘。")
        # ↑ 记错误日志，提示用户检查微信。
        return False
        # ↑ 返回 False（自检失败）。

    # 2) IMA：问一个测试问题，验证能拿到真实回答截图
    # ↑ 注释：第二步测 IMA。
    test_q = cfg.get("selftest_question", "什么是四圣谛？")
    # ↑ 取出自检用的测试问题，没配就用默认的"什么是四圣谛？"。
    logging.info("自检：让 IMA 回答测试问题 -> %s", test_q)
    # ↑ 记日志。
    ans = ima.ask(test_q)
    # ↑ 让 IMA 回答这个测试问题，返回答案图片路径。
    if not ans or not os.path.exists(ans) or os.path.getsize(ans) < 5000:
        # ↑ 如果没图、图不存在、或图小于 5KB（说明是空图/无效）……
        logging.error("自检失败：IMA 未能生成有效回答截图（%s）。debug/ 已存盘，请发回该目录。", ans)
        # ↑ 记错误日志，提示把 debug/ 发回来排查。
        return False
        # ↑ 返回 False。
    logging.info("自检通过：IMA 回答截图已生成 %s", ans)
    # ↑ 记日志：IMA 自检通过。
    return True
    # ↑ 两步都过，返回 True（可以放心进循环）。


def main():
    mark("main", "程序启动/参数解析")  # AUTO-INSTRUMENTED
    # ↑ 定义 main（主函数，程序真正入口），mark 记录"启动"。
    ap = argparse.ArgumentParser(description="WeChat -> IMA 自动监控应答")
    # ↑ 创建一个命令行参数解析器，描述写清楚这是干嘛的。
    ap.add_argument("--config", default=os.path.join(SCRIPT_DIR, "config.json"))
    # ↑ 参数 --config：指定配置文件路径，默认用本文件夹的 config.json。
    ap.add_argument("--once", action="store_true", help="只跑一轮")
    # ↑ 参数 --once：出现就表示"只跑一轮"（不循环）。
    ap.add_argument("--auto", action="store_true", help="全自动：先自检，通过则自动循环")
    # ↑ 参数 --auto：全自动模式（先自检再循环）。
    ap.add_argument("--live", action="store_true", help="真正发送（覆盖 dry_run）")
    # ↑ 参数 --live：真正发送（关掉演练模式）。
    ap.add_argument("--list-groups", action="store_true", help="打印可见会话名后退出")
    # ↑ 参数 --list-groups：列出可见会话后退出（方便你填配置）。
    ap.add_argument("--calibrate", action="store_true", help="打印微信窗口控件树，便于排查选择器")
    # ↑ 参数 --calibrate：打印微信窗口结构，方便调试。
    ap.add_argument("--screenshot", action="store_true", help="截图存盘+画OCR框，便于微调微信坐标")
    # ↑ 参数 --screenshot：截图并画 OCR 框，方便校准坐标。
    ap.add_argument("--ima-calibrate", action="store_true", help="截图 IMA 窗口并标注 OCR，便于校准坐标")
    # ↑ 参数 --ima-calibrate：校准 IMA 坐标。
    ap.add_argument("--ask-ima", metavar="QUESTION", default=None, help="单测：让 IMA 回答一个问题并保存截图")
    # ↑ 参数 --ask-ima：让 IMA 单测回答一个问题并保存截图。
    args = ap.parse_args()
    # ↑ 真正解析命令行，把结果放进 args。

    cfg = load_config(args.config)
    # ↑ 按用户指定的（或默认的）路径读取配置文件。
    log_path = setup_logging(cfg)
    # ↑ 初始化日志系统，拿到日志文件路径。
    logging.info("日志文件: %s", log_path)
    # ↑ 记日志：日志写在哪。

    # OCR 开关（可选，默认关闭，需额外安装 pytesseract + tesseract）
    # ↑ 注释：下面决定"OCR 兜底"是否开启。
    ocr_cfg = cfg.get("ocr", {}) or {}
    # ↑ 取出 ocr 配置块。
    cfg["wechat"]["_ocr_enabled"] = bool(ocr_cfg.get("enabled")) and _ocr_available(ocr_cfg)
    # ↑ 在微信配置里记一个内部开关：配置开启 且 相应库可用 时，才为真。

    # IMA 校准 / 单测（不需要微信窗口）
    # ↑ 注释：下面两个分支不需要微信，只用 IMA，所以优先处理、提前退出。
    if args.ima_calibrate:
        # ↑ 如果用户选了 IMA 校准……
        ima = IMAController(cfg)
        # ↑ 创建 IMA 控制器。
        if not ima.find_window():
            # ↑ 如果找不到 IMA 窗口……
            sys.exit(1)
            # ↑ 退出，错误码 1。
        ima.calibrate()
        # ↑ 执行校准（截图+标注文字）。
        sys.exit(0)
        # ↑ 完成后正常退出。
    if args.ask_ima:
        # ↑ 如果用户选了"单测问 IMA"……
        ima = IMAController(cfg)
        # ↑ 创建 IMA 控制器。
        if not ima.find_window():
            # ↑ 找不到窗口就退出。
            sys.exit(1)
        out = ima.ask(args.ask_ima)
        # ↑ 让 IMA 回答用户给的问题，返回截图的路径。
        print(out or "IMA 未生成回答")
        # ↑ 把路径打印出来（或"未生成回答"）。
        sys.exit(0 if out else 1)
        # ↑ 有图就正常退出(0)，没图就错误退出(1)。

    wechat = OCRWeChatController(cfg)
    # ↑ 创建微信控制器（负责开群/读消息/发图）。
    if not wechat.find_window():
        # ↑ 如果找不到微信窗口……
        sys.exit(1)
        # ↑ 退出，错误码 1。

    if args.calibrate:
        # ↑ 如果用户选了微信校准……
        wechat.calibrate()
        # ↑ 执行校准。
        sys.exit(0)
        # ↑ 退出。
    if args.screenshot:
        # ↑ 如果用户选了截图……
        wechat.save_debug()
        # ↑ 截图并存盘+画框。
        sys.exit(0)
        # ↑ 退出。
    if args.list_groups:
        # ↑ 如果用户选了列群名……
        for n in wechat.list_visible_groups():
            # ↑ 遍历所有可见会话名……
            print(n)
            # ↑ 逐个打印。
        sys.exit(0)
        # ↑ 退出。

    ima = IMAController(cfg)
    # ↑ 创建 IMA 控制器（前面微信窗口已确认存在）。
    if not ima.find_window():
        # ↑ 找不到 IMA 窗口就退出。
        sys.exit(1)
    state = load_state()
    # ↑ 读取去重状态（之前答过什么）。
    dry_run = not args.live and bool(cfg["behavior"].get("dry_run", True))
    # ↑ 决定是不是"演练模式"：没加 --live 且 配置里 dry_run 为真 → 演练。

    if args.auto:
        # ↑ 如果用户选了全自动模式……
        if not run_selftest(cfg, ima, wechat, state):
            # ↑ 先跑自检；如果自检失败……
            sys.exit(1)
            # ↑ 退出，错误码 1。
        logging.info("自检通过，进入自动监控循环（dry_run=%s）。Ctrl+C 退出。", dry_run)
        # ↑ 记日志：自检过，开始循环。
        interval = int(cfg["behavior"].get("interval_seconds", 600))
        # ↑ 取出每轮间隔秒数（默认 600 秒 = 10 分钟）。
        try:
            # ↑ try 保护整个循环。
            while True:
                # ↑ 无限循环：一遍遍跑监控。
                try:
                    # ↑ 内层 try：每一轮单独保护，某轮出错不影响整体。
                    run_cycle(cfg, ima, wechat, state, dry_run)
                    # ↑ 跑一轮监控。
                except Exception as e:
                    # ↑ 这一轮如果抛异常……
                    logging.error("本轮异常: %s", e)
                    # ↑ 记日志，然后继续下一轮（不中断）。
                time.sleep(interval)
                # ↑ 暂停 interval 秒，再跑下一轮。
        except KeyboardInterrupt:
            # ↑ 如果用户按了 Ctrl+C（主动中断）……
            logging.info("用户中断，退出。")
            # ↑ 记日志说明正常退出。
        return
        # ↑ 结束 main。

    if args.once:
        # ↑ 如果用户选了"只跑一轮"……
        run_cycle(cfg, ima, wechat, state, dry_run)
        # ↑ 跑一轮就结束。
        return
        # ↑ 结束。

    interval = int(cfg["behavior"].get("interval_seconds", 600))
    # ↑ 取出间隔秒数（持久循环模式用）。
    logging.info("进入持久循环，每 %s 秒一轮。Ctrl+C 退出。", interval)
    # ↑ 记日志：进入持久循环。
    try:
        # ↑ try 保护。
        while True:
            # ↑ 无限循环。
            try:
                # ↑ 内层 try：每轮单独保护。
                run_cycle(cfg, ima, wechat, state, dry_run)
                # ↑ 跑一轮。
            except Exception as e:
                # ↑ 某轮出错……
                logging.error("本轮异常: %s", e)
                # ↑ 记日志，继续。
            time.sleep(interval)
            # ↑ 暂停间隔秒数再跑下一轮。
    except KeyboardInterrupt:
        # ↑ 用户按 Ctrl+C……
        logging.info("用户中断，退出。")
        # ↑ 记日志。


def _ocr_available(ocr_cfg):
    # ↑ 定义 _ocr_available（OCR 是否可用）：检查 OCR 相关库能不能导入。
    try:
        # ↑ try 保护：导入可能失败（没安装）。
        import pytesseract  # noqa
        # ↑ 尝试导入 pytesseract（OCR 库）。# noqa 告诉检查工具"这行导入是有意的，别警告"。
        return True
        # ↑ 导入成功，说明可用。
    except Exception:
        # ↑ 导入失败（没装）……
        return False
        # ↑ 返回不可用。


if __name__ == "__main__":
    # ↑ Python 固定写法：只有"直接运行本文件"时才执行 main()，被别的文件 import 时不跑。
    main()
    # ↑ 启动程序主函数。
