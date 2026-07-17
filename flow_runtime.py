# -*- coding: utf-8 -*-
# ↑ 这一行告诉 Python：本文件用 UTF-8 编码保存，这样中文注释不会乱码。

"""
运行时追踪器：被监控程序在每个关键函数里调用 mark(node_id, msg)，
把"当前节点 + 日志"写入 flow_state.json，供 flow_visualizer.py 实时读取高亮。

设计原则：
  * 全部操作包 try/except，即使文件写失败也绝不影响主程序运行。
  * 写文件用「临时文件 + os.replace」原子替换，避免可视化端读到半截 JSON。
  * 与可视化端通过文件解耦，两端可独立启停。
"""
# ↑ 三引号是「模块说明」（docstring）：讲清这个文件是"流程追踪器"——
#   主程序每走到一个关键步骤就调用 mark()，把"现在到第几步"写进一个文件，
#   可视化窗口读这个文件，就能实时高亮当前进度。

import json        # 导入 json：把追踪数据写成 JSON 文件
import os          # 导入 os：处理文件路径
import time        # 导入 time：获取当前时间（给日志打时间戳）
import threading   # 导入 threading：多线程锁（防止同时写文件出错）

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flow_state.json")
# ↑ 追踪状态文件路径：本文件夹下的 flow_state.json（主程序和可视化窗口都读它）。
_lock = threading.Lock()
# ↑ 创建一个"锁"：保证同一时刻只有一个线程能写文件，避免写到一半被打断出错。


class FlowTracer:
    # ↑ 定义一个类 FlowTracer（流程追踪器），负责记录并保存当前执行到哪一步。
    def __init__(self, state_path=STATE_PATH, max_log=300):
        # ↑ 初始化方法：state_path=状态文件路径；max_log=日志最多保留条数。
        self.state_path = state_path
        # ↑ 记住状态文件路径。
        self.max_log = max_log
        # ↑ 记住日志上限。
        self._log = []
        # ↑ 准备一个列表，存历史日志。
        self._visited = []
        # ↑ 准备一个列表，存"已经走过的节点"（用于高亮显示）。
        self._current = None
        # ↑ 当前所在节点，初始 None（还没开始）。
        self._write()
        # ↑ 初始化时先写一次文件（创建空状态）。

    def mark(self, node_id, msg=None):
        """标记进入某个节点（函数）。msg 为可选的中文说明，会进入日志面板。"""
        # ↑ 定义 mark（标记）：记录"现在进入了哪个节点"，并可附一句中文说明。
        self._current = node_id
        # ↑ 把"当前节点"设成 node_id。
        if node_id not in self._visited:
            # ↑ 如果这个节点之前没记录过……
            self._visited.append(node_id)
            # ↑ 加进"已访问"列表（可视化上显示成"已走过"的绿色）。
        if msg:
            # ↑ 如果提供了中文说明……
            self._log.append("[%s] ▶ %s" % (time.strftime("%H:%M:%S"), msg))
            # ↑ 把"[时间] ▶ 说明"追加进日志（时间格式 时:分:秒）。
            if len(self._log) > self.max_log:
                # ↑ 如果日志太多（超过上限）……
                self._log = self._log[-self.max_log:]
                # ↑ 只保留最后 max_log 条（删掉最早的，省内存）。
        self._write()
        # ↑ 把最新状态写进文件，供可视化窗口读取。

    def log(self, msg):
        """追加一条任意日志（不切换当前节点）。"""
        # ↑ 定义 log（记日志）：只记一条日志，但不改变"当前节点"。
        self._log.append("[%s] %s" % (time.strftime("%H:%M:%S"), msg))
        # ↑ 把"[时间] 内容"追加进日志。
        if len(self._log) > self.max_log:
            # ↑ 日志超上限……
            self._log = self._log[-self.max_log:]
            # ↑ 只保留最后 max_log 条。
        self._write()
        # ↑ 写文件。

    def reset(self):
        # ↑ 定义 reset（重置）：清空所有追踪状态。
        self._log = []
        # ↑ 清空日志。
        self._visited = []
        # ↑ 清空已访问节点。
        self._current = None
        # ↑ 当前节点设回 None。
        self._write()
        # ↑ 写文件。

    def _write(self):
        # ↑ 定义 _write（写文件）：把当前状态保存到 flow_state.json。
        try:
            # ↑ try 保护：写文件可能失败，包一下（失败也不能影响主程序）。
            with _lock:
                # ↑ 用"锁"包住写操作，保证同一时刻只一个人写，不出错。
                data = {
                    # ↑ 准备要写入的数据字典：
                    "current": self._current,
                    # ↑ 当前节点。
                    "visited": self._visited,
                    # ↑ 已访问节点列表。
                    "log": self._log,
                    # ↑ 历史日志列表。
                }
                tmp = self.state_path + ".tmp"
                # ↑ 先写到一个临时文件（文件名加 .tmp）。
                with open(tmp, "w", encoding="utf-8") as f:
                    # ↑ 以写入、UTF-8 打开临时文件……
                    json.dump(data, f, ensure_ascii=False)
                    # ↑ 把数据写成 JSON（ensure_ascii=False 让中文正常）。
                os.replace(tmp, self.state_path)
                # ↑ 用临时文件"原子替换"正式文件：要么全成功、要么还是旧的，不会读到半截。

        except Exception:
            # ↑ 任何写入异常都忽略（追踪功能绝不能拖垮主程序）。
            pass


