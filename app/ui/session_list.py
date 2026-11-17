"""侧栏会话列表：新建、切换、删除。只负责交互，不渲染对话内容。"""
from __future__ import annotations

import html
import uuid
from datetime import date, datetime, timedelta

import streamlit as st


def _switch(session_id: str) -> None:
    """切换当前会话：写进 URL，刷新和分享都能恢复。"""
    st.query_params["s"] = session_id
    st.session_state.pop("auto_submit", None)
    st.session_state.pop("auto_from_example", None)
    st.rerun()


def _group_label(updated_at: str) -> str:
    """把时间戳归到 今天 / 昨天 / MM-DD 三档。"""
    try:
        day = datetime.fromisoformat(str(updated_at)).date()
    except (TypeError, ValueError):
        return "更早"
    today = date.today()
    if day == today:
        return "今天"
    if day == today - timedelta(days=1):
        return "昨天"
    return day.strftime("%m-%d")


def render_session_list(st_module, memory, current_id: str) -> None:
    """渲染侧栏；st_module 传入 streamlit，便于单测时不绑定全局。"""
    if st_module.button("＋ 新建会话", type="primary", width="stretch",
                        key="new_session"):
        _switch(uuid.uuid4().hex[:8])

    st_module.markdown('<div class="side-label">会话</div>', unsafe_allow_html=True)
    sessions = memory.list_sessions(limit=12)
    if current_id not in {str(s["id"]) for s in sessions}:
        sessions.insert(0, {
            "id": current_id,
            "turns": 0,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "title": datetime.now().strftime("%m-%d %H:%M") + " · 新会话",
        })
    if not sessions:
        st_module.caption("还没有会话，先问一个问题。")
        return

    last_group = ""
    for item in sessions:
        title = str(item.get("title") or "新会话")
        stamp, _, question = title.partition(" · ")
        active = item["id"] == current_id

        group = _group_label(item.get("updated_at") or "")
        if group != last_group:
            st_module.markdown(
                '<div class="side-group">' + html.escape(group) + "</div>",
                unsafe_allow_html=True,
            )
            last_group = group

        time_label = stamp[-5:] if len(stamp) >= 5 else stamp
        st_module.markdown(
            '<div class="side-time">' + html.escape(time_label) + "</div>",
            unsafe_allow_html=True,
        )
        left, right = st_module.columns(
            [0.84, 0.16], gap="small", vertical_alignment="center"
        )
        with left:
            label = ("● " if active else "") + (question or "新会话")
            if st_module.button(
                label,
                key="sess_" + str(item["id"]),
                width="stretch",
                type="primary" if active else "secondary",
            ):
                _switch(str(item["id"]))
        with right:
            with st_module.popover("", icon=":material/more_horiz:",
                                   width="content", key="pop_" + str(item["id"])):
                st_module.caption("会话操作")
                if st_module.button(
                    "删除这个会话",
                    key="del_" + str(item["id"]),
                    width="stretch",
                ):
                    memory.delete_session(str(item["id"]))
                    if active:
                        _switch(uuid.uuid4().hex[:8])
                    st_module.rerun()
