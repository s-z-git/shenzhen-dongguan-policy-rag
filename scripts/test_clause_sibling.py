"""F3 条款兄弟块开关测试：同 doc_id 内全文块→条款块，条数与 doc_id 序列不变。"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.retrieve.doc_select import prefer_clause_sibling

BY_ID = {
    "c1": {"chunk_id": "c1", "doc_id": "d1", "clause_no": "", "clause_path": "全文",
           "text": "本通知规定补贴对象、补贴标准与申请材料。"},
    "c2": {"chunk_id": "c2", "doc_id": "d1", "clause_no": "第二条", "clause_path": "第二条",
           "text": "第二条 补贴标准为每人一次性发放三万元。"},
    "c3": {"chunk_id": "c3", "doc_id": "d1", "clause_no": "第三条", "clause_path": "第三条",
           "text": "第三条 申请材料包括身份证明与社保记录。"},
    "c4": {"chunk_id": "c4", "doc_id": "d2", "clause_no": "", "clause_path": "全文",
           "text": "本解读对补贴标准作了说明，具体以正式文件为准。"},
}
BY_DOC = {"d1": [BY_ID["c1"], BY_ID["c2"], BY_ID["c3"]], "d2": [BY_ID["c4"]]}
SCORES = [0.1, 0.9, 0.8, 0.5]
INDEX = {"c1": 0, "c2": 1, "c3": 2, "c4": 3}


def doc_seq(ids):
    return [BY_ID[i]["doc_id"] for i in ids]


def test_replaces_full_text_with_best_clause():
    out = prefer_clause_sibling(["c1", "c4"], "补贴标准是多少", BY_ID, BY_DOC, SCORES, INDEX)
    assert out[0] == "c2", out
    assert out[1] == "c4", out
    assert doc_seq(out) == ["d1", "d2"], out
    assert BY_ID[out[0]]["clause_no"] == "第二条", out


def test_keeps_clause_and_full_only():
    out = prefer_clause_sibling(["c2", "c4"], "补贴标准是多少", BY_ID, BY_DOC, SCORES, INDEX)
    assert out == ["c2", "c4"], out


def test_no_duplicate_when_best_already_selected():
    out = prefer_clause_sibling(["c1", "c2"], "补贴标准是多少", BY_ID, BY_DOC, SCORES, INDEX)
    assert out == ["c1", "c2"], out
    assert doc_seq(out) == ["d1", "d1"], out


def test_no_change_for_full_only_doc():
    out = prefer_clause_sibling(["c4"], "补贴标准是多少", BY_ID, BY_DOC, SCORES, INDEX)
    assert out == ["c4"], out


def main() -> int:
    cases = [test_replaces_full_text_with_best_clause, test_keeps_clause_and_full_only,
             test_no_duplicate_when_best_already_selected, test_no_change_for_full_only_doc]
    for case in cases:
        case()
    print("clause sibling tests: " + str(len(cases)) + "/" + str(len(cases)) + " PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
