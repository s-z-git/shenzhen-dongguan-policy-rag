"""对已有 eval_report 的回答做 L1 答案要点覆盖率评测（GLM 独立裁判）。

用法：
    python judge_l1.py --report data/eval_report.v3_final.json
    python judge_l1.py --report data/eval_report.v3_final.json --limit 3   # 冒烟
    python judge_l1.py --report data/eval_report.v3_final.json --workers 4
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.eval.answer_points import (DEFAULT_MODEL, aggregate_answer_points,
                                    judge_records)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="data/eval_report.v3_final.json")
    ap.add_argument("--gold", default="data/eval_gold.reviewed.v3.jsonl")
    ap.add_argument("--out", default="data/l1_report_v3_final.json")
    ap.add_argument("--cache", default="data/l1_cache.jsonl")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--fallback-model", default="glm-5.3-flash",
                    help="主裁判超时/失败时的降级模型；留空则禁用")
    a = ap.parse_args()

    report = json.loads(Path(a.report).read_text(encoding="utf-8"))
    records = report.get("records") or []
    if not records:
        raise SystemExit("报告里没有 records（ablation-only 报告不能做 L1）")
    gold = [json.loads(ln) for ln in Path(a.gold).read_text(encoding="utf-8").splitlines()
            if ln.strip()]
    gold_map = {x["query"]: x for x in gold}

    for rec in records:
        # gold 文件优先：v3 strict gold 需要覆盖报告里内嵌的旧 answer_points，
        # 否则换 --gold 仍会命中旧口径缓存。
        pts = gold_map.get(rec.get("query"), {}).get("answer_points") or rec.get("answer_points") or []
        rec["answer_points"] = pts

    recs, stats = judge_records(records, gold_map=gold_map, model=a.model,
                                workers=a.workers, cache_path=a.cache, limit=a.limit)
    summary = aggregate_answer_points(recs)
    out = {"summary": summary, "judge_model": a.model, "stats": stats,
           "source_report": a.report, "records": [
               {"query": r.get("query"), "type": r.get("type"),
                "answer_points": r.get("answer_points") or [],
                "answer": r.get("answer") or "", "l1": r.get("l1")}
               for r in recs]}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[l1] " + json.dumps(summary, ensure_ascii=False))
    print("[l1] 缓存命中 %d，本次调用 %d，降级 %d，失败 %d"
          % (stats["n_cached"], stats["n_called"], stats.get("n_fallback", 0),
             stats["n_error"]))
    rows = sorted([r for r in recs if r.get("l1")], key=lambda r: r["l1"]["coverage"])
    print("[l1] 覆盖率最低的题：")
    for r in rows[:10]:
        miss = [p["point"] for p in r["l1"]["points"] if not p["covered"]]
        print("   %.2f  %s | 未覆盖: %s" % (r["l1"]["coverage"], r["query"][:50],
                                           "；".join(miss)[:100]))
    print("[l1] 结果 -> " + a.out)


if __name__ == "__main__":
    main()
