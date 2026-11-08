"""
app.retrieve.filter —— 元数据过滤

对应 DESIGN.md 的 app/retrieve/filter.py 与 §4.6。

这里放的是**规则版**条件抽取（零成本、可解释）；LLM 版在
app/generate/query_analysis.py，两者可以叠加使用。
规则版同时是 LLM 失败时的兜底，这样系统不会因为一次 API 抖动就完全失效。
"""
from __future__ import annotations

import re


def extract_filters(query: str) -> dict:
    """从问题里抽 {city_in, year_gte, year_lte}。抽不到就返回空 dict（= 不过滤）。"""
    f: dict = {}
    cities = [c for c in ("深圳", "东莞") if c in query]
    if cities:
        f["city_in"] = cities
    m = re.search(r"(20\d{2})\s*年\s*(?:以后|之后|以来|起)", query)
    if m:
        f["year_gte"] = int(m.group(1))
    m = re.search(r"(20\d{2})\s*年\s*(?:以前|之前|及以前)", query)
    if m:
        f["year_lte"] = int(m.group(1))
    return f


def apply_filters(chunks: list[dict], filters: dict | None) -> list[dict]:
    """按 city / year_gte / year_lte 过滤候选。
    当前在内存过滤（4583 块足够快）；换 Milvus 时这一步应下推成标量过滤。"""
    if not filters:
        return chunks
    out = chunks
    if filters.get("city_in"):
        allow = set(filters["city_in"])
        out = [c for c in out if c["city"] in allow]
    if filters.get("year_gte"):
        out = [c for c in out if (c.get("year") or 0) >= filters["year_gte"]]
    if filters.get("year_lte"):
        out = [c for c in out if (c.get("year") or 0) <= filters["year_lte"]]
    return out

def filter_with_fallback(fused_ids: list[str], chunks: list[dict],
                         filters: dict | None, top_k: int) -> tuple[list[str], bool]:
    """按元数据过滤候选；不足 top_k 且有限定年份时，保留城市、放宽年份补齐。

    硬约束是 city：政策问答里答错城市比答不全更致命，所以**绝不跨城市放宽**。
    返回 (ids, relaxed_year)；relaxed_year=True 表示有条款是因为年份约束被放宽补进来的。
    """
    if not filters:
        return list(fused_ids), False
    allowed = {c["chunk_id"] for c in apply_filters(chunks, filters)}
    kept = [cid for cid in fused_ids if cid in allowed]          # 保持原融合顺序
    if len(kept) >= top_k or not (filters.get("year_gte") or filters.get("year_lte")):
        return kept, False
    # 只保留城市约束，把年份放开
    city_filters = {"city_in": filters["city_in"]} if filters.get("city_in") else {}
    city_allowed = {c["chunk_id"] for c in apply_filters(chunks, city_filters)}
    picked = set(kept)
    extra = [cid for cid in fused_ids if cid not in picked and cid in city_allowed]
    return kept + extra, bool(extra)
