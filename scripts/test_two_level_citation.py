"""两级引用策略测试：doc_kind 分组不改判 + 两个新指标的聚合口径。

不依赖 LLM、不读索引，纯函数级验证。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.eval.metrics import aggregate_grounding
from app.generate.verify import build_doc_clause_index, check_grounding

# 一份"有条款块"的文档：第二条、第三条都切出来了
CLAUSE_DOC = "深圳市人力资源保障局关于印发《人才引进补贴实施细则》的通知"
# 一份"只有全文块"的文档：政策解读，整篇一个块，没有条款号
FILE_ONLY_DOC = "关于《人才引进补贴实施细则》的政策解读"
CHUNKS = [
    {"chunk_id": "c1", "doc_id": "d1", "doc_title": CLAUSE_DOC,
     "clause_no": "第二条", "clause_path": "第二条",
     "text": "第二条 补贴标准为每人一次性发放三万元。"},
    {"chunk_id": "c2", "doc_id": "d1", "doc_title": CLAUSE_DOC,
     "clause_no": "第三条", "clause_path": "第三条",
     "text": "第三条 申请材料包括身份证明与社保记录。"},
    {"chunk_id": "c3", "doc_id": "d2", "doc_title": FILE_ONLY_DOC,
     "clause_no": "", "clause_path": "",
     "text": "本解读对补贴标准与申请材料作了说明，具体以正式文件为准。"},
]
INDEX = build_doc_clause_index(CHUNKS)          # {"d1": True, "d2": False}


def test_doc_kind_split_and_status_unchanged() -> None:
    """有条款文档的引用标 clause_doc；无条款文档标 file_only_doc，且 status 不变。"""
    answer = (
        "补贴标准为每人一次性发放三万元[出处: " + CLAUSE_DOC + "（第二条）]。\n"
        "具体以正式文件为准[出处: " + FILE_ONLY_DOC + "]。"
    )
    res = check_grounding(answer, CHUNKS, doc_clause_index=INDEX)
    items = res["citations"]["items"]
    assert len(items) == 2, items

    clause_item = next(i for i in items if "实施细则》的通知" in i["raw"])
    file_item = next(i for i in items if "政策解读" in i["raw"])

    # doc_kind 只做分组，不参与定级
    assert clause_item["doc_kind"] == "clause_doc"
    assert file_item["doc_kind"] == "file_only_doc"
    assert clause_item["status"] == "verified"
    # 关键：file_only_doc 的引用仍然是 no_clause，没有被改判成 verified
    assert file_item["status"] == "no_clause", file_item["status"]

    cites = res["citations"]
    assert cites["total"] == 2
    assert cites["verified"] == 1
    assert cites["no_clause"] == 1
    assert cites["clause_doc"] == 1
    assert cites["clause_verified"] == 1
    assert cites["file_only_doc"] == 1


def test_f3_retrieved_only_fulltext_still_counts_as_clause_doc() -> None:
    """F3 回归：全库有条款块，但召回只带回全文块 #0 —— 必须仍算 clause_doc。

    这是第十轮指出的分母瑕疵：若按"召回块里有没有条款号"判断，这份文档会被
    错标成 file_only_doc，把"召回漏了条款块"从分母里删掉，核验率虚高。
    """
    fulltext_only = dict(CHUNKS[0])                      # d1 的全文块，clause_no 为空
    fulltext_only["chunk_id"] = "d1_full"
    fulltext_only["clause_no"] = ""
    fulltext_only["clause_path"] = ""
    fulltext_only["text"] = "本通知对补贴标准作了规定，其余内容见后续条款。"
    retrieved = [fulltext_only]                          # 只召回了这一个块

    answer = "补贴标准见该通知[出处: " + CLAUSE_DOC + "]。"
    # 不传 index 时按召回块判 → 会误判成 file_only_doc。
    # 注意：这条回退路径仅用于测试与兼容，**不是生产口径**；
    # 生产路径（pipeline.py / ask.py）必须传全库 doc_clause_index。
    naive = check_grounding(answer, retrieved)["citations"]
    assert naive["file_only_doc"] == 1, naive
    assert naive["clause_doc"] == 0, naive

    # 传入全库索引后 → d1 全库有条款块，必须算 clause_doc
    fixed = check_grounding(answer, retrieved, doc_clause_index=INDEX)["citations"]
    assert fixed["clause_doc"] == 1, fixed
    assert fixed["file_only_doc"] == 0, fixed
    # 但 status 判定不受影响：仍然没写坐标，还是 no_clause
    assert fixed["no_clause"] == 1, fixed


def test_clause_verify_rate_uses_clause_doc_denominator() -> None:
    """条款级分母只算 clause_doc：1/1 而不是全口径的 1/2。"""
    answer = (
        "补贴标准为每人一次性发放三万元[出处: " + CLAUSE_DOC + "（第二条）]。\n"
        "具体以正式文件为准[出处: " + FILE_ONLY_DOC + "]。"
    )
    recs = [{
        "type": "fact",
        "grounding": check_grounding(answer, CHUNKS, doc_clause_index=INDEX),
    }]
    agg = aggregate_grounding(recs)
    assert agg["citation_verify_rate"] == 0.5, agg["citation_verify_rate"]
    # 条款级换分母后是 1/1，与全口径并列报告而不是替代它
    assert agg["citation_clause_verify_rate"] == 1.0, agg["citation_clause_verify_rate"]
    assert agg["file_only_doc_citation_share"] == 0.5, agg["file_only_doc_citation_share"]


def test_no_clause_rate_and_file_only_share_are_independent() -> None:
    """两个率各算各的，差值没有含义，不能用来推断 N 类。

    构造：A 指向有条款文档且写了正确坐标(verified)；
         B 指向有条款文档但只写文件名(no_clause)；
         C 指向无条款文档(file_only)。
    no_clause_rate 与 file_only_share 分母语义不同，相加相减都无意义；
    N 类必须显式计数，不能靠两个率相减推。
    """
    answer = (
        "三万元[出处: " + CLAUSE_DOC + "（第二条）]。\n"
        "材料要求见该通知[出处: " + CLAUSE_DOC + "]。\n"
        "以正式文件为准[出处: " + FILE_ONLY_DOC + "]。"
    )
    agg = aggregate_grounding([
        {"type": "fact", "grounding": check_grounding(answer, CHUNKS, doc_clause_index=INDEX)}
    ])
    assert agg["citation_no_clause_rate"] == round(2 / 3, 4), agg["citation_no_clause_rate"]
    assert agg["file_only_doc_citation_share"] == round(1 / 3, 4), agg["file_only_doc_citation_share"]
    assert agg["citation_clause_verify_rate"] == 0.5, agg["citation_clause_verify_rate"]


def test_unmatched_item_has_no_doc_kind() -> None:
    """找不到文件时 doc_kind 为空串，不能算进任何一层的分母。"""
    answer = "某个说法[出处: 完全不存在的文件名称甲乙丙丁戊己庚辛]。"
    res = check_grounding(answer, CHUNKS, doc_clause_index=INDEX)
    item = res["citations"]["items"][0]
    assert item["status"] == "unmatched", item["status"]
    assert item["doc_kind"] == "", item["doc_kind"]

    agg = aggregate_grounding([{"type": "fact", "grounding": res}])
    # 分母为 0 时返回 None，而不是 0.0 假装算过
    assert agg["citation_clause_verify_rate"] is None, agg["citation_clause_verify_rate"]
    assert agg["file_only_doc_citation_share"] == 0.0


def main() -> int:
    cases = [
        test_doc_kind_split_and_status_unchanged,
        test_f3_retrieved_only_fulltext_still_counts_as_clause_doc,
        test_clause_verify_rate_uses_clause_doc_denominator,
        test_no_clause_rate_and_file_only_share_are_independent,
        test_unmatched_item_has_no_doc_kind,
    ]
    for case in cases:
        case()
    print("two-level citation tests: " + str(len(cases)) + "/" + str(len(cases)) + " PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())