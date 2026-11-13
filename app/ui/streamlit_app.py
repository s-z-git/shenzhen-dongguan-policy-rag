"""app.ui.streamlit_app —— 深莞人社政策问答网页界面

启动（必须在项目根目录执行）：
    streamlit run app/ui/streamlit_app.py

设计原则：
- 左侧是会话，不再把回答正文塞进侧栏；
- 中间按“时效 → 结论 → 要点 → 依据”四层展示；
- 会话消息落 SQLite，刷新和切换会话都能回放；
- 只做展示，不改变检索、生成与评测口径。
"""
from __future__ import annotations

import difflib
import html
import pathlib
import re
import sys
import uuid

# 将项目根目录加入模块搜索路径，使 app.xxx 绝对导入可用
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import streamlit as st

from app.config import DEFAULT
from app.memory import SessionStore
from app.retrieve.rerank import rerank_available
from app.ui.citation_view import (_citation_summary, find_citation_item,
                                  find_matched_chunks, render_citation_cards)
from app.ui.session_list import render_session_list

MODE_FAST = "快速"
MODE_HIGH = "高质量"
MODE_NOTE = {
    "none": "快速模式 · 约 2–3 倍速度",
    "default": "高质量模式 · 更稳",
}

EXAMPLES = [
    {"q": "东莞本科应届生入户能领哪些补贴？", "label": "东莞应届生能领哪些补贴？",
     "note": "多份文件 · 时效提示"},
    {"q": "深圳博士人才生活补贴多少？", "note": "金额 + 适用条件"},
    {"q": "深圳失业保险金标准是多少？", "note": "单条款直答 · 引用核验"},
]

CSS = """
<style>
.stApp { background: #ffffff; }
.block-container { max-width: 880px; padding-top: 1.2rem; padding-bottom: 7rem; }
#MainMenu, footer, header [data-testid="stToolbar"] { visibility: hidden; height: 0; }
[data-testid="stSidebar"] { background: #FAFBFC; border-right: 1px solid #E5E7EB; width: 280px !important; }
[data-testid="stSidebar"] > div:first-child { padding-top: .8rem; }
html, body, .stMarkdown, p, li, h1, h2, h3, button, input, textarea {
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei",
                 "Noto Sans SC", sans-serif;
}
.rag-title { font-size: 1.55rem; font-weight: 700; letter-spacing: -.01em;
             color: #111827; line-height: 1.2; }
.rag-sub { color: #6B7280; font-size: .82rem; margin-top: .3rem; }
.rag-note { color: #9CA3AF; font-size: .74rem; margin: .15rem 0 .9rem; }
.rag-kicker { color: #9CA3AF; font-size: .72rem; letter-spacing: .06em;
              margin: .25rem 0 .1rem; }
.rag-q { margin: 1.1rem 0 .7rem auto; max-width: 76%; width: fit-content;
         background: #F3F4F6; color: #111827; padding: .55rem .8rem;
         border-radius: 10px; font-size: .92rem; line-height: 1.6; }
.rag-sep { border-top: 1px solid #F1F2F4; margin: .4rem 0 1.1rem; }
.rag-stale { border: 1px solid #F0E2B6; background: #FFFBEB; color: #8A5A00;
             border-radius: 8px; padding: .4rem .7rem; font-size: .8rem;
             margin: .15rem 0 .55rem; }
.rag-stale.ok { border-color: #E8EAED; background: #FAFAFA; color: #6B7280; }
.rag-conclusion { font-size: 1.16rem; font-weight: 650; line-height: 1.62;
                  color: #111827; padding: .15rem 0 .5rem; }
.rag-points { margin: .1rem 0 .5rem 0; padding-left: 1.15rem; }
.rag-points li { margin: .22rem 0; line-height: 1.7; font-size: .93rem; color: #1F2937; }
sup.cite { color: #2563EB; font-size: .7em; font-weight: 600; padding: 0 1px; }
.cite-body { max-height: 320px; overflow: auto; line-height: 1.8;
             font-size: .88rem; color: #374151; }
.cite-body mark { background: #FFF3BF; padding: 0 .1rem; }
.rag-foot { color: #9CA3AF; font-size: .72rem; margin-top: .6rem; }
.state-card { border-radius: 8px; padding: .7rem .85rem; font-size: .86rem;
              line-height: 1.7; margin: .2rem 0 .5rem; }
.state-info { background: #F5F8FF; border: 1px solid #DCE6FB; color: #1E3A8A; }
.state-warn { background: #FFFBEB; border: 1px solid #F0E2B6; color: #8A5A00; }
.state-error { background: #FEF2F2; border: 1px solid #F5D0CE; color: #991B1B; }
.side-brand { font-size: .95rem; font-weight: 700; color: #111827;
              margin: .1rem 0 .8rem .1rem; }
.side-label { color: #9CA3AF; font-size: .68rem; letter-spacing: .08em;
              margin: .7rem 0 .2rem .15rem; }
.side-time { color: #9CA3AF; font-size: .66rem; margin: .35rem 0 -.3rem .2rem; }
.side-group { color: #6B7280; font-size: .66rem; font-weight: 600;
              margin: .7rem 0 .05rem .2rem; }
[data-testid="stMain"] .stButton button { min-height: 2.3rem; }
[data-testid="stSidebar"] .stButton button { text-align: left; justify-content: flex-start;
    border: 0; background: transparent; padding: .2rem .35rem; font-size: .8rem;
    min-height: 1.7rem; color: #374151; }
[data-testid="stSidebar"] .stButton button:hover { background: #EEF2F7; color: #111827; }
[data-testid="stSidebar"] .stButton button p { font-size: .8rem !important; text-align: left; width: 100%; }
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stPopover"] button { min-height: 1.5rem; height: 1.5rem; width: 1.6rem; padding: 0; color: #9CA3AF; border: 0; background: transparent; }
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stPopover"] button:hover { background: #EEF2F7; color: #111827; }
[data-testid="stSidebar"] [data-testid="stPopover"] button { min-height: 1.9rem; }
[data-testid="stChatInput"] { border-radius: 12px; }
[data-testid="stMain"] [data-testid="stPopover"] button { font-size: .7rem; min-height: 1.6rem; padding: 0 .45rem; color: #9CA3AF; }
</style>
"""


