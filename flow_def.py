# -*- coding: utf-8 -*-
# ↑ 这一行告诉 Python：本文件用 UTF-8 编码保存，这样中文注释不会乱码。

"""
程序执行流程的节点与连线定义（用于流程图与动态高亮）。

节点 (id, 显示标签, phase行)：phase 越大越靠右（从左到右是执行顺序）。
连线 (from, to)：表示"下一步 / 调用关系"。
"""
# ↑ 三引号是「模块说明」（docstring）：讲清这个文件定义了"流程图的数据"——
#   把程序里每一步拆成"节点"，再用"连线"表示先后关系，供可视化窗口画流程图用。

NODES = [
    # ↑ NODES 是一个列表，里面每一项是一个"节点"（程序里的一个步骤）。
    # 阶段0：启动与初始化
    # ↑ 注释：下面这些是"启动阶段"的节点（最先执行）。
    ("main", "main()\n程序入口/参数解析", 0),
    # ↑ 节点1：程序入口 main()（解析命令行参数）。
    ("load_config", "load_config\n读取配置", 0),
    # ↑ 节点2：读取配置文件。
    ("setup_logging", "setup_logging\n初始化日志", 0),
    # ↑ 节点3：初始化日志系统。
    ("load_state", "load_state\n读取去重状态", 0),
    # ↑ 节点4：读取"已回答记录"状态。

    # 阶段1：进入监控循环
    # ↑ 注释：阶段1：开始一轮轮监控。
    ("run_cycle", "run_cycle\n一轮监控开始", 1),
    # ↑ 节点5：一轮监控的入口。
    ("list_visible_groups", "list_visible_groups\n枚举可见会话", 1),
    # ↑ 节点6：列出可见的微信会话。

    # 阶段2：微信打开监控群
    # ↑ 注释：阶段2：打开要监控的微信群。
    ("open_group", "open_group\n打开监控群", 2),
    # ↑ 节点7：打开群。
    ("_find_and_click_in_sidebar", "_find_and_click_in_sidebar\n侧栏精确匹配点击", 2),
    # ↑ 节点8：在侧栏里找群名并点。
    ("_scroll_sidebar_find", "_scroll_sidebar_find\n滚动侧栏查找", 2),
    # ↑ 节点9：滚动侧栏继续找。
    ("_via_contacts_open_group", "_via_contacts_open_group\n通讯录-群聊路线", 2),
    # ↑ 节点10：走通讯录→群聊找。
    ("_find_and_click_anywhere", "_find_and_click_anywhere\n全窗模糊点击", 2),
    # ↑ 节点11：全窗找群名点。
    ("_via_search_suggestion", "_via_search_suggestion\n搜索建议打开", 2),
    # ↑ 节点12：搜索建议打开群。
    ("_current_chat_title_matches", "_current_chat_title_matches\n校验当前标题", 2),
    # ↑ 节点13：校验标题栏是不是这个群。
    ("_sidebar_still_shows", "_sidebar_still_shows\n校验侧栏仍显示", 2),
    # ↑ 节点14：校验侧栏是否仍显示该群。

    # 阶段3：读取群消息
    # ↑ 注释：阶段3：读取群里的消息。
    ("read_messages", "read_messages\n读取群消息", 3),
    # ↑ 节点15：读取消息。
    ("_read_via_ocr", "_read_via_ocr\nOCR 截取消息", 3),
    # ↑ 节点16：用 OCR 截取消息（注：实际代码里 read_messages 内联了 OCR，这里作示意）。
    ("extract_latest_question", "extract_latest_question\n提取最新提问", 3),
    # ↑ 节点17：从消息里提取最新提问。

    # 阶段4：IMA 问答入口与导航
    # ↑ 注释：阶段4：让 IMA 回答（进入问答、导航知识库）。
    ("ask", "ask\nIMA 问答入口", 4),
    # ↑ 节点18：IMA 问答的总入口。
    ("_ask_once", "_ask_once\n单次提问流程", 4),
    # ↑ 节点19：单次提问的完整流程。
    ("ensure_window_and_kb", "ensure_window_and_kb\n定位+确保知识库", 4),
    # ↑ 节点20：确保 IMA 窗口 + 进入知识库。
    ("ensure_kb", "ensure_kb\n确保目标知识库", 4),
    # ↑ 节点21：确保进入目标知识库。
    ("_navigate_to_kb_chat", "_navigate_to_kb_chat\n导航到知识库问答", 4),
    # ↑ 节点22：导航到知识库问答视图。

    # 阶段5：知识库导航子步骤
    # ↑ 注释：阶段5：导航知识库的内部小步骤。
    ("_click_kb_by_norm", "_click_kb_by_norm\n模糊匹配知识库名", 5),
    # ↑ 节点23：模糊匹配并点击知识库名。
    ("_enter_kb_chat", "_enter_kb_chat\n进入问答视图", 5),
    # ↑ 节点24：点击入口进入问答视图。
    ("_in_kb_chat_view", "_in_kb_chat_view\n判定是否在知识库问答", 5),
    # ↑ 节点25：判断当前是否在知识库问答视图。

    # 阶段6：输入问题并等待回答
    # ↑ 注释：阶段6：输入问题、等回答。
    ("_focus_input_box", "_focus_input_box\n聚焦输入框", 6),
    # ↑ 节点26：聚焦输入框。
    ("_verify_question_sent", "_verify_question_sent\n校验问题已发送", 6),
    # ↑ 节点27：校验问题已发送。
    ("_wait_real_answer", "_wait_real_answer\n轮询等待回答", 6),
    # ↑ 节点28：轮询等待 IMA 生成回答。

    # 阶段7：截取并拼接回答
    # ↑ 注释：阶段7：把回答截成图并拼接。
    ("_is_stale_answer", "_is_stale_answer\n过滤旧回答", 7),
    # ↑ 节点29：过滤掉陈旧旧答案。
    ("_capture_full_answer", "_capture_full_answer\n截取回答区域", 7),
    # ↑ 节点30：滚动截取完整回答。
    ("_stitch", "_stitch\n拼接长回答截图", 7),
    # ↑ 节点31：拼接长回答图（备用旧法）。

    # 阶段8：回发与保存状态
    # ↑ 注释：阶段8：把答案发回群、保存状态。
    ("send_image", "send_image\n回发回答截图", 8),
    # ↑ 节点32：把答案图发回微信群。
    ("_copy_image_to_clipboard", "_copy_image_to_clipboard\n图片入剪贴板", 8),
    # ↑ 节点33：把图片写入剪贴板。
    ("save_state", "save_state\n保存去重状态", 8),
    # ↑ 节点34：保存"已回答"状态（去重用）。
]

