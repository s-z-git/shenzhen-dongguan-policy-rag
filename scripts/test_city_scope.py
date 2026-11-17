"""A8 测试：纯域外城市规则 + pipeline 短路。

覆盖：
- city_scope 判定（域外/混合/深莞/省名/港澳）
- pipeline.ask：广州题不进检索、返回拒答
- pipeline.ask_stream：同样短路
- 混合题/东莞题不短路（retriever 会被调用）
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.generate.query_analysis import city_scope  # noqa: E402
from app.generate.generator import REFUSAL  # noqa: E402
from app.pipeline import RAGPipeline  # noqa: E402


class BoomRetriever:
    """任何检索调用都抛异常——用来证明短路真的没进检索。"""

    def retrieve(self, *a, **kw):  # noqa: ARG002
        raise AssertionError("retriever should not be called")


def main() -> int:
    n = 0
    # 1 域外：广州
    s = city_scope("广州灵活就业社保补贴一个月多少钱？")
    assert s["out_of_scope"] == ["广州"] and s["pure_out_of_scope"] is True, s
    n += 1
    # 2 混合：深圳 + 广州（不能拒）
    s = city_scope("在深圳工作，广州的社保能转入深圳吗？")
    assert s["in_scope"] == ["深圳"] and s["out_of_scope"] == ["广州"]
    assert s["pure_out_of_scope"] is False, s
    n += 1
    # 3 深莞：不触发
    s = city_scope("东莞职业介绍补贴的标准是多少？")
    assert s["pure_out_of_scope"] is False and s["in_scope"] == ["东莞"], s
    n += 1
    # 4 省名：不是城市
    s = city_scope("广东省就业创业补贴申请办理指导清单有哪些？")
    assert s["out_of_scope"] == [] and s["pure_out_of_scope"] is False, s
    n += 1
    # 5 港澳/北京：域外
    for q in ("去香港工作需要办什么签证？", "北京落户政策是什么？"):
        assert city_scope(q)["pure_out_of_scope"] is True, q
    n += 1
    # 6 广东其它地级市：域外
    s = city_scope("佛山的灵活就业补贴标准是多少？")
    assert s["pure_out_of_scope"] is True and s["out_of_scope"] == ["佛山"], s
    n += 1

    # 7-10 pipeline 短路
    pipe = RAGPipeline.load()
    pipe.cfg.trace_enabled = False
    pipe.retriever = BoomRetriever()          # 一旦进检索就炸

    r = pipe.ask("广州灵活就业社保补贴一个月多少钱？")
    assert r["answer"] == REFUSAL and r["refused"] is True, r.get("answer")
    assert r["status"] == "ok" and r["citations"] == [], r
    assert r["out_of_scope_city"] == ["广州"], r
    assert r["notes"] == ["当前知识库未收录该地区或该事项的相关政策。"], r["notes"]
    n += 1

    sink: dict = {}
    toks = list(pipe.ask_stream("广州灵活就业社保补贴一个月多少钱？", sink=sink))
    assert toks == [], toks
    res = sink.get("result") or {}
    assert res.get("answer") == REFUSAL and res.get("refused") is True, res
    assert res.get("out_of_scope_city") == ["广州"], res
    assert sink.get("will_refuse") is True, sink
    n += 1

    # 混合题不短路：retriever 被调用 → BoomRetriever 抛错
    try:
        pipe.ask("在深圳工作，广州的社保能转入深圳吗？")
        raise AssertionError("mixed question should reach retriever")
    except AssertionError as exc:
        assert "retriever should not be called" in str(exc), exc
    n += 1

    # 东莞题不短路
    try:
        pipe.ask("东莞职业介绍补贴的标准是多少？")
        raise AssertionError("in-scope question should reach retriever")
    except AssertionError as exc:
        assert "retriever should not be called" in str(exc), exc
    n += 1

    print("city scope tests: " + str(n) + "/" + str(n) + " PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
