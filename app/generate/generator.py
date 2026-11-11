"""
app.generate.generator —— DeepSeek 生成 + 引用解析 + 时效提示

对应 DESIGN.md 的 app/generate/ 与 §4.8。

两个容易做错的地方：
1. **出处不是让模型"顺便写一下"**：prompt 里给了强约束 + 固定格式，
   生成后再用正则把 [出处: ...] 抽出来 —— 这样"引用正确率"才有可计算的定义。
2. **时效提示分两层**：规则层（文件年份、标题含废止）不依赖模型，
   模型层只负责在回答里带上对应说明。规则层保证"即使模型偷懒，警告也一定出现"。
"""
from __future__ import annotations

import os
import re
from datetime import datetime

from app.config import DEFAULT, Config
from app.generate.prompt import ANSWER_PROMPT, ANSWER_PROMPT_V2, format_context
from app.ingest.metadata import citation

NL = chr(10)                                      # 换行符常量
REFUSAL = "根据现有资料无法回答。"                    # LLM 拒答时的标志文本
OBSOLETE_WORDS = ("废止", "失效", "已失效")          # 标题中暗示文件已失效的关键词
# 正则：匹配回答中 [出处: xxx] 或 [出处：xxx] 格式，捕获括号内的文本
CITE_RE = re.compile(r"\[出处[:：]\s*([^\]\[]+?)\s*\]")

# 模块级 LLM 客户端缓存：reasoning_effort 必须进 key，否则 A/B 会复用同一个客户端。
_LLM_CACHE: dict = {}


def get_llm(cfg: Config | None = None, temperature: float | None = None):
    """按 (模型, base_url, temperature, reasoning_effort) 缓存 ChatOpenAI 实例。

    DeepSeek 提供 OpenAI 兼容接口，所以直接用 langchain_openai 的 ChatOpenAI 对接。
    """
    cfg = cfg or DEFAULT
    key = (cfg.llm_model, cfg.llm_base_url, temperature, cfg.reasoning_effort)
    if key not in _LLM_CACHE:
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("请先设置 DEEPSEEK_API_KEY 环境变量")
        llm_kwargs = {
            "model": cfg.llm_model,
            "api_key": SecretStr(api_key),                  # SecretStr 避免日志/打印时泄露密钥
            "base_url": cfg.llm_base_url,
            "temperature": cfg.temperature if temperature is None else temperature,
            "max_tokens": cfg.max_tokens,                   # DeepSeek 兼容接口用 max_tokens 限输出
            "timeout": 60,                                  # 60 秒超时，防止长时间挂起
            "max_retries": 2,                               # 网络异常时重试 2 次
            "stream_usage": True,                           # 流式调用也要返回 token 用量
        }
        effort = (cfg.reasoning_effort or "").strip()
        if effort and effort.lower() != "default":
            llm_kwargs["reasoning_effort"] = effort
        _LLM_CACHE[key] = ChatOpenAI(**llm_kwargs)
    return _LLM_CACHE[key]


def _message_usage(msg) -> dict:
    """从同步消息或流式 chunk 中提取 token 用量。"""
    raw_usage = getattr(msg, "usage_metadata", None)
    if isinstance(raw_usage, dict) and raw_usage:
        return dict(raw_usage)
    meta = getattr(msg, "response_metadata", None) or {}
    return dict(meta.get("token_usage") or meta.get("usage") or {})


def _llm_text(llm, prompt: str) -> tuple[str, dict]:
    """调用 LLM，返回 (纯文本, token 用量)。"""
    msg = llm.invoke(prompt)
    text = msg.content if hasattr(msg, "content") else str(msg)
    return text, _message_usage(msg)


def staleness_notes(chunks: list[dict], cfg: Config | None = None) -> list[str]:
    """DESIGN §4.8：生成时效性提示。

    两层判断（均为规则层，不依赖模型）：
    1. 最新引用文件的年份距今超过 stale_years 年 → 提示"可能已更新"
    2. 文档标题含"废止/失效"等字眼 → 提示"该文件可能已失效"

    注意：只说"可能过期"，绝不声称"已失效"——系统无法知道是否真的被废止。
    """
    cfg = cfg or DEFAULT
    notes: list[str] = []
    if not chunks:
        return notes
    cur = datetime.now().year                              # 当前年份
    newest = max((c.get("year") or 0) for c in chunks)    # 引用文件中最新的年份
    # 规则层判断：只提示"可能过期"，绝不声称"已失效"——系统无法知道是否真的废止
    if newest and cur - newest > cfg.stale_years:
        notes.append("本回答依据的最新文件是 " + str(newest) + " 年（距今超过 "
                     + str(cfg.stale_years) + " 年），政策可能已更新，建议以人社局最新公告为准。")
    # 遍历所有引用文档的标题，检查是否含废止/失效关键词
    for c in chunks:
        title = c.get("doc_title") or ""
        if any(w in title for w in OBSOLETE_WORDS):
            n = "引用的《" + title + "》标题含【废止/失效】，该文件可能已失效，请核对最新文件。"
            if n not in notes:                              # 去重：同一文件不在 notes 里重复出现
                notes.append(n)
    return notes


