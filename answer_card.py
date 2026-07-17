# -*- coding: utf-8 -*-
# ↑ 这一行告诉 Python：本文件用 UTF-8 编码保存，这样中文注释不会乱码。

"""把「问题 + IMA 知识库检索结果」渲染成一张中文答案卡片图片。

用途：微信群里有人提问 -> 程序从 IMA 知识库检索到相关段落 ->
用本模块把"问题 + 答案段落 + 来源"画成一张图 -> 作为图片回复到微信群。

依赖：Pillow。中文字体优先用 Windows 自带的微软雅黑（msyh.ttc），
找不到时退化为默认字体（可能缺字形，仅作兜底）。
"""
# ↑ 三引号包起来的是「模块说明」（docstring），解释这个文件是干嘛的：
#   它的工作像"做海报"——把"群友问了什么"和"知识库里找到的答案"拼成一张漂亮图片。
#   为什么要做成图片？因为微信里直接发大段文字排版难看，做成卡片图发出去既整齐又清晰。
#   做图用的是 Pillow 这个图像处理库；中文要显示正常，得用带中文字形的字体（如微软雅黑）。

import os   # 导入 os：处理文件路径、判断字体文件是否存在

from PIL import Image, ImageDraw, ImageFont
# ↑ 从 Pillow 库导入三样画图的工具：
#   Image     = 画布（用来新建/保存一张图）
#   ImageDraw = 画笔（在画布上写字、画方块）
#   ImageFont = 字体（决定文字长什么样、多大）


# Windows 上常见中文字体候选（按顺序尝试）
# ↑ 下面这个列表，是"可能的中文字体文件"的候选清单（按优先顺序排）。
FONT_CANDIDATES = [
    # ↑ 定义一个全局列表 FONT_CANDIDATES，里面是几个常见中文字体在电脑上的路径。
    "C:/Windows/Fonts/msyh.ttc",      # 微软雅黑（常规），最常见的 Windows 中文默认字体
    "C:/Windows/Fonts/msyhbd.ttc",    # 微软雅黑（粗体）
    "C:/Windows/Fonts/simhei.ttf",    # 黑体（SIMHEI）
    "C:/Windows/Fonts/simsun.ttc",    # 宋体（SIMSUN）
    "C:/Windows/Fonts/SourceHanSansSC-Regular.otf",  # 思源黑体（某些系统会装）
]


def load_font(size):
    # ↑ 定义一个函数 load_font（加载字体），参数 size 是想要的字体大小（单位：像素）。
    for p in FONT_CANDIDATES:
        # ↑ 挨个尝试候选字体路径：从最优先的微软雅黑开始，逐一下去。
        if os.path.exists(p):
            # ↑ 如果电脑上这个字体文件确实存在……
            try:
                # ↑ try 保护：加载字体这步偶尔也可能失败（文件损坏等），包一下。
                return ImageFont.truetype(p, size)
                # ↑ 用这个字体文件、指定大小，加载成一个字体对象并返回。
                #   找到第一个能用的就立刻返回，后面的候选不再试（省时间）。
            except Exception:
                # ↑ 如果加载失败，就跳过这个，继续尝试下一个候选字体。
                continue
    return ImageFont.load_default()
    # ↑ 如果候选列表里一个都没成功（连中文字体都没有），就用 Pillow 自带的默认字体兜底。
    #   注意：默认字体通常不含中文字形，中文可能显示成方块，但至少程序不会崩。


