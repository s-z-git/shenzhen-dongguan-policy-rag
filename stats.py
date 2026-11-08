"""读取 trace JSONL，输出 P50/P95、拒答率、token 均值等运行统计。

用法：
    python stats.py
    python stats.py --path data/traces/ask.jsonl --last 50
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from app.observability import read_traces


def percentile(values: list[float], p: float) -> float:
    """最近秩百分位；样本为空时返回 0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * p))
    return round(ordered[idx], 2)


def summarize(rows: list[dict]) -> dict:
    """汇总 trace 的关键指标。"""
    if not rows:
        return {"n": 0}
    timings: dict[str, list[float]] = {}
    for row in rows:
        for key, value in (row.get("timings_ms") or {}).items():
            if isinstance(value, (int, float)):
                timings.setdefault(key, []).append(float(value))
    timing_stats = {}
    for key, values in sorted(timings.items()):
        timing_stats[key] = {
            "p50": percentile(values, 0.50),
            "p95": percentile(values, 0.95),
            "mean": round(sum(values) / len(values), 2),
            "n": len(values),
        }
    answer_chars = [row.get("answer_chars") for row in rows
                    if isinstance(row.get("answer_chars"), (int, float))]
    first_token_ms = [row.get("first_token_ms") for row in rows
                      if isinstance(row.get("first_token_ms"), (int, float))]
    ttft_ms = [row.get("ttft_ms") for row in rows
               if isinstance(row.get("ttft_ms"), (int, float))]
    total_tokens = []
    reasoning_tokens = []
    output_tokens = []
    for row in rows:
        usage = row.get("usage") or {}
        if isinstance(usage.get("total_tokens"), (int, float)):
            total_tokens.append(float(usage["total_tokens"]))
        out = usage.get("output_tokens") or usage.get("completion_tokens")
        if isinstance(out, (int, float)):
            output_tokens.append(float(out))
        reasoning = (usage.get("output_token_details") or {}).get("reasoning")
        if isinstance(reasoning, (int, float)):
            reasoning_tokens.append(float(reasoning))
    return {
        "n": len(rows),
        "refusal_rate": round(
            sum(1 for row in rows if row.get("refused")) / len(rows), 4),
        "mean_answer_chars": round(sum(answer_chars) / len(answer_chars), 1)
        if answer_chars else None,
        "mean_total_tokens": round(sum(total_tokens) / len(total_tokens), 1)
        if total_tokens else None,
        "mean_reasoning_tokens": round(sum(reasoning_tokens) / len(reasoning_tokens), 1)
        if reasoning_tokens else None,
        "reasoning_token_share": round(sum(reasoning_tokens) / sum(output_tokens), 4)
        if output_tokens and sum(output_tokens) else None,
        "first_token_ms": {
            "p50": percentile(first_token_ms, 0.50),
            "p95": percentile(first_token_ms, 0.95),
            "mean": round(sum(first_token_ms) / len(first_token_ms), 2),
            "n": len(first_token_ms),
        } if first_token_ms else None,
        "ttft_ms": {
            "p50": percentile(ttft_ms, 0.50),
            "p95": percentile(ttft_ms, 0.95),
            "mean": round(sum(ttft_ms) / len(ttft_ms), 2),
            "n": len(ttft_ms),
        } if ttft_ms else None,
        "prompt_versions": dict(Counter(row.get("prompt_version") or "unknown"
                                        for row in rows)),
        "timings_ms": timing_stats,
    }


def _parse_time(value: str | None, end_of_day: bool = False) -> datetime | None:
    """解析 --since/--until；只给日期时用本地时区。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit("时间格式应为 ISO，例如 2026-11-08 或 2026-11-08T20:00:00") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    if len(value) == 10 and end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt


def in_time_window(row: dict, since: datetime | None, until: datetime | None) -> bool:
    """按 trace.ts 过滤；无法解析 ts 的记录在显式时间窗下剔除。"""
    if since is None and until is None:
        return True
    raw = row.get("ts")
    if not raw:
        return False
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    if since is not None and dt < since:
        return False
    if until is not None and dt > until:
        return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="data/traces/ask.jsonl")
    ap.add_argument("--last", type=int, default=0)
    ap.add_argument("--since", default="", help="只统计 ts >= 该 ISO 时间的记录，如 2026-11-08")
    ap.add_argument("--until", default="", help="只统计 ts <= 该 ISO 时间的记录；只给日期时取当天 23:59:59")
    a = ap.parse_args()
    since = _parse_time(a.since)
    until = _parse_time(a.until, end_of_day=True)
    rows = read_traces(a.path, last=a.last)
    rows = [row for row in rows if in_time_window(row, since, until)]
    summary = summarize(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not rows:
        print("[stats] 没有可读的 trace：" + a.path)


if __name__ == "__main__":
    main()