def used_citations(chunks: list[dict], answer_text: str) -> list[str]:
    """从 LLM 回答中解析实际使用的引用出处。

    策略：
    1. 优先用正则匹配 [出处: ...] 格式 —— 这是 prompt 里约定的标准格式
    2. 正则未匹配到时，退化为完整文件名或前 20 个字符在回答中查找
    3. 每个出处截断到 120 字符，防止模型输出异常长文本撑爆 UI

    返回: 去重后的出处描述列表，如 ["深圳市人才安居办法（第15条）"]
    """
    out: list[str] = []
    for m in CITE_RE.findall(answer_text):
        f = m.strip()
        if f and f not in out:
            out.append(f[:120])                             # 截断防护，防止异常长文本
    # 优先返回正则匹配结果（评测的引用正确率依赖这个口径）
    if out:
        return out
    # 降级方案：完整文件名或前 20 字符匹配，避免通用前缀误报
    for c in chunks:
        title = c.get("doc_title") or ""
        if title and (title in answer_text
                      or (len(title) >= 20 and title[:20] in answer_text)):
            tag = citation(c)
            if tag not in out:
                out.append(tag)
    return out


def cited_doc_ids(answer_text: str, chunks: list[dict]) -> list[str]:
    """从回答中识别引用了哪些文档的 doc_id，用于评测"引用正确率"。

    与 used_citations 的区别：used_citations 返回人类可读的出处描述
    （如"文件名（条款号）"），本函数返回的是 doc_id（文档唯一标识），
    方便与 ground truth 做精确匹配评估。
    """
    # 复用生成后核验的标题/条款匹配，避免只用文件名前缀造成通用前缀误报。
    # 局部导入避免 generator -> verify -> generator 的模块级循环依赖。
    from app.generate.verify import check_grounding

    by_id = {c.get("chunk_id"): c for c in (chunks or [])}
    ids: list[str] = []
    grounding = check_grounding(answer_text, chunks)
    for item in ((grounding.get("citations") or {}).get("items") or []):
        for cid in item.get("matched_ids") or []:
            c = by_id.get(cid)
            if c and c.get("doc_id") and c["doc_id"] not in ids:
                ids.append(c["doc_id"])
    return ids


def is_refusal(text: str) -> bool:
    """判断回答是否属于拒答。

    不能只认“无法回答”：v2 prompt 允许用“资料未载明 X”表达资料不足。
    但正常回答也可能顺带说“某项未载明”，所以只在“短回答且没有金额/比例/期限等单位”时判拒答。
    """
    t = (text or "").strip()
    if not t:
        return False
    body = CITE_RE.sub("", t).strip()          # 去掉引用里的文号/标题编号
    # 只有“整题拒答”才算拒答；正文里局部说“某小问无法回答”不应覆盖整题。
    if t.startswith(REFUSAL.rstrip("。")):
        return True
    # 只要给了可核验出处，就说明回答已经落到具体条款上，属于“某项未载明”
    # 或“已失效但无替代文件”，不是整题拒答。实测 8 条非 boundary 误判
    # 全部带 1 条引用，5 条 boundary 全是 0 引用。
    if CITE_RE.search(t):
        return False
    if "无法回答" in body and len(body) <= 80:
        return True
    if len(body) <= 120 and any(p in body for p in ("不能提供", "不能泄露", "不便提供", "无法提供")):
        return True
    negative = any(p in body for p in ("资料未载明", "资料未提供", "未提供具体", "现有资料未提供"))
    # 「2027年」这类年份不是实质数量，不能因为它把资料不足的回答判成正常回答。
    body_measure = re.sub(r"(?:19|20)\d{2}\s*年", "", body)
    has_measure = re.search(
        r"\d+(?:\.\d+)?\s*(?:元|万元|亿元|%|％|年|个月|月|天|日|次|倍|岁|人|份|工作日)", body_measure)
    substantive = bool(has_measure) or len(body) > 400
    # 短回答且以“资料未载明”为主，才判拒答；有数字/长正文说明仍在回答。
    return negative and not substantive and len(body) <= 200