@st.cache_resource(show_spinner=False)
def load_pipeline():
    """加载 RAG 管线并预热；cache_resource 保证切换会话不重载。"""
    from app.pipeline import RAGPipeline

    pipe = RAGPipeline.load(DEFAULT)
    try:
        pipe.warmup()
    except Exception:
        pass
    return pipe


# ---------- 文本处理（纯函数，便于单测） ----------

def strip_citations(text: str) -> str:
    """去掉 [出处: ...]，并压缩空白。"""
    text = re.sub(r"\[出处[:：][^\]]+\]", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _downgrade_claims(text: str) -> str:
    """把“已废止/已失效”的事实断言降级为“可能已…”。

    先替换成占位符再还原，避免“可能已废止”被二次替换。
    文件名里的“（已废止）”不经过这个函数，保持原样。
    """
    text = str(text or "")
    text = text.replace("可能已废止", "\x00").replace("可能已失效", "\x01")
    text = text.replace("均已废止或失效", "\x02").replace("均已废止", "\x03")
    text = text.replace("已废止", "\x04").replace("已失效", "\x05")
    return (text.replace("\x02", "可能均已失效")
                .replace("\x03", "可能均已失效")
                .replace("\x04", "可能已废止")
                .replace("\x05", "可能已失效")
                .replace("\x00", "可能已废止")
                .replace("\x01", "可能已失效"))


def split_summary(answer: str) -> tuple[str, str]:
    """拆出“综上”句；正文不再重复这句话。"""
    text = (answer or "").strip()
    if not text:
        return "", ""
    body_lines, summary_lines = [], []
    for line in text.splitlines():
        if line.strip().startswith("综上"):
            summary_lines.append(line.strip())
        else:
            body_lines.append(line)
    body = "\n".join(body_lines).strip()
    summary = " ".join(summary_lines).strip()
    if summary:
        return body, summary
    # 没有“综上”句就不硬造结论：宁可少一行，也不要和要点重复
    return text, ""


def conclusion_text(answer: str, max_chars: int = 60) -> str:
    """一句话结论：优先“综上”句，去标记、降级断言、限长。"""
    _, summary = split_summary(answer)
    text = strip_citations(summary)
    text = re.sub(r"^\s*(?:综上[，,：:]?|[-*•]|\d+[.、)])\s*", "", text).strip()
    text = _downgrade_claims(text)
    parts = [p for p in re.split(r"(?<=[。！？!?])", text) if p.strip()]
    text = parts[0].strip() if parts else text
    if len(text) > max_chars:
        cut = max(text.rfind("，", 0, max_chars), text.rfind("；", 0, max_chars))
        text = (text[:cut] + "…") if cut > 20 else (text[:max_chars] + "…")
    return text


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = max(text.rfind("，", 0, limit), text.rfind("；", 0, limit))
    return (text[:cut] + "…") if cut > 10 else (text[:limit] + "…")


def key_points(answer: str, max_items: int = 4, max_chars: int = 40) -> list[str]:
    """从编号/项目符号行提取要点；无列表时退化为前 3 句。"""
    body, _ = split_summary(answer)
    items: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^\s*(?:\d+[.、)]|[-*•])\s+(.+)$", line)
        if not match:
            continue
        text = strip_citations(match.group(1))
        text = re.split(r"[。；]", text)[0]
        if text:
            items.append(_clip(_downgrade_claims(text), max_chars))
        if len(items) >= max_items:
            break
    if len(items) < 2:
        clean = strip_citations(body)
        sentences = [s.strip() for s in re.split(r"(?<=[。！？!?])", clean) if s.strip()]
        items = [_clip(_downgrade_claims(s), max_chars) for s in sentences[:3]]
    items = [
        i for i in items
        if "无法回答" not in i and not re.fullmatch(r"[（(].*[）)]", i.strip())
    ]
    concl = conclusion_text(answer)
    if concl:
        items = [
            i for i in items
            if i and i not in concl and concl not in i
            and difflib.SequenceMatcher(None, i, concl).ratio() < 0.55
        ]
    return items[:max_items]


