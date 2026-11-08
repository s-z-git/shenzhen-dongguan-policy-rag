"""app.ui.streamlit_app —— 网页界面（先答案，后过程）

启动（必须在项目根目录执行）：
    streamlit run app/ui/streamlit_app.py

注意：streamlit run 是“按脚本跑”，包级相对导入会失效，
所以这里手动把项目根塞进 sys.path，再用绝对导入。
"""
from __future__ import annotations

import html
import pathlib
import re
import sys
import uuid

# 将项目根目录加入 Python 模块搜索路径，使 app.xxx 绝对导入可用
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import streamlit as st

from app.ui.citation_view import render_citation_cards

MODE_FAST_LABEL = "⚡ 快速（默认）"
MODE_HIGH_LABEL = "🎯 高质量"
MODE_FAST_CAPTION = "关闭思考：更快，但数字核验/stale 题可能下降。"
MODE_HIGH_CAPTION = "复现 README 主数字；更稳，但约 2–3 倍生成时间。"

# 这 3 个问题已用 ask.py 和页面内 warm pipeline 各跑过一次；
# caption 写页面内快速模式实测，避免把冷启动加载时间算进单次问答。
EXAMPLES = [
    {"q": "东莞本科应届生入户后能领哪些补贴？", "time": "10.6s"},
    {"q": "深圳新引进博士人才生活补贴标准是多少", "time": "7.7s"},
    {"q": "深圳失业保险金标准是多少？", "time": "5.1s"},
]

CSS = """
<style>
.block-container { padding-top: 2.0rem; max-width: 1100px; }
.rag-mode-note { color: #6b7280; font-size: 0.84rem; margin-top: -0.35rem; }
.rag-conclusion {
    background: #f0f7ff;
    border-left: 4px solid #2f80ed;
    padding: 10px 14px;
    border-radius: 6px;
    margin: 0.2rem 0 0.55rem;
}
.rag-badges { display: flex; flex-wrap: wrap; gap: 7px; margin: 0.15rem 0 0.9rem; }
.rag-badge {
    background: #f3f4f6;
    border: 1px solid #e5e7eb;
    border-radius: 999px;
    padding: 2px 10px;
    font-size: 0.82rem;
    color: #374151;
}
</style>
"""


@st.cache_resource(show_spinner=False)
def load_pipeline():
    """加载 RAG 管线并预热；由 cache_resource 保证切换模式不重载。"""
    from app.config import DEFAULT
    from app.pipeline import RAGPipeline

    pipe = RAGPipeline.load(DEFAULT)
    try:
        pipe.warmup()
    except Exception:
        # 预热失败不阻断页面；真正的检索错误会在提问时暴露
        pass
    return pipe


def mode_to_effort(mode_label: str) -> str:
    """UI 标签转配置值；只有快速模式关闭思考。"""
    return "none" if mode_label == MODE_FAST_LABEL else "default"


def effort_to_badge(effort: str) -> str:
    """结果徽章展示本次实际使用的模式，而不是当前开关位置。"""
    return "⚡ 快速" if effort == "none" else "🎯 高质量"