def build_answer_prompt(question: str, chunks: list[dict], cfg: Config | None = None) -> str:
    """拼装回答 prompt；answer() 和 stream_answer() 共用同一模板。"""
    cfg = cfg or DEFAULT
    if not chunks:
        return ""
    ctx = format_context(chunks)
    template = ANSWER_PROMPT_V2 if cfg.answer_prompt_version == "v2" else ANSWER_PROMPT
    return template.replace("__CONTEXT__", ctx).replace("__QUESTION__", question)


def answer(question: str, chunks: list[dict], cfg: Config | None = None, llm=None) -> dict:
    """生成回答的顶层入口，串联上下文拼装 → LLM 调用 → 引用解析 → 拒答检测。

    参数:
        question: 用户原始问题
        chunks: 检索阶段产出的 top-k 文档块列表
        cfg: 配置对象，未传则使用 DEFAULT
        llm: 可选的预初始化 LLM 实例（测试时注入，避免重复建客户端）

    返回: dict，包含：
        answer    - LLM 生成的回答文本
        citations - 从回答中解析出的引用出处列表
        notes     - 规则层计算的时效/废止提示
        refused   - 是否拒答（无检索结果/回答含拒答字样）
        prompt    - 实际发送给 LLM 的完整 prompt（调试和评测用）
        error     - 仅在 LLM 调用失败时存在
    """
    cfg = cfg or DEFAULT
    llm = llm or get_llm(cfg)                              # 未注入则从缓存获取
    notes = staleness_notes(chunks, cfg)                   # 规则层：先计算时效提示
    if not chunks:
        # 无检索结果 → 直接拒答，不调用 LLM
        return {"answer": REFUSAL, "citations": [], "notes": notes, "refused": True, "prompt": ""}
    # 将检索到的条款拼装为格式化上下文，替换 prompt 模板中的两个占位符
    prompt = build_answer_prompt(question, chunks, cfg)
    try:
        text, usage = _llm_text(llm, prompt)
        text = text.strip()
    except Exception as exc:
        # LLM 调用失败时返回错误信息，不阻断主流程
        return {"answer": "调用 LLM 失败: " + str(exc), "citations": [], "notes": notes,
                "refused": False, "prompt": prompt, "error": str(exc)}
    # 拒答检测：回答以 REFUSAL 前 6 字开头，或包含"无法回答"
    refused = is_refusal(text)
    return {
        "answer": text,
        "citations": used_citations(chunks, text),          # 从回答中解析实际引用
        "notes": notes,                                     # 规则层计算的时效提示
        "refused": refused,
        "prompt": prompt,                                   # 调试/评测时查看完整 prompt
        "prompt_version": cfg.answer_prompt_version,        # v1 / v2，便于 A/B
        "usage": usage,                                     # token 用量，trace/stats 用
    }


def stream_answer(question: str, chunks: list[dict], cfg: Config | None = None, llm=None,
                  sink: dict | None = None):
    """流式生成回答；文本通过 yield 输出，完整字段写入 sink。

    sink 在流结束后包含 answer/citations/refused/notes/usage/prompt；
    调用方不要依赖生成器的 return 值。
    """
    cfg = cfg or DEFAULT
    sink = sink if sink is not None else {}
    notes = staleness_notes(chunks, cfg)
    sink.update({
        "answer": "", "citations": [], "refused": False, "notes": notes,
        "usage": {}, "prompt": "", "prompt_version": cfg.answer_prompt_version,
    })
    if not chunks:
        sink.update({"answer": REFUSAL, "refused": True})
        yield REFUSAL
        return

    llm = llm or get_llm(cfg)
    prompt = build_answer_prompt(question, chunks, cfg)
    sink["prompt"] = prompt
    parts: list[str] = []
    usage: dict = {}
    try:
        for chunk in llm.stream(prompt):
            content = getattr(chunk, "content", "")
            chunk_usage = _message_usage(chunk)
            if chunk_usage:
                usage = chunk_usage
            if not content:
                continue
            if not isinstance(content, str):
                content = str(content)
            parts.append(content)
            yield content
    except Exception as exc:
        # 流式失败时保留已经输出的文本；如果一字未出，给用户一个明确错误
        sink["error"] = str(exc)
        if not parts:
            text = "调用 LLM 失败: " + str(exc)
            parts.append(text)
            yield text
    text = "".join(parts).strip()
    sink.update({
        "answer": text,
        "citations": used_citations(chunks, text),
        "refused": is_refusal(text),
        "usage": usage,
    })
