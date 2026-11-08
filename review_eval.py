"""
入口：交互式评测集复核（一条条看原文，按 y/n/s 决定收录）

用法：
    python review_eval.py                       # 复核候选池，写 data/eval_gold.reviewed.jsonl
    python review_eval.py --limit 10            # 只过前 10 条（先试手感）
    python review_eval.py --targets fact=20,multi=15,stale=10,boundary=5

按键：
    y  收录（verified=true）
    n  剔除（记进 review_state.json，以后不再问）
    s  本次跳过（下次还会问）
    e  改 answer_points（用分号分隔），改完再问 y/n
    t  改类型（fact → multi → stale → boundary 循环）
    v  看全文目录（该文档全部块，按条款顺序）
    o <编号>  看目录里第 n 块的整段原文（如 o 7）
    q  保存并退出

设计说明：
1. 默认写 `data/eval_gold.reviewed.jsonl`，**不覆盖**原评测集；确认无误后再自己替换。
2. 进度存在 `data/review_state.json`，中断后重跑会从没复核的条目继续。
3. 每条都会贴出 golden 文档里的**原文片段**——复核看的应该是原文，不是自己的记忆。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from app.retrieve import tokenize

NL = chr(10)
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
TYPE_ORDER = ["fact", "multi", "stale", "boundary"]
_LAST_BLOCKS: list[dict] = []


def load_jsonl(path: Path) -> list[dict]:
    """读 JSONL；文件不存在返回空列表。"""
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def save_jsonl(path: Path, items: list[dict]) -> None:
    """整文件重写（几百条规模，重写最简单也最不容易出错）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline=NL) as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + NL)


def load_state(path: Path) -> dict:
    """复核状态：{query: "y"|"n"}。"""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def quotas_of(items: list[dict]) -> dict:
    """按类型统计已收录条数（每次重算，避免重复计数）。"""
    return {t: sum(1 for x in items if x.get("type") == t) for t in TYPE_ORDER}


def build_pool(files: list[Path]) -> list[dict]:
    """合并多个池子，按 query 去重（先出现的字段优先，后来的只补缺）。"""
    seen: dict[str, dict] = {}
    for f in files:
        for it in load_jsonl(f):
            q = str(it.get("query") or "").strip()
            if not q:
                continue
            if q not in seen:
                seen[q] = dict(it)
            else:
                for k, v in it.items():
                    if not seen[q].get(k):
                        seen[q][k] = v
    order = {t: i for i, t in enumerate(TYPE_ORDER)}
    return sorted(seen.values(), key=lambda x: (order.get(x.get("type"), 9), x["query"]))


def snippet_of(chunk: dict, width: int = 200) -> str:
    """把正文压成一行、截断，便于在终端里看。"""
    t = re.sub(r"\s+", " ", str(chunk.get("text") or "")).strip()
    return t[:width] + ("…" if len(t) > width else "")


_STOP = {"什么", "怎么", "哪些", "多少", "多久", "可以", "是否", "需要", "以及", "还有",
         "我们", "你们", "请问", "一下", "这个", "那个", "的话", "标准是", "分别是"}


def _keywords(text: str) -> list[str]:
    """抽用于打分的词：≥2 字、去停用词、去重（中文两字词才是主要信息载体）。"""
    out: list[str] = []
    for t in tokenize(text or ""):
        if len(t) >= 2 and t not in _STOP and t not in out:
            out.append(t)
    return out


def pick_fragment(query: str, title: str) -> str:
    """一问答两件事时，给每篇 golden 只喂「跟它最相关的那半句问题」。

    否则第二问的关键词会把第一问的原文挑歪（实测：问「乡村补贴按什么标准」，
    却挑出了「补贴期限」那一句）。
    """
    frags = [f.strip() for f in re.split(r"[？?；;]", query or "") if f.strip()]
    if len(frags) <= 1:
        return query or ""
    ttoks = set(_keywords(title))
    return max(frags, key=lambda f: len(set(_keywords(f)) & ttoks))


