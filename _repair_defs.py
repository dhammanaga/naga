# -*- coding: utf-8 -*-
"""
修复脚本：第一次(坏掉的)插桩把多行 def 的 `):` 续行合并进了注释，
又被 --clean 删除，导致这些 def 永远缺少 `):` 而语法错误。

本脚本只把 `def X(\n` 收敛成正确的单行 `def X(args):\n`，
函数体/文档串原样保留。每处替换断言恰好发生 1 次。
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

REPAIRS = {
    "wechat_ocr.py": [
        ("    def list_visible_groups(\n", "    def list_visible_groups(self):\n"),
        ("    def open_group(\n", "    def open_group(self, name):\n"),
        ("    def _find_and_click_in_sidebar(\n", "    def _find_and_click_in_sidebar(self, name, save_debug=True):\n"),
        ("    def _scroll_sidebar_find(\n", "    def _scroll_sidebar_find(self, name, max_down_rounds=8):\n"),
        ("    def _via_contacts_open_group(\n", "    def _via_contacts_open_group(self, name):\n"),
        ("    def _find_and_click_anywhere(\n", "    def _find_and_click_anywhere(self, name):\n"),
        ("    def _via_search_suggestion(\n", "    def _via_search_suggestion(self, name):\n"),
        ("    def _current_chat_title_matches(\n", "    def _current_chat_title_matches(self, name, save_debug=True):\n"),
        ("    def _sidebar_still_shows(\n", "    def _sidebar_still_shows(self, name):\n"),
        ("    def read_messages(\n", "    def read_messages(self):\n"),
        ("    def send_image(\n", "    def send_image(self, image_path):\n"),
        ("    def _copy_image_to_clipboard(\n", "    def _copy_image_to_clipboard(path):\n"),
    ],
    "ima_ocr.py": [
        ("    def _in_kb_chat_view(\n", "    def _in_kb_chat_view(self, img):\n"),
        ("    def ensure_window_and_kb(\n", "    def ensure_window_and_kb(self):\n"),
        ("    def ensure_kb(\n", "    def ensure_kb(self):\n"),
        ("    def _navigate_to_kb_chat(\n", "    def _navigate_to_kb_chat(self):\n"),
        ("    def _click_kb_by_norm(\n", "    def _click_kb_by_norm(self, img):\n"),
        ("    def _enter_kb_chat(\n", "    def _enter_kb_chat(self, img):\n"),
        ("    def _focus_input_box(\n", "    def _focus_input_box(self, img):\n"),
        ("    def _verify_question_sent(\n", "    def _verify_question_sent(self, question):\n"),
        ("    def _wait_real_answer(\n", "    def _wait_real_answer(self, pre_text, question):\n"),
        ("    def _is_stale_answer(\n", "    def _is_stale_answer(self, post, question):\n"),
        ("    def ask(\n", "    def ask(self, question, out_path=None):\n"),
        ("    def _ask_once(\n", "    def _ask_once(self, question, out_path):\n"),
        ("    def _capture_full_answer(\n", "    def _capture_full_answer(self):\n"),
        ("    def _stitch(\n", "    def _stitch(shots):\n"),
    ],
    "wechat_ima_monitor.py": [
        ("def load_config(\n", "def load_config(path):\n"),
        ("def setup_logging(\n", "def setup_logging(cfg):\n"),
        ("def load_state(\n", "def load_state():\n"),
        ("def save_state(\n", "def save_state(state):\n"),
        ("def extract_latest_question(\n", "def extract_latest_question(texts, det):\n"),
        ("def run_cycle(\n", "def run_cycle(cfg, ima, wechat, state, dry_run):\n"),
        ("def main(\n", "def main():\n"),
    ],
}


def main():
    for fname, reps in REPAIRS.items():
        path = os.path.join(HERE, fname)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for old, new in reps:
            cnt = text.count(old)
            if cnt != 1:
                print("  ERROR: %s 中 '%s' 出现 %d 次（应为1）" % (fname, old.strip(), cnt))
                raise SystemExit(1)
            text = text.replace(old, new, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print("  repaired %s: %d 处" % (fname, len(reps)))
    print("REPAIR_DONE")


if __name__ == "__main__":
    main()
