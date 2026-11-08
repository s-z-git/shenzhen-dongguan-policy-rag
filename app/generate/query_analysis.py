"""
app.generate.query_analysis —— 查询分析（结构化条件 + 子问题分解）

对应 DESIGN.md 的 app/generate/ 与 §4.7（多条件组合提问 = 项目最重要的亮点）。

两个目的：
1. 抽出结构化过滤条件（city / year_gte），让检索不必只靠语义相似度
2. 把"硕士+深圳+龙岗"这种多约束问题拆成 ≤3 个子问题，避免向量把约束稀释
"""
from __future__ import annotations

import json
import re

from app.config import DEFAULT, Config
from app.generate.prompt import ANALYZE_PROMPT
from app.retrieve.filter import extract_filters

NL = chr(10)   # 换行符常量


def _loads_maybe(text: str):
    """LLM 常把 JSON 包在 ```json 代码块里，本函数做容错解析。

    处理流程：
    1. 剥离首尾空白和可能的 markdown 围栏标记（```json / ```）
    2. 用正则从文本中提取第一个花括号包围的 JSON 片段
    3. 交给 json.loads 做最终解析
    """
    t = text.strip()
    # 移除开头的 ```json 或 ``` —— LLM 的典型输出格式
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    # 从剩余文本中提取第一个花括号包围的完整 JSON 对象
    m = re.search(r"[{].*[}]", t, re.S)
    if m:
        t = m.group(0)
    return json.loads(t)


def analyze_query(question: str, cfg: Config | None = None, use_llm: bool = True) -> dict:
    """用 LLM 分析用户问题，输出结构化过滤条件与子问题列表。

    参数:
        question: 用户原始查询
        cfg: 配置对象
        use_llm: 为 False 时跳过 LLM，直接使用规则版 fallback（零成本、零延迟）

    返回: {"city_in": [...], "year_gte": int|null, "year_lte": int|null, "sub_questions": [...]}

    容错策略：LLM 失败时自动降级为规则版 extract_filters，保证不阻断主流程。
    """
    cfg = cfg or DEFAULT
    # 规则版 fallback：纯字符串匹配，不依赖 LLM
    f = extract_filters(question)
    fallback = {
        "city_in": f.get("city_in", []),
        "year_gte": f.get("year_gte"),
        "year_lte": f.get("year_lte"),
        "sub_questions": [question],                        # 不拆分，原问题直接当唯一子问题
    }
    if not use_llm:
        return fallback
    try:
        from app.generate.generator import get_llm
        # temperature=0.0：查询分析不需要创造性，要确定性输出
        msg = get_llm(cfg, temperature=0.0).invoke(ANALYZE_PROMPT + NL + question)
        raw = msg.content if hasattr(msg, "content") else str(msg)
        d = _loads_maybe(raw)                               # 容错解析 LLM 返回的 JSON
        # 清洗 sub_questions：去空、去重、限制最多 3 条
        subs = [str(s).strip() for s in (d.get("sub_questions") or []) if str(s).strip()]
        return {
            # city_in 白名单校验：只保留"深圳""东莞"，过滤掉 LLM 的幻觉值
            "city_in": [c for c in (d.get("city_in") or []) if c in ("深圳", "东莞")],
            "year_gte": d.get("year_gte"),
            "year_lte": d.get("year_lte"),
            "sub_questions": subs[:3] or [question],        # 至少保留原问题，避免空列表
        }
    except Exception:
        # LLM 调用异常（网络/超时/解析失败）→ 降级为规则版
        return fallback


REWRITE_PROMPT = """你是多轮对话的查询改写器。根据对话历史，把当前问题改写成一条独立、可检索的问题。

规则：
1. 只输出改写后的问题，不要回答，不要解释。
2. 保留历史里的城市、对象、年份、补贴类型等条件。
3. 指代词（那东莞呢、这个、还要什么材料）要还原成明确对象。
4. 当前问题已经独立时，原样输出。
5. 不要添加历史中不存在的新条件。

对话历史：
__HISTORY__

当前问题：__QUESTION__

改写后的问题："""


def rewrite_query(question: str, history: list[dict] | None, cfg: Config | None = None) -> str:
    """用最近对话把追问改写成独立问题；失败时回退原问题。"""
    if not history:
        return question
    cfg = cfg or DEFAULT
    lines = []
    for item in history[-6:]:
        role = "用户" if item.get("role") == "user" else "助手"
        lines.append(role + "：" + str(item.get("content") or "")[:500])
    prompt = (REWRITE_PROMPT
              .replace("__HISTORY__", NL.join(lines))
              .replace("__QUESTION__", question))
    try:
        from app.generate.generator import get_llm
        msg = get_llm(cfg, temperature=0.0).invoke(prompt)
        text = msg.content if hasattr(msg, "content") else str(msg)
        text = str(text).strip().strip("\"")
        # 改写结果异常长或为空时，宁可回退原问题
        if not text or len(text) > 300:
            return question
        return text
    except Exception:
        return question


def filters_from_analysis(analysis: dict) -> dict:
    """把 LLM 查询分析的结果转成 retriever 能消费的过滤条件字典。

    只保留 city_in / year_gte / year_lte 三个字段，其余字段丢弃（安全性：不做多余透传）。
    year_gte / year_lte 做 int 强制转换，转换失败则丢弃该字段。
    """
    f: dict = {}
    if analysis.get("city_in"):
        f["city_in"] = analysis["city_in"]
    if analysis.get("year_gte"):
        try:
            f["year_gte"] = int(analysis["year_gte"])       # 保险起见做类型强制
        except (TypeError, ValueError):
            pass                                            # 转换失败则丢弃，不影响其他字段
    if analysis.get("year_lte"):
        try:
            f["year_lte"] = int(analysis["year_lte"])
        except (TypeError, ValueError):
            pass
    return f