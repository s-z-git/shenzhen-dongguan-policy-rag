"""问答应答的轻量 trace：一次问答写一行 JSONL。

设计原则：
- 不引入 middleware，也不依赖 LangChain callback；
- 只在 pipeline 的关键阶段记时间点和少量结构化字段；
- 写文件用锁，避免 Streamlit 并发时行交错；
- trace 是可选的运行产物，默认不参与回答逻辑。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

_WRITE_LOCK = threading.Lock()


def new_trace_id() -> str:
    """生成短 trace_id，便于在日志里检索一次问答。"""
    return uuid.uuid4().hex[:16]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class TraceRecorder:
    """记录一次问答的阶段耗时和关键字段。

    用法：
        trace = TraceRecorder(question, prompt_version)
        trace.mark("retrieval", 120.5)
        trace.set("n_sub_questions", 2)
        trace.finish(path)
    """

    def __init__(self, question: str, prompt_version: str = "v2",
                 session_id: str | None = None):
        self.trace_id = new_trace_id()
        self.started = time.perf_counter()
        self.data = {
            "trace_id": self.trace_id,
            "ts": _now_iso(),
            "question": question,
            "prompt_version": prompt_version,
            "session_id": session_id or "",
            "timings_ms": {},
        }

    def mark(self, stage: str, elapsed_ms: float) -> None:
        """记录一个阶段耗时（毫秒）。"""
        self.data["timings_ms"][stage] = round(float(elapsed_ms), 2)

    def set(self, key: str, value) -> None:
        """记录一个结构化字段；值必须是 JSON 可序列化的。"""
        self.data[key] = value

    def finish(self, path: str | Path | None = None, **extra) -> dict:
        """收尾：写总耗时、附加字段，并在给了 path 时落盘。"""
        self.data.update(extra)
        self.data["timings_ms"]["total"] = round(
            (time.perf_counter() - self.started) * 1000, 2)
        if path:
            try:
                write_trace(path, self.data)
            except Exception as exc:
                # 观测写盘失败不能反过来打断问答主流程
                self.data["trace_write_error"] = str(exc)
        return self.data


def write_trace(path: str | Path, record: dict) -> None:
    """追加写一行 JSONL；同进程内用锁避免并发写坏行。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with _WRITE_LOCK:
        with p.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def read_traces(path: str | Path, last: int = 0) -> list[dict]:
    """读 JSONL trace；last>0 时只返回最后 N 条。"""
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if last > 0:
        return rows[-last:]
    return rows
