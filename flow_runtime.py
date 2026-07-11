# -*- coding: utf-8 -*-
"""
运行时追踪器：被监控程序在每个关键函数里调用 mark(node_id, msg)，
把"当前节点 + 日志"写入 flow_state.json，供 flow_visualizer.py 实时读取高亮。

设计原则：
  * 全部操作包 try/except，即使文件写失败也绝不影响主程序运行。
  * 写文件用「临时文件 + os.replace」原子替换，避免可视化端读到半截 JSON。
  * 与可视化端通过文件解耦，两端可独立启停。
"""
import json
import os
import time
import threading

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flow_state.json")
_lock = threading.Lock()


class FlowTracer:
    def __init__(self, state_path=STATE_PATH, max_log=300):
        self.state_path = state_path
        self.max_log = max_log
        self._log = []
        self._visited = []
        self._current = None
        self._write()

    def mark(self, node_id, msg=None):
        """标记进入某个节点（函数）。msg 为可选的中文说明，会进入日志面板。"""
        self._current = node_id
        if node_id not in self._visited:
            self._visited.append(node_id)
        if msg:
            self._log.append("[%s] ▶ %s" % (time.strftime("%H:%M:%S"), msg))
            if len(self._log) > self.max_log:
                self._log = self._log[-self.max_log:]
        self._write()

    def log(self, msg):
        """追加一条任意日志（不切换当前节点）。"""
        self._log.append("[%s] %s" % (time.strftime("%H:%M:%S"), msg))
        if len(self._log) > self.max_log:
            self._log = self._log[-self.max_log:]
        self._write()

    def reset(self):
        self._log = []
        self._visited = []
        self._current = None
        self._write()

    def _write(self):
        try:
            with _lock:
                data = {
                    "current": self._current,
                    "visited": self._visited,
                    "log": self._log,
                }
                tmp = self.state_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, self.state_path)
        except Exception:
            pass


_tracer = None


def get_tracer():
    global _tracer
    if _tracer is None:
        _tracer = FlowTracer()
    return _tracer


def mark(node_id, msg=None):
    try:
        get_tracer().mark(node_id, msg)
    except Exception:
        pass


def log(msg):
    try:
        get_tracer().log(msg)
    except Exception:
        pass


def reset():
    try:
        get_tracer().reset()
    except Exception:
        pass


if __name__ == "__main__":
    # 自测：模拟一段执行轨迹，便于在没跑主程序时验证可视化
    import time as _t
    for nid, m in [
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
        _t.sleep(0.4)
    print("SELFTEST_DONE ->", STATE_PATH)