def citation_key(raw: str, grounding: dict | None, chunks: list[dict]):
    """按“文件名 + 条款 + 年份”归一，同一文件的不同写法只算一条。"""
    item = find_citation_item(raw, grounding)
    matched = find_matched_chunks(item, chunks)
    title, clause, year = _citation_summary(raw, item, matched)
    return (title, clause, year)


def canonical_citations(citations: list[str], grounding: dict | None,
                        chunks: list[dict]) -> list[str]:
    """保留首次出现的写法，去掉指向同一文件同一-clause 的重复出处。"""
    seen, out = set(), []
    for raw in citations or []:
        key = citation_key(raw, grounding, chunks)
        if key in seen:
            continue
        seen.add(key)
        out.append(raw)
    return out


def body_markdown(answer: str, citations: list[str], grounding: dict | None = None,
                  chunks: list[dict] | None = None) -> str:
    """正文中的 [出处: X] 换成 [n] 角标，顺序与去重后的依据列表一致。"""
    body, _ = split_summary(answer)
    key_to_num: dict = {}
    for raw in citations or []:
        key = citation_key(raw, grounding, chunks or []) if grounding is not None else raw
        if key not in key_to_num:
            key_to_num[key] = len(key_to_num) + 1

    def replace(match: re.Match) -> str:
        raw = match.group(1).strip()
        key = citation_key(raw, grounding, chunks or []) if grounding is not None else raw
        number = key_to_num.get(key)
        if number is None:
            number = len(key_to_num) + 1
            key_to_num[key] = number
        return '<sup class="cite">[%d]</sup>' % number

    return _downgrade_claims(re.sub(r"\[出处[:：]\s*([^\]]+)\]", replace, body))


def format_seconds(elapsed_ms) -> str:
    if elapsed_ms is None:
        return "—"
    return "%.1fs" % (float(elapsed_ms) / 1000.0)


def citation_issues(ci: dict) -> list[str]:
    issues = []
    for item in ci.get("items") or []:
        status = item.get("status")
        if status == "unmatched":
            issues.append("未匹配：" + str(item.get("raw") or ""))
        elif status == "clause_conflict":
            actual = "、".join(item.get("actual_clause") or [])
            issues.append("条款冲突：" + str(item.get("raw") or "") + "；原文实际为 " + actual)
    return issues


# ---------- 渲染 ----------

