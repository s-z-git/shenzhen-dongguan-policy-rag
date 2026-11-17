"""F3 真实样本演示：3 份文档的全文块 #0 换成条款兄弟块，doc_id 必须不变。"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.retrieve.doc_select import prefer_clause_sibling

BASE = pathlib.Path(__file__).resolve().parents[1]
CHUNKS = [json.loads(l) for l in open(BASE / "data" / "chunks.jsonl", encoding="utf-8") if l.strip()]
BY_ID = {c["chunk_id"]: c for c in CHUNKS}
BY_DOC = {}
for c in CHUNKS:
    BY_DOC.setdefault(c["doc_id"], []).append(c)
INDEX = {c["chunk_id"]: i for i, c in enumerate(CHUNKS)}
SCORES = [0.5] * len(CHUNKS)

TITLES = [
    "关于做好贯彻执行我市最低工资标准相关工作的通知",
    "《东莞市本科生引进培养暂行办法》政策解读（已失效）",
    "东莞市引进特色奖励申领暂行细则(东人社发〔2022〕43号)政策解读（已失效）",
]


def main() -> int:
    failed = 0
    for title in TITLES:
        docs = [d for d in BY_DOC.values() if d[0].get("doc_title") == title]
        if not docs:
            print("MISS", title)
            failed += 1
            continue
        doc_chunks = docs[0]
        full = next((c for c in doc_chunks if not (c.get("clause_no") or "").strip()), None)
        if not full:
            print("NO_FULL", title)
            failed += 1
            continue
        out = prefer_clause_sibling([full["chunk_id"]], "补贴标准是多少", BY_ID, BY_DOC, SCORES, INDEX)
        new = BY_ID[out[0]]
        same_doc = new["doc_id"] == full["doc_id"]
        has_clause = bool((new.get("clause_no") or "").strip())
        print(f"{title[:28]:30} #0(全文) -> #{new['chunk_id'].split('#')[-1]} "
              f"clause={new.get('clause_no')!r} same_doc={same_doc}")
        if not same_doc or not has_clause:
            failed += 1
    if failed:
        print("FAIL", failed)
        return 1
    print("F3 demo: 3/3 PASS (doc_id unchanged, clause filled)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
