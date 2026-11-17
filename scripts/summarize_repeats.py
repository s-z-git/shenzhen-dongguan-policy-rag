"""汇总 v2 prompt 的 3 次重复评测：主指标、引用、L1。"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

RUNS = [
    ("run1", "data/eval_report.v2_prompt2.json", "data/l1_report_v2_prompt2_air.json"),
    ("run2", "data/eval_report.v2_run2.json", "data/l1_report_v2_run2_air.json"),
    ("run3", "data/eval_report.v2_run3.json", "data/l1_report_v2_run3_air.json"),
]
METRICS = [
    ("recall_at_k", "Recall@5"),
    ("mrr", "MRR"),
    ("golden_coverage", "golden coverage"),
    ("full_recall", "full recall"),
    ("citation_file_match_rate", "引用文件级匹配率"),
    ("citation_verify_rate", "引用条款级可验证率"),
    ("citation_clause_conflict_rate", "条款冲突率"),
    ("number_grounded_rate", "数字可溯源率"),
    ("mean_latency_ms", "mean latency (ms)"),
    ("p95_latency_ms", "P95 latency (ms)"),
]


def load_json(path: str) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> None:
    rows = []
    for name, report_path, l1_path in RUNS:
        report = load_json(report_path)
        l1 = load_json(l1_path)
        if not report:
            print("[skip]", name, report_path)
            continue
        item = {"run": name, "report": report_path, "l1": l1_path}
        item.update(report.get("summary") or {})
        if l1:
            s = l1.get("summary") or {}
            item["l1_coverage"] = s.get("answer_point_coverage")
            item["l1_full"] = s.get("question_full_coverage")
            multi = (s.get("by_type") or {}).get("multi") or {}
            item["l1_multi_coverage"] = multi.get("coverage")
            item["l1_multi_full"] = multi.get("question_full_coverage")
        rows.append(item)

    all_metrics = METRICS + [
        ("l1_coverage", "L1 总覆盖"),
        ("l1_full", "L1 整题全中"),
        ("l1_multi_coverage", "L1 multi 覆盖"),
        ("l1_multi_full", "L1 multi full"),
    ]
    lines = ["# v2 prompt 重复评测汇总", "",
             "| 指标 | " + " | ".join(r["run"] for r in rows) + " | 均值 | 范围 |",
             "|---|" + "---:|" * len(rows) + "---:|---:|"]
    for key, label in all_metrics:
        vals = [r.get(key) for r in rows]
        vals = [float(v) for v in vals if isinstance(v, (int, float))]
        if not vals:
            continue
        mean = statistics.mean(vals)
        spread = max(vals) - min(vals)
        lines.append("| " + label + " | " + " | ".join(
            ("%.4f" % v) if isinstance(v, float) else str(v) for v in
            [r.get(key) for r in rows]) + " | %.4f | %.4f |" % (mean, spread))
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/repeat_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    Path("data/reports/repeat_summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("written data/reports/repeat_summary.md")


if __name__ == "__main__":
    main()
