"""citation_view 纯函数测试：不依赖 Streamlit、不调 LLM。"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.ui.citation_view import find_citation_item, find_matched_chunks, highlight_clause

GROUNDING = {
    "citations": {
        "items": [
            {"raw": "文件A（第二条）", "status": "verified", "matched_ids": ["c1"]},
            {"raw": "文件B（第三条）", "status": "clause_conflict",
             "matched_ids": ["c2"], "actual_clause": ["全文"]},
        ]
    }
}
CHUNKS = [
    {"chunk_id": "c1", "doc_title": "文件A", "clause_path": "第二条",
     "text": "第二条 这是原文。"},
    {"chunk_id": "c2", "doc_title": "文件B", "clause_path": "全文",
     "text": "全文原文。"},
]


def main() -> int:
    exact = find_citation_item("文件A（第二条）", GROUNDING)
    assert exact is not None and exact["status"] == "verified"

    prefix = find_citation_item("文件A（第二条）补充说明", GROUNDING)
    assert prefix is not None and prefix["raw"] == "文件A（第二条）"

    assert find_citation_item("不存在的文件（第九条）", GROUNDING) is None

    matched = find_matched_chunks(exact, CHUNKS)
    assert [c["chunk_id"] for c in matched] == ["c1"]

    highlighted = highlight_clause("第二条 这是原文。", "第二条")
    assert "<mark>第二条</mark>" in highlighted

    print("citation_view tests: 5/5 PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