def render_question(question: str) -> None:
    st.markdown('<div class="rag-q">' + html.escape(question) + "</div>",
                unsafe_allow_html=True)


def cited_years(result: dict) -> list[int]:
    """只统计真正被引用到的条款年份，不用整批检索结果冒充依据。"""
    grounding = result.get("grounding") or {}
    chunks = result.get("chunks") or []
    years = set()
    for raw in result.get("citations") or []:
        item = find_citation_item(raw, grounding)
        for chunk in find_matched_chunks(item, chunks):
            if chunk.get("year"):
                years.add(int(chunk["year"]))
    return sorted(years)


def render_stale_bar(result: dict) -> None:
    """一条聚合时效提示；没有风险时只给一句弱化说明。"""
    notes = [str(n) for n in (result.get("notes") or []) if str(n).strip()]
    quoted = len(result.get("citations") or [])
    years = cited_years(result)
    if not years:
        years = sorted({int(c["year"]) for c in (result.get("chunks") or []) if c.get("year")})
    relaxed = bool(result.get("relaxed_year"))

    if not notes and not relaxed:
        if quoted and years:
            st.caption("依据 %d 份引用文件 · 最新 %d 年 · 未触发时效提示"
                       % (quoted, years[-1]))
        else:
            st.caption("未触发时效提示")
        return

    older = next((n for n in notes if "距今超过" in n), "")
    obsolete_count = sum(1 for n in notes if "标题含" in n)
    parts = []
    if older:
        parts.append(re.split(r"，", older)[0])
    if obsolete_count:
        parts.append("%d 份文件标题标注失效" % obsolete_count)
    if relaxed:
        parts.append("部分条款年份早于所问范围")
    text = "；".join(parts) or "部分依据可能已更新"
    st.markdown('<div class="rag-stale">⚠ 可能已更新/失效 · ' + html.escape(text) + "</div>",
                unsafe_allow_html=True)
    with st.expander("时效明细", expanded=False):
        for note in notes:
            st.markdown("- " + note)
        if relaxed:
            st.markdown("- 符合年份条件的条款不足，回答可能包含更早年份的文件。")


def render_conclusion(text: str) -> None:
    if not text:
        return
    st.markdown('<div class="rag-conclusion">' + html.escape(text) + "</div>",
                unsafe_allow_html=True)


def render_key_points(answer: str) -> list[str]:
    points = key_points(answer)
    if not points:
        return points
    items = "".join("<li>" + html.escape(p) + "</li>" for p in points)
    st.markdown('<ul class="rag-points">' + items + "</ul>", unsafe_allow_html=True)
    return points


def render_state_card(kind: str, title: str, body: str) -> None:
    cls = {"info": "state-info", "warn": "state-warn", "error": "state-error"}.get(kind, "state-info")
    st.markdown('<div class="state-card %s"><b>%s</b><br>%s</div>'
                % (cls, html.escape(_downgrade_claims(title)),
                   html.escape(_downgrade_claims(body))), unsafe_allow_html=True)


def render_process(result: dict) -> None:
    analysis = result.get("analysis") or {}
    filters = result.get("filters") or {}
    if not analysis and not filters:
        return
    with st.expander("检索过程", expanded=False):
        if analysis:
            st.caption("查询分析（结构化条件）")
            st.json(analysis)
        if filters:
            st.caption("实际过滤条件：" + str(filters))


def render_footer(result: dict) -> None:
    ci = (result.get("grounding") or {}).get("citations") or {}
    nu = (result.get("grounding") or {}).get("numbers") or {}
    mode = "快速" if str(result.get("ui_mode") or "none") == "none" else "高质量"
    bits = [mode, format_seconds(result.get("elapsed_ms"))]
    if ci.get("total"):
        bits.append("核验 %d/%d" % (ci.get("verified", 0), ci["total"]))
    if nu.get("total"):
        bits.append("数字 %d/%d" % (nu.get("grounded", 0), nu["total"]))
    bits.append("引用 %d 条" % len(result.get("citations") or []))
    bits.append("检索 %d 条" % len(result.get("chunks") or []))
    left, right = st.columns([0.86, 0.14], vertical_alignment="center")
    with left:
        st.markdown('<div class="rag-foot">' + html.escape(" · ".join(bits)) + "</div>",
                    unsafe_allow_html=True)
    with right:
        with st.popover("trace", width="content"):
            st.code(str(result.get("trace_id") or "无"))


