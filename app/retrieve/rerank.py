"""
app.retrieve.rerank —— Cross-Encoder 精排

对应 DESIGN.md 的 app/retrieve/rerank.py 与 §4.4。

为什么需要两级：向量/BM25 是"双塔"式的粗排，query 和 doc 各自编码、不见面；
Cross-Encoder 把 (query, doc) 拼起来一起过一遍模型，能捕捉细粒度交互，
但代价是慢（不能预计算），所以只能对粗排后的 20 条做。
"""
from __future__ import annotations

from pathlib import Path

# 模块级缓存：避免每次调用都重新加载 CrossEncoder 模型（首次加载需数秒）
_CACHE: dict = {}


def rerank_available(model_name: str) -> bool:
    """模型名是本地路径时检查存在；HF 模型名默认可下载。"""
    if not model_name:
        return False
    p = Path(model_name)
    if p.is_absolute() or ("/" in model_name or "\\" in model_name):
        return p.exists()
    return True


def get_reranker(model_name: str, device: str = "cpu"):
    """按模型名缓存 CrossEncoder 实例，首次加载后后续调用直接复用。

    参数:
        model_name: HuggingFace 模型名或路径（如 "BAAI/bge-reranker-base"）
        device: 推理设备，"cpu" 或 "cuda"

    返回: CrossEncoder 实例，或不支持的模型名时返回 None
    """
    if not model_name:
        return None
    key = (model_name, device)
    if key not in _CACHE:
        from sentence_transformers import CrossEncoder
        # max_length=512：长文档截断为前 512 token，平衡精度与速度
        _CACHE[key] = CrossEncoder(model_name, device=device, max_length=512)
    return _CACHE[key]


def rerank(query: str, candidates: list[tuple[str, str]], model_name: str,
           device: str = "cpu", batch_size: int = 16) -> list[tuple[str, float]]:
    """对粗排候选集进行 Cross-Encoder 精排重打分。

    参数:
        query: 用户原始查询
        candidates: [(chunk_id, text)] 粗排阶段产出的候选列表
        model_name: 精排模型名
        device: 推理设备
        batch_size: 预测时的批大小，数值越大越快但越耗显存

    返回: 按精排分数降序排列的 [(chunk_id, score)] 列表
    """
    model = get_reranker(model_name, device)
    if model is None or not candidates:
        # 无模型或无候选时直接原样返回，分数置 0
        return [(cid, 0.0) for cid, _ in candidates]
    # 构造 (query, doc_text) 配对列表 —— Cross-Encoder 是一起编码的
    pairs = [(query, text) for _, text in candidates]
    # model.predict 返回相关性分数数组：越高越相关，下面按降序排列
    scores = model.predict(pairs, batch_size=batch_size)
    # zip 将 chunk_id 与分数一一对应，按分数降序排序
    ranked = sorted(zip([c[0] for c in candidates], [float(s) for s in scores]),
                    key=lambda kv: -kv[1])
    return ranked