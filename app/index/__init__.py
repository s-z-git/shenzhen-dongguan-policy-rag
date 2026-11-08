"""index 子包：embedder（向量化）+ store_faiss（VectorStore 协议实现）。"""
from .base import VectorStore
from .embedder import get_embeddings
from .store_faiss import FaissStore, build_index, load_index

__all__ = ["VectorStore", "get_embeddings", "FaissStore", "build_index", "load_index"]