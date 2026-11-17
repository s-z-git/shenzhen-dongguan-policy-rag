"""审计 multi 题的 answer_points 是否超出问题范围，并区分标注/裁判问题。

只读 gold、L1 报告和人工审计文件，不修改原始金标，也不调用 LLM。

用法：
    python scripts/audit_multi_points.py
    python scripts/audit_multi_points.py --audit data/annotation/multi_point_audit.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

CATEGORIES = {
    "direct": "直接回答",
    "required_condition": "必要条件",
    "supplementary": "补充信息",
    "version_mismatch": "版本冲突",
    "wrong": "错误要点",
}
STRICT_CATEGORIES = {"direct", "required_condition"}


def load_jsonl(path: str | Path) -> list[dict]:
    """读取 JSONL，跳过空行。"""
    p = Path(path)
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def load_json(path: str | Path) -> dict:
    """读取 JSON。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pct(value: float) -> str:
    """统一输出百分比。"""
    return "%.2f%%" % (value * 100)


def expand_strict_items(question_audit: dict, l1_points: list[dict]) -> list[dict]:
    """把审计标注展开成严格口径的要点列表。

    普通要点直接用 L1 结果，必要时由 audit_covered 人工纠正；
    parts 用于拆开“直接事实 + 无关补充”的混合要点；
    replacement 用于把旧政策要点替换成当前政策口径。
    """
    items: list[dict] = []
    for point_audit in question_audit["points"]:
        index = int(point_audit["index"])
        l1_point = l1_points[index - 1]
        l1_covered = bool(l1_point.get("covered"))
        point_text = l1_point.get("point") or ""
        parts = point_audit.get("parts") or []

        if parts:
            for part in parts:
                category = part["category"]
                items.append({
                    "index": index,
                    "text": part["text"],
                    "category": category,
                    "strict": category in STRICT_CATEGORIES,
                    "covered": bool(part.get("covered")),
                    "l1_covered": l1_covered,
                    "source": "part",
                    "reason": part.get("reason") or point_audit.get("reason") or "",
                })
            continue

        replacement = point_audit.get("replacement")
        if replacement:
            category = replacement["category"]
            items.append({
                "index": index,
                "text": replacement["text"],
                "category": category,
                "strict": category in STRICT_CATEGORIES,
                "covered": bool(replacement.get("covered")),
                "l1_covered": l1_covered,
                "source": "replacement",
                "reason": replacement.get("reason") or point_audit.get("reason") or "",
            })
            continue

        category = point_audit["category"]
        covered = bool(point_audit.get("audit_covered", l1_covered))
        items.append({
            "index": index,
            "text": point_text,
            "category": category,
            "strict": category in STRICT_CATEGORIES,
            "covered": covered,
            "l1_covered": l1_covered,
            "source": "point",
            "reason": point_audit.get("reason") or "",
        })
    return items


def audit_question(gold: dict, l1_record: dict, question_audit: dict) -> dict:
    """计算单题的原始 L1 口径与范围校正口径。"""
    l1 = l1_record.get("l1") or {}
    l1_points = l1.get("points") or []
    gold_points = gold.get("answer_points") or []
    if len(l1_points) != len(gold_points):
        raise ValueError("L1 要点数与 gold 不一致：%s" % gold.get("query"))
    if len(question_audit.get("points") or []) != len(gold_points):
        raise ValueError("审计要点数与 gold 不一致：%s" % gold.get("query"))

    expected_indexes = list(range(1, len(gold_points) + 1))
    actual_indexes = [int(x["index"]) for x in question_audit["points"]]
    if actual_indexes != expected_indexes:
        raise ValueError("审计要点 index 不连续：%s" % gold.get("query"))

    reference_total = len(l1_points)
    reference_covered = sum(1 for x in l1_points if x.get("covered"))
    strict_items = expand_strict_items(question_audit, l1_points)
    strict_kept = [x for x in strict_items if x["strict"]]
    strict_covered = sum(1 for x in strict_kept if x["covered"])

    reference_items = []
    for point_audit in question_audit["points"]:
        index = int(point_audit["index"])
        l1_point = l1_points[index - 1]
        reference_items.append({
            "index": index,
            "text": l1_point.get("point") or "",
            "category": point_audit["category"],
            "covered": bool(l1_point.get("covered")),
        })

    false_negatives = [x for x in strict_kept
                       if x["source"] == "point" and not x["l1_covered"] and x["covered"]]
    false_positives = [x for x in strict_kept
                       if x["source"] == "point" and x["l1_covered"] and not x["covered"]]
    gaps = [x for x in strict_kept if not x["covered"]]

    return {
        "query": gold["query"],
        "reference_total": reference_total,
        "reference_covered": reference_covered,
        "reference_coverage": reference_covered / reference_total if reference_total else 0.0,
        "reference_full": reference_total > 0 and reference_covered == reference_total,
        "strict_total": len(strict_kept),
        "strict_covered": strict_covered,
        "strict_coverage": strict_covered / len(strict_kept) if strict_kept else 0.0,
        "strict_full": bool(strict_kept) and strict_covered == len(strict_kept),
        "strict_items": strict_items,
        "reference_items": reference_items,
        "gaps": gaps,
        "l1_false_negatives": false_negatives,
        "l1_false_positives": false_positives,
        "categories": Counter(x["category"] for x in question_audit["points"]),
        "notes": question_audit.get("notes") or "",
    }


