"""多轮会话存储：SQLite 保存会话与消息，只用于追问改写和页面回放。

设计边界：
- 不存长期用户画像；
- 不把历史回答当知识；
- 只保存 user/assistant 文本和少量展示元数据；
- 默认最近 5 轮，避免上下文无限增长。

表结构：
    sessions(id, title, created_at, updated_at)
    messages(id, session_id, role, content, created_at, meta_json)

meta_json 只存渲染需要的小字段（引用、时效提示、核验计数、trace_id、chunk_id），
不复制原文条款；原文由 chunk_id 从 chunks.jsonl 反查。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path


class SessionStore:
    """一个很薄的 SQLite 会话存储；每次操作开连接，避免跨线程共享连接。"""

    def __init__(self, db_path: str | Path = "data/memory.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ---------- 基础 ----------

    def _connect(self) -> sqlite3.Connection:
        """每次调用都新建连接，用完即关；timeout 避免并发写直接报错。"""
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """建表、补列、回填旧会话；全部幂等。"""
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
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, id)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_updated
                ON sessions(updated_at DESC)
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
            if "meta_json" not in columns:
                conn.execute("ALTER TABLE messages ADD COLUMN meta_json TEXT")
            self._backfill_meta(conn)
            self._backfill_sessions(conn)
            conn.execute(
                "DELETE FROM sessions WHERE id NOT IN (SELECT DISTINCT session_id FROM messages)"
            )

    @staticmethod
    def _legacy_conclusion(text: str) -> str:
        """从旧版回答里提取一句结论，供 short_history 使用。"""
        clean = re.sub(r"\[出处[:：][^\]]+\]", "", text or "")
        summary = ""
        for line in clean.splitlines():
            if line.strip().startswith("综上"):
                summary = line.strip()
                break
        if not summary:
            flat = re.sub(r"\s+", " ", clean).strip()
            parts = [p.strip() for p in re.split(r"(?<=[。！？!?])", flat) if p.strip()]
            summary = parts[0] if parts else flat
        summary = re.sub(r"^\s*综上[，,：:]?\s*", "", summary)
        return summary[:60]

    def _backfill_meta(self, conn: sqlite3.Connection) -> None:
        """给旧版 assistant 消息补 conclusion，避免 short_history 回退到整段正文。"""
        rows = conn.execute("""
            SELECT id, content FROM messages
            WHERE role = 'assistant' AND (meta_json IS NULL OR meta_json = '')
        """).fetchall()
        for row in rows:
            conclusion = self._legacy_conclusion(row["content"])
            if not conclusion:
                continue
            conn.execute(
                "UPDATE messages SET meta_json = ? WHERE id = ?",
                (json.dumps({"conclusion": conclusion, "legacy": True},
                            ensure_ascii=False), row["id"]),
            )

    def _backfill_sessions(self, conn: sqlite3.Connection) -> None:
        """把旧库里的 messages 汇总成 sessions；已有会话不覆盖。"""
        rows = conn.execute("""
            SELECT session_id, MIN(created_at) AS first_at, MAX(created_at) AS last_at
            FROM messages
            GROUP BY session_id
        """).fetchall()
        for row in rows:
            session_id = row["session_id"]
            exists = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if exists:
                continue
            first = conn.execute("""
                SELECT content, created_at FROM messages
                WHERE session_id = ? AND role = 'user'
                ORDER BY id LIMIT 1
            """, (session_id,)).fetchone()
            question = (first["content"] if first else "") or ""
            created_at = (first["created_at"] if first else row["first_at"]) or ""
            conn.execute(
                "INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, self.format_title(question, created_at),
                 created_at, row["last_at"] or created_at),
            )

    # ---------- 会话 ----------

    @staticmethod
    def format_title(question: str, created_at: str | None = None) -> str:
        """标题 = MM-DD HH:mm · 首问前 12 字；空问题回退为“新会话”。"""
        stamp = ""
        if created_at:
            try:
                stamp = datetime.fromisoformat(created_at).strftime("%m-%d %H:%M")
            except ValueError:
                stamp = created_at[5:16].replace("T", " ")
        if not stamp:
            stamp = datetime.now().strftime("%m-%d %H:%M")
        text = re.sub(r"\s+", " ", (question or "")).strip()
        if not text:
            return stamp + " · 新会话"
        short = text[:12] + ("…" if len(text) > 12 else "")
        return stamp + " · " + short

    def ensure_session(self, session_id: str, question: str = "") -> None:
        """创建会话；已存在则不动。"""
        if not session_id:
            return
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, self.format_title(question), now, now),
            )

    def session_exists(self, session_id: str) -> bool:
        if not session_id:
            return False
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone() is not None

    def list_sessions(self, limit: int = 20) -> list[dict]:
        """按最近活跃倒序返回会话，附带轮数。"""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT s.id, s.title, s.created_at, s.updated_at,
                       COALESCE(SUM(CASE WHEN m.role = 'user' THEN 1 ELSE 0 END), 0) AS turns
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                GROUP BY s.id
                ORDER BY s.updated_at DESC, s.created_at DESC
                LIMIT ?
            """, (max(1, limit),)).fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> None:
        """删除会话及其全部消息。"""
        if not session_id:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    # ---------- 消息 ----------

    def add_turn(self, session_id: str, role: str, content: str,
                 meta: dict | None = None) -> None:
        """追加一条消息；首次用户消息会更新会话标题和活跃时间。"""
        text = (content or "").strip()
        if not session_id or not text:
            return
        now = datetime.now().isoformat(timespec="seconds")
        payload = json.dumps(meta, ensure_ascii=False) if meta else None
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages(session_id, role, content, created_at, meta_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, role, text[:4000], now, payload),
            )
            conn.execute(
                "INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, self.format_title(text if role == "user" else ""), now, now),
            )
            if role == "user":
                user_turns = conn.execute(
                    "SELECT COUNT(*) AS n FROM messages "
                    "WHERE session_id = ? AND role = 'user'",
                    (session_id,),
                ).fetchone()["n"]
                if user_turns == 1:
                    conn.execute(
                        "UPDATE sessions SET title = ? WHERE id = ?",
                        (self.format_title(text, now), session_id),
                    )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
            )

    def get_messages(self, session_id: str) -> list[dict]:
        """按时间正序返回全部消息，解析 meta_json。"""
        if not session_id:
            return []
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT id, role, content, meta_json, created_at
                FROM messages WHERE session_id = ? ORDER BY id
            """, (session_id,)).fetchall()
        out = []
        for row in rows:
            meta = {}
            if row["meta_json"]:
                try:
                    meta = json.loads(row["meta_json"])
                except (TypeError, ValueError):
                    meta = {}
            out.append({
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
                "meta": meta,
            })
        return out

    def recent(self, session_id: str, limit: int = 10) -> list[dict]:
        """按时间正序返回最近 limit 条原始消息（兼容 ask.py）。"""
        if not session_id:
            return []
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT role, content FROM messages
                WHERE session_id = ? ORDER BY id DESC LIMIT ?
            """, (session_id, max(1, limit))).fetchall()
        return [{"role": row["role"], "content": row["content"]}
                for row in reversed(rows)]

    def short_history(self, session_id: str, turns: int = 3) -> list[dict]:
        """给 query rewrite 用的短历史：问题 + 结论，避免整段政策正文进 prompt。"""
        if not session_id:
            return []
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT role, content, meta_json FROM messages
                WHERE session_id = ? ORDER BY id DESC LIMIT ?
            """, (session_id, max(1, turns) * 2)).fetchall()
        out = []
        for row in reversed(rows):
            content = row["content"] or ""
            if row["role"] == "user":
                out.append({"role": "user", "content": content[:200]})
                continue
            conclusion = ""
            if row["meta_json"]:
                try:
                    conclusion = str((json.loads(row["meta_json"]) or {}).get("conclusion") or "")
                except (TypeError, ValueError):
                    conclusion = ""
            out.append({"role": "assistant", "content": (conclusion or content)[:120]})
        return out

    def clear(self, session_id: str) -> None:
        """只清消息、保留会话壳（兼容旧入口）。"""
        if not session_id:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))

