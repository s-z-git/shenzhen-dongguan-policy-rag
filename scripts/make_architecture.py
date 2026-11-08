"""生成 docs/architecture.png：离线索引 + 在线检索/生成/规则核验。"""
from __future__ import annotations

import pathlib
from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "architecture.png"

W, H = 2000, 1080
BG = "#F8FAFC"
INK = "#1F2937"
MUTED = "#64748B"

REG = "C:/Windows/Fonts/msyh.ttc"
BOLD = "C:/Windows/Fonts/msyhbd.ttc"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def box(draw, x, y, w, h, title, sub, fill, border, title_font, sub_font):
    draw.rounded_rectangle((x, y, x + w, y + h), radius=16, fill=fill, outline=border, width=3)
    tw = draw.textbbox((0, 0), title, font=title_font)
    tw = tw[2] - tw[0]
    draw.text((x + (w - tw) / 2, y + 20), title, font=title_font, fill=INK)
    if sub:
        sw = draw.textbbox((0, 0), sub, font=sub_font)
        sw = sw[2] - sw[0]
        draw.text((x + (w - sw) / 2, y + 58), sub, font=sub_font, fill=MUTED)


def arrow(draw, x1, y, x2, color=MUTED, width=3):
    draw.line((x1, y, x2 - 10, y), fill=color, width=width)
    draw.polygon([(x2, y), (x2 - 14, y - 7), (x2 - 14, y + 7)], fill=color)


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f_title = font(BOLD, 46)
    f_sub = font(REG, 22)
    f_lane = font(BOLD, 26)
    f_box = font(BOLD, 22)
    f_box_sub = font(REG, 18)
    f_legend = font(REG, 21)

    d.text((90, 42), "深莞人社政策问答 · RAG 架构", font=f_title, fill=INK)
    d.text((92, 104), "离线索引  +  在线分析/混合检索/生成/规则核验", font=f_sub, fill=MUTED)

    # 离线 lane
    d.rounded_rectangle((60, 160, 1940, 400), radius=20, fill="#F1F6FB", outline="#D8E5F2", width=2)
    d.text((90, 178), "离线构建（一次性）", font=f_lane, fill="#315A7D")
    off = [
        ("458 份政策", "深圳 268 / 东莞 190", "#E8F0F8", "#4A78A8"),
        ("条款切分", "+ 元数据", "#E8F0F8", "#4A78A8"),
        ("4583 块", "chunk_id / 条款路径", "#E8F0F8", "#4A78A8"),
        ("FAISS / BM25", "向量 + 关键词索引", "#E8F0F8", "#4A78A8"),
    ]
    ox, oy, ow, oh = 180, 225, 320, 105
    for i, (t, s, fill, border) in enumerate(off):
        x = ox + i * 430
        box(d, x, oy, ow, oh, t, s, fill, border, f_box, f_box_sub)
        if i < len(off) - 1:
            arrow(d, x + ow + 8, oy + oh / 2, x + 430 - 8)

    # 在线 lane
    d.rounded_rectangle((60, 460, 1940, 720), radius=20, fill="#F3F8F3", outline="#D8E8D8", width=2)
    d.text((90, 478), "在线问答（每次请求）", font=f_lane, fill="#3E6A45")
    online = [
        ("问题", "用户输入", "#E8F0F8", "#4A78A8"),
        ("查询分析", "城市/年份/子问题", "#E8F0F8", "#4A78A8"),
        ("混合检索", "向量 + BM25", "#EAF4EA", "#4F8A5B"),
        ("RRF 融合", "去重 + 合并", "#EAF4EA", "#4F8A5B"),
        ("精排", "Cross-Encoder", "#EAF4EA", "#4F8A5B"),
        ("生成", "DeepSeek", "#F3EAF8", "#7A4E9E"),
        ("规则核验", "引用 / 数字 / 时效", "#FFF4E5", "#D9822B"),
        ("UI 输出", "结论 + 出处 + 徽章", "#E8F3FF", "#2F80ED"),
    ]
    bx, by, bw, bh, gap = 145, 525, 198, 112, 27
    for i, (t, s, fill, border) in enumerate(online):
        x = bx + i * (bw + gap)
        box(d, x, by, bw, bh, t, s, fill, border, f_box, f_box_sub)
        if i < len(online) - 1:
            arrow(d, x + bw + 4, by + bh / 2, x + bw + gap - 4)

    # 离线索引到在线检索的虚线提示
    d.line((1780, 400, 1780, 432), fill="#D9822B", width=3)
    d.line((1780, 432, 1050, 432), fill="#D9822B", width=3)
    d.line((1050, 432, 1050, 460), fill="#D9822B", width=3)
    d.text((1100, 410), "索引供在线检索", font=f_box_sub, fill="#B36B1F")

    # 图例
    d.text((90, 785), "橙色 = 生成后规则核验与时效/废止提示（不是让模型自己保证）", font=f_legend, fill="#8A4B12")
    d.rounded_rectangle((90, 825, 120, 855), radius=6, fill="#FFF4E5", outline="#D9822B", width=2)
    d.text((132, 823), "规则核验 / 时效提示", font=f_legend, fill=INK)
    d.rounded_rectangle((390, 825, 420, 855), radius=6, fill="#E8F0F8", outline="#4A78A8", width=2)
    d.text((432, 823), "查询分析与检索", font=f_legend, fill=INK)
    d.rounded_rectangle((680, 825, 710, 855), radius=6, fill="#EAF4EA", outline="#4F8A5B", width=2)
    d.text((722, 823), "混合检索 / RRF / 精排", font=f_legend, fill=INK)
    d.rounded_rectangle((1040, 825, 1070, 855), radius=6, fill="#F3EAF8", outline="#7A4E9E", width=2)
    d.text((1082, 823), "生成 + UI", font=f_legend, fill=INK)
    d.text((90, 930), "关键约束：出处必须能回到本轮检索条款；数字必须能在原文找到；时效提示由规则层兜底。",
           font=f_sub, fill=MUTED)
    d.text((90, 975), "458 份 → 条款切分 + 元数据 → 4583 块 → FAISS/BM25 → 在线混合检索 → 规则核验",
           font=f_sub, fill=MUTED)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG")
    print(str(OUT))


if __name__ == "__main__":
    main()
