"""
app.index.base —— 向量库协议

对应 DESIGN.md §4.5「为什么先 FAISS 再换 Milvus」的落点：
上层 retrieve 只依赖这个协议，W3 换 Milvus 时上层不用改。
"""
from __future__ import annotations

from typing import Protocol


class VectorStore(Protocol):
    def add(self, chunk_ids: list[str], texts: list[str]) -> None:
        """写入向量。编码由实现方负责（上层只传文本和 id）。"""
        ...

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        """返回 [(chunk_id, 距离)]，按距离升序（越小越相似）。"""
        ...

    def save(self) -> None:
        """落盘。Milvus 这类服务端实现可以是空实现。"""
        ...