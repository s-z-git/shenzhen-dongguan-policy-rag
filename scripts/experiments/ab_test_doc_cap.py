"""doc_cap=0 vs 1 的受控 A/B：同一查询分析、同一检索，只改最终文档选择。

默认只跑 rank 实验里 cap 实际改变了 top5 的 2 道 multi 题：
  1. 深圳户籍、毕业5年内的毕业生...（cap1 补回 golden）
  2. 深圳新引进博士... / 深圳最低工资...（cap1 名次 5->4）
其余题 cap1 的 top5 与 cap0 完全相同，答案不会变，不必重复生成。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.config import DEFAULT
from app.eval.answer_points import aggregate_answer_points, judge_records
from app.eval.metrics import doc_equivalence, golden_ids
from app.generate import analyze_query, answer, filters_from_analysis
from app.pipeline import RAGPipeline
from app.ingest import citation

DEFAULT_PREFIXES = ("深圳户籍、毕业5年内的毕业生", "深圳新引进博士人才入户补贴多少")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data/eval_gold.reviewed.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "data/doc_cap_ab.json"))
    ap.add_argument("--prefix", action="append", default=[])
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()

    prefixes = tuple(a.prefix) or DEFAULT_PREFIXES
    gold = [json.loads(l) for l in Path(a.gold).read_text(encoding="utf-8").splitlines() if l.strip()]
    items = [x for x in gold if any(x["query"].startswith(p) for p in prefixes)]
    print("[ab] 题目 %d 条: %s" % (len(items), [x["query"][:24] for x in items]))

    pipe = RAGPipeline.load(DEFAULT)
    if not pipe.chunks:
        raise RuntimeError("chunks 为空：请先运行 python build_index.py 构建索引")
    equiv = doc_equivalence(pipe.chunks)
    rows = []
    for item in items:
        q = item["query"]
        an = analyze_query(q, DEFAULT, use_llm=True)
        f = filters_from_analysis(an)
        print("[ab] " + q[:45] + " | 子问题 " + str(an.get("sub_questions")), flush=True)
        for cap in (0, 1):
            cfg = replace(DEFAULT, doc_cap=cap)
            pipe.cfg = cfg
            pipe.retriever.cfg = cfg
            res = pipe._retrieve_multi(q, an.get("sub_questions"), f, None, cfg.final_top_k)
            out = answer(q, res["final"], cfg)
            rows.append({
                "query": q, "type": item.get("type", "multi"), "cap": cap,
                "answer_points": item.get("answer_points") or [],
                "sub_questions": an.get("sub_questions"),
                "chunks": [c["chunk_id"] for c in res["final"]],
                "retrieved": [equiv.get(c["doc_id"], c["doc_id"]) for c in res["final"]],
                "golden": sorted({equiv.get(g, g) for g in golden_ids(item)}),
                "sources": [citation(c) for c in res["final"]],
                "answer": out.get("answer", ""), "citations": out.get("citations") or [],
                "refused": bool(out.get("refused")),
            })

    recs, stats = judge_records(rows, model="glm-5.3", workers=a.workers,
                                cache_path=str(ROOT / "data/l1_cache.jsonl"), fallback_model="glm-5.3-flash")
    for cap in (0, 1):
        sub = [r for r in rows if r["cap"] == cap]
        print("[ab] cap=%d L1 %s" % (cap, json.dumps(aggregate_answer_points(sub), ensure_ascii=False)))
        for r in sub:
            print("   %.2f | %s" % (r["l1"]["coverage"], "；".join(
                p["point"] for p in r["l1"]["points"] if not p["covered"])[:100]))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"stats": stats, "records": rows}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print("[ab] -> " + a.out)


if __name__ == "__main__":
    main()