def render_answer(result: dict) -> None:
    if result.get("error"):
        raw = str(result.get("error") or "未知错误")
        lowered = raw.lower()
        if "401" in raw or "authentication" in lowered or "api key" in lowered:
            message = "模型服务鉴权失败（401）。请检查 API Key 配置后重试。"
        elif "timeout" in lowered or "timed out" in lowered:
            message = "模型服务响应超时，请稍后重试。"
        else:
            message = "调用没有完成，请检查网络或模型服务配置。"
        render_state_card("error", "这次调用没有完成", message)
        with st.expander("错误详情", expanded=False):
            st.code(raw)
        st.caption("trace_id：" + str(result.get("trace_id") or "无"))
        if st.button("重试这次提问", type="primary", key="retry_" + str(result.get("trace_id") or "x")):
            st.session_state["auto_submit"] = result.get("question") or ""
            st.rerun()
        return

    if result.get("refused"):
        if not result.get("citations"):
            render_state_card("info", "这个问题超出知识库范围",
                              "当前知识库只覆盖深圳、东莞两地人社政策。")
            st.caption("可以试试：")
            cols = st.columns(3)
            for idx, example in enumerate(EXAMPLES):
                with cols[idx]:
                    if st.button(example.get("label") or example["q"],
                             key="refuse_ex_" + str(idx), width="stretch"):
                        st.session_state["auto_submit"] = example["q"]
                        st.session_state["auto_from_example"] = True
                        st.rerun()
            return
        # 有检索结果但模型认为资料不足：继续走下面同一套四层渲染
        st.caption("模型判断资料不足；以下是可确认的部分。")

    answer = str(result.get("answer") or "")
    if result.get("refused"):
        answer = re.sub(r"^\s*根据现有资料无法回答[。；]?\s*", "", answer)
    grounding = result.get("grounding") or {}
    result["citations"] = canonical_citations(result.get("citations") or [],
                                              grounding, result.get("chunks") or [])
    issues = citation_issues(grounding.get("citations") or {})
    if issues:
        with st.expander("⚠ 引用核验：%d 条需要人工核对" % len(issues), expanded=False):
            for issue in issues:
                st.markdown("- " + issue)

    render_stale_bar(result)
    conclusion = conclusion_text(answer)
    points = key_points(answer)
    if not conclusion and points:
        conclusion = points.pop(0)
    render_conclusion(conclusion)
    if points:
        items = "".join("<li>" + html.escape(p) + "</li>" for p in points)
        st.markdown('<ul class="rag-points">' + items + "</ul>", unsafe_allow_html=True)
    body = body_markdown(answer, result.get("citations") or [],
                          grounding, result.get("chunks") or [])
    body_plain = strip_citations(answer).replace("综上", "")
    redundant = bool(conclusion) and difflib.SequenceMatcher(
        None, body_plain, conclusion).ratio() >= 0.55
    if len(points) == 1 and len(body) < 120:
        points = []
    if points:
        with st.expander("完整回答", expanded=False):
            st.markdown(body, unsafe_allow_html=True)
    elif body and not redundant:
        st.markdown(body, unsafe_allow_html=True)

    render_citation_cards(st, result.get("citations") or [], grounding,
                          result.get("chunks") or [])
    render_process(result)
    render_footer(result)


def result_from_message(message: dict, chunks_by_id: dict) -> dict:
    meta = message.get("meta") or {}
    chunk_ids = meta.get("chunk_ids") or []
    return {
        "answer": message.get("content") or "",
        "question": meta.get("question") or "",
        "citations": meta.get("citations") or [],
        "notes": meta.get("notes") or [],
        "grounding": meta.get("grounding") or {},
        "chunks": [chunks_by_id[c] for c in chunk_ids if c in chunks_by_id],
        "elapsed_ms": meta.get("elapsed_ms"),
        "trace_id": meta.get("trace_id"),
        "ui_mode": meta.get("ui_mode") or "none",
        "refused": bool(meta.get("refused")),
        "error": meta.get("error"),
        "relaxed_year": bool(meta.get("relaxed_year")),
        "analysis": meta.get("analysis") or {},
        "filters": meta.get("filters") or {},
    }


