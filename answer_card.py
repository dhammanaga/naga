# -*- coding: utf-8 -*-
"""把「问题 + IMA 知识库检索结果」渲染成一张中文答案卡片图片。

用途：微信群里有人提问 -> 程序从 IMA 知识库检索到相关段落 ->
用本模块把"问题 + 答案段落 + 来源"画成一张图 -> 作为图片回复到微信群。

依赖：Pillow。中文字体优先用 Windows 自带的微软雅黑（msyh.ttc），
找不到时退化为默认字体（可能缺字形，仅作兜底）。
"""
import os

from PIL import Image, ImageDraw, ImageFont

# Windows 上常见中文字体候选（按顺序尝试）
FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/SourceHanSansSC-Regular.otf",
]


def load_font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def wrap_cjk(text, font, max_width):
    """按像素宽度折行，支持中英文混排（逐字判断）。"""
    lines = []
    for para in (text or "").split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            test = cur + ch
            try:
                w = font.getlength(test)
            except Exception:
                w = len(test) * (font.size * 0.6)
            if w <= max_width:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines


def _round_rect(d, box, radius, fill):
    try:
        d.rounded_rectangle(box, radius=radius, fill=fill)
    except Exception:
        d.rectangle(box, fill=fill)


def build_answer_card(question, passages, source_label="知识库",
                      out_path="answer_card.png", max_chars_per_passage=300,
                      accent="#3a6ea5"):
    """渲染答案卡片并返回图片路径。

    question: 群里的原始提问文本
    passages: [{"title":..., "content":...}, ...]
    """
    title_font = load_font(26)
    q_font = load_font(22)
    a_font = load_font(22)
    src_font = load_font(16)
    foot_font = load_font(15)

    W = 760
    margin = 36
    inner_w = W - 2 * margin
    line_h_title = 36
    line_h = 32
    line_h_src = 24
    gap_section = 18

    header_lines = wrap_cjk(source_label, title_font, inner_w)
    q_lines = wrap_cjk("问：" + (question or ""), q_font, inner_w)

    pas_blocks = []
    for p in passages or []:
        title = (p.get("title") or "").strip()
        content = (p.get("content") or "").strip()
        if max_chars_per_passage and len(content) > max_chars_per_passage:
            content = content[:max_chars_per_passage] + "…"
        block = []
        if title:
            block.append(("src", title))
        for ln in wrap_cjk(content, a_font, inner_w):
            block.append(("ans", ln))
        if block:
            pas_blocks.append(block)

    # 先算高度
    H = margin
    H += line_h_title * len(header_lines) + 12
    H += gap_section
    qbox_h = line_h * len(q_lines) + 16
    H += qbox_h + gap_section
    for blk in pas_blocks:
        for kind, _ in blk:
            H += line_h if kind == "ans" else line_h_src
        H += gap_section
    H += line_h_src + margin
    H = max(H, 240)

    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    # 顶部强调条
    d.rectangle([0, 0, W, 10], fill=accent)

    y = margin
    for ln in header_lines:
        d.text((margin, y), ln, font=title_font, fill=accent)
        y += line_h_title
    y += 12

    # 问题气泡
    qbox_top = y
    _round_rect(d, [margin, qbox_top, W - margin, qbox_top + qbox_h], 12, "#EEF3FA")
    yy = qbox_top + 8
    for ln in q_lines:
        d.text((margin + 14, yy), ln, font=q_font, fill="#222222")
        yy += line_h
    y = qbox_top + qbox_h + gap_section

    # 答案段落（来源资料列表）
    # 若段落含正文则展示"标题+正文"；若只有标题（如音频开示无正文），
    # 则以项目符号列出相关开示/资料名称，诚实指向知识库原文。
    d.text((margin, y), "相关开示 / 资料", font=src_font, fill=accent)
    y += line_h_src + 4
    for blk in pas_blocks:
        for kind, ln in blk:
            if kind == "src":
                d.text((margin + 6, y), "• " + ln, font=src_font, fill="#5a5a5a")
                y += line_h_src
            else:
                d.text((margin + 6, y), ln, font=a_font, fill="#1a1a1a")
                y += line_h
        y += 8

    # 页脚
    d.text((margin, y), "以上为知识库中相关开示 / 资料，供参考。",
           font=foot_font, fill="#b0b0b0")

    try:
        img.save(out_path)
    except Exception as e:
        # 某些环境无写权限，退一步存到当前目录
        fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.path.basename(out_path))
        img.save(fallback)
        out_path = fallback
    return out_path
