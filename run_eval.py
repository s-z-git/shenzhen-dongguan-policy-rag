"""
入口：评测 + 消融

用法：
    python run_eval.py                  # 完整评测（检索 + DeepSeek 生成，慢）
    python run_eval.py --ablation-only   # 只跑消融（不调 LLM，快）
    python run_eval.py --limit 5         # 只跑前 N 条（调试用）

评测集格式（data/eval_gold.jsonl，一行一条）：
    {"query": "...", "type": "fact|multi|stale|boundary",
     "golden_docs": ["doc_id 或 doc_id#clause"], "answer_points": ["..."], "verified": true}
注意：golden_docs 为空的只能标 boundary，否则会污染召回率分母。
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path

from app.config import DEFAULT
from app.eval import run_ablation
from app.eval.metrics import (aggregate_generation, aggregate_grounding,
                              aggregate_retrieval, doc_equivalence, golden_ids)
from app.generate import analyze_query, cited_doc_ids, filters_from_analysis
from app.pipeline import RAGPipeline, dedupe_sub_questions


def load_gold(path) -> list[dict]:
    """读 JSONL 格式评测集（一行一条）。
    读 JSONL 格式评测集（一行一条）。"""
    with open(path, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def compare_subq(pipe, gold: list[dict], cfg) -> None:
    """检索层对比：单查询 vs 子问题合并（不调生成模型）。

    用覆盖率而不是 hit 来比较：一问答两件事的样本有 2 篇 golden，
    hit 只要命中一篇就算成功，看不出子问题合并的价值。
    """
    # 先预热一次：第一次检索要建 BM25 索引（约 0.5s），不预热会把耗时算到第一条上
    pipe.retriever.retrieve("预热", use_rerank=False, top_k=1)
    base_recs, multi_recs, rows = [], [], []
    for item in gold:
        q = item["query"]
        gids = golden_ids(item)
        an = analyze_query(q, cfg, use_llm=True)
        f = filters_from_analysis(an)
        subs = dedupe_sub_questions(q, an.get("sub_questions"))

        t0 = time.perf_counter()
        b = pipe.retriever.retrieve(q, filters=f, use_rerank=False, top_k=cfg.final_top_k)
        tb = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        m = pipe._retrieve_multi(q, an.get("sub_questions"), f, False, cfg.final_top_k)
        tm = (time.perf_counter() - t0) * 1000

        def rec(res, dt):
            ids = [c["doc_id"] for c in res["final"]]
            return {"query": q, "type": "multi", "retrieved": ids, "golden": sorted(gids),
                    "hit": bool(gids & set(ids)),
                    "rank": next((i + 1 for i, d in enumerate(ids) if d in gids), 0),
                    "latency_ms": dt}

        base_recs.append(rec(b, tb))
        multi_recs.append(rec(m, tm))
        rows.append((q, len(subs), base_recs[-1], multi_recs[-1]))

    ab, am = aggregate_retrieval(base_recs), aggregate_retrieval(multi_recs)
    print("[subq] 子问题拆分: " + str(sum(1 for r in rows if r[1] > 1)) + "/" + str(len(rows))
          + " 条；平均 " + str(round(sum(r[1] for r in rows) / max(len(rows), 1), 2)) + " 个")
    print("[subq] %-18s %10s %10s" % ("指标", "单查询", "子问题合并"))
    for k in ("recall_at_k", "golden_coverage", "full_recall", "mean_latency_ms"):
        print("[subq] %-18s %10s %10s" % (k, round(ab[k], 3), round(am[k], 3)))
    for q, n, b, m in rows:
        mark = "上升" if _cov(m) > _cov(b) else ("下降" if _cov(m) < _cov(b) else "持平")
        print("[subq] %s | %-24s | 覆盖 %s -> %s | 子问题 %d"
              % (mark, q[:24], round(_cov(b), 2), round(_cov(m), 2), n))


def _cov(rec: dict) -> float:
    g = set(rec["golden"])
    return len(set(rec["retrieved"]) & g) / len(g) if g else 0.0


def main() -> None:
    """评测入口：完整评测（含 LLM 生成）或只跑消融（不调 LLM）。
    结果同时打印到终端并写入 data/eval_report.json。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--ablation-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--gold-file", default="", help="换一个评测集文件（如 data/eval_gold.multi.jsonl）")
    ap.add_argument("--subq-compare", action="store_true",
                    help="只做检索对比：单查询 vs 子问题合并（不生成回答）")
    ap.add_argument("--doc-cap", type=int, default=None,
                    help="最终 top-k 每个 doc 最多保留几个 chunk；0=不限")
    ap.add_argument("--report-out", default="",
                    help="报告输出路径；默认写 cfg.eval_report")
    ap.add_argument("--clause-gold", default="",
                    help="clause-level gold 文件；默认尝试 data/eval_gold.clause.jsonl")
    ap.add_argument("--no-ablation", action="store_true",
                    help="只跑生成，不重跑检索消融（消融已有独立报告时用）")
    ap.add_argument("--no-llm-analysis", action="store_true",
                    help="跳过 LLM 查询分析，使用规则版过滤/子问题，作为独立对照列")
    ap.add_argument("--prompt-version", choices=["v1", "v2"], default=None,
                    help="回答 prompt 版本；默认 v2")
    a = ap.parse_args()

    # 用 replace 而不是改 DEFAULT，避免同进程里其他实验被污染
    changes = {}
    if a.doc_cap is not None:
        changes["doc_cap"] = a.doc_cap
    if a.prompt_version:
        changes["answer_prompt_version"] = a.prompt_version
    cfg = replace(DEFAULT, **changes) if changes else DEFAULT
    gold_path = Path(a.gold_file) if a.gold_file else cfg.eval_gold
    gold = load_gold(gold_path)
    if a.limit:
        gold = gold[:a.limit]
    clause_path = Path(a.clause_gold) if a.clause_gold else Path("data/eval_gold.clause.jsonl")
    clause_map = {}
    if clause_path.exists():
        for item in load_gold(clause_path):
            if item.get("golden_chunks"):
                clause_map[item["query"]] = item["golden_chunks"]
    print("[eval] 评测集 " + str(len(gold)) + " 条 -> " + str(gold_path)
          + " | doc_cap=" + str(cfg.doc_cap)
          + " | prompt=" + str(cfg.answer_prompt_version)
          + " | reasoning=" + str(cfg.reasoning_effort)
          + " | llm_analysis=" + str(not a.no_llm_analysis))

    pipe = RAGPipeline.load(cfg)
    equiv = doc_equivalence(pipe.chunks)      # 同一文件被两个站点各采一次时算作同一篇
    recs: list[dict] = []
    summary = None

    if a.subq_compare:
        compare_subq(pipe, gold, cfg)
        return

    if not a.ablation_only:
        for item in gold:
            print("[eval] " + str(len(recs) + 1) + "/" + str(len(gold)) + " "
                  + str(item["query"])[:34], flush=True)
            t0 = time.perf_counter()
            # multi 题默认启用“文档级选择 + 按问题类型选块”；其他题型保持旧路径。
            r = pipe.ask(item["query"], use_doc_select=(item.get("type") == "multi"),
                         use_llm_analysis=not a.no_llm_analysis)
            dt = (time.perf_counter() - t0) * 1000
            ids = [equiv.get(c["doc_id"], c["doc_id"]) for c in r["chunks"]]
            gids = {equiv.get(g, g) for g in golden_ids(item)}
            rank = next((i + 1 for i, d in enumerate(ids) if d in gids), 0)
            cg = clause_map.get(item["query"]) or []
            cids = [c["chunk_id"] for c in r["chunks"]]
            c_rank = next((i + 1 for i, cid in enumerate(cids) if cid in cg), 0)
            c_cov = len(set(cids) & set(cg)) / len(cg) if cg else 0.0
            recs.append({
                "query": item["query"], "type": item.get("type", "fact"),
                "retrieved": ids,
                "retrieved_chunks": [c["chunk_id"] for c in r["chunks"]],
                "golden": sorted(gids),
                "hit": bool(gids & set(ids)), "rank": rank,
                "clause_golden": cg, "clause_hit": bool(cg and set(cids) & set(cg)),
                "clause_rank": c_rank, "clause_coverage": round(c_cov, 4),
                "answer": r["answer"], "citations": r["citations"],
                "answer_points": item.get("answer_points") or [],
                "prompt_version": r.get("prompt_version", cfg.answer_prompt_version),
                "cited_docs": sorted({equiv.get(d, d)
                                      for d in cited_doc_ids(r["answer"], r["chunks"])}),
                "refused": r["refused"], "notes": r["notes"], "latency_ms": dt,
                "grounding": r.get("grounding"), "usage": r.get("usage") or {},
                "n_sub_questions": r.get("n_sub_questions", 1),
                "relaxed_year": r.get("relaxed_year", False),
            })
        summary = aggregate_retrieval(recs)
        summary.update(aggregate_generation(recs))
        summary.update(aggregate_grounding(recs))
        print("[eval] 汇总:")
        for k, v in summary.items():
            print("   " + k + " = " + (str(round(v, 4)) if isinstance(v, float) else str(v)))

    if a.no_ablation:
        abl = []
        print("[eval] 跳过消融（--no-ablation）")
    else:
        abl = run_ablation(pipe.retriever, gold, cfg)
        print("[eval] 消融:")
        for x in abl:
            print("   " + json.dumps(x, ensure_ascii=False))

    report = {"summary": summary, "ablation": abl, "records": recs,
              "doc_cap": cfg.doc_cap, "prompt_version": cfg.answer_prompt_version,
              "reasoning_effort": cfg.reasoning_effort,
              "llm_analysis": not a.no_llm_analysis,
              "gold_file": str(gold_path),
              "clause_gold_file": str(clause_path) if clause_map else ""}
    report_path = Path(a.report_out) if a.report_out else Path(cfg.eval_report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print("[eval] 报告 -> " + str(report_path))


if __name__ == "__main__":
    main()