def result_to_meta(result: dict) -> dict:
    return {
        "question": result.get("question") or "",
        "citations": result.get("citations") or [],
        "notes": result.get("notes") or [],
        "grounding": result.get("grounding") or {},
        "elapsed_ms": result.get("elapsed_ms"),
        "trace_id": result.get("trace_id"),
        "ui_mode": result.get("ui_mode") or "none",
        "refused": bool(result.get("refused")),
        "error": result.get("error"),
        "relaxed_year": bool(result.get("relaxed_year")),
        "analysis": result.get("analysis") or {},
        "filters": result.get("filters") or {},
        "chunk_ids": [c.get("chunk_id") for c in (result.get("chunks") or [])],
        "conclusion": conclusion_text(str(result.get("answer") or "")),
    }


def render_empty_state() -> None:
    st.markdown('<div class="rag-kicker">可以这样问</div>', unsafe_allow_html=True)
    cols = st.columns(3)
    for idx, example in enumerate(EXAMPLES):
        with cols[idx]:
            if st.button(example.get("label") or example["q"],
                         key="example_" + str(idx), width="stretch"):
                st.session_state["auto_submit"] = example["q"]
                st.session_state["auto_from_example"] = True
                st.rerun()
            st.caption(example["note"])


def pair_turns(messages: list[dict]) -> list[tuple[dict, dict | None]]:
    """把消息按 user → assistant 配对；孤立的助手消息丢弃。"""
    turns: list[tuple[dict, dict | None]] = []
    for message in messages:
        if message.get("role") == "user":
            turns.append((message, None))
        elif message.get("role") == "assistant" and turns and turns[-1][1] is None:
            turns[-1] = (turns[-1][0], message)
    return turns


def resolve_session(memory: SessionStore) -> str:
    """URL 里的 ?s= 优先；无效则新建，保证刷新能恢复。"""
    raw = st.query_params.get("s")
    session_id = raw if isinstance(raw, str) else (raw[0] if raw else "")
    if not session_id or not memory.session_exists(session_id):
        # 首问才落库：只访问页面不会产生空会话
        session_id = uuid.uuid4().hex[:8]
        st.query_params["s"] = session_id
    return session_id


def handle_question(pipe, memory: SessionStore, session_id: str, question: str,
                    use_rerank: bool, top_k: int, effort: str,
                    history: list[dict]) -> None:
    """写入用户消息 → 流式生成 → 写库 → rerun 统一回放，避免双路径渲染。"""
    pipe.cfg.reasoning_effort = effort
    memory.add_turn(session_id, "user", question)
    st.session_state["scroll_to_latest"] = True
    render_question(question)

    sink: dict = {}
    with st.status("正在检索条款…", expanded=False) as status:
        try:
            stream = pipe.ask_stream(
                question, use_rerank=use_rerank, top_k=top_k,
                history=history, session_id=session_id, sink=sink,
            )
        except Exception as exc:  # noqa: BLE001 - 页面必须给出可读错误
            result = {"answer": "调用失败：" + str(exc), "error": str(exc),
                      "question": question, "citations": [], "notes": [],
                      "refused": False, "chunks": [], "grounding": {},
                      "analysis": {}, "filters": {}, "trace_id": "",
                      "elapsed_ms": None, "ui_mode": effort}
            memory.add_turn(session_id, "assistant", result["answer"], result_to_meta(result))
            status.update(label="检索失败", state="error")
            st.rerun()
            return
        chunks = sink.get("chunks") or []
        status.update(
            label=("检索失败" if sink.get("error") else "找到 %d 条依据" % len(chunks)),
            state=("error" if sink.get("error") else "complete"),
        )

    if sink.get("error"):
        result = sink.get("result") or {"error": "检索失败", "question": question}
        result["ui_mode"] = effort
        memory.add_turn(session_id, "assistant", result.get("answer") or "检索失败",
                        result_to_meta(result))
        st.rerun()
        return

    if sink.get("will_refuse"):
        for _ in stream:
            pass
        result = sink.get("result") or {"refused": True, "question": question}
        result["ui_mode"] = effort
        memory.add_turn(session_id, "assistant", result.get("answer") or "",
                        result_to_meta(result))
        st.rerun()
        return

    st.write_stream(stream)
    result = sink.get("result") or {}
    result["ui_mode"] = effort
    memory.add_turn(session_id, "assistant", result.get("answer") or "",
                    result_to_meta(result))
    st.rerun()