def aggregate(rows: list[dict]) -> dict:
    """汇总 25 条 multi 的结果。"""
    ref_total = sum(x["reference_total"] for x in rows)
    ref_covered = sum(x["reference_covered"] for x in rows)
    strict_total = sum(x["strict_total"] for x in rows)
    strict_covered = sum(x["strict_covered"] for x in rows)
    categories = Counter()
    miss_by_category = Counter()
    for row in rows:
        categories.update(row["categories"])
        for item in row["reference_items"]:
            if not item["covered"]:
                miss_by_category[item["category"]] += 1
    return {
        "n_questions": len(rows),
        "reference_total": ref_total,
        "reference_covered": ref_covered,
        "reference_coverage": ref_covered / ref_total if ref_total else 0.0,
        "reference_full_rate": sum(1 for x in rows if x["reference_full"]) / len(rows),
        "strict_total": strict_total,
        "strict_covered": strict_covered,
        "strict_coverage": strict_covered / strict_total if strict_total else 0.0,
        "strict_full_rate": sum(1 for x in rows if x["strict_full"]) / len(rows),
        "categories": dict(categories),
        "reference_miss_by_category": dict(miss_by_category),
        "n_reference_misses": sum(miss_by_category.values()),
        "n_gaps": sum(len(x["gaps"]) for x in rows),
        "n_false_negatives": sum(len(x["l1_false_negatives"]) for x in rows),
        "n_false_positives": sum(len(x["l1_false_positives"]) for x in rows),
    }


def format_point_line(item: dict) -> str:
    """格式化单条要点。"""
    l1_mark = "L1✓" if item["l1_covered"] else "L1✗"
    audit_mark = "审计✓" if item["covered"] else "审计✗"
    line = "- P%d [%s；%s；%s] %s" % (
        item["index"], CATEGORIES[item["category"]], l1_mark, audit_mark, item["text"])
    if item["reason"]:
        line += "（%s）" % item["reason"]
    return line


