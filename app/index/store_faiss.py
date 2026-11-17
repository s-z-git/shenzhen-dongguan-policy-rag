"""
app.index.store_faiss —— FAISS 实现

对应 DESIGN.md 的 app/index/store_faiss.py。

两个实现细节：
1. 向量已归一化，所以 FAISS 用 L2 距离等价于余弦相似度 —— 距离越小越相似。
2. 向量库里只存 chunk_id，真正的元数据留在 chunks.jsonl。
   本规模（约 4500 块）元数据过滤在内存做足够快；换 Milvus 时下推成标量过滤。
3. FAISS 的 C++ 文件 API 用窄字符路径，**不能写含中文的目录**，
   所以索引目录由 config.INDEX_DIR 指定到纯 ASCII 路径。
"""
from __future__ import annotations

from typing import Any

from app.config import DEFAULT, Config
from app.index.embedder import get_embeddings


class FaissStore:
    def __init__(self, cfg: Config | None = None, store: Any = None):
        """store 为 None 表示新建；传入已有 FAISS 实例表示复用（load 时用）。"""
        self.cfg = cfg or DEFAULT
        self.store: Any = store  # 底层 FAISS 向量库（langchain 封装），可空

    def add(self, chunk_ids: list[str], texts: list[str]) -> None:
        """把 chunk 写进向量库。
        只写 chunk_id 当 metadata —— 真正的元数据留在 chunks.jsonl，避免同一份数据存两处。"""
        # 延迟导入，避免冷启动阶段就引入重量级依赖
        from langchain_community.vectorstores import FAISS
        from langchain_core.documents import Document
        # 把每段文本封装成 Document，metadata 里只带 chunk_id
        docs = [Document(page_content=t, metadata={"chunk_id": cid})
                for cid, t in zip(chunk_ids, texts)]
        if self.store is None:
            # 首次：从文档集合新建索引
            self.store = FAISS.from_documents(docs, get_embeddings(self.cfg))
        else:
            # 已有实例：增量追加文档
            self.store.add_documents(docs)

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        """返回 [(chunk_id, 距离)]，按距离升序（越小越相似）。
        向量已归一化，所以 L2 距离与余弦相似度单调等价。"""
        # langchain 返回 (Document, 距离) 列表，且已按距离升序排好
        pairs = self.store.similarity_search_with_score(query, k=k)
        # 抽取出 chunk_id（metadata）与相似度分数
        return [(d.metadata["chunk_id"], float(s)) for d, s in pairs]

    def save(self) -> None:
        """落盘到 cfg.index_dir。该路径必须是纯 ASCII，原因见 config.INDEX_DIR 注释。"""
        self.cfg.index_dir.mkdir(parents=True, exist_ok=True)   # 确保目录存在
        self.store.save_local(str(self.cfg.index_dir))          # FAISS 本地文件落盘

    @classmethod
    def load(cls, cfg: Config | None = None) -> "FaissStore":
        """从磁盘恢复 FAISS 实例（类方法，因为它要返回构造好的对象）。"""
        from langchain_community.vectorstores import FAISS
        cfg = cfg or DEFAULT
        # load_local 需要一致的 embedding 才能正确比对向量维度
        store = FAISS.load_local(str(cfg.index_dir), get_embeddings(cfg),
                                 allow_dangerous_deserialization=True)
        return cls(cfg, store)


def build_index(chunks: list[dict], cfg: Config | None = None, verbose: bool = True) -> FaissStore:
    """把整个语料编码并建索引，然后落盘。
    编码设备由 cfg.embed_device 决定（默认 cpu：本机 16GB 内存是瓶颈，8GB 显存留给精排更划算）。"""
    cfg = cfg or DEFAULT
    cfg.ensure_dirs()                                   # 确保所需目录就绪
    vs = FaissStore(cfg)
    # 一次性提交全部 chunk 的 id 与文本
    vs.add([c["chunk_id"] for c in chunks], [c["text"] for c in chunks])
    vs.save()                                           # 建好即落盘
    if verbose:
        print("[index] " + cfg.embed_model + " 编码 " + str(len(chunks))
              + " 块 -> " + str(cfg.index_dir))
    return vs


def load_index(cfg: Config | None = None) -> FaissStore:
    """加载已保存的索引；不存在会抛异常，请先跑 build_index.py。"""
    return FaissStore.load(cfg)