def strip_citation_markers(text: str) -> str:
    """去掉回答里的 [出处: ...]，让一句话结论更干净。"""
    text = re.sub(r"\[出处[:：][^\]]+\]", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def one_sentence_conclusion(answer: str) -> str:
    """纯规则提取一句话结论：优先“综上”，否则第一句，再否则前 80 字。"""
    text = strip_citation_markers(answer)
    if not text:
        return ""
    text = re.sub(r"^\s*(?:[-*•]|\d+[.、])\s*", "", text)
    parts = [p.strip() for p in re.split(r"(?<=[。！？!?])\s*|\n+", text) if p.strip()]
    for part in parts:
        if part.startswith("综上"):
            return part
    if parts:
        return parts[0]
    return text[:80]


def answer_preview(answer: str, max_lines: int = 6) -> tuple[str, bool]:
    """返回前 N 行和“是否还有更多”。"""
    lines = (answer or "").splitlines()
    return "\n".join(lines[:max_lines]), len(lines) > max_lines


def format_seconds(elapsed_ms: float | int | None) -> str:
    """毫秒转秒；没有耗时显示 —。"""
    if elapsed_ms is None:
        return "—"
    return "%.1fs" % (float(elapsed_ms) / 1000.0)


def badge_html(mode: str, elapsed_ms, ci: dict, nu: dict, chunk_count: int) -> str:
    """生成徽章行；所有数字都来自本次返回。"""
    if ci.get("total"):
        mark = "✅" if not (ci.get("unmatched") or ci.get("clause_conflict")) else "⚠️"
        cite_text = "%s 引用 %d/%d" % (mark, ci.get("verified", 0), ci["total"])
    else:
        cite_text = "⚠️ 无引用"
    if nu.get("total"):
        mark = "⚠️" if nu.get("ungrounded") else "🔢"
        num_text = "%s 数字 %d/%d" % (mark, nu.get("grounded", 0), nu["total"])
    else:
        num_text = "🔢 数字 —"
    items = [mode, "⏱ " + format_seconds(elapsed_ms), cite_text, num_text,
             "📄 依据 %d 份" % chunk_count]
    spans = "".join('<span class="rag-badge">' + html.escape(x) + "</span>" for x in items)
    return '<div class="rag-badges">' + spans + "</div>"


def store_result(result: dict, question: str, memory, session_id: str, effort: str,
                 use_rerank: bool, top_k: int, record_history: bool = True) -> None:
    """把本次结果和 UI 设置存入 session_state。"""
    result["ui_mode"] = effort
    if record_history:
        memory.add_turn(session_id, "user", question)
        memory.add_turn(session_id, "assistant", result.get("answer") or "")
    st.session_state["last_result"] = result
    st.session_state["last_query"] = question
    st.session_state["last_use_rerank"] = use_rerank
    st.session_state["last_top_k"] = top_k
    st.session_state["last_mode"] = effort


def render_conclusion(result: dict, target=None) -> None:
    """一句话结论；可写入预先占好的占位区，保证流式后仍在正文上方。"""
    conclusion = one_sentence_conclusion(str(result.get("answer") or ""))
    if not conclusion:
        return
    out = target if target is not None else st
    out.markdown(
        '<div class="rag-conclusion"><strong>一句话结论</strong><br>'
        + html.escape(conclusion) + "</div>",
        unsafe_allow_html=True,
    )


def render_badge(result: dict, target=None) -> None:
    """核验徽章；只能在流结束后调用。"""
    grounding = result.get("grounding") or {}
    ci = grounding.get("citations") or {}
    nu = grounding.get("numbers") or {}
    out = target if target is not None else st
    out.markdown(
        badge_html(effort_to_badge(str(result.get("ui_mode") or "none")),
                   result.get("elapsed_ms"), ci, nu, len(result.get("chunks") or [])),
        unsafe_allow_html=True,
    )


def render_refusal_body(result: dict) -> None:
    """范围外问题：info，不用黄色 warning。"""
    st.info("这属于范围外问题。当前知识库只覆盖深圳、东莞两地人社政策。")
    st.markdown("可以试试这些方向：")
    st.markdown("- 深圳/东莞的就业补贴怎么申请？")
    st.markdown("- 某类人才的补贴标准是多少？")
    st.markdown("- 养老、失业、工伤等社保待遇如何计算？")
    st.caption("系统回答：" + str(result.get("answer") or ""))
    st.caption("trace_id：" + str(result.get("trace_id") or "无"))


def render_error_body(result: dict, question: str, retry_key: str) -> None:
    """调用失败：error + trace_id + 重试。重试走同一流式入口。"""
    st.error("调用失败：" + str(result.get("error") or result.get("answer") or ""))
    st.caption("trace_id：" + str(result.get("trace_id") or "无"))
    if st.button("重试这次提问", type="primary", key=retry_key):
        st.session_state["auto_submit"] = question
        st.rerun()


def render_warnings(result: dict) -> None:
    """时效/废止和年份放宽警告统一放在结论上方。"""
    for note in result.get("notes") or []:
        st.warning("⚠️ " + str(note))
    if result.get("relaxed_year"):
        st.warning("符合你所提年份条件的条款不足，回答中可能包含更早年份的文件。")


def citation_issues(ci: dict) -> list[str]:
    """收集未匹配/条款冲突，供核验失败状态展示。"""
    issues = []
    for item in ci.get("items") or []:
        status = item.get("status")
        if status == "unmatched":
            issues.append("未匹配：" + str(item.get("raw") or ""))
        elif status == "clause_conflict":
            actual = "、".join(item.get("actual_clause") or [])
            issues.append("条款冲突：" + str(item.get("raw") or "") + "；原文实际为 " + actual)
    return issues


def render_process(result: dict) -> None:
    """把查询分析和原始条款收进折叠区。"""
    with st.expander("系统是怎么找到这些条款的", expanded=False):
        st.markdown("**查询分析（结构化条件）**")
        analysis = result.get("analysis") or {}
        if analysis:
            st.json(analysis)
        else:
            st.caption("本次没有查询分析结果。")
        if result.get("filters"):
            st.caption("实际过滤条件：" + str(result["filters"]))
        st.markdown("**原始条款**")
        chunks = result.get("chunks") or []
        if not chunks:
            st.caption("本次没有检索到条款。")
        for idx, chunk in enumerate(chunks, 1):
            title = str(chunk.get("doc_title") or chunk.get("doc_id") or "")
            clause = str(chunk.get("clause_path") or "全文")
            st.markdown("**%d. %s（%s）**" % (idx, title, clause))
            st.caption("城市 " + str(chunk.get("city")) + " | 年份 " + str(chunk.get("year"))
                       + " | 发布 " + str(chunk.get("publish_date")) + " | "
                       + str(chunk.get("source_url")))
            st.text(str(chunk.get("text") or "")[:800])


def render_result(result: dict, question: str, pipe, memory, session_id: str,
                  use_rerank: bool, top_k: int) -> None:
    """渲染一次结果：拒答/调用失败/核验失败/正常回答四种分支。"""
    st.chat_message("user").markdown(question)
    with st.chat_message("assistant"):
        if result.get("error"):
            render_error_body(result, question, "retry_call")
            return

        if result.get("refused"):
            render_refusal_body(result)
            return

        grounding = result.get("grounding") or {}
        ci = grounding.get("citations") or {}
        issues = citation_issues(ci)

        render_warnings(result)
        if issues:
            st.error("引用核验失败：有 %d 条出处未匹配或条款冲突。trace_id：%s"
                     % (len(issues), str(result.get("trace_id") or "无")))
            for issue in issues:
                st.markdown("- " + issue)

        render_conclusion(result)
        render_badge(result)

        answer = str(result.get("answer") or "")
        preview, has_more = answer_preview(answer, max_lines=6)
        st.markdown(preview)
        if has_more:
            with st.expander("完整回答", expanded=False):
                st.markdown(answer)

        render_citation_cards(st, result.get("citations") or [], grounding,
                              result.get("chunks") or [])
        render_process(result)
        st.caption("生成首字 " + format_seconds(result.get("first_token_ms"))
                   + " · 端到端首字 " + format_seconds(result.get("ttft_ms"))
                   + " · trace_id：" + str(result.get("trace_id") or "无"))


def stream_query_and_render(pipe, question: str, memory, session_id: str,
                          use_rerank: bool, top_k: int, effort: str,
                          history: list[dict]) -> None:
    """检索显示 status，生成用 write_stream；流结束后才核验和显示徽章。"""
    pipe.cfg.reasoning_effort = effort
    st.chat_message("user").markdown(question)
    sink: dict = {}
    with st.chat_message("assistant"):
        with st.status("正在检索条款…", expanded=False) as status:
            try:
                stream = pipe.ask_stream(
                    question, use_rerank=use_rerank, top_k=top_k,
                    history=history, session_id=session_id, sink=sink,
                )
            except Exception as exc:
                result = {"answer": "调用失败：" + str(exc), "error": str(exc),
                          "citations": [], "notes": [], "refused": False, "chunks": [],
                          "grounding": {}, "analysis": {}, "trace_id": ""}
                status.update(label="检索失败", state="error")
                store_result(result, question, memory, session_id, effort,
                             use_rerank, top_k, record_history=False)
                render_error_body(result, question, "retry_stream_retrieve")
                return
            chunks = sink.get("chunks") or []
            if sink.get("error"):
                status.update(label="检索失败", state="error")
            else:
                status.update(label="找到 %d 条依据" % len(chunks), state="complete")

        if sink.get("error"):
            result = sink.get("result") or {}
            store_result(result, question, memory, session_id, effort,
                         use_rerank, top_k, record_history=False)
            render_error_body(result, question, "retry_stream_retrieve")
            return

        render_warnings(sink)

        if sink.get("will_refuse"):
            for _ in stream:
                pass
            result = sink.get("result") or {}
            store_result(result, question, memory, session_id, effort,
                         use_rerank, top_k, record_history=True)
            render_refusal_body(result)
            return

        # 先占位，流结束后再填；这样结论和徽章仍在正文上方。
        error_slot = st.empty()
        conclusion_slot = st.empty()
        badge_slot = st.empty()
        st.write_stream(stream)

        result = sink.get("result") or {}
        result["ui_mode"] = effort
        if result.get("error"):
            store_result(result, question, memory, session_id, effort,
                         use_rerank, top_k, record_history=False)
            error_slot.error("调用失败：" + str(result.get("error")))
            st.caption("trace_id：" + str(result.get("trace_id") or "无"))
            if st.button("重试这次提问", type="primary", key="retry_stream_call"):
                st.session_state["auto_submit"] = question
                st.rerun()
            return

        if result.get("refused"):
            store_result(result, question, memory, session_id, effort,
                         use_rerank, top_k, record_history=True)
            render_refusal_body(result)
            return

        grounding = result.get("grounding") or {}
        issues = citation_issues(grounding.get("citations") or {})
        if issues:
            error_slot.error("引用核验失败：有 %d 条出处未匹配或条款冲突。trace_id：%s"
                             % (len(issues), str(result.get("trace_id") or "无")))
            for issue in issues:
                st.markdown("- " + issue)

        render_conclusion(result, conclusion_slot)
        render_badge(result, badge_slot)
        render_citation_cards(st, result.get("citations") or [], grounding,
                              result.get("chunks") or [])
        render_process(result)
        st.caption("生成首字 " + format_seconds(result.get("first_token_ms"))
                   + " · 端到端首字 " + format_seconds(result.get("ttft_ms"))
                   + " · trace_id：" + str(result.get("trace_id") or "无"))

    store_result(result, question, memory, session_id, effort,
                 use_rerank, top_k, record_history=True)


def main() -> None:
    """页面主体：先渲染标题与模式，再加载索引；回答按 Q → A 展示。"""
    st.set_page_config(page_title="深莞人社政策问答", page_icon="📋", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    # 先渲染首屏，再执行耗时加载；这是冷启动 <2s 的关键。
    st.title("深莞人社政策问答")
    st.caption("回答给出原文出处（文件名 + 条款号）；引用超期/已废止政策时会主动提示。")
    load_slot = st.empty()
    if not st.session_state.get("pipeline_loaded_once"):
        load_slot.info("首次启动：正在加载索引与精排模型（约 30 秒），之后就快了。")

    mode_label = st.radio("回答模式", [MODE_FAST_LABEL, MODE_HIGH_LABEL],
                          horizontal=True, index=0)
    effort = mode_to_effort(mode_label)
    mode_caption = MODE_FAST_CAPTION if effort == "none" else MODE_HIGH_CAPTION
    st.markdown('<div class="rag-mode-note">' + mode_caption + "</div>",
                unsafe_allow_html=True)

    submitted = st.session_state.pop("auto_submit", "")
    from_example = False
    if st.session_state.get("last_result") is None:
        st.markdown("#### 不知道从哪问起？点一个示例")
        cols = st.columns(3)
        for idx, example in enumerate(EXAMPLES):
            with cols[idx]:
                if st.button(example["q"], key="example_" + str(idx), use_container_width=True):
                    submitted = example["q"]
                    from_example = True
                st.caption("⚡ 快速模式实测 " + example["time"])

    # 到这里才导入并加载 FAISS / 精排模型；标题和模式开关已经可见。
    pipe = load_pipeline()
    load_slot.empty()
    st.session_state["pipeline_loaded_once"] = True
    # 开关只改这个字段；get_llm 的缓存 key 已包含它，客户端可共存。
    pipe.cfg.reasoning_effort = effort
    cfg = pipe.cfg
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = uuid.uuid4().hex[:8]
    session_id = st.session_state["session_id"]

    from app.memory import SessionStore
    from app.retrieve.rerank import rerank_available

    memory = SessionStore(cfg.memory_db)
    history = memory.recent(session_id, limit=10)

    with st.sidebar:
        with st.expander("高级设置", expanded=False):
            rerank_ready = rerank_available(cfg.rerank_model)
            use_rerank = st.checkbox("启用精排（BGE-Reranker）", value=rerank_ready)
            top_k = st.slider("返回条款数 top-k", 3, 10, cfg.final_top_k)
        with st.expander("模型与语料", expanded=False):
            st.caption("嵌入：" + cfg.embed_model)
            st.caption("精排：" + (cfg.rerank_model if rerank_ready else "未配置/路径不存在"))
            st.caption("语料：" + str(len(pipe.chunks or [])) + " 条款块")
        with st.expander("最近对话（用于追问改写）", expanded=False):
            if history:
                for msg in history:
                    role = "你" if msg["role"] == "user" else "助手"
                    st.markdown("**" + role + "：** " + str(msg["content"])[:300])
            else:
                st.caption("暂无历史消息。")
        st.divider()
        st.caption("会话：" + session_id)
        if st.button("清空对话"):
            memory.clear(session_id)
            for key in ("last_result", "last_query", "last_mode", "last_use_rerank",
                        "last_top_k", "citation_locator", "auto_submit"):
                st.session_state.pop(key, None)
            st.rerun()

    typed = st.chat_input("也可以直接输入问题，例如：深圳失业保险金标准是多少？",
                          max_chars=500)
    if typed:
        submitted = typed
        from_example = False

    if submitted.strip():
        # 示例是独立问题，不受上一题 rewrite_query 影响。
        active_history = [] if from_example else history
        stream_query_and_render(pipe, submitted.strip(), memory, session_id,
                                use_rerank, top_k, effort, active_history)
        return

    result = st.session_state.get("last_result")
    if result:
        last_question = st.session_state.get("last_query", "")
        render_result(result, last_question, pipe, memory, session_id, use_rerank, top_k)


if __name__ == "__main__":
    main()
