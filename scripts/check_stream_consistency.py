"""检查 ask() 与 ask_stream() 使用相同检索结果；不调用 LLM 生成。

做法：把 answer / stream_answer 替换成桩函数，只比较 retrieved_chunks。
用法：python scripts/check_stream_consistency.py
"""
from __future__ import annotations

import json
import pathlib
import sys
from dataclasses import replace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.pipeline as pipeline
from app.config import DEFAULT
from app.pipeline import RAGPipeline

QUESTIONS = [
    "深圳失业保险金标准是多少？",
    "东莞本科应届生入户后能领哪些补贴？",
    "深圳新引进博士人才生活补贴的标准是多少",
]


def fake_answer(question, chunks, cfg=None, llm=None):
    """不调 LLM，仅让 ask() 走完检索后的同步组装路径。"""
    return {"answer": "一致性检查桩", "citations": [], "notes": [], "refused": False,
            "prompt": "", "usage": {}}


def fake_stream(question, chunks, cfg=None, llm=None, sink=None):
    """不调 LLM，仅让 ask_stream() 走完流式组装路径。"""
    payload = sink if sink is not None else {}
    payload.update({"answer": "一致性检查桩", "citations": [], "refused": False,
                    "notes": [], "prompt": "", "usage": {}})
    yield "一致性检查桩"


def main() -> int:
    cfg = replace(DEFAULT, trace_enabled=False, reasoning_effort="none")
    pipe = RAGPipeline.load(cfg)
    pipeline.answer = fake_answer
    pipeline.stream_answer = fake_stream
    results = []
    ok = True
    for question in QUESTIONS:
        sync = pipe.ask(question, use_llm_analysis=False)
        sink = {}
        for _ in pipe.ask_stream(question, use_llm_analysis=False, sink=sink):
            pass
        streamed = sink.get("result") or {}
        sync_ids = [c.get("chunk_id") for c in sync.get("chunks") or []]
        stream_ids = [c.get("chunk_id") for c in streamed.get("chunks") or []]
        same = sync_ids == stream_ids
        ok = ok and same
        results.append({"query": question, "same": same,
                        "retrieved_chunks": sync_ids})
    print(json.dumps({"ok": ok, "results": results}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
