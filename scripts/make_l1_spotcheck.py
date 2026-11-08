"""生成 L1 人工抽检表：分层抽 20 条，并标出可疑的裁判 false negative。

用法：
    python scripts/make_l1_spotcheck.py
输出：
    data/reports/l1_spotcheck.md
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

from app.generate.verify import _numbers


def norm(text: str) -> str:
    return re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", text or "")


def possible_false_negative(point: str, answer: str) -> bool:
    """回答里出现了要点中的全部数字，或长原句基本出现，则标为可疑。"""
    p_nums = [round(v, 4) for v, _, _ in _numbers(point)]
    a_nums = {round(v, 4) for v, _, _ in _numbers(answer)}
    if p_nums and all(v in a_nums for v in p_nums):
        return True
    np = norm(point)
    if len(np) >= 8 and np in norm(answer):
        return True
    return False


def main() -> None:
    report = json.loads(Path("data/l1_report_v2_prompt2_air.json").read_text(encoding="utf-8"))
    rows = [r for r in report["records"] if r.get("l1")]
    rnd = random.Random(42)
    picked = []
    for typ, n in (("fact", 7), ("multi", 8), ("stale", 5)):
        group = [r for r in rows if r.get("type") == typ]
        picked.extend(rnd.sample(group, min(n, len(group))))
    picked.sort(key=lambda r: (r.get("type", ""), r.get("query", "")))

    out = ["# L1 人工抽检（v2 prompt，20 条）", "",
           "标记说明：⚠️ = 回答里出现了要点数字/原句，但裁判判 false，优先人工核对。", ""]
    suspicious = 0
    for i, r in enumerate(picked, 1):
        l1 = r["l1"]
        out.append("## %d. %s" % (i, r["query"]))
        out.append("")
        out.append("- 类型：%s；覆盖率：%.2f（%d/%d）" % (
            r.get("type"), l1["coverage"], l1["covered"], l1["total"]))
        out.append("- 回答：" + (r.get("answer") or "").replace("\n", " ")[:1200])
        out.append("")
        for p in l1["points"]:
            flag = ""
            if not p["covered"] and possible_false_negative(p["point"], r.get("answer") or ""):
                flag = " ⚠️"
                suspicious += 1
            out.append("- [%s]%s %s" % ("覆盖" if p["covered"] else "未覆盖", flag, p["point"]))
            if p.get("evidence"):
                out.append("  - 裁判依据：" + p["evidence"])
        out.append("")
    out.insert(3, "- 可疑 false negative：%d 条" % suspicious)
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/l1_spotcheck.md").write_text("\n".join(out), encoding="utf-8")
    print("written data/reports/l1_spotcheck.md; suspicious =", suspicious)


if __name__ == "__main__":
    main()
