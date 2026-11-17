"""从人工审计文件生成 strict gold（v3），不覆盖旧金标。

用法：
    python scripts/build_strict_gold.py
    python scripts/build_strict_gold.py --out data/eval_gold.reviewed.v3.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

STRICT_CATEGORIES = {"direct", "required_condition"}


def load_jsonl(path: str | Path) -> list[dict]:
    """读取 JSONL。"""
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def strict_points(record: dict, question_audit: dict) -> list[str]:
    """按审计分类提取问题范围内的要点，并替换已过时的政策口径。"""
    original_points = record.get("answer_points") or []
    points: list[str] = []
    for point_audit in question_audit["points"]:
        index = int(point_audit["index"])
        parts = point_audit.get("parts") or []
        if parts:
            for part in parts:
                if part["category"] in STRICT_CATEGORIES:
                    points.append(str(part["text"]))
            continue

        replacement = point_audit.get("replacement")
        if replacement:
            if replacement["category"] in STRICT_CATEGORIES:
                points.append(str(replacement["text"]))
            continue

        if point_audit["category"] in STRICT_CATEGORIES:
            points.append(str(original_points[index - 1]))
    return points


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", default="data/eval_gold.reviewed.jsonl")
    parser.add_argument("--audit", default="data/annotation/multi_point_audit.json")
    parser.add_argument("--out", default="data/eval_gold.reviewed.v3.jsonl")
    args = parser.parse_args()

    gold = load_jsonl(args.gold)
    audit_data = json.loads(Path(args.audit).read_text(encoding="utf-8"))
    audit_map = {x["query"]: x for x in audit_data.get("questions") or []}

    out_rows = []
    changed = 0
    for record in gold:
        item = dict(record)
        question_audit = audit_map.get(item.get("query"))
        if item.get("type") == "multi" and question_audit:
            item["reference_answer_points"] = list(item.get("answer_points") or [])
            item["answer_points"] = strict_points(item, question_audit)
            item["gold_version"] = "v3-strict"
            item["point_audit"] = args.audit.replace("\\", "/")
            changed += 1
        out_rows.append(item)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in out_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    multi_points = sum(len(x["answer_points"]) for x in out_rows if x.get("type") == "multi")
    print("[strict-gold] 写入 %d 条；multi 改写 %d 条；strict multi 要点 %d 条" % (
        len(out_rows), changed, multi_points))
    print("[strict-gold] -> " + str(out_path))


if __name__ == "__main__":
    main()
