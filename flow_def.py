# -*- coding: utf-8 -*-
"""
程序执行流程的节点与连线定义（用于流程图与动态高亮）。

节点 (id, 显示标签, phase行)：phase 越大越靠右（从左到右是执行顺序）。
连线 (from, to)：表示"下一步 / 调用关系"。
"""

NODES = [
    # 阶段0：启动与初始化
    ("main", "main()\n程序入口/参数解析", 0),
    ("load_config", "load_config\n读取配置", 0),
    ("setup_logging", "setup_logging\n初始化日志", 0),
    ("load_state", "load_state\n读取去重状态", 0),

    # 阶段1：进入监控循环
    ("run_cycle", "run_cycle\n一轮监控开始", 1),
    ("list_visible_groups", "list_visible_groups\n枚举可见会话", 1),

    # 阶段2：微信打开监控群
    ("open_group", "open_group\n打开监控群", 2),
    ("_find_and_click_in_sidebar", "_find_and_click_in_sidebar\n侧栏精确匹配点击", 2),
    ("_scroll_sidebar_find", "_scroll_sidebar_find\n滚动侧栏查找", 2),
    ("_via_contacts_open_group", "_via_contacts_open_group\n通讯录-群聊路线", 2),
    ("_find_and_click_anywhere", "_find_and_click_anywhere\n全窗模糊点击", 2),
    ("_via_search_suggestion", "_via_search_suggestion\n搜索建议打开", 2),
    ("_current_chat_title_matches", "_current_chat_title_matches\n校验当前标题", 2),
    ("_sidebar_still_shows", "_sidebar_still_shows\n校验侧栏仍显示", 2),

    # 阶段3：读取群消息
    ("read_messages", "read_messages\n读取群消息", 3),
    ("_read_via_ocr", "_read_via_ocr\nOCR 截取消息", 3),
    ("extract_latest_question", "extract_latest_question\n提取最新提问", 3),

    # 阶段4：IMA 问答入口与导航
    ("ask", "ask\nIMA 问答入口", 4),
    ("_ask_once", "_ask_once\n单次提问流程", 4),
    ("ensure_window_and_kb", "ensure_window_and_kb\n定位+确保知识库", 4),
    ("ensure_kb", "ensure_kb\n确保目标知识库", 4),
    ("_navigate_to_kb_chat", "_navigate_to_kb_chat\n导航到知识库问答", 4),

    # 阶段5：知识库导航子步骤
    ("_click_kb_by_norm", "_click_kb_by_norm\n模糊匹配知识库名", 5),
    ("_enter_kb_chat", "_enter_kb_chat\n进入问答视图", 5),
    ("_in_kb_chat_view", "_in_kb_chat_view\n判定是否在知识库问答", 5),

    # 阶段6：输入问题并等待回答
    ("_focus_input_box", "_focus_input_box\n聚焦输入框", 6),
    ("_verify_question_sent", "_verify_question_sent\n校验问题已发送", 6),
    ("_wait_real_answer", "_wait_real_answer\n轮询等待回答", 6),

    # 阶段7：截取并拼接回答
    ("_is_stale_answer", "_is_stale_answer\n过滤旧回答", 7),
    ("_capture_full_answer", "_capture_full_answer\n截取回答区域", 7),
    ("_stitch", "_stitch\n拼接长回答截图", 7),

    # 阶段8：回发与保存状态
    ("send_image", "send_image\n回发回答截图", 8),
    ("_copy_image_to_clipboard", "_copy_image_to_clipboard\n图片入剪贴板", 8),
    ("save_state", "save_state\n保存去重状态", 8),
]

EDGES = [
    ("main", "load_config"),
    ("load_config", "setup_logging"),
    ("setup_logging", "load_state"),
    ("load_state", "run_cycle"),
    ("run_cycle", "list_visible_groups"),
    ("list_visible_groups", "open_group"),
    # open_group 内部兜底链路
    ("open_group", "_find_and_click_in_sidebar"),
    ("_find_and_click_in_sidebar", "_scroll_sidebar_find"),
    ("_scroll_sidebar_find", "_via_contacts_open_group"),
    ("_via_contacts_open_group", "_find_and_click_anywhere"),
    ("_find_and_click_anywhere", "_via_search_suggestion"),
    ("_via_search_suggestion", "_current_chat_title_matches"),
    ("_current_chat_title_matches", "_sidebar_still_shows"),
    ("_sidebar_still_shows", "read_messages"),
    # 读取消息
    ("read_messages", "_read_via_ocr"),
    ("_read_via_ocr", "extract_latest_question"),
    ("extract_latest_question", "ask"),
    # IMA 问答
    ("ask", "_ask_once"),
    ("_ask_once", "ensure_window_and_kb"),
    ("ensure_window_and_kb", "ensure_kb"),
    ("ensure_kb", "_navigate_to_kb_chat"),
    ("_navigate_to_kb_chat", "_click_kb_by_norm"),
    ("_click_kb_by_norm", "_enter_kb_chat"),
    ("_enter_kb_chat", "_in_kb_chat_view"),
    ("_in_kb_chat_view", "_focus_input_box"),
    ("_focus_input_box", "_verify_question_sent"),
    ("_verify_question_sent", "_wait_real_answer"),
    ("_wait_real_answer", "_is_stale_answer"),
    ("_is_stale_answer", "_capture_full_answer"),
    ("_capture_full_answer", "_stitch"),
    ("_stitch", "send_image"),
    ("send_image", "_copy_image_to_clipboard"),
    ("_copy_image_to_clipboard", "save_state"),
    # 回到下一轮
    ("save_state", "run_cycle"),
]