def best_sentences(text: str, query: str, points: list[str], k: int = 2,
                   width: int = 180) -> list[str]:
    """从长正文里挑「最可能回答问题」的 1–2 句，而不是傻傻截开头。

    打分：命中问题词 +2 / 含数字 +1 / 命中要点里的数字 +1 / 以「补贴标准」「简述」开头 +1。
    数字权重刻意压得很低——否则问「按什么标准」会被「补贴期限：最长 3 年」抢走。
    """
    raw = str(text or "")
    sents = [x.strip() for x in re.split(r"(?<=[。；;])|\n", raw)
             if x.strip()
             and not x.strip().endswith(("？", "?"))          # 设问句不是答案
             and not x.strip().startswith(("点击查看", "详见附件"))  # 跳转链接类噪音
             and not re.search(r"(关于印发|的通知|的实施意见|的公告)$", x.strip())]  # 文件标题行
    if not sents:
        return [raw[:width]]
    words = _keywords(query or "")
    nums = [re.sub(r"[^\d.]", "", p) for p in (points or [])]
    nums = [n for n in nums if n]

    bad = ("申请表", "复印件", "提交材料", "申请程序", "网办路径", "受理资料", "填表")

    def score(x: str) -> int:
        v = sum(1 for w in words if w in x) * 2
        v += 1 if re.search(r"\d", x) else 0
        v += 1 if any(n in x for n in nums) else 0
        v += 1 if x.startswith(("补贴标准", "补贴期限", "简述", "标准", "发放标准")) else 0
        # 「有哪几种 / 多久」这类问句：直接命中「种类为 / 期间为」的句子权重最高
        if re.search(r"几种|哪几种|哪些种类|什么种类", query or "") and re.search(r"种类为|分为.{0,8}种", x):
            v += 8
        if re.search(r"多久|多长|期限|期间|几年|几个月", query or "") and "期间为" in x:
            v += 8
        if re.search(r"多久|多长|期限|期间|几年|几个月", query or "") and re.search(r"工作日内|个工作日|个月内|次月", x):
            v += 4
        v -= 4 if any(b in x for b in bad) else 0     # 申请材料/流程类句子没有信息量
        return v

    def with_items(i: int) -> str:
        """「第五条 处分的种类为：」这种标题句，把后面的（一）（二）…接上。"""
        x = sents[i]
        if not (x.endswith(("：", ":")) or len(x) < 20):
            return x
        extra = []
        for j in range(i + 1, min(i + 6, len(sents))):
            nxt = sents[j]
            if re.match(r"^[（(][一二三四五六七八九十\d]", nxt) or re.match(r"^\d+[\.、]", nxt):
                extra.append(nxt)
            else:
                break
        return (x + " " + " ".join(extra)) if extra else x

    # 「有哪几种 / 多久」：直接命中「种类为 / 期间为」的句子**无条件排最前**。
    # 不做这一步的话，像「第三条 给予处分应当坚持公正公平…」这种满篇都是
    # 「事业单位/工作人员/处分」的句子会把真正的定义句挤掉。
    ask_kind = re.search(r"几种|哪几种|哪些种类|什么种类", query or "")
    ask_dur = re.search(r"多久|多长|期限|期间|几年|几个月", query or "")

    def structural(x: str) -> bool:
        return bool((ask_kind and re.search(r"种类为|分为.{0,8}种", x))
                    or (ask_dur and "期间为" in x))

    order = sorted(range(len(sents)),
                   key=lambda x: (0 if structural(sents[x]) else 1, -score(sents[x])))
    picked, used = [], set()
    for idx in order:
        x = with_items(idx)
        if x in used or (picked and not structural(sents[idx]) and score(sents[idx]) < 7):
            continue
        picked.append(x)
        used.add(x)
        if len(picked) >= k:
            break
    return [(x[:width] + ("…" if len(x) > width else "")) for x in picked] or [raw[:width]]


