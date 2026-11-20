"""会话回顾测试：规则识别 + 确定性答案 + 不检索/不调 LLM。"""
from __future__ import annotations

import pathlib
import sys
import tempfile
from dataclasses import replace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.config import DEFAULT  # noqa: E402
from app.generate.recall import detect_recall, format_recall  # noqa: E402
from app.memory import SessionStore  # noqa: E402
from app.pipeline import RAGPipeline  # noqa: E402


class BoomRetriever:
    """任何检索调用都抛异常，证明回顾分支没有进检索。"""

    def retrieve(self, *a, **kw):  # noqa: ARG002
        raise AssertionError("retriever should not be called")


def make_pipe(tmp: str):
    cfg = replace(DEFAULT, memory_db=pathlib.Path(tmp) / "memory.db", trace_enabled=False)
    return RAGPipeline(chunks=[], retriever=BoomRetriever(), cfg=cfg), SessionStore(cfg.memory_db)


def add_turn(mem: SessionStore, sid: str, q: str, a: str) -> None:
    mem.add_turn(sid, "user", q)
    mem.add_turn(sid, "assistant", a, meta={"conclusion": a[:60]})


def main() -> int:
    n = 0

    # 1 规则识别
    assert detect_recall("我刚才问了什么？") == "last"
    assert detect_recall("我刚刚问的什么") == "last"
    assert detect_recall("我上一个问题是什么") == "last"
    assert detect_recall("我刚才都问了什么？") == "list"
    assert detect_recall("这轮会话我都问了哪些问题") == "list"
    assert detect_recall("我第一个问题是什么") == "first"
    assert detect_recall("我之前问过哪些问题？") == "list"
    assert detect_recall("我上次问的啥") == "last"
    assert detect_recall("我都问了啥") == "list"
    assert detect_recall("我前面问的什么") == "first"
    assert detect_recall("北京落户政策是什么？") is None
    assert detect_recall("帮我查一下我刚才问的政策") is None
    assert detect_recall("我刚才问了什么医保政策？") is None
    n += 1

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        pipe, mem = make_pipe(tmp)
        sid = "s1"

        # 2 无历史
        r = pipe.ask("我刚才问了什么？", session_id="new")
        assert r["answer_type"] == "session_recall" and r["llm_calls"] == 0
        assert "第一个问题" in r["answer"] and r["chunks"] == []
        n += 1

        # 3 一轮后 last
        add_turn(mem, sid, "东莞灵活就业社保补贴多少钱？", "每人每月 600 元。")
        r = pipe.ask("我刚才问了什么？", session_id=sid)
        assert "东莞灵活就业社保补贴多少钱" in r["answer"]
        assert r["answer_type"] == "session_recall" and r["llm_calls"] == 0
        n += 1

        # 4 两轮后 last 应该是第二个问题
        add_turn(mem, sid, "深圳应届生入户有什么补贴？", "本科应届生入户补贴。")
        r = pipe.ask("我上一个问题是什么？", session_id=sid)
        assert "深圳应届生入户有什么补贴" in r["answer"]
        assert "东莞灵活就业" not in r["answer"]
        n += 1

        # 5 list 按时间正序
        add_turn(mem, sid, "东莞创业补贴怎么申请？", "一次性创业补贴。")
        r = pipe.ask("我刚才都问了什么？", session_id=sid)
        assert "东莞灵活就业" in r["answer"]
        assert "深圳应届生" in r["answer"]
        assert "东莞创业补贴" in r["answer"]
        assert r["answer"].index("东莞灵活就业") < r["answer"].index("深圳应届生") < r["answer"].index("东莞创业补贴")
        n += 1

        # 6 first
        r = pipe.ask("我第一个问题是什么？", session_id=sid)
        assert "东莞灵活就业社保补贴多少钱" in r["answer"]
        n += 1

        # 7 流式回顾：返回完整文本且不检索
        sink: dict = {}
        tokens = list(pipe.ask_stream("我刚才问了什么？", session_id=sid, sink=sink))
        assert tokens and "东莞创业补贴怎么申请" in tokens[0]
        assert sink.get("answer_type") == "session_recall"
        assert sink.get("chunks") == [] and sink.get("will_refuse") is False
        assert (sink.get("result") or {}).get("llm_calls") == 0
        n += 1

        # 8 域外城市优先：带“北京”的混合句不能走回顾
        called = {"n": 0}

        def fake_retrieve(*a, **kw):  # noqa: ARG001
            called["n"] += 1
            return ("北京落户政策是什么？", {}, {}, {
                "final": [], "used_rerank": False, "stages": {},
                "n_sub_questions": 1, "doc_cap": 0,
            })

        pipe._retrieve = fake_retrieve  # type: ignore[method-assign]
        r = pipe.ask("北京落户政策是什么？我刚才问了什么？", session_id=sid)
        assert called["n"] == 1, "域外城市问句应走 _retrieve，不应被回顾短路"
        assert r.get("answer_type") != "session_recall"
        n += 1

    # 9 模板本身：last / list / first
    turns = [{"question": "A", "answer": "a1"}, {"question": "B", "answer": ""}]
    assert "B" in format_recall("last", turns)
    assert "B" in format_recall("last", turns)
    assert "A" in format_recall("first", turns)
    assert "1. A" in format_recall("list", turns)
    assert not format_recall("last", [{"question": "Q", "answer": "短答案"}]).endswith("…")
    n += 1

    print(f"[recall] PASS {n}/9")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())