def build_markdown(rows: list[dict], summary: dict, audit_data: dict) -> str:
    """生成可提交的 Markdown 审计报告。"""
    lines = [
        "# multi 题 answer_points 对齐审计",
        "",
        "审计日期：%s" % audit_data["meta"].get("reviewed_at", ""),
        "",
        "## 结论",
        "",
        "- 原始 L1：%d 条要点覆盖 %d 条（%s），整题全中 %d/%d（%s）。" % (
            summary["reference_total"], summary["reference_covered"],
            pct(summary["reference_coverage"]),
            sum(1 for x in rows if x["reference_full"]), summary["n_questions"],
            pct(summary["reference_full_rate"])),
        "- 范围校正后：%d 条严格要点覆盖 %d 条（%s），整题全中 %d/%d（%s）。" % (
            summary["strict_total"], summary["strict_covered"],
            pct(summary["strict_coverage"]),
            sum(1 for x in rows if x["strict_full"]), summary["n_questions"],
            pct(summary["strict_full_rate"])),
        "- 原始 L1 的 %d 条漏点中，%d 条是严格口径下的真实缺口，%d 条属于补充信息，%d 条为版本冲突，%d 条为 L1 误判。" % (
            summary["n_reference_misses"],
            summary["n_gaps"],
            summary["reference_miss_by_category"].get("supplementary", 0),
            summary["reference_miss_by_category"].get("version_mismatch", 0),
            summary["n_false_negatives"]),
        "- 因此 **multi full 0.36 不能直接归因于生成能力差**；当前金标范围和版本需要先修订。",
        "",
        "## 总览",
        "",
        "| 口径 | 要点数 | 覆盖 | 整题全中 |",
        "|---|---:|---:|---:|",
        "| 原始 L1 | %d | %d（%s） | %d/%d（%s） |" % (
            summary["reference_total"], summary["reference_covered"],
            pct(summary["reference_coverage"]),
            sum(1 for x in rows if x["reference_full"]), summary["n_questions"],
            pct(summary["reference_full_rate"])),
        "| 范围校正 | %d | %d（%s） | %d/%d（%s） |" % (
            summary["strict_total"], summary["strict_covered"],
            pct(summary["strict_coverage"]),
            sum(1 for x in rows if x["strict_full"]), summary["n_questions"],
            pct(summary["strict_full_rate"])),
        "",
        "## 要点分类",
        "",
        "| 分类 | 数量 | 含义 |",
        "|---|---:|---|",
    ]
    for key in ["direct", "required_condition", "supplementary", "version_mismatch", "wrong"]:
        lines.append("| %s | %d | %s |" % (
            CATEGORIES[key], summary["categories"].get(key, 0), audit_data["meta"]["category_meaning"][key]))
    lines += [
        "",
        "## 按题结果",
        "",
        "| # | 问题 | 原始 L1 | 范围校正 | 结论 |",
        "|---:|---|---:|---:|---|",
    ]
    for i, row in enumerate(rows, 1):
        result = "全中" if row["strict_full"] else "有缺口"
        lines.append("| %d | %s | %d/%d | %d/%d | %s |" % (
            i, row["query"], row["reference_covered"], row["reference_total"],
            row["strict_covered"], row["strict_total"], result))
    lines += ["", "## 剩余真实缺口", ""]
    gap_rows = [x for x in rows if x["gaps"]]
    if not gap_rows:
        lines.append("无。")
    else:
        for row in gap_rows:
            lines.append("### %s" % row["query"])
            lines.append("")
            for gap in row["gaps"]:
                lines.append("- [%s] %s（%s）" % (
                    CATEGORIES[gap["category"]], gap["text"], gap["reason"] or "回答未覆盖"))
            lines.append("")
    lines += ["## 金标/裁判问题", ""]
    version_rows = []
    false_negative_rows = []
    for row in rows:
        if row["categories"].get("version_mismatch"):
            version_rows.append(row)
        if row["l1_false_negatives"]:
            false_negative_rows.append(row)
    if version_rows:
        lines.append("### 版本冲突")
        lines.append("")
        for row in version_rows:
            lines.append("- %s" % row["query"])
            for item in row["strict_items"]:
                if item["source"] == "replacement":
                    lines.append("  - 旧要点已由当前口径替换：%s" % item["text"])
        lines.append("")
    if false_negative_rows:
        lines.append("### L1 误判")
        lines.append("")
        for row in false_negative_rows:
            for item in row["l1_false_negatives"]:
                lines.append("- %s：P%d「%s」回答已写，L1 判 false。" % (
                    row["query"], item["index"], item["text"]))
        lines.append("")
    lines += ["## 逐题明细", ""]
    for i, row in enumerate(rows, 1):
        lines.append("### %d. %s" % (i, row["query"]))
        lines.append("")
        lines.append("原始 L1：%d/%d；范围校正：%d/%d。" % (
            row["reference_covered"], row["reference_total"],
            row["strict_covered"], row["strict_total"]))
        if row["notes"]:
            lines.append("")
            lines.append("审计备注：%s" % row["notes"])
        lines.append("")
        for item in row["strict_items"]:
            lines.append(format_point_line(item))
        lines.append("")
    lines += ["## 建议", ""]
    for item in audit_data.get("conclusions") or []:
        lines.append("- " + item)
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", default="data/eval_gold.reviewed.jsonl")
    parser.add_argument("--l1", default="data/l1_report_v2_prompt2_air.json")
    parser.add_argument("--audit", default="data/annotation/multi_point_audit.json")
    parser.add_argument("--out", default="data/reports/multi_point_audit.md")
    args = parser.parse_args()

    gold_rows = [x for x in load_jsonl(args.gold) if x.get("type") == "multi"]
    l1_report = load_json(args.l1)
    l1_map = {x["query"]: x for x in l1_report.get("records") or []}
    audit_data = load_json(args.audit)
    audit_map = {x["query"]: x for x in audit_data.get("questions") or []}

    if len(gold_rows) != len(audit_map):
        raise SystemExit("multi 题数不一致：gold=%d，audit=%d" % (len(gold_rows), len(audit_map)))

    rows = []
    for gold in gold_rows:
        query = gold["query"]
        if query not in l1_map:
            raise SystemExit("L1 报告缺少问题：" + query)
        if query not in audit_map:
            raise SystemExit("审计文件缺少问题：" + query)
        rows.append(audit_question(gold, l1_map[query], audit_map[query]))

    summary = aggregate(rows)
    markdown = build_markdown(rows, summary, audit_data)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown + "\n", encoding="utf-8")

    print("[audit] " + json.dumps(summary, ensure_ascii=False))
    print("[audit] 结果 -> " + args.out)


if __name__ == "__main__":
    main()
