# -*- coding: utf-8 -*-
# ↑ 这一行告诉 Python：本文件用 UTF-8 编码保存，这样中文注释不会乱码。

"""
流程图可视化（matplotlib 本地窗口，动态高亮执行过程）。

用法：
  python flow_visualizer.py            # 打开实时窗口，每 150ms 轮询 flow_state.json 高亮当前节点
  python flow_visualizer.py --static   # 不弹窗，直接导出一张静态流程图 PNG（flow_static.png）

与主程序（wechat_ima_monitor.py）完全解耦：它只往 flow_state.json 写状态，
本工具只读。两端可独立启停、顺序任意。
"""
# ↑ 三引号是「模块说明」（docstring）：讲清这个文件是"流程图可视化窗口"——
#   它读 flow_state.json（主程序写的），把流程图画出来，并实时高亮当前执行的步骤。
import argparse      # 导入 argparse：解析命令行参数（如 --static）
import json          # 导入 json：读取状态 JSON 文件
import os            # 导入 os：处理文件路径
import sys           # 导入 sys：系统相关

import matplotlib
matplotlib.use("TkAgg")
# ↑ 设置 matplotlib 用 TkAgg 后端（在本地弹出一个图形窗口）。
import matplotlib.pyplot as plt
# ↑ 导入 matplotlib 的 pyplot（画图主工具），起别名 plt。
# 让窗口中的中文标签/日志正常显示（Windows 自带微软雅黑/黑体）
# ↑ 注释：下面设置中文字体，让图里的中文不乱码。
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun", "Arial Unicode MS"]
# ↑ 指定候选中文字体（按顺序尝试，有哪个用哪个）。
plt.rcParams["axes.unicode_minus"] = False
# ↑ 关闭"负号显示成方块"的问题（和中文显示相关）。
from matplotlib.patches import FancyBboxPatch
# ↑ 导入 FancyBboxPatch（画圆角矩形框，用来画流程节点）。
from matplotlib.animation import FuncAnimation
# ↑ 导入 FuncAnimation（定时刷新动画，用来做"实时高亮"）。

from flow_def import NODES, EDGES
# ↑ 从 flow_def 引入节点列表和连线列表（流程图的数据）。

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flow_state.json")
# ↑ 状态文件路径：和主程序、追踪器共用同一个 flow_state.json。

node_ids = [n[0] for n in NODES]
# ↑ 从 NODES 里取出所有节点的 id 列表。
label_of = {n[0]: n[1] for n in NODES}
# ↑ 建立"节点id → 显示标签"的字典（画图时显示标签）。
phase_of = {n[0]: n[2] for n in NODES}
# ↑ 建立"节点id → 阶段号"的字典（决定节点画在哪一列）。

from collections import defaultdict
# ↑ 导入 defaultdict（带默认值的字典，方便按阶段分组）。
by_phase = defaultdict(list)
# ↑ 准备一个"阶段 → 节点列表"的分组字典。
for nid, lab, ph in NODES:
    # ↑ 遍历每个节点（id, 标签, 阶段）……
    by_phase[ph].append(nid)
    # ↑ 把它加进对应阶段的列表。
phases = sorted(by_phase.keys())
# ↑ 把所有阶段号排序（从左到右的顺序）。

COL_GAP = 3.6
# ↑ 列间距（不同阶段之间的水平距离）。
ROW_GAP = 1.0
# ↑ 行间距（同阶段内节点之间的垂直距离）。
BOX_W = 3.0
# ↑ 节点框宽度。
BOX_H = 0.72
# ↑ 节点框高度。

col_x = {ph: i for i, ph in enumerate(phases)}
# ↑ 建立"阶段 → 列号"的字典（第几个阶段 = 第几列）。
pos = {}
# ↑ 准备"节点id → 坐标(x,y)"的字典（最终每个节点画在哪）。
for ph in phases:
    # ↑ 遍历每个阶段（列）……
    lst = by_phase[ph]
    # ↑ 取出这个阶段里的节点列表。
    n = len(lst)
    # ↑ 节点个数。
    for i, nid in enumerate(lst):
        # ↑ 遍历这个阶段里的每个节点（i 是行序号）……
        x = col_x[ph] * COL_GAP
        # ↑ 横坐标 = 列号 × 列间距。
        y = (n - 1) / 2.0 - i
        # ↑ 纵坐标：让这一列的节点上下居中排列。
        pos[nid] = (x, y * ROW_GAP)
        # ↑ 记下这个节点的坐标。

max_rows = max(len(v) for v in by_phase.values())
# ↑ 算出"节点最多的那一列"有多少行（用来算画布高度）。
X_MIN = -BOX_W
# ↑ 画布左边边界。
X_MAX = (len(phases) - 1) * COL_GAP + BOX_W
# ↑ 画布右边边界。
Y_MIN = -max_rows / 2.0 * ROW_GAP - BOX_H
# ↑ 画布下边边界。
Y_MAX = max_rows / 2.0 * ROW_GAP + BOX_H
# ↑ 画布上边边界。

