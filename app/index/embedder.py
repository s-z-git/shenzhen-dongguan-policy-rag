"""
app.index.embedder —— BGE 向量化

对应 DESIGN.md 的 app/index/embedder.py。
默认 BAAI/bge-small-zh-v1.5（已缓存、CPU 编码）；bge-m3 作为消融项（W3）。
"""
from __future__ import annotations

from app.config import DEFAULT, Config

_CACHE: dict = {}   # (模型名, 设备) -> embedding 实例 的进程级缓存


def get_embeddings(cfg: Config | None = None):
    """按 (模型名, device) 缓存，避免同一进程重复加载模型。"""
    cfg = cfg or DEFAULT        # 缺省用默认配置
    # 以 (模型名, 设备) 作为缓存键
    key = (cfg.embed_model, cfg.embed_device)
    if key not in _CACHE:
        # 首次才真正加载 HuggingFace 嵌入模型
        from langchain_huggingface import HuggingFaceEmbeddings
        _CACHE[key] = HuggingFaceEmbeddings(
            model_name=cfg.embed_model,
            model_kwargs={"device": cfg.embed_device},          # 指定推理设备（cpu/gpu）
            encode_kwargs={"normalize_embeddings": True},       # 归一化，便于 L2 等价余弦
        )
    return _CACHE[key]