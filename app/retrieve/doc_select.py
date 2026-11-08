"""多子问题检索的文档级选择与“按问题类型选块”。

背景：政策文件常把“对象 / 条件 / 标准 / 期限 / 项目”分散在同一文档的不同块。
只按正文 BM25 取一个块，容易选到“申请材料”或“前言”。这里用问题意图匹配
小标题/关键词，优先选主文块；文档之间则按标题去重并保留最新年份。
"""
from __future__ import annotations

import re

# 意图匹配规则：列表顺序决定优先级（先匹配到的意图胜出）。
# 顺序有讲究：先判断更具体的“险种/期限”，再判断泛化的“标准/条件”。
# 每个元组为 (意图标签, [触发关键词列表])
INTENT_RULES = [
    ("险种", ["险种", "哪些保险", "覆盖哪些"]),
    ("期限", ["期限", "多久", "最长", "领多久"]),
    ("标准", ["标准", "多少", "金额", "比例", "每月", "每人"]),
    ("条件", ["条件", "资格", "要求", "能不能", "是否符合", "能申请"]),
    ("对象", ["对象", "哪些人", "谁可以", "适用"]),
    ("项目", ["项目", "包括", "有哪些"]),
]

# 按意图分类的文档段落关键词：用于在文档块中定位与问题意图最匹配的段落。
# 当识别到用户意图后，用它从文档块的 clause_path 和 text 中查找对应关键词，
# 命中次数越多的块越优先被选中。
SECTION_PATTERNS = {
    "险种": ["险种", "基本养老保险", "基本医疗保险", "失业保险", "工伤保险"],
    "期限": ["补贴期限", "最长", "享受至退休", "发放期", "累计最长"],
    "标准": ["补贴标准", "标准为", "最低工资标准", "每月", "每人", "元/月", "元/人"],
    "条件": ["补贴条件", "应当同时具备", "条件为", "补贴对象", "未享受过创业",
             "正常经营", "签订1年以上", "缴纳社会保险费", "缴交社会保险费"],
    "对象": ["补贴对象", "适用对象", "对象为"],
    "项目": ["补贴项目", "包括", "项目包括"],
}


def detect_intent(query: str) -> str:
    """识别问题要问的那一类信息；识别不到时返回空字符串。"""
    q = query or ""  # 容错处理：query 可能为 None
    for intent, words in INTENT_RULES:
        # 按 INTENT_RULES 的优先级顺序逐条匹配
        if any(word in q for word in words):
            return intent  # 命中第一个匹配的意图即返回
    return ""  # 无匹配时返回空字符串，后续走降级逻辑


def normalize_title(title: str) -> str:
    """去掉标题里的年份，用来合并"同一条政策的不同年份版本"。"""
    # 移除 2000-2099 范围内的年份数字，使不同年份的同名政策归为一组
    return re.sub(r"20\d{2}", "", title or "")


def dedupe_docs_newest(doc_ids: list[str], by_doc: dict) -> list[str]:
    """同标题文档只保留最新年份，保持第一次出现的顺序。"""
    order: list[str] = []       # 记录去重后标题的首次出现顺序
    best: dict[str, str] = {}   # title_key -> 当前最优的 doc_id
    for doc_id in doc_ids:
        chunks = by_doc.get(doc_id) or []  # 获取该文档的所有块
        if not chunks:
            continue  # 无内容则跳过
        title_key = normalize_title(str(chunks[0].get("doc_title") or ""))
        if title_key not in best:
            # 首次遇到该标题，直接记录
            order.append(title_key)
            best[title_key] = doc_id
            continue
        # 已存在同名标题，比较年份，保留较新的
        old_chunks = by_doc.get(best[title_key]) or []
        old_year = (old_chunks[0].get("year") or 0) if old_chunks else 0
        new_year = chunks[0].get("year") or 0
        if new_year > old_year:
            best[title_key] = doc_id  # 替换为最新年份的文档
    # 按首次出现顺序返回去重后的 doc_id 列表
    return [best[key] for key in order]


def pick_section_chunk(query: str, doc_chunks: list[dict],
                       chunk_scores: list[float], chunk_index: dict[str, int]) -> dict:
    """在同一文档内按问题类型选块；识别不到类型时退回最长主文块。"""
    if not doc_chunks:
        raise ValueError("doc_chunks 不能为空")
    intent = detect_intent(query)  # 先识别用户问题的意图类型
    if intent:
        scored = []  # 候选块：(命中数, 是否条款块, BM25分数, 文本长度, chunk)
        for chunk in doc_chunks:
            # 将 clause_path（小标题路径）和正文拼接后匹配关键词
            text = str(chunk.get("clause_path") or "") + " " + str(chunk.get("text") or "")
            hits = sum(1 for pattern in SECTION_PATTERNS[intent] if pattern in text)
            if hits:
                cid = chunk["chunk_id"]
                # 同命中数时优先非“全文”块，再按 BM25，最后才用长度兜底。
                is_clause = 0 if chunk.get("clause_path") == "全文" else 1
                scored.append((hits, is_clause, float(chunk_scores[chunk_index[cid]]),
                               len(str(chunk.get("text") or "")), chunk))
        if scored:
            # 排序优先级：命中数 > 条款块 > BM25 > 文本长度
            return max(scored, key=lambda x: (x[0], x[1], x[2], x[3]))[4]
    # 意图识别失败或无匹配块时：优先条款块，再 BM25，最后长度。
    return max(doc_chunks, key=lambda c: (
        c.get("clause_path") != "全文",
        float(chunk_scores[chunk_index[c["chunk_id"]]]),
        len(str(c.get("text") or "")),
    ))