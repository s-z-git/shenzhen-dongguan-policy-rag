"""最小多轮记忆：SQLite 存储会话消息，只保留最近几轮用于 query rewrite。

设计边界：
- 不存长期用户画像；
- 不把历史回答当知识；
- 只保存 user/assistant 文本，按 session_id 隔离；
- 默认最近 5 轮，避免上下文无限增长。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


class SessionStore:
    """一个很薄的 SQLite 会话存储；每次操作开连接，避免跨线程共享连接。"""

    def __init__(self, db_path: str | Path = "data/memory.db"):
        self.db_path = Path(db_path)
        # 自动创建数据库文件所在的目录（如 data/）
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()  # 初始化表结构（首次运行时建表）

    def _connect(self) -> sqlite3.Connection:
        """每次调用都新建一个连接，用完即关，避免跨线程共享连接的问题。"""
        conn = sqlite3.connect(self.db_path)
        # 设置 row_factory 为 Row，使查询结果支持按列名访问（如 row["role"]）
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """建表 & 建索引；IF NOT EXISTS 保证多次调用幂等。"""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            # (session_id, id) 联合索引加速按会话查询最近消息
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, id)
            """)

    def add_turn(self, session_id: str, role: str, content: str) -> None:
        """追加一条消息；内容截断到 4000 字，防止异常长文本撑爆库。"""
        text = (content or "").strip()  # 容错：处理 None 和空白字符串
        if not session_id or not text:
            return  # 无效参数直接跳过
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages(session_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    session_id,
                    role,
                    text[:4000],  # 硬截断到 4000 字符，防止超长文档注入
                    datetime.now().isoformat(timespec="seconds"),  # 精确到秒的时间戳
                ),
            )

    def recent(self, session_id: str, limit: int = 10) -> list[dict]:
        """按时间正序返回最近 limit 条消息（limit 按消息条数，不是轮数）。"""
        if not session_id:
            return []
        with self._connect() as conn:
            # DESC 取最新 N 条，然后在 Python 中 reverse 恢复为正序（时间从早到晚）
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE session_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (session_id, max(1, limit)),  # 保证 limit 至少为 1
            ).fetchall()
        # reversed: SQL 返回的是 DESC（最新在前），反转后变为时间正序
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    def clear(self, session_id: str) -> None:
        """删除一个会话的全部消息。"""
        if not session_id:
            return  # 防护：空 session_id 不做任何操作
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))