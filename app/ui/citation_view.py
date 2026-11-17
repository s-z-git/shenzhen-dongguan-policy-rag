"""引用展示：把回答里的出处映射回本轮 chunk，并高亮条款标题。

注意：这里只做展示，不改变检索/生成结果。
"""
from __future__ import annotations

import html
import re


def find_citation_item(citation: str, grounding: dict | None) -> dict | None:
    """在 grounding.citations.items 里找与 citation 最匹配的一条。"""
    items = ((grounding or {}).get("citations") or {}).get("items") or []
    c = (citation or "").strip()
    if not c:
        return None
    for item in items:
        raw = (item.get("raw") or "").strip()
        if raw == c or raw.startswith(c) or c.startswith(raw):
            return item
    for item in items:
        raw = (item.get("raw") or "").strip()
        if c[:20] and c[:20] in raw:
            return item
    return None


def find_matched_chunks(item: dict | None, chunks: list[dict]) -> list[dict]:
    """按 matched_ids 取回 chunk；保序、去重。"""
    if not item:
        return []
    ids = item.get("matched_ids") or []
    by_id = {c.get("chunk_id"): c for c in (chunks or [])}
    out = []
    seen = set()
    for cid in ids:
        if cid in by_id and cid not in seen:
            out.append(by_id[cid])
            seen.add(cid)
    return out


def _clause_headings(clause_path: str) -> list[str]:
    """把 clause_path 拆成可高亮的小标题，如 二、 > （一） -> ['二、', '（一）']。"""
    parts = re.split(r"\s*>\s*", clause_path or "")
    out = []
    for part in parts:
        part = part.strip()
        if part and part != "全文":
            out.append(part)
    return out


def highlight_clause(text: str, clause_path: str) -> str:
    """转义 HTML 后，把 clause_path 里的小标题包成 <mark>。"""
    safe = html.escape(text or "")
    for heading in _clause_headings(clause_path):
        safe_heading = html.escape(heading)
        if safe_heading and safe_heading in safe:
            safe = safe.replace(safe_heading, "<mark>" + safe_heading + "</mark>", 1)
    return safe


def _citation_status_label(status: str) -> str:
    """把核验状态翻译成用户能看懂的短标签。"""
    if status == "verified":
        return "✅ 可验证"
    if status == "no_clause":
        return "⚠️ 仅文件级"
    if status == "clause_conflict":
        return "❌ 条款冲突"
    if status == "unmatched":
        return "❌ 未匹配"
    return "○ 未核验"


def _citation_summary(raw: str, item: dict | None, matched: list[dict]) -> tuple[str, str, str]:
    """生成出处摘要：文件名、条款号、年份。"""
    if matched:
        first = matched[0]
        title = str(first.get("doc_title") or first.get("doc_id") or raw)
        clause = str(first.get("clause_path") or "全文")
        year = str(first.get("year") or "年份未知")
        return title, clause, year
    year_match = re.search(r"(?:19|20)\d{2}", raw or "")
    clause_match = re.search(r"（([^（）]*(?:条|项)[^（）]*)）", raw or "")
    year = year_match.group(0) if year_match else "年份未知"
    clause = clause_match.group(1) if clause_match else (item or {}).get("clause") or "条款未知"
    return raw or "未知文件", str(clause), year


def render_citation_cards(st, citations: list[str], grounding: dict | None,
                          chunks: list[dict], expanded_first: bool = False) -> None:
    """渲染依据：卡头常显（序号/文件名/条款/年份/状态），原文默认折叠。"""
    if not citations:
        return
    st.markdown('<div class="rag-kicker">依据</div>', unsafe_allow_html=True)
    for idx, raw in enumerate(citations, 1):
        item = find_citation_item(raw, grounding)
        matched = find_matched_chunks(item, chunks)
        title, clause, year = _citation_summary(raw, item, matched)
        status = (item or {}).get("status", "unknown")
        label = "[%d] %s（%s） · %s · %s" % (
            idx, title, clause, year, _citation_status_label(status))
        with st.expander(label, expanded=bool(expanded_first and idx == 1)):
            if item:
                st.caption("回答中的原始出处：" + raw)
            if status == "clause_conflict":
                actual = "、".join((item or {}).get("actual_clause") or [])
                st.warning("条款号与原文不一致，原文实际为：" + actual)
            if not matched:
                st.caption("这条出处没有映射到本轮原文，可能只匹配到文件名。")
                continue
            for chunk in matched:
                path = str(chunk.get("clause_path") or "全文")
                st.caption("条款路径：" + path)
                st.markdown(
                    '<div class="cite-body">'
                    + highlight_clause(str(chunk.get("text") or ""), path)
                    + "</div>",
                    unsafe_allow_html=True,
                )
                url = str(chunk.get("source_url") or "").strip()
                if url.startswith("http"):
                    st.markdown("[查看政策原文](" + url + ")")


def render_citation_locator(st, citations: list[str], grounding: dict | None,
                            chunks: list[dict]) -> None:
    """兼容旧入口：选择一个出处，查看原文高亮。"""
    if not citations:
        return
    st.subheader("引用定位")
    selected = st.selectbox("选择一个出处，查看原文位置", citations, key="citation_locator")
    item = find_citation_item(selected, grounding)
    matched = find_matched_chunks(item, chunks)
    if not matched:
        st.warning("这条出处没有映射到本轮 top-k 原文，可能只是文件名匹配。")
        return
    for chunk in matched:
        st.markdown("**" + html.escape(str(chunk.get("doc_title") or chunk.get("doc_id"))) + "**")
        st.caption("条款路径：" + str(chunk.get("clause_path") or "全文"))
        st.markdown(
            '<div class="cite-body">'
            + highlight_clause(str(chunk.get("text") or ""), str(chunk.get("clause_path") or ""))
            + "</div>",
            unsafe_allow_html=True,
        )