COLOR_CURRENT = "#ff7043"   # 当前节点（橙）
# ↑ 当前节点颜色：橙色。
COLOR_VISITED = "#a5d6a7"  # 已走过（绿）
# ↑ 已访问节点颜色：绿色。
COLOR_PENDING = "#eceff1"   # 未到达（灰）
# ↑ 未到达节点颜色：灰色。
EDGE_COLOR = "#90a4ae"
# ↑ 普通连线颜色：灰蓝。
EDGE_COLOR_ACTIVE = "#ef6c00"
# ↑ 当前激活的连线颜色：橙色。


def load_state():
    # ↑ 定义 load_state（加载状态）：读取 flow_state.json 返回当前执行状态。
    try:
        # ↑ try 保护：读文件可能失败。
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            # ↑ 以只读、UTF-8 打开……
            return json.load(f)
            # ↑ 读成字典返回。
    except Exception:
        # ↑ 读失败（比如主程序还没启动）……
        return {"current": None, "visited": [], "log": []}
        # ↑ 返回"空状态"（待机）。


def draw(ax, ax_log, state):
    # ↑ 定义 draw（绘制一帧）：根据当前状态把流程图和日志画出来。
    current = state.get("current")
    # ↑ 取出"当前节点"id。
    visited = set(state.get("visited", []))
    # ↑ 取出"已访问"节点集合（用 set 方便判断"在不在里面"）。

    ax.clear()
    # ↑ 清空主画布（每帧重画）。
    ax.set_axis_off()
    # ↑ 关掉坐标轴（流程图不需要坐标轴）。
    ax.set_xlim(X_MIN, X_MAX)
    # ↑ 设置横向显示范围。
    ax.set_ylim(Y_MIN, Y_MAX)
    # ↑ 设置纵向显示范围。

    # 连线
    # ↑ 注释：下面先画所有"连线"（箭头）。
    for (a, b) in EDGES:
        # ↑ 遍历每条连线（从节点 a 到节点 b）……
        if a not in pos or b not in pos:
            # ↑ 如果 a 或 b 没有坐标（数据异常）……
            continue
            # ↑ 跳过这条线。
        xa, ya = pos[a]
        # ↑ 取出起点 a 的坐标。
        xb, yb = pos[b]
        # ↑ 取出终点 b 的坐标。
        active = (b == current) or (a == current)
        # ↑ 判断这条线是否"与当前节点相关"（高亮用）。
        ax.annotate(
            # ↑ 画一条带箭头的注释线（即连线）……
            "",
            # ↑ 文字为空（只画箭头，不写字）。
            xy=(xb, yb), xytext=(xa, ya),
            # ↑ 箭头终点 b、起点 a。
            arrowprops=dict(
                # ↑ 箭头样式设置：
                arrowstyle="-|>",
                # ↑ 箭头形状（实心三角）。
                color=EDGE_COLOR_ACTIVE if active else EDGE_COLOR,
                # ↑ 当前相关线用橙色，否则灰蓝。
                lw=1.6 if active else 1.0,
                # ↑ 当前相关线粗一点。
                connectionstyle="arc3,rad=0.04",
                # ↑ 线稍微带点弧度，更好看。
            ),
        )

    # 节点
    # ↑ 注释：下面画所有"节点"（圆角框 + 文字）。
    for nid in node_ids:
        # ↑ 遍历每个节点……
        if nid not in pos:
            # ↑ 没坐标就跳过。
            continue
        x, y = pos[nid]
        # ↑ 取出节点坐标。
        if nid == current:
            # ↑ 如果是当前节点……
            color = COLOR_CURRENT
            # ↑ 用橙色。
        elif nid in visited:
            # ↑ 否则如果已经走过……
            color = COLOR_VISITED
            # ↑ 用绿色。
        else:
            # ↑ 否则（还没走到）……
            color = COLOR_PENDING
            # ↑ 用灰色。
        box = FancyBboxPatch(
            # ↑ 创建一个圆角矩形框：
            (x - BOX_W / 2.0, y - BOX_H / 2.0), BOX_W, BOX_H,
            # ↑ 左上角坐标 + 宽高（让框中心在 (x,y)）。
            boxstyle="round,pad=0.02,rounding_size=0.18",
            # ↑ 圆角样式。
            linewidth=1.8, edgecolor="#455a64", facecolor=color,
            # ↑ 边框颜色 + 填充颜色（color 变量决定的橙/绿/灰）。
        )
        ax.add_patch(box)
        # ↑ 把框加到画布上。
        ax.text(x, y, label_of[nid], ha="center", va="center",
                fontsize=7.2, color="#1a1a1a")
        # ↑ 在框中心写节点标签文字（中文，居中）。

    # 标题 / 当前节点名
    # ↑ 注释：下面画顶部标题，显示"当前是哪个节点"。
    title = "当前：%s" % (label_of.get(current, "（待机）") if current else "（待机）")
    # ↑ 标题文字：当前节点标签，或"（待机）"。
    ax.text(X_MIN + 0.2, Y_MAX - 0.2, title, fontsize=11, fontweight="bold",
            color="#e65100")
    # ↑ 在左上角画标题（橙色加粗）。

    # 图例
    # ↑ 注释：下面画右下角的"图例"（说明三种颜色含义）。
    legend = [
        # ↑ 图例列表：(颜色, 文字)……
        (COLOR_CURRENT, "当前节点"),
        (COLOR_VISITED, "已执行"),
        (COLOR_PENDING, "未执行"),
    ]
    lx = X_MAX - 3.6
    # ↑ 图例左上角 x。
    ly = Y_MAX - 0.2
    # ↑ 图例左上角 y。
    for i, (c, t) in enumerate(legend):
        # ↑ 遍历图例每一项……
        yy = ly - i * 0.45
        # ↑ 每项往下排。
        ax.add_patch(FancyBboxPatch((lx, yy - 0.12), 0.3, 0.24,
                       boxstyle="round,pad=0.02",
                       facecolor=c, edgecolor="#455a64"))
        # ↑ 画一个小色块。
        ax.text(lx + 0.45, yy, t, fontsize=8, va="center")
        # ↑ 在色块右边写说明文字。

    # 日志面板
    # ↑ 注释：下面画右侧下方的"日志面板"，滚动显示主程序记的日志。
    ax_log.clear()
    # ↑ 清空日志画布。
    ax_log.set_axis_off()
    # ↑ 关掉坐标轴。
    ax_log.set_xlim(0, 1)
    # ↑ 设置横向范围（0~1，无所谓，只为定位）。
    ax_log.set_ylim(0, 1)
    # ↑ 设置纵向范围。
    logs = state.get("log", [])[-26:]
    # ↑ 取最近 26 条日志。
    text = "\n".join(logs) if logs else "（暂无日志，启动 wechat_ima_monitor.py 后这里会实时滚动）"
    # ↑ 把日志拼成一段文字；没有就显示提示语。
    ax_log.text(0.0, 1.0, text, fontsize=8.2, va="top", ha="left",
                color="#263238",
                wrap=True)
    # ↑ 在日志面板左上角写日志文字（顶部对齐、自动换行）。