_tracer = None
# ↑ 全局变量：追踪器实例，初始 None（第一次用时才创建）。


def get_tracer():
    # ↑ 定义 get_tracer（取追踪器）：返回全局唯一的追踪器（懒加载，第一次才建）。
    global _tracer
    # ↑ 声明要修改全局变量 _tracer。
    if _tracer is None:
        # ↑ 如果还没创建……
        _tracer = FlowTracer()
        # ↑ 创建一个追踪器实例。
    return _tracer
    # ↑ 返回这个实例。


def mark(node_id, msg=None):
    # ↑ 定义 mark（对外接口）：主程序调用的就是它，记录进入某节点。
    try:
        # ↑ try 保护：追踪失败绝不能影响主程序。
        get_tracer().mark(node_id, msg)
        # ↑ 拿到追踪器并调用它的 mark 方法。
    except Exception:
        # ↑ 失败忽略。
        pass


def log(msg):
    # ↑ 定义 log（对外接口）：主程序用它记一条日志。
    try:
        # ↑ try 保护。
        get_tracer().log(msg)
        # ↑ 拿到追踪器并调用它的 log 方法。
    except Exception:
        # ↑ 失败忽略。
        pass


def reset():
    # ↑ 定义 reset（对外接口）：重置追踪状态。
    try:
        # ↑ try 保护。
        get_tracer().reset()
        # ↑ 调用追踪器的 reset。
    except Exception:
        # ↑ 失败忽略。
        pass


if __name__ == "__main__":
    # ↑ 固定写法：直接运行本文件（而非被 import）时才执行下面这段自测。
    # 自测：模拟一段执行轨迹，便于在没跑主程序时验证可视化
    # ↑ 注释：下面模拟一串步骤，方便不跑主程序也能测试可视化窗口。
    import time as _t
    # ↑ 导入 time 起个别名 _t（避免和外面的 time 冲突）。
    for nid, m in [
        # ↑ 准备一组"节点+说明"，模拟程序走一遍：
        ("main", "程序启动"),
        ("load_config", "读取配置"),
        ("setup_logging", "初始化日志"),
        ("load_state", "读取去重状态"),
        ("run_cycle", "一轮监控开始"),
        ("open_group", "打开微信群"),
        ("_find_and_click_in_sidebar", "侧栏匹配"),
        ("read_messages", "读取群消息"),
        ("extract_latest_question", "提取最新提问"),
        ("ask", "IMA 问答入口"),
        ("ensure_kb", "确保目标知识库"),
        ("_navigate_to_kb_chat", "导航到知识库问答"),
        ("_in_kb_chat_view", "判定在知识库问答"),
        ("_focus_input_box", "聚焦输入框"),
        ("_wait_real_answer", "轮询等待回答"),
        ("_capture_full_answer", "截取回答"),
        ("send_image", "回发回答截图"),
        ("save_state", "保存状态"),
    ]:
        mark(nid, m)
        # ↑ 依次标记每个节点。
        _t.sleep(0.4)
        # ↑ 每步停 0.4 秒，方便肉眼看可视化窗口逐个高亮。
    print("SELFTEST_DONE ->", STATE_PATH)
    # ↑ 打印自测完成提示，以及状态文件路径。
