# -*- coding: utf-8 -*-
"""
流程图可视化（matplotlib 本地窗口，动态高亮执行过程）。

用法：
  python flow_visualizer.py            # 打开实时窗口，每 150ms 轮询 flow_state.json 高亮当前节点
  python flow_visualizer.py --static   # 不弹窗，直接导出一张静态流程图 PNG（flow_static.png）

与主程序（wechat_ima_monitor.py）完全解耦：它只往 flow_state.json 写状态，
本工具只读。两端可独立启停、顺序任意。
"""
import argparse
import json
import os
import sys

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from matplotlib.animation import FuncAnimation

from flow_def import NODES, EDGES

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flow_state.json")

node_ids = [n[0] for n in NODES]
label_of = {n[0]: n[1] for n in NODES}
phase_of = {n[0]: n[2] for n in NODES}

from collections import defaultdict
by_phase = defaultdict(list)
for nid, lab, ph in NODES:
    by_phase[ph].append(nid)
phases = sorted(by_phase.keys())

COL_GAP = 3.6
ROW_GAP = 1.0
BOX_W = 3.0
BOX_H = 0.72

col_x = {ph: i for i, ph in enumerate(phases)}
pos = {}
for ph in phases:
    lst = by_phase[ph]
    n = len(lst)
    for i, nid in enumerate(lst):
        x = col_x[ph] * COL_GAP
        y = (n - 1) / 2.0 - i
        pos[nid] = (x, y * ROW_GAP)

max_rows = max(len(v) for v in by_phase.values())
X_MIN = -BOX_W
X_MAX = (len(phases) - 1) * COL_GAP + BOX_W
Y_MIN = -max_rows / 2.0 * ROW_GAP - BOX_H
Y_MAX = max_rows / 2.0 * ROW_GAP + BOX_H

COLOR_CURRENT = "#ff7043"   # 当前节点（橙）
COLOR_VISITED = "#a5d6a7"  # 已走过（绿）
COLOR_PENDING = "#eceff1"   # 未到达（灰）
EDGE_COLOR = "#90a4ae"
EDGE_COLOR_ACTIVE = "#ef6c00"


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"current": None, "visited": [], "log": []}


def draw(ax, ax_log, state):
    current = state.get("current")
    visited = set(state.get("visited", []))

    ax.clear()
    ax.set_axis_off()
    ax.set_xlim(X_MIN, X_MAX)
    ax.set_ylim(Y_MIN, Y_MAX)

    # 连线
    for (a, b) in EDGES:
        if a not in pos or b not in pos:
            continue
        xa, ya = pos[a]
        xb, yb = pos[b]
        active = (b == current) or (a == current)
        ax.annotate(
            "",
            xy=(xb, yb), xytext=(xa, ya),
            arrowprops=dict(
                arrowstyle="-|>",
                color=EDGE_COLOR_ACTIVE if active else EDGE_COLOR,
                lw=1.6 if active else 1.0,
                connectionstyle="arc3,rad=0.04",
            ),
        )

    # 节点
    for nid in node_ids:
        if nid not in pos:
            continue
        x, y = pos[nid]
        if nid == current:
            color = COLOR_CURRENT
        elif nid in visited:
            color = COLOR_VISITED
        else:
            color = COLOR_PENDING
        box = FancyBboxPatch(
            (x - BOX_W / 2.0, y - BOX_H / 2.0), BOX_W, BOX_H,
            boxstyle="round,pad=0.02,rounding_size=0.18",
            linewidth=1.8, edgecolor="#455a64", facecolor=color,
        )
        ax.add_patch(box)
        ax.text(x, y, label_of[nid], ha="center", va="center",
                fontsize=7.2, color="#1a1a1a")

    # 标题 / 当前节点名
    title = "当前：%s" % (label_of.get(current, "（待机）") if current else "（待机）")
    ax.text(X_MIN + 0.2, Y_MAX - 0.2, title, fontsize=11, fontweight="bold",
            color="#e65100")

    # 图例
    legend = [
        (COLOR_CURRENT, "当前节点"),
        (COLOR_VISITED, "已执行"),
        (COLOR_PENDING, "未执行"),
    ]
    lx = X_MAX - 3.6
    ly = Y_MAX - 0.2
    for i, (c, t) in enumerate(legend):
        yy = ly - i * 0.45
        ax.add_patch(FancyBboxPatch((lx, yy - 0.12), 0.3, 0.24,
                       boxstyle="round,pad=0.02",
                       facecolor=c, edgecolor="#455a64"))
        ax.text(lx + 0.45, yy, t, fontsize=8, va="center")

    # 日志面板
    ax_log.clear()
    ax_log.set_axis_off()
    ax_log.set_xlim(0, 1)
    ax_log.set_ylim(0, 1)
    logs = state.get("log", [])[-26:]
    text = "\n".join(logs) if logs else "（暂无日志，启动 wechat_ima_monitor.py 后这里会实时滚动）"
    ax_log.text(0.0, 1.0, text, fontsize=8.2, va="top", ha="left",
                family="monospace", color="#263238",
                wrap=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--static", action="store_true", help="仅导出静态 PNG，不弹窗")
    args = ap.parse_args()

    fig = plt.figure(figsize=(17, 10))
    ax = fig.add_axes([0.02, 0.30, 0.96, 0.66])
    ax_log = fig.add_axes([0.02, 0.02, 0.96, 0.25])

    if args.static:
        draw(ax, ax_log, load_state())
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flow_static.png")
        fig.savefig(out, dpi=150, bbox_inches="tight")
        print("STATIC_PNG_SAVED ->", out)
        return

    def update(_frame):
        draw(ax, ax_log, load_state())

    ani = FuncAnimation(fig, update, interval=150, cache_frame_data=False)
    fig.suptitle("WeChat → IMA 监控程序 · 执行流程图（实时）", fontsize=13)
    plt.show()


if __name__ == "__main__":
    main()
