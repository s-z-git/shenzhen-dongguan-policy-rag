"""
app.ingest.clause_splitter —— 多级编号条款切分

对应 DESIGN.md 的 app/ingest/clause_splitter.py 与 §4.1。

【与 DESIGN 的偏差 —— 必须能解释】
DESIGN 假设政策正文是「第X条」结构。实测 458 份真实文件后发现只对了一半：
    含「第X条」的只有 234 份（51%），共 7256 次
    其余编号形态：（一）6994 / 1．1627 / 一、1341 /（1）423
所以这里做【多级编号识别 + 分层切分 + 二次切分 + 碎片合并】，
而不是单一「第X条」正则。
"""
from __future__ import annotations

import hashlib
import re

from app.config import DEFAULT, Config

NL = chr(10)  # 换行符

# 层级从粗到细。不同文件的“主层级”不同，靠递归切分自适应。
# L1 最粗：××条 / 一、 等大层级编号
L1 = [
    re.compile(r"^\s*第[一二三四五六七八九十百零〇\d]{1,4}条\s*"),
    re.compile(r"^\s*[一二三四五六七八九十]{1,3}\s*、"),
]
# L2 次粗：（一） 等括号层级编号
L2 = [re.compile(r"^\s*[（(][一二三四五六七八九十]{1,3}[)）]")]
# L3 最细：1．/ (1) 等数字层级编号
L3 = [
    re.compile(r"^\s*\d{1,2}\s*[．.、]\s*"),
    re.compile(r"^\s*[（(]\s*\d{1,2}\s*[)）]"),
]
# 按 level 1/2/3 索引的全部层级模式
_LEVELS = (L1, L2, L3)


def _marker_of(line: str, level: int) -> str | None:
    """判断该行属于第几级编号；返回去掉首尾空白的编号文本，没有编号返回 None。
    识别失败时返回 None，而不是空字符串，以区分“没有编号”和“空行”。"""
    # 取对应层级的全部编号正则模式逐个匹配
    for pat in _LEVELS[level - 1]:  # level 从 1 开始，_LEVELS 索引从 0 起
        m = pat.match(line)         # 从行首匹配编号
        if m:
            return m.group(0).strip()  # 命中：返回去掉首尾空白的编号文本
    return None                      # 所有模式都未命中，判定为非编号行


def split_at_level(text: str, level: int) -> list[tuple[str, str]]:
    """按指定层级切分，返回 [(marker, block)]；首个 marker 之前的内容 marker 为 ''。

    据此层级（如标题/条款层级）将文本切分为多个逻辑块，
    每个块附带其起始标记 marker（如条款编号文本）。
    """
    # parts：临时块列表，元素为 (marker, 该块所有行组成的列表)
    # cur_marker：当前块的标记（编号文本）；cur_lines：当前块累积的行
    parts: list[tuple[str, list[str]]] = []
    cur_marker, cur_lines = None, []

    # 逐行扫描
    for ln in text.splitlines():
        # 判断当前行是否为该层级的标记行
        mk = _marker_of(ln, level)

        if mk:
            # 遇到新标记行：先把已累积的上一块保存（若有），并补空标记为 ""
            if cur_lines:
                parts.append((cur_marker or "", cur_lines))  # 保存上一个块
            # 开启新块：以当前标记行为块首
            cur_marker, cur_lines = mk, [ln]                 # 开始新块
        else:
            # 非标记行，累积到当前块中
            cur_lines.append(ln)

    # 扫尾：最后一块若仍有内容也保存（标记可能是 None，统一转为 ""）
    if cur_lines:
        parts.append((cur_marker or "", cur_lines))

    # 将每个块的行列表拼接成整段字符串，并去掉首尾空白行，
    # 避免生成的 chunk 中出现多余的空白/换行
    out = []
    for mk, ls in parts:
        blk = NL.join(ls).strip()        # 用换行连接各行，再去除首尾空白
        if blk:                          # 跳过完全空白的块
            out.append((mk, blk))

    # 返回 [(标记, 块内容)] 列表
    return out


