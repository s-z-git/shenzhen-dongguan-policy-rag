"""
app.eval.ablations —— 消融实验

对应 DESIGN.md §4.9 的四组消融：
    1 嵌入模型   bge-small-zh-v1.5 vs bge-m3      -> 改 cfg.embed_model 后重建索引再跑
    2 混合检索   纯向量 vs 向量+BM25               -> use_bm25 开关（本文件实现）
    3 精排       RRF 后直接取 top5 vs 加 Reranker  -> use_rerank 开关（本文件实现）
    4 向量库     FAISS vs Milvus                   -> W3 换 Store 实现，上层不用改

消融只跑检索指标（不调 LLM），否则一次实验要几十次 API 调用，成本和时间都吃不消。
"""
from __future__ import annotations

import time

from app.config import DEFAULT, Config
from app.eval.metrics import (aggregate_retrieval, doc_equivalence, golden_ids,
                              paired_bootstrap_diff)
from app.generate import analyze_query, filters_from_analysis

# 预定义的消融变体列表：每个变体通过 retrieve_kw 调整检索开关
ABLATIONS = [
    # 基线必须显式 use_rerank=False：retrieve() 在 None 时会按配置自动开启精排，
    # 不写的话「混合RRF」会偷偷跑精排，导致 +精排 与基线完全相同。
    {"name": "纯向量",         "retrieve_kw": {"use_bm25": False, "use_rerank": False}},
    {"name": "纯BM25",         "retrieve_kw": {"use_vector": False, "use_rerank": False}},
    {"name": "混合RRF",        "retrieve_kw": {"use_rerank": False}},
    {"name": "混合RRF+精排",   "retrieve_kw": {"use_rerank": True}},
    {"name": "纯向量+精排",    "retrieve_kw": {"use_bm25": False, "use_rerank": True}},
]


def retrieval_records(retriever, gold: list[dict], cfg: Config, use_llm_analysis: bool = False,
                      k: int | None = None, **retrieve_kw) -> list[dict]:
    """按给定开关跑一遍检索，产出逐条记录（供 aggregate_retrieval 汇总）。

    **只跑检索、不调 LLM** —— 这是消融能在几分钟内跑完五组的前提。

    参数:
        retriever: HybridRetriever 实例
        gold: 评测集（每项含 query 和 golden_docs）
        cfg: 配置对象
        use_llm_analysis: 是否用 LLM 分析查询（消融中通常关闭以节省 API 调用）
        k: 检索 top-k
        retrieve_kw: 传递给 retriever.retrieve() 的额外参数（消融开关）
    """
    k = k or cfg.final_top_k
    # 双保险：调用方漏写 use_rerank 时，消融默认走不精排，避免基线被污染
    retrieve_kw.setdefault("use_rerank", False)
    equiv = doc_equivalence(retriever.chunks)
    recs = []
    for item in gold:
        q = item["query"]
        # 查询分析：提取过滤条件（消融中通常不开 LLM，用规则版）
        an = analyze_query(q, cfg, use_llm=use_llm_analysis)
        t0 = time.perf_counter()                                                    # 高精度计时
        res = retriever.retrieve(q, filters=filters_from_analysis(an), top_k=k, **retrieve_kw)
        dt = (time.perf_counter() - t0) * 1000                                      # 毫秒
        ids = [equiv.get(c["doc_id"], c["doc_id"]) for c in res["final"]]
        gids = {equiv.get(g, g) for g in golden_ids(item)}
        # 找到第一个命中 golden 的排名（1-indexed），未命中则为 0
        rank = next((i + 1 for i, d in enumerate(ids) if d in gids), 0)
        recs.append({
            "query": q, "type": item.get("type", "fact"),
            "retrieved": ids, "golden": sorted(gids),
            "hit": bool(gids & set(ids)),                                           # 是否至少命中一篇
            "rank": rank, "latency_ms": dt,
        })
    return recs


def run_ablation(retriever, gold: list[dict], cfg: Config | None = None,
                 variants: list[dict] | None = None) -> list[dict]:
    """跑多组消融，并对关键两两组合做配对 bootstrap 显著性检验。

    参数:
        retriever: HybridRetriever 实例
        gold: 评测集
        cfg: 配置对象
        variants: 自定义变体列表，默认使用 ABLATIONS

    返回: 列表，包含各变体的指标字典和配对比较结果
          配对比较含 {"pair", "diff", "ci95", "significant"}，
          这样报告时能写「差异不显著」，而不是拿点估计硬说 A 比 B 好。
    """
    cfg = cfg or DEFAULT
    variants = variants or ABLATIONS
    out, hits_by_name = [], {}
    for v in variants:
        kw = dict(v["retrieve_kw"])
        # 如果变体要求精排但未配置 rerank_model，则跳过
        if kw.get("use_rerank") and not cfg.rerank_model:
            out.append({"name": v["name"], "skipped": "未配置 rerank_model"})
            continue
        # 跑一遍检索，产出逐条记录
        recs = retrieval_records(retriever, gold, cfg, **kw)
        # 提取有 golden 的条目用于后续差异检验
        scored = [r for r in recs if r["golden"]]
        hits_by_name[v["name"]] = [1.0 if r["hit"] else 0.0 for r in scored]
        m = aggregate_retrieval(recs)
        m["name"] = v["name"]
        out.append(m)

    # 三组关键对比：混合 vs 纯向量、加精排 vs 不加、BM25 vs 向量
    for a, b in (("混合RRF", "纯向量"), ("混合RRF+精排", "混合RRF"), ("纯BM25", "纯向量")):
        if a in hits_by_name and b in hits_by_name:
            cmp = paired_bootstrap_diff(hits_by_name[a], hits_by_name[b])
            cmp["pair"] = a + " - " + b
            out.append(cmp)
    return out