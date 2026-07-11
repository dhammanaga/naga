# -*- coding: utf-8 -*-
"""
一次性插桩脚本：给三个源文件的关键函数开头插入 `mark("函数名", "中文说明")`，
使主程序在运行时把执行轨迹写入 flow_state.json，供 flow_visualizer.py 实时高亮。

只插入一行、不改动任何现有逻辑。
  python _instrument.py        # 先 --clean 再插桩
  python _instrument.py --clean  # 仅移除所有插桩（含 import），还原到原始状态

注意：def 签名可能跨多行（如 `def f(\n    x):`），因此正则匹配到 `):` 为止，
把 mark 插在完整签名之后，避免被塞进参数列表导致语法错误。
"""
import re
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

TARGETS = {
    "wechat_ima_monitor.py": {
        "main": "程序启动/参数解析",
        "load_config": "读取配置",
        "setup_logging": "初始化日志",
        "load_state": "读取去重状态",
        "run_cycle": "一轮监控开始",
        "_read_via_ocr": "OCR 截取消息区",
        "extract_latest_question": "提取最新提问",
        "save_state": "保存去重状态",
    },
    "wechat_ocr.py": {
        "list_visible_groups": "枚举可见会话",
        "open_group": "打开监控群",
        "_find_and_click_in_sidebar": "侧栏精确匹配点击",
        "_scroll_sidebar_find": "滚动侧栏查找",
        "_via_contacts_open_group": "通讯录-群聊路线",
        "_find_and_click_anywhere": "全窗模糊点击",
        "_via_search_suggestion": "搜索建议打开",
        "_current_chat_title_matches": "校验当前聊天标题",
        "_sidebar_still_shows": "校验侧栏仍显示群",
        "read_messages": "读取群消息",
        "send_image": "回发回答截图",
        "_copy_image_to_clipboard": "图片写入剪贴板",
    },
    "ima_ocr.py": {
        "ask": "IMA 问答入口",
        "_ask_once": "单次提问流程",
        "ensure_window_and_kb": "定位窗口+确保知识库",
        "ensure_kb": "确保目标知识库",
        "_navigate_to_kb_chat": "导航到知识库问答",
        "_click_kb_by_norm": "模糊匹配知识库名",
        "_enter_kb_chat": "进入问答视图",
        "_in_kb_chat_view": "判定是否在知识库问答",
        "_focus_input_box": "聚焦输入框",
        "_verify_question_sent": "校验问题已发送",
        "_wait_real_answer": "轮询等待回答",
        "_is_stale_answer": "过滤旧回答",
        "_capture_full_answer": "截取回答区域",
        "_stitch": "拼接长回答截图",
    },
}

IMPORT_LINE = "from flow_runtime import mark  # AUTO-INSTRUMENTED"
MARK_TAG = "AUTO-INSTRUMENTED"


def clean(path):
    full = os.path.join(HERE, path)
    with open(full, encoding="utf-8") as f:
        lines = f.readlines()
    kept = [ln for ln in lines if MARK_TAG not in ln]
    with open(full, "w", encoding="utf-8") as f:
        f.writelines(kept)
    print("  cleaned:", path, "(removed %d lines)" % (len(lines) - len(kept)))


def instrument_file(path, funcs):
    full = os.path.join(HERE, path)
    with open(full, encoding="utf-8") as f:
        text = f.read()

    # 在最后一个 import 行之后插入 import flow_runtime
    lines = text.split("\n")
    insert_at = 0
    for i, ln in enumerate(lines):
        if re.match(r"^(import|from)\s", ln):
            insert_at = i + 1
    text = "\n".join(lines[:insert_at] + [IMPORT_LINE] + lines[insert_at:])

    done = 0
    for fn, msg in funcs.items():
        if ('mark("%s"' % fn) in text:
            continue
        # 匹配完整 def 签名（含跨多行），到 `):` 为止
        pat = re.compile(
            r"^(?P<ind>[ \t]*)def\s+" + re.escape(fn) + r"\s*\(.*?\):[ \t]*\n",
            re.DOTALL | re.M,
        )
        m = pat.search(text)
        if not m:
            print("  WARN: 未找到函数定义:", fn, "in", path)
            continue
        ind = m.group("ind")
        ins = '%smark("%s", "%s")  # %s' % (ind + "    ", fn, msg, MARK_TAG)
        text = text[: m.end()] + ins + "\n" + text[m.end():]
        done += 1

    with open(full, "w", encoding="utf-8") as f:
        f.write(text)
    print("  instrumented %s: %d functions" % (path, done))


def main():
    do_clean = "--clean" in sys.argv
    if do_clean:
        for fpath in TARGETS:
            clean(fpath)
        print("CLEAN_DONE")
        if len(sys.argv) == 1 or (len(sys.argv) == 2 and do_clean):
            return
    for fpath, fns in TARGETS.items():
        print("=>", fpath)
        instrument_file(fpath, fns)
    print("ALL_DONE")


if __name__ == "__main__":
    main()