def pick_chunks(doc_id: str, clause: str, by_doc: dict, by_id: dict,
                points: list[str], query: str = "", limit: int = 1) -> list[dict]:
    """挑最能支撑判断的条款：指定条款 > 与问题词重合最多的块 > 含数字的块 > 前两块。"""
    if "#" in doc_id and not clause:
        doc_id, clause = doc_id.split("#", 1)
    if clause:
        hit = [c for c in by_doc.get(doc_id, [])
               if clause in str(c.get("clause_no") or "") or clause in str(c.get("clause_path") or "")]
        if hit:
            return hit[:limit]
    chunks = by_doc.get(doc_id, [])
    nums = [re.sub(r"[^\d.]", "", p) for p in points]
    nums = [n for n in nums if n]
    if nums:
        hit = [c for c in chunks if any(n in str(c.get("text") or "") for n in nums)]
        if hit:
            return hit[:limit]
    # 用问题里的词给块打分（长文档的关键条款通常不在开头）
    qwords = _keywords(query or "")
    if qwords:
        def chunk_score(c: dict) -> int:
            t = str(c.get("text") or "")
            v = sum(1 for w in qwords if w in t)
            # 「有哪几种 / 多久」这类问句，优先找含「种类 / 期间」和枚举的块
            if re.search(r"几种|哪几种|哪些种类|什么种类", query or "") and re.search(r"种类为|分为.{0,8}种", t):
                v += 3
            if re.search(r"多久|多长|期限|期间|几年|几个月", query or "") and "期间为" in t:
                v += 3
            # 问「多久」时，含明确时限表述的块优先（工作日内 / 次月 / 个月内）
            if re.search(r"多久|多长|期限|期间|几年|几个月", query or "") and re.search(r"工作日内|个工作日|个月内|次月", t):
                v += 3
            if re.search(r"哪些|有哪", query or "") and "（一）" in t and "（二）" in t:
                v += 2
            return v

        scored = sorted(chunks, key=lambda c: -chunk_score(c))
        if scored and chunk_score(scored[0]) > 0:
            return scored[:limit]
    # 再退一步：优先贴含数字的块（统计/金额通常在那里），否则取前两块
    hit = [c for c in chunks if re.search(r"\d", str(c.get("text") or ""))]
    if hit:
        return hit[:limit]
    if doc_id in by_id:
        return [by_id[doc_id]]
    return chunks[:limit]


def show(item: dict, idx: int, total: int, by_doc: dict, by_id: dict, quotas: dict) -> None:
    """打印一条待复核样本：问题、每条 golden 单独成块、原文、要点。"""
    print(NL + "=" * 78)
    print("[" + str(idx) + "/" + str(total) + "] 类型: " + str(item.get("type"))
          + "   已收录: fact " + str(quotas.get("fact", 0)) + " / multi " + str(quotas.get("multi", 0))
          + " / stale " + str(quotas.get("stale", 0)) + " / boundary " + str(quotas.get("boundary", 0)))
    print("问题: " + str(item.get("query")))
    print()

    goldens = item.get("golden_docs") or []
    if not goldens:
        print("【golden】无（边界型：正确做法是拒答）"
              + (("   语料关键词命中: " + str(item.get("corpus_hits")))
                 if "corpus_hits" in item else ""))
        print()
    for n, g in enumerate(goldens, 1):
        doc_id, clause = (g.split("#", 1) + [""])[:2] if "#" in g else (g, "")
        doc = by_doc.get(doc_id, [{}])[0] if by_doc.get(doc_id) else {}
        print("【golden " + str(n) + "/" + str(len(goldens)) + "】"
              + str(doc.get("doc_title") or doc_id)
              + "   （共 " + str(len(by_doc.get(doc_id, []))) + " 块）")
        if clause:
            print("   指定条款: " + clause)
        # 只有一篇 golden 时用整句问题；两篇以上才需要「拆给各自最相关的那半句」
        frag = (str(item.get("query") or "") if len(goldens) <= 1
                else pick_fragment(str(item.get("query") or ""), str(doc.get("doc_title") or "")))
        for c in pick_chunks(doc_id, clause, by_doc, by_id,
                             item.get("answer_points") or [], frag):
            # 问题里有几个问号，就多贴几句（一个问题贴 2 句，最多 4 句）
            # 问号越多，贴的句子越多：1 问 2 句、2 问 4 句、3 问 6 句（最多 6）
            nq = str(item.get("query") or "").count("？") + str(item.get("query") or "").count("?")
            sents = best_sentences(c.get("text"), frag, item.get("answer_points") or [],
                                   k=min(6, max(2, 2 * nq)))
            print("   · [" + str(c.get("clause_path")) + "] " + sents[0])
            for extra in sents[1:]:
                print("     " + extra)
        print()          # 每条 golden 之间留空行，方便分清

    if any(("失效" in str(g)) or ("废止" in str(g)) for g in goldens):
        print("⚠ 这篇 golden 标题含「失效/废止」→ 建议按 t 把类型改成 stale，"
              "要点里加一条「该文件已失效」")
        print()

    pts = item.get("answer_points") or []
    print("要点: " + ("; ".join(str(p) for p in pts) if pts else "(空)  ← 按 e 补，别让它空着进评测集"))
    if item.get("evidence"):
        print("依据: " + str(item["evidence"])[:120])
    if item.get("note"):
        print("备注: " + str(item["note"])[:80])


