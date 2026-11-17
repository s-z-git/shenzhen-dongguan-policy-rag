"""A7 回归测试：LLM 调用失败（402/超时/鉴权/网络）不得被当成答案。

覆盖：
- classify_llm_error 五类
- answer() 错误分支：answer 为空、status=error、error_kind 正确
- stream_answer() 首 token 前失败 / 中途失败：citations 必须为空
- 正常路径 status=ok，拒答判断未受影响
"""
from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.generate.generator import (  # noqa: E402
    REFUSAL, answer, classify_llm_error, is_refusal, stream_answer)

CHUNKS = [{
    "chunk_id": "c1", "doc_id": "d1", "doc_title": "测试文件",
    "clause_no": "一、", "clause_path": "一、", "year": 2025, "city": "深圳",
    "text": "失业保险金标准为本市月最低工资标准的百分之八十。",
}]


class RaiseInvoke:
    def __init__(self, exc):
        self.exc = exc

    def invoke(self, prompt):
        raise self.exc


class RaiseStream:
    def __init__(self, exc):
        self.exc = exc

    def stream(self, prompt):
        raise self.exc


class FailAfterOne:
    """先产出一个 token，然后抛 402。"""

    def stream(self, prompt):
        def gen():
            yield SimpleNamespace(content="部分回答", usage_metadata={})
            raise Exception("Error code: 402 - Insufficient Balance")
        return gen()


class Good:
    def invoke(self, prompt):
        return SimpleNamespace(content="标准为 2360 元/月 [出处: 测试文件（一、）]",
                               usage_metadata={"total_tokens": 10})

    def stream(self, prompt):
        yield SimpleNamespace(content="标准为 2360 元/月 ", usage_metadata={})
        yield SimpleNamespace(content="[出处: 测试文件（一、）]",
                              usage_metadata={"total_tokens": 10})


def main() -> int:
    n = 0

    # 1-5 错误分类
    assert classify_llm_error(Exception("Error code: 402 - Insufficient Balance")) == "balance"
    n += 1
    assert classify_llm_error(TimeoutError("Request timed out")) == "timeout"
    n += 1
    assert classify_llm_error(Exception("Error code: 401 - authentication failed")) == "auth"
    n += 1
    assert classify_llm_error(ConnectionError("connection reset by peer")) == "network"
    n += 1
    assert classify_llm_error(RuntimeError("boom")) == "other"
    n += 1

    # 6 answer() 402：空答案 + status=error
    r = answer("深圳失业保险金标准？", CHUNKS,
               llm=RaiseInvoke(Exception("Error code: 402 - Insufficient Balance")))
    assert r["status"] == "error", r
    assert r["error_kind"] == "balance", r
    assert r["answer"] == "", r
    assert r["citations"] == [] and r["refused"] is False, r
    assert "402" in r["error"], r
    n += 1

    # 7 流式首 token 前失败：不产出任何 token，citations 为空
    sink: dict = {}
    toks = list(stream_answer("深圳失业保险金标准？", CHUNKS, sink=sink,
                              llm=RaiseStream(Exception("Error code: 402 - Insufficient Balance"))))
    assert toks == [], toks
    assert sink["status"] == "error" and sink["error_kind"] == "balance", sink
    assert sink["answer"] == "" and sink["citations"] == [], sink
    assert sink["error_after_tokens"] is False, sink
    n += 1

    # 8 流式中途失败：保留已出文本，但引用必须清空
    sink2: dict = {}
    toks2 = list(stream_answer("深圳失业保险金标准？", CHUNKS, sink=sink2, llm=FailAfterOne()))
    assert toks2 == ["部分回答"], toks2
    assert sink2["status"] == "error" and sink2["error_kind"] == "balance", sink2
    assert sink2["answer"] == "部分回答", sink2
    assert sink2["citations"] == [], sink2
    assert sink2["error_after_tokens"] is True, sink2
    n += 1

    # 9 正常路径：status=ok，引用正常解析
    ok = answer("深圳失业保险金标准？", CHUNKS, llm=Good())
    assert ok["status"] == "ok", ok
    assert ok["answer"].startswith("标准为 2360"), ok
    assert ok["citations"] == ["测试文件（一、）"], ok
    assert ok["refused"] is False, ok
    n += 1

    # 10 拒答判断未受影响：空答案不误判、错误文本不误判
    assert is_refusal("") is False
    assert is_refusal(REFUSAL) is True
    assert is_refusal("调用 LLM 失败: Error code: 402 - Insufficient Balance") is False
    n += 1

    print("llm error tests: " + str(n) + "/" + str(n) + " PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
