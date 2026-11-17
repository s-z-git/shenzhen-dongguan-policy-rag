"""诊断 multi 题：子查询候选并集 vs 每子问题配额 topN 的最终文档覆盖。

不调 LLM：按问号拆子问题，只测检索/精排。
"""
from __future__ import annotations

import json
import re

from app.config import DEFAULT
from app.pipeline import RAGPipeline
from app.eval.metrics import doc_equivalence, golden_ids
from app.retrieve.filter import extract_filters
from app.retrieve.rerank import rerank

QUERIES = [
    "东莞职业介绍补贴标准是多少？员工制家政企业吸纳就业补贴怎么算？",
    "深圳新引进博士人才入户补贴多少？深圳最低工资标准是多少？",
    "深圳职业技能培训补贴包括哪些项目？自主创业场租补贴第二年每月多少？",
    "深圳自主创业的场租补贴每月多少？就业困难人员灵活就业社保补贴按什么标准？",
    "深圳青年人才安居补贴每月多少？就业困难人员灵活就业补贴最长能领多久？",
]


def doc_ids(ids, by, eq):
    return [eq.get(by[i]["doc_id"], by[i]["doc_id"]) for i in ids]


def main() -> None:
    gold = {x["query"]: x for x in (json.loads(l) for l in open(
        "data/eval_gold.reviewed.jsonl", encoding="utf-8") if l.strip())}
    pipe = RAGPipeline.load(DEFAULT)
    if not pipe.chunks:
        raise RuntimeError("chunks 为空：请先运行 python build_index.py 构建索引")
    by, eq = pipe.retriever.by_id, doc_equivalence(pipe.chunks)
    for q in QUERIES:
        gids = {eq.get(x, x) for x in golden_ids(gold[q])}
        f = extract_filters(q)
        base = pipe.retriever.retrieve(q, filters=f, use_rerank=True, top_k=5)
        base_ids = doc_ids([c["chunk_id"] for c in base["final"]], by, eq)
        print("\n###", q)
        print(" current", round(len(gids & set(base_ids)) / len(gids), 2), base_ids)
        subs = [x.strip() for x in re.split(r"[？?]", q) if x.strip()]
        ranked_by_sub = []
        for s in subs:
            r = pipe.retriever.retrieve(s, filters=f, use_rerank=False, top_k=20, keep_stages=True)
            ranked = rerank(s, [(cid, by[cid]["text"]) for cid in r["final_ids"]],
                            DEFAULT.rerank_model, DEFAULT.embed_device)
            ranked_by_sub.append([cid for cid, _ in ranked])
        union = []
        for ids in ranked_by_sub:
            for cid in ids:
                if cid not in union:
                    union.append(cid)
        union_docs = doc_ids(union, by, eq)
        print(" union", round(len(gids & set(union_docs)) / len(gids), 2), "ranks",
              [next((i + 1 for i, d in enumerate(union_docs) if d == g), None) for g in gids])
        for n in (1, 2, 3):
            picked = []
            for ids in ranked_by_sub:
                for cid in ids[:n]:
                    if cid not in picked:
                        picked.append(cid)
            for cid in union:
                if len(picked) >= 5:
                    break
                if cid not in picked:
                    picked.append(cid)
            picked = picked[:5]
            picked_docs = doc_ids(picked, by, eq)
            print(" per-sub top%d" % n, round(len(gids & set(picked_docs)) / len(gids), 2), picked_docs)


if __name__ == "__main__":
    main()
