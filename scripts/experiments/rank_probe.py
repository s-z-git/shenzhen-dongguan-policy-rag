"""rank 分布实验（设计记录 §11.5）。

不调 LLM：查询分析使用规则版（与 run_eval 的检索消融同口径），单查询检索。

对每个池子大小 K（vector_top_k = bm25_top_k = K）：
  1. 取 RRF 融合后的完整候选顺序 stages["fused"]；
  2. 把 chunk 映射到 doc（doc_equivalence 合并重复采集件）；
  3. 统计 golden 文档在 fused 中的最好名次（该文档最早出现的 chunk）。
可选：对同一池子跑 Cross-Encoder，直接验证扩池 + 精排是否真的提升 Recall@5/MRR。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.config import DEFAULT
from app.eval.metrics import (aggregate_retrieval, doc_equivalence, golden_ids,
                              paired_bootstrap_diff)
from app.generate import analyze_query, filters_from_analysis
from app.retrieve import HybridRetriever
from app.pipeline import RAGPipeline

BUCKETS = [("1-5", 1, 5), ("6-20", 6, 20), ("21-40", 21, 40),
           ("41-100", 41, 100), (">100", 101, 10 ** 9)]


def parse_ints(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x.strip()]


def doc_of(chunk_id: str, by_id: dict, equiv: dict) -> str:
    did = by_id[chunk_id]["doc_id"]
    return equiv.get(did, did)


def ranks_in(ids: list[str], by_id: dict, equiv: dict, gids: set[str]) -> dict[str, int]:
    """每个 golden doc 在 ids 中最早出现的名次（1-based）。"""
    out: dict[str, int] = {}
    for i, cid in enumerate(ids, start=1):
        d = doc_of(cid, by_id, equiv)
        if d in gids and d not in out:
            out[d] = i
    return out


def bucket_of(rank: int | None) -> str:
    if rank is None:
        return "not_in_candidates"
    for name, lo, hi in BUCKETS:
        if lo <= rank <= hi:
            return name
    return "not_in_candidates"


def pct(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


def summarize_ranks(rows: list[dict], n_docs: int, n_queries: int) -> dict:
    doc_counts = {name: 0 for name, _, _ in BUCKETS}
    doc_counts["not_in_candidates"] = 0
    query_counts = {name: 0 for name, _, _ in BUCKETS}
    query_counts["not_in_candidates"] = 0
    for r in rows:
        for rank in r["golden_doc_ranks"].values():
            doc_counts[bucket_of(rank)] += 1
        # ranks_in 只返回“在候选里找到的” golden；完全没进候选的必须补进 not_in_candidates
        doc_counts["not_in_candidates"] += r["n_golden"] - len(r["golden_doc_ranks"])
        ranks = list(r["golden_doc_ranks"].values())
        query_counts[bucket_of(min(ranks) if ranks else None)] += 1
    lengths = sorted(r["n_fused"] for r in rows)
    return {
        "n_queries": n_queries,
        "n_golden_docs": n_docs,
        "per_golden_doc": {k: {"n": v, "pct": pct(v, n_docs)} for k, v in doc_counts.items()},
        "per_query_first_golden": {k: {"n": v, "pct": pct(v, n_queries)}
                                   for k, v in query_counts.items()},
        "fused_length": {"min": lengths[0] if lengths else 0,
                         "median": lengths[len(lengths) // 2] if lengths else 0,
                         "max": lengths[-1] if lengths else 0},
        "cutoff_hit": {
            str(c): {
                "golden_docs": pct(sum(1 for r in rows for x in r["golden_doc_ranks"].values() if x <= c), n_docs),
                "queries_any": pct(sum(1 for r in rows if any(x <= c for x in r["golden_doc_ranks"].values())), n_queries),
                "queries_all": pct(sum(1 for r in rows if r["golden_doc_ranks"] and
                                       len(r["golden_doc_ranks"]) == r["n_golden"] and
                                       all(x <= c for x in r["golden_doc_ranks"].values())), n_queries),
            } for c in (5, 10, 20, 40, 100)
        },
    }


def make_rec(item: dict, res: dict, by_id: dict, equiv: dict, dt_ms: float) -> dict:
    ids = [doc_of(c["chunk_id"], by_id, equiv) for c in res["final"]]
    gids = {equiv.get(g, g) for g in golden_ids(item)}
    rank = next((i + 1 for i, d in enumerate(ids) if d in gids), 0)
    return {"query": item["query"], "type": item.get("type", "fact"),
            "retrieved": ids, "golden": sorted(gids),
            "hit": bool(gids & set(ids)), "rank": rank, "latency_ms": dt_ms}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data/eval_gold.reviewed.jsonl"))
    ap.add_argument("--pools", default="20,50,100")
    ap.add_argument("--rerank-pools", default="")
    ap.add_argument("--out", default=str(ROOT / "data/rank_probe.json"))
    ap.add_argument("--k", type=int, default=5)
    a = ap.parse_args()

    gold = [json.loads(ln) for ln in open(a.gold, encoding="utf-8") if ln.strip()]
    gold = [x for x in gold if x.get("golden_docs")]
    print("[probe] 有 golden 的题目: %d" % len(gold), flush=True)

    cfg = DEFAULT
    pipe = RAGPipeline.load(cfg)
    chunks = pipe.chunks
    if not chunks:
        raise RuntimeError("chunks 为空：请先运行 python build_index.py 构建索引")
    equiv = doc_equivalence(chunks)
    by_id = pipe.retriever.by_id
    pipe.retriever._ensure_bm25()          # 预热，后面各池子共享

    pools = parse_ints(a.pools)
    rerank_pools = set(parse_ints(a.rerank_pools))
    result: dict = {"gold_file": a.gold, "n_queries": len(gold),
                    "n_golden_docs": sum(len(golden_ids(x)) for x in gold),
                    "analysis": "rule-based (no LLM), single query",
                    "pools": {}}

    for K in pools:
        print("[probe] pool=%d 检索..." % K, flush=True)
        cfg_k = replace(cfg, vector_top_k=K, bm25_top_k=K)
        retr = HybridRetriever(chunks, cfg_k, store=pipe.retriever.store)
        retr._bm25 = pipe.retriever._bm25
        rows = []
        recs_no = []
        t0 = time.perf_counter()
        for i, item in enumerate(gold, 1):
            q = item["query"]
            f = filters_from_analysis(analyze_query(q, cfg_k, use_llm=False))
            gids = {equiv.get(g, g) for g in golden_ids(item)}
            r = retr.retrieve(q, filters=f, use_rerank=False, top_k=a.k, keep_stages=True)
            fused = r["stages"]["fused"]
            filtered = r["stages"]["filtered"]
            rows.append({
                "query": q, "type": item.get("type", "fact"),
                "filters": f,
                "n_fused": len(fused), "n_filtered": len(filtered),
                "n_golden": len(gids),
                "golden_doc_ranks": ranks_in(fused, by_id, equiv, gids),
                "golden_doc_ranks_filtered": ranks_in(filtered, by_id, equiv, gids),
                "golden_doc_ranks_final": ranks_in(r["final_ids"], by_id, equiv, gids),
                "relaxed_year": r["relaxed_year"],
            })
            recs_no.append(make_rec(item, r, by_id, equiv, 0.0))
            if i % 20 == 0:
                print("[probe]   %d/%d" % (i, len(gold)), flush=True)
        dt = time.perf_counter() - t0
        summary = summarize_ranks(rows, result["n_golden_docs"], len(gold))
        summary["seconds"] = round(dt, 1)
        summary["no_rerank"] = aggregate_retrieval(recs_no)
        summary["by_type"] = {}
        for typ in sorted({x.get("type", "fact") for x in gold}):
            sub = [r for r in rows if r["type"] == typ]
            nd = sum(r["n_golden"] for r in sub)
            summary["by_type"][typ] = summarize_ranks(sub, nd, len(sub))
        summary["missed_queries"] = [
            {"query": r["query"], "type": r["type"],
             "golden_doc_ranks": r["golden_doc_ranks"], "n_fused": r["n_fused"]}
            for r in rows if not any(x <= a.k for x in r["golden_doc_ranks"].values())
        ]
        summary["rows"] = rows

        if K in rerank_pools:
            print("[probe] pool=%d 精排..." % K, flush=True)
            recs_re = []
            changed = 0
            promoted = 0
            t1 = time.perf_counter()
            for i, item in enumerate(gold, 1):
                q = item["query"]
                f = filters_from_analysis(analyze_query(q, cfg_k, use_llm=False))
                gids = {equiv.get(g, g) for g in golden_ids(item)}
                base = retr.retrieve(q, filters=f, use_rerank=False, top_k=a.k)
                rr = retr.retrieve(q, filters=f, use_rerank=True, top_k=a.k)
                ids_no = [doc_of(c["chunk_id"], by_id, equiv) for c in base["final"]]
                ids_re = [doc_of(c["chunk_id"], by_id, equiv) for c in rr["final"]]
                changed += (ids_no != ids_re)
                r_no = next((j + 1 for j, d in enumerate(ids_no) if d in gids), None)
                r_re = next((j + 1 for j, d in enumerate(ids_re) if d in gids), None)
                promoted += bool(r_no is None and r_re is not None)
                recs_re.append(make_rec(item, rr, by_id, equiv, 0.0))
                if i % 20 == 0:
                    print("[probe]   %d/%d" % (i, len(gold)), flush=True)
            summary["rerank"] = aggregate_retrieval(recs_re)
            summary["rerank"]["seconds"] = round(time.perf_counter() - t1, 1)
            summary["rerank"]["n_top5_changed"] = changed
            summary["rerank"]["n_promoted_into_top5"] = promoted
            summary["rerank"]["pair_vs_no_rerank"] = paired_bootstrap_diff(
                [1.0 if x["hit"] else 0.0 for x in recs_re],
                [1.0 if x["hit"] else 0.0 for x in recs_no])
        result["pools"][str(K)] = summary
        print("[probe] pool=%d 完成 %.1fs | fused 中位 %d | >20 名 %.1f%% (docs)"
              % (K, dt, summary["fused_length"]["median"],
                 100 * (1 - summary["cutoff_hit"]["20"]["golden_docs"])), flush=True)

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[probe] 结果 -> " + a.out, flush=True)


if __name__ == "__main__":
    main()