def main() -> None:
    st.set_page_config(page_title="深莞人社政策问答", page_icon="📋", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    head_left, head_right = st.columns([0.72, 0.28], vertical_alignment="center")
    with head_left:
        st.markdown('<div class="rag-title">深莞人社政策问答</div>', unsafe_allow_html=True)
        st.markdown('<div class="rag-sub">每个结论都能回到原文条款；时效存疑会单独提示。</div>',
                    unsafe_allow_html=True)
    with head_right:
        mode = st.segmented_control("回答模式", [MODE_FAST, MODE_HIGH],
                                    default=MODE_FAST, label_visibility="collapsed",
                                    key="mode")
    effort = "none" if (mode or MODE_FAST) == MODE_FAST else "default"
    st.markdown('<div class="rag-note">' + MODE_NOTE[effort] + "</div>",
                unsafe_allow_html=True)

    memory = SessionStore(DEFAULT.memory_db)
    session_id = resolve_session(memory)

    with st.sidebar:
        render_session_list(st, memory, session_id)

    load_slot = st.empty()
    if not st.session_state.get("pipeline_loaded_once"):
        load_slot.info("首次启动：正在加载索引与精排模型（约 30 秒），之后就快了。")
    pipe = load_pipeline()
    load_slot.empty()
    st.session_state["pipeline_loaded_once"] = True
    cfg = pipe.cfg
    cfg.reasoning_effort = effort

    with st.sidebar:
        st.divider()
        with st.popover("⚙ 设置", width="stretch"):
            rerank_ready = rerank_available(cfg.rerank_model)
            use_rerank = st.checkbox("启用精排（BGE-Reranker）", value=rerank_ready)
            top_k = st.slider("返回条款数 top-k", 3, 10, cfg.final_top_k)
            st.caption("快速模式会关闭思考，数字核验可能略降。")
            st.divider()
            st.caption("嵌入：" + cfg.embed_model)
            st.caption("精排：" + (cfg.rerank_model if rerank_ready else "未配置/路径不存在"))
            st.caption("语料：" + str(len(pipe.chunks or [])) + " 条款块")
        st.caption("会话 ID：" + session_id)

    messages = memory.get_messages(session_id)
    if not messages:
        render_empty_state()
    else:
        chunks_by_id = {c["chunk_id"]: c for c in (pipe.chunks or [])}
        turns = pair_turns(messages)
        for idx, (user_msg, assistant_msg) in enumerate(turns):
            if idx:
                st.markdown('<div class="rag-sep"></div>', unsafe_allow_html=True)
            if idx == len(turns) - 1 and assistant_msg is not None:
                st.html('<div id="latest-answer"></div>')
            render_question(str(user_msg.get("content") or ""))
            if assistant_msg is not None:
                render_answer(result_from_message(assistant_msg, chunks_by_id))
        should_scroll = st.session_state.pop("scroll_to_latest", False)
        if should_scroll and len(turns) > 1:
            st.html("""
<script>
function scrollToLatest() {
  var box = document.querySelector('[data-testid="stAppScrollToBottomContainer"]');
  var el = document.getElementById('latest-answer');
  if (!box || !el) return;
  var top = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 10;
  box.scrollTo({top: Math.max(0, top), behavior: 'instant'});
}
setTimeout(scrollToLatest, 250);
setTimeout(scrollToLatest, 900);
</script>
""", unsafe_allow_javascript=True)

    submitted = st.session_state.pop("auto_submit", "")
    from_example = st.session_state.pop("auto_from_example", False)
    typed = st.chat_input("输入问题，例如：深圳失业保险金标准是多少？", max_chars=500)
    if typed:
        submitted = typed.strip()
        from_example = False

    if str(submitted or "").strip():
        history = [] if from_example else memory.short_history(session_id, turns=3)
        handle_question(pipe, memory, session_id, str(submitted).strip(),
                        use_rerank, top_k, effort, history)


if __name__ == "__main__":
    main()








