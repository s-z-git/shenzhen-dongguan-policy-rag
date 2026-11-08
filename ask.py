
"""
入口：单问 CLI（用来看检索链路和最终回答）

用法：
    python ask.py "东莞本科应届生入户后能领哪些补贴"
    python ask.py "..." --no-rerank          # 关精排
    python ask.py "..." --vector-only        # 只走向量
    python ask.py "..." --no-llm-analysis    # 查询分析走规则版，不调 LLM
    python ask.py "..." --show-chunks        # 打印命中的条款原文
    python ask.py "..." --show-stages        # 打印检索各阶段的 id 列表
"""
from __future__ import annotations

import argparse
from dataclasses import replace

from app.config import DEFAULT
from app.memory import SessionStore
from app.generate import check_grounding
from app.ingest import citation
from app.pipeline import RAGPipeline


def main() -> None:
    """解析命令行参数，按开关选择走 pipeline.ask 还是直连 retriever。
    解析命令行参数，按开关选择走 pipeline.ask 还是直连 retriever。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("--vector-only", action="store_true")
    ap.add_argument("--no-bm25", action="store_true")
    ap.add_argument("--no-llm-analysis", action="store_true")
    ap.add_argument("--top-k", type=int, default=DEFAULT.final_top_k)
    ap.add_argument("--doc-cap", type=int, default=None,
                    help="最终 top-k 每个 doc 最多保留几个 chunk；0=不限")
    ap.add_argument("--prompt-version", choices=["v1", "v2"], default=None,
                    help="回答 prompt 版本；默认 v2")
    ap.add_argument("--multi-doc-select", action="store_true",
                    help="多主题问题启用文档级选择 + 按问题类型选块")
    ap.add_argument("--session-id", default="",
                    help="多轮会话 id；设置后使用最近 10 条消息做 query rewrite")
    ap.add_argument("--clear-session", action="store_true",
                    help="清空指定 session 的历史消息后退出")
    ap.add_argument("--no-trace", action="store_true",
                    help="本次不写 trace JSONL")
    ap.add_argument("--show-chunks", action="store_true")
    ap.add_argument("--show-stages", action="store_true")
    a = ap.parse_args()

    changes = {}
    if a.doc_cap is not None:
        changes["doc_cap"] = a.doc_cap
    if a.prompt_version:
        changes["answer_prompt_version"] = a.prompt_version
    if a.no_trace:
        changes["trace_enabled"] = False
    cfg = replace(DEFAULT, **changes) if changes else DEFAULT
    memory = SessionStore(cfg.memory_db) if a.session_id else None
    if memory and a.clear_session:
        memory.clear(a.session_id)
        print("已清空会话：" + a.session_id)
        return
    pipe = RAGPipeline.load(cfg)

    # vector-only / no-bm25 这类开关直接打到 retriever 上，避免 pipeline.ask 只为这种情况开口子
    if a.vector_only or a.no_bm25 or a.show_stages:
        from app.generate import analyze_query, answer, filters_from_analysis
        analysis = analyze_query(a.question, cfg, use_llm=not a.no_llm_analysis)
        res = pipe.retriever.retrieve(
            a.question,
            filters=filters_from_analysis(analysis),
            use_vector=not a.vector_only,
            use_bm25=not (a.no_bm25 or a.vector_only),
            use_rerank=False if a.no_rerank else None,
            top_k=a.top_k,
            keep_stages=True,
        )
        out = answer(a.question, res["final"], cfg)
        print("查询分析:", analysis)
        print("精排:", res["used_rerank"], "| 向量:", res["used_vector"], "| BM25:", res["used_bm25"])
        if a.show_stages and "stages" in res:
            for k, v in res["stages"].items():
                if isinstance(v, dict):
                    print("  " + k + ": " + str(len(v)) + " 个子问题")
                elif isinstance(v, list):
                    print("  " + k + ": " + str(len(v)) + " -> " + str(v[:5]))
                else:
                    print("  " + k + ": " + str(v))
        chunks = res["final"]
    else:
        history = memory.recent(a.session_id, limit=10) if memory else None
        r = pipe.ask(a.question, use_rerank=False if a.no_rerank else None,
                     top_k=a.top_k, use_llm_analysis=not a.no_llm_analysis,
                     use_doc_select=True if a.multi_doc_select else None,
                     history=history, session_id=a.session_id or None)
        out = r
        print("查询分析:", r["analysis"])
        if r.get("search_question") and r["search_question"] != a.question:
            print("多轮改写:", r["search_question"])
        print("精排:", r["used_rerank"], "| trace:", r.get("trace_id", ""))
        if memory:
            memory.add_turn(a.session_id, "user", a.question)
            memory.add_turn(a.session_id, "assistant", r.get("answer") or "")
        if r.get("n_sub_questions", 1) > 1:
            print("检索: 子问题 x" + str(r["n_sub_questions"]) + " 分别召回后 RRF 合并")
        if a.show_stages:
            for k, v in r["stages"].items():
                if isinstance(v, dict):
                    print("  " + k + ": " + str(len(v)) + " 个子问题")
                elif isinstance(v, list):
                    print("  " + k + ": " + str(len(v)) + " -> " + str(v[:5]))
                else:
                    print("  " + k + ": " + str(v))
        chunks = r["chunks"]

    print("命中条款:")
    for c in chunks:
        print("   - " + citation(c))
    if a.show_chunks:
        for c in chunks:
            print("")
            print("[" + citation(c) + "]  " + str(c["chars"]) + " 字")
            print(c["text"][:600])

    print("")
    print(out["answer"])
    for n in out["notes"]:
        print("⚠️ " + n)
    print("")
    print("出处:", out["citations"] if out["citations"] else "(未解析到)")

    g = out.get("grounding") or check_grounding(out.get("answer", ""), chunks)
    ci, nu = g["citations"], g["numbers"]
    if ci["total"]:
        line = "引用核验: " + str(ci["verified"]) + "/" + str(ci["total"]) + " 条可验证"
        if ci["no_clause"]:
            line += " | 只给文件名未给条款号: " + str(ci["no_clause"])
        if ci["ambiguous"]:
            line += " | 同名多版本: " + str(ci["ambiguous"])
        print(line)
        for it in ci["items"]:
            if it["status"] == "unmatched":
                print("  [未匹配] " + it["raw"] + " -> 本轮检索结果里没有这个文件")
            elif it["status"] == "clause_conflict":
                print("  [条款冲突] " + it["raw"] + " -> 原文实际: "
                      + "、".join(it["actual_clause"]))
    if nu["total"]:
        line = "数字核验: " + str(nu["grounded"]) + "/" + str(nu["total"]) + " 条可溯源"
        if nu["ungrounded"]:
            line += " | 未溯源: " + "、".join(nu["ungrounded"])
        print(line)


if __name__ == "__main__":
    main()