def wrap_cjk(text, font, max_width):
    """按像素宽度折行，支持中英文混排（逐字判断）。"""
    # ↑ 定义一个函数 wrap_cjk（按宽度折行），专门解决"一行太长要换行"的问题。
    #   参数：text = 原始文字；font = 用哪个字体（用于测量每个字多宽）；
    #        max_width = 一行最多能多宽（超过就换行）。
    #   为什么要自己做换行？因为 Pillow 不会自动换行，得我们手动按"像素宽度"算在哪里断行。
    lines = []
    # ↑ 准备一个空列表 lines，用来装"折好的一行行文字"，最后返回它。
    for para in (text or "").split("\n"):
        # ↑ 先把整段文字按"换行符"切成若干"段落"（para 是一段）。
        #   (text or "") 是保险：万一 text 是空(None)，就当成空字符串，避免报错。
        if not para:
            # ↑ 如果这一段是空的（原文本里有个空行）……
            lines.append("")
            # ↑ 就在结果里也加一个空行，保持原有段落间距。
            continue
            # ↑ 处理完空段，跳到下一个段落。
        cur = ""
        # ↑ cur（current 的缩写）= "当前正在凑的这一行文字"，初始为空。
        for ch in para:
            # ↑ 把这一段文字"一个字一个字"拆开，逐个处理（中英文混排也能逐字判断）。
            test = cur + ch
            # ↑ 试着把当前这个字 ch 接到正在凑的行 cur 后面，组成 test。
            try:
                # ↑ try 保护：测量文字宽度的函数偶尔可能异常，包一下。
                w = font.getlength(test)
                # ↑ 测量"如果这一行变成 test，它有多宽（像素）"。getlength 是 Pillow 量宽度的功能。
            except Exception:
                # ↑ 万一测量失败，就用一个粗略估算代替：每个字按"字号×0.6"算宽。
                w = len(test) * (font.size * 0.6)
                # ↑ len(test) = 字数；font.size = 字号；×0.6 是经验系数，凑个近似值。
            if w <= max_width:
                # ↑ 如果加上这个字后，整行宽度还没超过上限 max_width……
                cur = test
                # ↑ 就保留它：把 ch 正式接进当前行 cur。
            else:
                # ↑ 否则（加这个字就超宽了）……
                if cur:
                    # ↑ 只要当前行不是空的，就先把已有的内容存为一行。
                    lines.append(cur)
                    # ↑ 把凑好的这一行 cur 加进结果列表。
                cur = ch
                # ↑ 然后让"新的一行"从这个超宽的字 ch 重新开始。
        lines.append(cur)
        # ↑ 一个段落的所有字都处理完后，把最后凑着的一行也加进结果（别漏掉末尾）。
    return lines
    # ↑ 返回折好行的列表，每行都是"不会超过最大宽度"的一串文字。


def _round_rect(d, box, radius, fill):
    # ↑ 定义一个内部函数 _round_rect（画圆角矩形），给画卡片时的"气泡框"用。
    #   参数：d = 画笔；box = 矩形位置[左,上,右,下]；radius = 圆角半径；
    #        fill = 填充颜色。
    try:
        # ↑ try 保护：新版本 Pillow 支持圆角矩形，但老版本可能没有，包一下。
        d.rounded_rectangle(box, radius=radius, fill=fill)
        # ↑ 用画笔 d 画一个"圆角矩形"：位置 box、圆角半径 radius、填充色 fill。
    except Exception:
        # ↑ 如果当前 Pillow 版本太老、不支持圆角……
        d.rectangle(box, fill=fill)
        # ↑ 就退而求其次，画一个普通的直角矩形（功能不变，只是边角是尖的）。