def show_more(item: dict, by_doc: dict, by_id: dict) -> None:
    """按 v 时调用：按条款顺序列出该文档的全部块（每块前 90 字）。

    为什么不再只挑 4 块：默认挑块是按关键词打分，问「处分有哪几种」时会挑到
    满篇都是「处分」的第九条，而真正的答案在第五条/第六条。要看全，才不会漏。
    """
    global _LAST_BLOCKS
    _LAST_BLOCKS = []
    print(NL + "---- 全文目录（按条款顺序，每块前 90 字；用 o 编号 看整块全文）----")
    for n, g in enumerate(item.get("golden_docs") or [], 1):
        doc_id, clause = (g.split("#", 1) + [""])[:2] if "#" in g else (g, "")
        chunks = by_doc.get(doc_id, [])
        print("【golden " + str(n) + "】" + doc_id[:60] + "   （共 " + str(len(chunks)) + " 块）")
        for c in chunks:
            _LAST_BLOCKS.append(c)
            print("   [" + str(len(_LAST_BLOCKS)) + "] [" + str(c.get("clause_path"))
                  + "] " + snippet_of(c, 90))
        print()


def main() -> int:
    """逐条复核；每按一次键就把结果落盘，随时可以退出。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="", help="候选池文件，逗号分隔；默认 candidates + 现有 gold + multi")
    ap.add_argument("--out", default=str(DATA / "eval_gold.reviewed.jsonl"))
    ap.add_argument("--state", default=str(DATA / "review_state.json"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--targets", default="fact=20,multi=15,stale=10,boundary=5")
    a = ap.parse_args()

    pool_files = ([Path(x.strip()) for x in a.pool.split(",") if x.strip()] if a.pool
                  else [DATA / "eval_gold.candidates.jsonl", DATA / "eval_gold.jsonl",
                        DATA / "eval_gold.multi.jsonl"])
    out_path, state_path = Path(a.out), Path(a.state)
    targets = {}
    for kv in a.targets.split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            targets[k.strip()] = int(v)

    chunks = load_jsonl(DATA / "chunks.jsonl")
    by_doc: dict[str, list[dict]] = {}
    by_id: dict[str, dict] = {}
    for c in chunks:
        by_doc.setdefault(c["doc_id"], []).append(c)
        by_id[c["chunk_id"]] = c

    if not sys.stdin.isatty():
        print("[review] 注意：当前 stdin 不是交互终端，可能读不到按键。")
        print("[review] 请在 PowerShell / Windows Terminal 窗口里直接运行：python review_eval.py")
    counts = [len(load_jsonl(f)) for f in pool_files]
    pool = build_pool(pool_files)
    state = load_state(state_path)
    done = load_jsonl(out_path)
    done_q = {str(x.get("query")) for x in done}
    todo = [x for x in pool if str(x.get("query")) not in state]
    if a.limit:
        todo = todo[:a.limit]

    quotas = quotas_of(done)
    print("[review] 池子 " + str(len(pool)) + " 条（各文件 " + " + ".join(str(c) for c in counts)
          + "，去重后 " + str(len(pool)) + "）；已复核 " + str(len(state))
          + " 条；本次待过 " + str(len(todo)) + " 条")
    print("[review] 目标: " + " / ".join(k + " " + str(v) for k, v in targets.items()))
    print("[review] 按键: y 收录 / n 剔除 / s 跳过 / e 改要点 / t 改类型 / v 看全文目录 / o 3 看原文 / q 退出")

    skipped: set[str] = set()
    for i, item in enumerate(todo, 1):
        q = str(item["query"])
        show(item, i, len(todo), by_doc, by_id, quotas)
        while True:
            try:
                ans = input("→ [y/n/s/e/q]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print(NL + NL + "[review] 没有读到输入（EOF），已保存并退出。")
                print("[review] 如果在 IDE 的『运行』按钮里启动，请改用命令行：python review_eval.py")
                save_jsonl(out_path, done)
                save_state(state_path, state)
                return 0
            if ans == "v":
                show_more(item, by_doc, by_id)
                continue
            if ans.startswith("o"):
                parts = ans.split()
                if len(parts) == 2 and parts[1].isdigit():
                    k = int(parts[1])
                    if 1 <= k <= len(_LAST_BLOCKS):
                        c = _LAST_BLOCKS[k - 1]
                        print(NL + "---- [" + str(k) + "] " + str(c.get("clause_path"))
                              + " 全文 ----")
                        print(str(c.get("text")))
                        print()
                    else:
                        print("  编号超出范围（1-" + str(len(_LAST_BLOCKS)) + "），先按 v 看目录")
                else:
                    print("  用法：o 3  （先按 v 看目录编号）")
                continue
            if ans == "t":
                cur = item.get("type") if item.get("type") in TYPE_ORDER else "fact"
                item["type"] = TYPE_ORDER[(TYPE_ORDER.index(cur) + 1) % len(TYPE_ORDER)]
                print("  类型已改为: " + item["type"] + "（再按 t 继续切换）")
                continue
            if ans == "e":
                raw = input("新要点（分号分隔，直接回车不改）: ").strip()
                if raw:
                    item["answer_points"] = [p.strip() for p in raw.split(";") if p.strip()]
                    print("要点已改为: " + "; ".join(item["answer_points"]))
                continue
            if ans in ("y", "n", "s", "q"):
                break
            print("只认 y / n / s / e / t / v / q")

        if ans == "q":
            break
        if ans == "s":
            skipped.add(q)
            continue

        item["verified"] = (ans == "y")
        if ans == "y":
            done_q.add(q)
        else:
            done_q.discard(q)
        state[q] = ans
        done = [x for x in done if str(x.get("query")) != q]
        if ans == "y":
            done.append(item)
        save_jsonl(out_path, done)
        save_state(state_path, state)
        quotas = quotas_of(done)
        print("  已保存（" + ("收录" if ans == "y" else "剔除") + "）")

    save_jsonl(out_path, done)
    save_state(state_path, state)

    print(NL + "=" * 78)
    print("[review] 已复核 " + str(len(state)) + " 条（收录 " + str(len(done)) + " 条）")
    for t in TYPE_ORDER:
        goal = targets.get(t)
        print("  " + t.ljust(9) + str(quotas.get(t, 0)).rjust(3)
              + (" / " + str(goal) if goal else "")
              + ("   ✅" if goal and quotas.get(t, 0) >= goal else ""))
    if skipped:
        print("  本次跳过 " + str(len(skipped)) + " 条（下次还会问）")
    print("[review] 结果 -> " + str(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())