"""L1 答案要点覆盖率：用独立裁判模型逐条判断 answer_points 是否被回答覆盖。

为什么用 GLM：DeepSeek 是被测生成模型，不能自己判自己。默认 glm-5.3，
可用 RAG_L1_MODEL 环境变量或 --model 覆盖。

口径：
- 只评非 boundary、且有 answer_points 的题；
- 一条要点算 covered 当且仅当回答里能定位到支持它的原文片段；
- 数值/对象/条件必须一致，同义改写算覆盖；
- 结果缓存到 JSONL，重复跑不重复付费。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

PROMPT_VERSION = "l1-judge-v1"
DEFAULT_MODEL = os.environ.get("RAG_L1_MODEL", "glm-5.3")
DEFAULT_BASE_URL = os.environ.get("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")

JUDGE_PROMPT = '''你是严格的 RAG 答案评测员。给定问题、参考要点、模型回答，逐条判断参考要点是否被回答实质覆盖。

判定规则：
1. 数值、对象、城市、年份、条件必须一致；同义改写、语序调整算覆盖。
2. 回答只给部分信息（例如只给金额没给周期、只给结论没给条件）时，该要点判 false。
3. 回答没有写出处、格式不美观，不影响覆盖判断。
4. 回答拒答或说无法回答时，所有要点 false。
5. 不要因为回答提到相同主题就判 true；必须能在回答中找到支持该要点的原文片段。
6. 只依据回答文本，不补充外部知识。

只输出 JSON，格式：
{"points":[{"id":1,"covered":true,"evidence":"回答中的原文片段，最多80字；没有则空字符串"}]}

问题：__QUESTION__
参考要点：__POINTS__
模型回答：__ANSWER__'''


def build_prompt(question: str, answer: str, points: list[str]) -> str:
    return (JUDGE_PROMPT
            .replace("__QUESTION__", question or "")
            .replace("__POINTS__", json.dumps(points, ensure_ascii=False))
            .replace("__ANSWER__", answer or ""))


def cache_key(model: str, question: str, answer: str, points: list[str]) -> str:
    payload = json.dumps({"v": PROMPT_VERSION, "model": model, "q": question,
                          "a": answer, "p": points}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_cache(path: str | Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            if d.get("key"):
                out[d["key"]] = d
        except json.JSONDecodeError:
            continue
    return out


def _repair_evidence_quotes(t: str) -> str:
    """裁判偶尔在 evidence 里写未转义的英文双引号（如 "鹏城英才计划"），
    导致 JSON 非法。只修 evidence 的值，不动结构。"""
    def repl(m):
        return m.group(1) + m.group(2).replace('"', "'") + m.group(3)
    return re.sub(r'("evidence"\s*:\s*")(.*?)("\s*(?:,|}))', repl, t, flags=re.S)


def _extract_json(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    m = re.search(r"[{].*[}]", t, re.S)
    if m:
        t = m.group(0)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # 常见：evidence 里有未转义的中文引号/英文引号
        return json.loads(_repair_evidence_quotes(t))


def parse_judgment(text: str, n_points: int) -> list[dict]:
    d = _extract_json(text)
    raw = d.get("points") or []
    by_id = {}
    for x in raw:
        try:
            by_id[int(x.get("id"))] = x
        except (TypeError, ValueError):
            continue
    out = []
    for i in range(1, n_points + 1):
        x = by_id.get(i)
        if x is None and i - 1 < len(raw):
            x = raw[i - 1]
        if x is None:
            raise ValueError("裁判返回缺少第 %d 条要点" % i)
        out.append({"covered": bool(x.get("covered")),
                    "evidence": str(x.get("evidence") or "")[:160]})
    return out


def judge_one(question: str, answer: str, points: list[str], model: str = DEFAULT_MODEL,
              base_url: str = DEFAULT_BASE_URL, api_key: str | None = None,
              timeout: int = 90, retries: int = 3) -> list[dict]:
    key = api_key or os.environ.get("ZHIPU_API_KEY")
    if not key:
        raise RuntimeError("请先设置 ZHIPU_API_KEY 环境变量")
    prompt = build_prompt(question, answer, points)
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "response_format": {"type": "json_object"}}
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.post(base_url.rstrip("/") + "/chat/completions",
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json"},
                                 json=body, timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise RuntimeError("HTTP %d: %s" % (resp.status_code, resp.text[:200]))
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return parse_judgment(content, len(points))
        except Exception as exc:                 # 网络/限流/JSON 解析失败都重试
            last = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError("L1 裁判失败: " + str(last))


def judge_records(records: list[dict], gold_map: dict[str, dict] | None = None,
                  model: str = DEFAULT_MODEL, workers: int = 4,
                  cache_path: str | Path = "data/l1_cache.jsonl",
                  limit: int = 0, timeout: int = 120,
                  fallback_model: str | None = None) -> tuple[list[dict], dict]:
    """给 records 补 l1 字段；返回 (records, stats)。limit 限制本次新调用数。"""
    gold_map = gold_map or {}
    cache = load_cache(cache_path)
    pending: list[tuple[int, dict, str, list[str], str]] = []
    n_cached = 0
    for idx, rec in enumerate(records):
        if rec.get("type") == "boundary":
            continue
        pts = rec.get("answer_points") or gold_map.get(rec.get("query"), {}).get("answer_points") or []
        if not pts:
            continue
        q = rec.get("query") or ""
        ans = rec.get("answer") or ""
        key = cache_key(model, q, ans, pts)
        # 主模型缓存优先；也可复用之前降级模型的成功结果，避免重复超时
        hit = cache.get(key)
        if not hit and fallback_model:
            hit = cache.get(cache_key(fallback_model, q, ans, pts))
        if hit:
            used_model = hit.get("model") or model
            rec["l1"] = _attach_points(pts, hit.get("judgment") or [], used_model,
                                       hit.get("error"))
            n_cached += 1
            continue
        if limit and len(pending) >= limit:
            continue
        pending.append((idx, rec, q, pts, ans))

    stats = {"n_cached": n_cached, "n_called": 0, "n_error": 0}
    new_entries: list[dict] = []
    if pending:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            fut = {ex.submit(judge_one, q, ans, pts, model, DEFAULT_BASE_URL, None, timeout):
                   (idx, rec, q, pts, ans)
                   for idx, rec, q, pts, ans in pending}
            for f in as_completed(fut):
                idx, rec, q, pts, ans = fut[f]
                key = cache_key(model, q, ans, pts)
                try:
                    judgment = f.result()
                    rec["l1"] = _attach_points(pts, judgment, model)
                    new_entries.append({"key": key, "model": model, "query": q,
                                        "points": pts, "judgment": judgment,
                                        "ts": int(time.time())})
                    stats["n_called"] += 1
                    print("[l1] %d/%d %s" % (stats["n_called"], len(pending), q[:30]), flush=True)
                except Exception as exc:
                    rec["l1"] = _attach_points(pts, [], model, str(exc))
                    stats["n_error"] += 1
                    print("[l1] ERROR " + q[:30] + " -> " + str(exc)[:120], flush=True)
    # 主模型失败（常见于长答案触发超长思考）时，用更快的同代模型补判；
    # 每条记录会保留实际使用的 model，聚合时不会假装全是同一个裁判。
    if fallback_model:
        errs = [r for r in records if isinstance(r.get("l1"), dict) and r["l1"].get("error")
                and r.get("answer_points")]
        stats["n_fallback"] = 0
        for rec in errs:
            q = rec.get("query") or ""
            ans = rec.get("answer") or ""
            pts = rec["answer_points"]
            try:
                judgment = judge_one(q, ans, pts, fallback_model, DEFAULT_BASE_URL, None, timeout)
                rec["l1"] = _attach_points(pts, judgment, fallback_model)
                key = cache_key(fallback_model, q, ans, pts)
                new_entries.append({"key": key, "model": fallback_model, "query": q,
                                    "points": pts, "judgment": judgment,
                                    "ts": int(time.time())})
                stats["n_fallback"] += 1
                print("[l1] fallback %s %s" % (fallback_model, q[:30]), flush=True)
            except Exception as exc:
                print("[l1] FALLBACK ERROR " + q[:30] + " -> " + str(exc)[:120], flush=True)
        stats["n_error"] = sum(1 for r in records if isinstance(r.get("l1"), dict)
                               and r["l1"].get("error"))
    if new_entries:
        p = Path(cache_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            for e in new_entries:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    return records, stats


def _attach_points(points: list[str], judgment: list[dict], model: str,
                   error: str | None = None) -> dict:
    items = []
    for i, p in enumerate(points):
        j = judgment[i] if i < len(judgment) else {"covered": False, "evidence": ""}
        items.append({"point": p, "covered": bool(j.get("covered")),
                      "evidence": j.get("evidence") or ""})
    n = len(items)
    c = sum(1 for x in items if x["covered"])
    out = {"model": model, "total": n, "covered": c,
           "coverage": round(c / n, 4) if n else 0.0,
           "points": items}
    if error:
        out["error"] = error
    return out


def aggregate_answer_points(recs: list[dict]) -> dict:
    # 裁判失败（超时/限流/JSON 错）不能当成 0 分进分母，否则会把覆盖率压低
    rows = [r for r in recs if isinstance(r.get("l1"), dict) and r["l1"].get("total")
            and not r["l1"].get("error")]
    n_q = len(rows)
    n_p = sum(r["l1"]["total"] for r in rows)
    n_c = sum(r["l1"]["covered"] for r in rows)
    per_q = [r["l1"]["coverage"] for r in rows]
    by_type: dict[str, dict] = {}
    for typ in sorted({r.get("type", "fact") for r in rows}):
        sub = [r for r in rows if r.get("type", "fact") == typ]
        tp = sum(r["l1"]["total"] for r in sub)
        tc = sum(r["l1"]["covered"] for r in sub)
        by_type[typ] = {"n_questions": len(sub), "n_points": tp, "covered": tc,
                        "coverage": round(tc / tp, 4) if tp else 0.0,
                        "question_full_coverage": round(
                            sum(1 for r in sub if r["l1"]["covered"] == r["l1"]["total"]) / len(sub), 4)
                        if sub else 0.0}
    return {
        "n_questions": n_q,
        "n_points": n_p,
        "covered_points": n_c,
        "answer_point_coverage": round(n_c / n_p, 4) if n_p else None,
        "mean_question_coverage": round(sum(per_q) / n_q, 4) if n_q else None,
        "question_full_coverage": round(
            sum(1 for r in rows if r["l1"]["covered"] == r["l1"]["total"]) / n_q, 4) if n_q else None,
        "question_any_coverage": round(sum(1 for r in rows if r["l1"]["covered"] > 0) / n_q, 4)
        if n_q else None,
        "n_judge_errors": sum(1 for r in recs if isinstance(r.get("l1"), dict)
                              and r["l1"].get("error")),
        "by_type": by_type,
    }
