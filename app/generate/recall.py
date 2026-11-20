"""会话回顾：规则识别 + 确定性答案模板。

设计边界：
- 只处理“我刚才问了什么”这类会话元问题，不做时间解析；
- 纯规则，不调 LLM、不检索；
- 由 pipeline 在 city_scope 之后、_retrieve 之前短路。
"""
from __future__ import annotations

import re

# 去掉空白和标点后再匹配，避免“我刚才问了什么？”和“我刚才问了什么”分成两套规则。
_PUNCT = re.compile(r"[\s，。！？、,.!?；;：:]+")

_LAST = (
    r"^(我)?(刚才|刚刚|上一条|上一次|上一轮)(问|说)(了)?(的)?(是)?(什么|啥)(问题)?$",
    r"^(我)?上(一)?个问题(是)?(什么|啥)$",
    r"^(我)?上一轮(问|说)(的)?(是)?(什么|啥)$",
    r"^(我)?上次(问|说)(了)?(的)?(是)?(什么|啥)(问题)?$",
)
_LIST = (
    r"^(我)?(刚才|刚刚|这轮|这次)(都)?问(了)?(哪些|什么)(问题)?$",
    r"^这(轮|次)会话(里|中)?(我)?(都)?问(了)?(哪些|什么)(问题)?$",
    r"^(我)?(都)?问过(哪些|什么)问题$",
    r"^(我)?之前问过(哪些|什么)(问题)?$",
    r"^(我)?(都)?问(了)?(啥|什么)$",
)
_FIRST = (
    r"^(我)?(的)?第一个问题(是)?(什么|啥)$",
    r"^(我)?最(早|开始)(问|说)(的)?(是)?(什么|啥)$",
    r"^(我)?前面(问|说)(的)?(是)?(什么|啥)$",
)


def detect_recall(question: str) -> str | None:
    """返回 last / list / first；不是回顾类问句返回 None。"""
    q = _PUNCT.sub("", question or "")
    if not q:
        return None
    for kind, patterns in (("last", _LAST), ("list", _LIST), ("first", _FIRST)):
        if any(re.search(p, q) for p in patterns):
            return kind
    return None


def format_recall(kind: str, turns: list[dict]) -> str:
    """把 recall_turns() 的结构化结果拼成固定文本，不调 LLM。"""
    if not turns:
        return "这是本轮对话的第一个问题，之前没有提问记录。"

    if kind == "last":
        item = turns[-1]
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if answer:
            snippet = answer[:80] + ("…" if len(answer) > 80 else "")
            return f"你上一个问的是：「{question}」。我当时答的是：{snippet}"
        return f"你上一个问的是：「{question}」。上一轮没有生成答案。"

    if kind == "first":
        question = str(turns[0].get("question") or "").strip()
        return f"这一轮会话里你第一个问的是：「{question}」。"

    # list
    recent = turns[-5:]
    lines = [f"这一轮会话里你最近问了 {len(recent)} 个问题："]
    for idx, item in enumerate(recent, 1):
        lines.append(f"{idx}. {str(item.get('question') or '').strip()}")
    return "\n".join(lines)