EDGES = [
    # ↑ EDGES 是一个列表，里面每一项是一条"连线"（表示"下一步去哪"）。
    ("main", "load_config"),
    # ↑ 连线：main → load_config（启动后读配置）。
    ("load_config", "setup_logging"),
    # ↑ 连线：读配置 → 初始化日志。
    ("setup_logging", "load_state"),
    # ↑ 连线：初始化日志 → 读状态。
    ("load_state", "run_cycle"),
    # ↑ 连线：读状态 → 开始一轮监控。
    ("run_cycle", "list_visible_groups"),
    # ↑ 连线：一轮监控 → 列可见会话。
    ("list_visible_groups", "open_group"),
    # ↑ 连线：列会话 → 打开群。
    # open_group 内部兜底链路
    # ↑ 注释：下面几条是 open_group 内部"依次尝试"的兜底链路。
    ("open_group", "_find_and_click_in_sidebar"),
    # ↑ 连线：开群 → 侧栏点（先试这个）。
    ("_find_and_click_in_sidebar", "_scroll_sidebar_find"),
    # ↑ 连线：侧栏点失败 → 滚动找。
    ("_scroll_sidebar_find", "_via_contacts_open_group"),
    # ↑ 连线：滚动失败 → 通讯录路线。
    ("_via_contacts_open_group", "_find_and_click_anywhere"),
    # ↑ 连线：通讯录路线 → 全窗点。
    ("_find_and_click_anywhere", "_via_search_suggestion"),
    # ↑ 连线：全窗点 → 搜索建议。
    ("_via_search_suggestion", "_current_chat_title_matches"),
    # ↑ 连线：搜索建议 → 校验标题。
    ("_current_chat_title_matches", "_sidebar_still_shows"),
    # ↑ 连线：校验标题 → 校验侧栏。
    ("_sidebar_still_shows", "read_messages"),
    # ↑ 连线：校验侧栏 → 读消息。
    # 读取消息
    # ↑ 注释：下面几条是"读消息"的链路。
    ("read_messages", "_read_via_ocr"),
    # ↑ 连线：读消息 → OCR 截取。
    ("_read_via_ocr", "extract_latest_question"),
    # ↑ 连线：OCR 截取 → 提取提问。
    ("extract_latest_question", "ask"),
    # ↑ 连线：提取提问 → 让 IMA 回答。
    # IMA 问答
    # ↑ 注释：下面几条是"IMA 问答"的链路。
    ("ask", "_ask_once"),
    # ↑ 连线：问答入口 → 单次提问。
    ("_ask_once", "ensure_window_and_kb"),
    # ↑ 连线：单次提问 → 确保窗口+知识库。
    ("ensure_window_and_kb", "ensure_kb"),
    # ↑ 连线：确保窗口 → 确保知识库。
    ("ensure_kb", "_navigate_to_kb_chat"),
    # ↑ 连线：确保知识库 → 导航到问答。
    ("_navigate_to_kb_chat", "_click_kb_by_norm"),
    # ↑ 连线：导航 → 模糊点知识库名。
    ("_click_kb_by_norm", "_enter_kb_chat"),
    # ↑ 连线：模糊点 → 进入问答视图。
    ("_enter_kb_chat", "_in_kb_chat_view"),
    # ↑ 连线：进入问答 → 判断是否在问答视图。
    ("_in_kb_chat_view", "_focus_input_box"),
    # ↑ 连线：判断 → 聚焦输入框。
    ("_focus_input_box", "_verify_question_sent"),
    # ↑ 连线：聚焦 → 校验已发送。
    ("_verify_question_sent", "_wait_real_answer"),
    # ↑ 连线：校验 → 等待回答。
    ("_wait_real_answer", "_is_stale_answer"),
    # ↑ 连线：等待 → 过滤陈旧答案。
    ("_is_stale_answer", "_capture_full_answer"),
    # ↑ 连线：过滤 → 截取完整回答。
    ("_capture_full_answer", "_stitch"),
    # ↑ 连线：截取 → 拼接（备用旧法）。
    ("_stitch", "send_image"),
    # ↑ 连线：拼接 → 发回群。
    ("send_image", "_copy_image_to_clipboard"),
    # ↑ 连线：发图 → 图片入剪贴板。
    ("_copy_image_to_clipboard", "save_state"),
    # ↑ 连线：入剪贴板 → 保存状态。
    # 回到下一轮
    # ↑ 注释：下面这条连线让流程"回到下一轮"，形成循环。
    ("save_state", "run_cycle"),
    # ↑ 连线：保存状态 → 再跑一轮（循环）。
]