def main():
    # ↑ 定义 main（主函数）：可视化窗口的入口。
    ap = argparse.ArgumentParser()
    # ↑ 创建命令行参数解析器。
    ap.add_argument("--static", action="store_true", help="仅导出静态 PNG，不弹窗")
    # ↑ 参数 --static：只导出一张静态图，不弹窗口。
    args = ap.parse_args()
    # ↑ 解析命令行。

    fig = plt.figure(figsize=(17, 10))
    # ↑ 创建一张 17×10 英寸的画布。
    ax = fig.add_axes([0.02, 0.30, 0.96, 0.66])
    # ↑ 在主画布上方 66% 区域放流程图（左边距2%、宽96%、从30%高到顶部）。
    ax_log = fig.add_axes([0.02, 0.02, 0.96, 0.25])
    # ↑ 在下方 25% 区域放日志面板。

    if args.static:
        # ↑ 如果加了 --static（只要静态图）……
        draw(ax, ax_log, load_state())
        # ↑ 用当前状态画一帧。
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flow_static.png")
        # ↑ 拼出静态图输出路径。
        fig.savefig(out, dpi=150, bbox_inches="tight")
        # ↑ 保存为 PNG（分辨率150，紧凑裁剪）。
        print("STATIC_PNG_SAVED ->", out)
        # ↑ 打印保存路径。
        return
        # ↑ 直接结束（不弹窗）。

    def update(_frame):
        # ↑ 定义 update（每帧刷新）：动画每 150ms 调用一次，重画当前状态。
        draw(ax, ax_log, load_state())
        # ↑ 重新读取状态并重画。

    ani = FuncAnimation(fig, update, interval=150, cache_frame_data=False)
    # ↑ 创建动画：每 150 毫秒调用一次 update（实时轮询状态）。
    fig.suptitle("WeChat → IMA 监控程序 · 执行流程图（实时）", fontsize=13)
    # ↑ 设置窗口总标题。
    plt.show()
    # ↑ 弹出图形窗口（阻塞，直到用户关闭窗口）。


if __name__ == "__main__":
    # ↑ 固定写法：直接运行本文件时才执行 main()。
    main()
    # ↑ 启动可视化窗口。