def _window(text: str, max_chars: int, overlap: int) -> list[str]:
    """最细层级仍超长时的兜底：按空行切段，贪心装箱，单段超长则硬切。"""
    # 按“空行”切段（NL + 任意空白 + NL），过滤掉纯空白段（如多余空行）
    paras = [p for p in re.split(NL + r"\s*" + NL, text) if p.strip()]
    # pieces：最终结果列表；buf：当前正在累积的“箱子”
    pieces, buf = [], ""
    for p in paras:
        if len(p) > max_chars:                    # 情况一：单段本身超长
            # 先把已累积的 buf 收尾入列，避免与超长段混箱
            if buf:
                pieces.append(buf)
                buf = ""
            # 硬切：步长 = max_chars - overlap，相邻两片重叠 overlap 个字符，
            # 避免语义被截断丢失上下文；步长下限为 1 防止死循环
            step = max(max_chars - overlap, 1)
            for i in range(0, len(p), step):
                pieces.append(p[i:i + max_chars])  # 每次取一个 max_chars 宽度的窗口
            continue                               # 本段处理完，进入下一段
        # 情况二：正常段，尝试装入 buf；+1 表示两段之间用 NL 连接占用的 1 字符
        if len(buf) + len(p) + 1 <= max_chars:
            buf = (buf + NL + p) if buf else p    # 首段不加多余前缀 NL，其余用 NL 隔开
        else:
            pieces.append(buf)                     # 装不下则产出当前箱，另起新箱
            buf = p
    # 扫尾：循环结束后 buf 若仍有未产出内容，收尾加入结果
    if buf:
        pieces.append(buf)
    return pieces


def _chunk_block(text: str, cfg: Config, path: str, level: int, emit) -> None:
    """递归切分一个块：能切就往下切一级，切不动就按长度滑窗兜底。
    path 是条款路径（如 第二条 > （一） > 3．），会一路带进 chunk 元数据，最终变成回答里的出处。"""
    text = text.strip()
    if not text:
        return
    # 本块已不超长：直接产出，不再下钻
    if len(text) <= cfg.chunk_max_chars:
        emit(path, text)
        return
    # 层级还没到底（还有更细层级的编号可识别），尝试再切一级
    if level <= 3:
        parts = split_at_level(text, level)
        # 切出 2 段以上才算“切得动”；只有 1 段说明这一级没有编号，继续降级
        if len(parts) >= 2:
            for mk, blk in parts:
                # 拼接子路径：path + " > " + mk；处理空 path / 空 mk 的边界
                child = (path + " > " + mk) if (path and mk) else (mk or path)
                # 递归下钻一级处理子块
                _chunk_block(blk, cfg, child, level + 1, emit)
            return
    # 已到最细层级仍超长，用长度滑窗兜底切分并逐片产出
    for piece in _window(text, cfg.chunk_max_chars, cfg.window_overlap):
        emit(path, piece)


def _merge_short(items: list[tuple[str, str]], cfg: Config) -> list[tuple[str, str]]:
    """过短碎片：第 1 遍并入上一块；第 2 遍仍过短的并入下一块（文档开头的前缀）。"""
    # 第 1 遍：过短且能并入上一块时，并入上一块；否则保留
    out: list[tuple[str, str]] = []
    for path, text in items:
        if out and len(text) < cfg.chunk_min_chars:
            p_prev, t_prev = out[-1]
            # 并入当前块仍不超限才合并（+2 为两段间 NL 等连接符）
            if len(t_prev) + len(text) + 2 <= cfg.chunk_max_chars:
                out[-1] = (p_prev, t_prev + NL + text)
                continue
        out.append((path, text))

    # 第 2 遍：第 1 遍后仍过短的，尝试并入下一块（如文档开头的短前缀）
    res: list[tuple[str, str]] = []
    i = 0
    while i < len(out):
        path, text = out[i]
        if i + 1 < len(out) and len(text) < cfg.chunk_min_chars:
            n_path, n_text = out[i + 1]
            # 与下一块合并且不超限则合并，并跳过下一块
            if len(text) + len(n_text) + 2 <= cfg.chunk_max_chars:
                res.append((path or n_path, text + NL + n_text))
                i += 2
                continue
        res.append((path, text))
        i += 1
    return res


def chunk_body(body: str, cfg: Config | None = None) -> list[tuple[str, str]]:
    """正文 -> [(clause_path, chunk_text)]，已去重。"""
    cfg = cfg or DEFAULT            # 未显式传配置时使用默认配置
    items: list[tuple[str, str]] = []
    # 先按 L1（最粗层级）切出各大块，再对每块递归下钻
    for mk, blk in split_at_level(body, 1):
        # lambda 收集生成的 (路径, 文本) 到 items
        _chunk_block(blk, cfg, mk, 2, lambda p, t: items.append((p, t)))
    # 合并过短碎片（并入前/后块）
    items = _merge_short(items, cfg)

    # 按文本哈希去重，保留首次出现的顺序
    seen, uniq = set(), []
    for path, text in items:
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if h in seen:
            continue               # 已出现过，跳过
        seen.add(h)
        uniq.append((path, text))
    return uniq