def build_answer_card(question, passages, source_label="知识库",
                      out_path="answer_card.png", max_chars_per_passage=300,
                      accent="#3a6ea5"):
    """渲染答案卡片并返回图片路径。

    question: 群里的原始提问文本
    passages: [{"title":..., "content":...}, ...]
    """
    # ↑ 定义主函数 build_answer_card（制作答案卡片），它把问答画成一张图。
    #   参数说明：
    #     question          = 群友问的原始问题文字
    #     passages          = 知识库检索到的相关段落列表，每个段落是 {"title":标题, "content":正文}
    #     source_label      = 卡片顶部标签文字，默认"知识库"
    #     out_path          = 生成的图片存到哪里，默认当前目录的 answer_card.png
    #     max_chars_per_passage = 每段正文最多显示多少字（太长就截断），默认 300
    #     accent            = 强调色（标题、装饰条的颜色），默认一种蓝
    title_font = load_font(26)   # 顶部标题用的字体，大小 26
    q_font = load_font(22)       # 问题文字用的字体，大小 22
    a_font = load_font(22)       # 答案正文用的字体，大小 22
    src_font = load_font(16)     # 来源/小标题用的字体，大小 16
    foot_font = load_font(15)    # 页脚用的字体，大小 15

    W = 760                 # 卡片的总宽度（像素），固定 760 宽
    margin = 36             # 卡片四周留白（边距），上下左右都空 36 像素
    inner_w = W - 2 * margin  # 卡片内部"可写字区域"的宽度 = 总宽减去左右两个边距
    line_h_title = 36       # 标题每行的高度（行高）
    line_h = 32             # 问题/答案每行的行高
    line_h_src = 24         # 来源小字每行的行高
    gap_section = 18        # 不同区块之间的间距

    header_lines = wrap_cjk(source_label, title_font, inner_w)
    # ↑ 把顶部标签文字按宽度折行，得到"标题行"列表，用标题字体、限制在内部宽度内。
    q_lines = wrap_cjk("问：" + (question or ""), q_font, inner_w)
    # ↑ 把"问：+问题"折行，得到"问题行"列表。question or "" 防止问题为空时报错。

    pas_blocks = []
    # ↑ 准备一个空列表 pas_blocks，用来装"每一段相关知识"整理后的显示块。
    for p in passages or []:
        # ↑ 遍历每段相关知识（passages or [] 防止 passages 为空时报错）。
        title = (p.get("title") or "").strip()
        # ↑ 取出这段的标题，没有就当空字符串，并去掉首尾空格。
        content = (p.get("content") or "").strip()
        # ↑ 取出这段的正文，没有就当空字符串，并去掉首尾空格。
        if max_chars_per_passage and len(content) > max_chars_per_passage:
            # ↑ 如果设置了"每段最多字数"，且这段正文确实超长了……
            content = content[:max_chars_per_passage] + "…"
            # ↑ 就只保留前面那么多字，末尾加个省略号"…"，表示"后面还有、截断了"。
        block = []
        # ↑ 为这一段新建一个空显示块 block（里面会放若干行：标题行 + 正文行）。
        if title:
            # ↑ 如果这段有标题，就把标题作为一行加进 block，并标记为"src"（来源类样式）。
            block.append(("src", title))
        for ln in wrap_cjk(content, a_font, inner_w):
            # ↑ 把正文按宽度折成多行，逐行处理。
            block.append(("ans", ln))
            # ↑ 每一行作为"ans"（答案类样式）加进 block。
        if block:
            # ↑ 只要这一段整理出了内容（block 非空）……
            pas_blocks.append(block)
            # ↑ 就把这一整块加进总列表 pas_blocks。

    # 先算高度
    # ↑ 下面这段是"先计算卡片应该多高"，因为高度取决于内容行数，得先算清楚再建画布。
    H = margin
    # ↑ 卡片总高度 H 从顶部边距开始算起。
    H += line_h_title * len(header_lines) + 12
    # ↑ 加上"标题区"高度：每行标题高 line_h_title，共几行就乘几，再多 12 像素间距。
    H += gap_section
    # ↑ 再加一段区块间距。
    qbox_h = line_h * len(q_lines) + 16
    # ↑ 先算出"问题气泡框"的高度 qbox_h：问题行数×行高 + 内部上下各 8（共16）留白。
    H += qbox_h + gap_section
    # ↑ 总高度加上"问题框高度 + 区块间距"。
    for blk in pas_blocks:
        # ↑ 遍历每一段相关知识的显示块，累加它们占的高度。
        for kind, _ in blk:
            # ↑ 看这一块里的每一行：kind 是"src"还是"ans"，决定用哪种行高。
            H += line_h if kind == "ans" else line_h_src
            # ↑ 答案行用 line_h，来源行用 line_h_src，逐行把高度加上去。
        H += gap_section
        # ↑ 每一段结束，加一段区块间距。
    H += line_h_src + margin
    # ↑ 最后加上页脚那一行高度 + 底部边距。
    H = max(H, 240)
    # ↑ 保证卡片至少高 240 像素（内容太少时也不会窄得难看）。

    img = Image.new("RGB", (W, H), "white")
    # ↑ 正式建一张空白画布 img：RGB 彩色模式、尺寸 (宽 W, 高 H)、底色白色。
    d = ImageDraw.Draw(img)
    # ↑ 拿一支"画笔" d，后面所有写字、画框都用这支笔在 img 上操作。

    # 顶部强调条
    # ↑ 下面先画卡片最顶上那条装饰色带（强调条）。
    d.rectangle([0, 0, W, 10], fill=accent)
    # ↑ 用画笔在画布顶部画一个矩形：从左到右铺满(W)，高 10 像素，填充成强调色 accent。

    y = margin
    # ↑ 设定一个"当前纵坐标 y"，从顶部边距开始往下排内容；y 会随内容不断下移。
    for ln in header_lines:
        # ↑ 逐行画出顶部标签（如"知识库"）。
        d.text((margin, y), ln, font=title_font, fill=accent)
        # ↑ 在坐标 (左边距, 当前 y) 写这行字，用标题字体、强调色。
        y += line_h_title
        # ↑ 写完一行，y 下移一行标题的高度，准备写下一行。
    y += 12
    # ↑ 标题区结束，再下移 12 像素留点空隙。

    # 问题气泡
    # ↑ 下面画"问题气泡"：把问题文字放进一个淡蓝色圆角框里，像聊天软件的气泡。
    qbox_top = y
    # ↑ 记下问题框顶部的 y 坐标，方便下面画框和填字。
    _round_rect(d, [margin, qbox_top, W - margin, qbox_top + qbox_h], 12, "#EEF3FA")
    # ↑ 画一个圆角矩形作为气泡底：位置从左边距到右边距、高度 qbox_h、圆角半径 12、淡蓝底。
    yy = qbox_top + 8
    # ↑ 气泡内部文字的起始 y：比框顶再往下 8 像素（框内上留白）。
    for ln in q_lines:
        # ↑ 逐行写出问题文字。
        d.text((margin + 14, yy), ln, font=q_font, fill="#222222")
        # ↑ 在 (左边距+14, yy) 写字：左边多缩进 14 让文字不贴边；深灰色 #222222。
        yy += line_h
        # ↑ 写完一行，气泡内 y 下移一行高。
    y = qbox_top + qbox_h + gap_section
    # ↑ 问题区结束，把总 y 移到"气泡底 + 区块间距"，准备画答案区。

    # 答案段落（来源资料列表）
    # 若段落含正文则展示"标题+正文"；若只有标题（如音频开示无正文），
    # 则以项目符号列出相关开示/资料名称，诚实指向知识库原文。
    # ↑ 上面这段注释说明答案区的设计原则：有正文就显示标题+正文；
    #   只有标题（比如一条音频开示没有文字稿）就只列标题，并诚实标注"这是来源名"。
    d.text((margin, y), "相关开示 / 资料", font=src_font, fill=accent)
    # ↑ 先写一个小标题"相关开示 / 资料"，用来源字体、强调色。
    y += line_h_src + 4
    # ↑ y 下移一行来源字高 + 4 像素间距。
    for blk in pas_blocks:
        # ↑ 遍历每一段相关知识的显示块。
        for kind, ln in blk:
            # ↑ 取出这一块的每一行：kind 是"src"(标题)还是"ans"(正文)，ln 是文字。
            if kind == "src":
                # ↑ 如果是标题行……
                d.text((margin + 6, y), "• " + ln, font=src_font, fill="#5a5a5a")
                # ↑ 在前面加个圆点"• "当作项目符号，用来源字体、灰色，缩进 6 像素。
                y += line_h_src
                # ↑ y 下移一行来源字高。
            else:
                # ↑ 否则是正文行……
                d.text((margin + 6, y), ln, font=a_font, fill="#1a1a1a")
                # ↑ 直接写正文，用答案字体、近黑色，同样缩进 6 像素。
                y += line_h
                # ↑ y 下移一行答案字高。
        y += 8
        # ↑ 一段结束，再下移 8 像素，和下一个来源块拉开距离。

    # 页脚
    # ↑ 最后画卡片底部的"免责/说明"小字。
    d.text((margin, y), "以上为知识库中相关开示 / 资料，供参考。",
           font=foot_font, fill="#b0b0b0")
    # ↑ 在底部写一句灰色浅字，表明"内容来自知识库、仅供参考"，用页脚字体、浅灰色。

    try:
        # ↑ try 保护：保存图片这步可能因为没写权限而失败，包一下。
        img.save(out_path)
        # ↑ 把画好的卡片图存到 out_path 指定的路径。
    except Exception as e:
        # ↑ 如果保存失败（比如没权限写到那个目录）……
        # 某些环境无写权限，退一步存到当前目录
        # ↑ 下面换个更保险的位置：存到"本文件所在目录"，通常都有写权限。
        fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.path.basename(out_path))
        # ↑ 拼一个兜底路径：取 out_path 的文件名（basename），放到"本模块所在目录"。
        img.save(fallback)
        # ↑ 把图存到这个兜底位置。
        out_path = fallback
        # ↑ 同时把 out_path 更新成实际存的位置，保证最后返回的路径是"真的存好的那个"。
    return out_path
    # ↑ 把"最终图片存到哪了"这个路径返回给调用者，方便它拿去发到微信群。
