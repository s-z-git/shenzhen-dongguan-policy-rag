"""
app.retrieve.hybrid —— 混合检索主流程

对应 DESIGN.md 的 app/retrieve/hybrid.py 与「在线：问答」数据流：
    查询 -> 向量 top-20 + BM25 top-20 -> RRF 融合 -> 元数据过滤 -> 精排 -> top-5

为什么必须混合：向量擅长语义，但对"编号 / 金额 / 年份"这类字面量不敏感；
BM25 正好相反。两地政策问句里字面量极多（"第X条""2080元/月""2025年"），
所以单靠向量必漏 —— 消融实验里也能看到混合比单路多召回。

use_vector / use_bm25 / use_rerank 三个开关就是消融实验的旋钮。
"""
from __future__ import annotations

import re
from functools import lru_cache

from app.config import DEFAULT, Config
from app.retrieve.doc_select import dedupe_docs_newest
from app.retrieve.filter import filter_with_fallback
from app.retrieve.fusion import rrf_fuse
from app.retrieve.rerank import rerank, rerank_available


@lru_cache(maxsize=1)
def _jieba():
    """懒加载 jieba 分词库，避免在未真正使用时产生导入/建词典开销。
    maxsize=1 保证进程内只初始化一次。"""
    try:
        import jieba
        jieba.setLogLevel(60)   # 关闭 jieba 的建词典日志，减少噪声输出
        return jieba
    except Exception:
        return None             # 安装失败时返回 None，tokenize 会自动降级


def tokenize(text: str) -> list[str]:
    """中文切词：优先 jieba；装不上时退化为字符二元组（BM25 仍可用）。"""
    jb = _jieba()
    if jb is not None:
        # jieba 可用：精确模式切词，过滤掉纯空白 token
        return [t for t in jb.lcut(text) if t.strip()]
    # 降级方案：去掉所有空白后，按连续两个字符滑动切分
    # 例如 "生育津贴" -> ["生育", "育津", "津贴"]
    t = re.sub(r"\s+", "", text)
    n = len(t)
    return [t[i:i + 2] for i in range(n - 1)] if n > 1 else [t]


def cap_by_document(ids: list[str], by_id: dict, top_k: int, doc_cap: int = 0) -> list[str]:
    """按文档去重后再截断 top_k。

    doc_cap=0 表示不限制；doc_cap=1 表示每个 doc_id 最多保留 1 个 chunk。
    只按原始 doc_id 去重，不做跨 doc_id 的内容等价合并（那是评测层的事）。
    """
    if doc_cap <= 0:
        return list(ids[:top_k])
    out: list[str] = []
    counts: dict[str, int] = {}
    for cid in ids:
        d = by_id[cid]["doc_id"]
        if counts.get(d, 0) >= doc_cap:
            continue
        counts[d] = counts.get(d, 0) + 1
        out.append(cid)
        if len(out) >= top_k:
            break
    return out


class HybridRetriever:
    """一次初始化，多次查询。chunks 常驻内存（本规模约 4500 块，够用）。

    核心职责：组合向量检索与 BM25 检索，经 RRF 融合、元数据过滤、
    可选精排后返回 top-k 结果。所有索引都惰性加载以降低冷启动成本。
    """

    def __init__(self, chunks: list[dict], cfg: Config | None = None, store=None):
        """初始化混合检索器。

        参数:
            chunks: 全量文档块列表，每个块至少包含 chunk_id 和 text 字段
            cfg: 配置对象，未传则使用 DEFAULT
            store: 向量存储（FAISS）实例，允许外部注入以复用——测试和消融实验时
                   可避免每次重复加载嵌入模型
        """
        self.cfg = cfg or DEFAULT
        self.chunks = chunks
        self.by_id = {c["chunk_id"]: c for c in chunks}   # O(1) 查找，精排阶段用
        self._chunk_index = {c["chunk_id"]: i for i, c in enumerate(chunks)}
        self.by_doc: dict[str, list[dict]] = {}
        for c in chunks:
            self.by_doc.setdefault(c["doc_id"], []).append(c)
        self._store = store                                # 外部注入或惰性加载
        self._bm25 = None                                  # 首次使用时才建索引
        self._title_bm25 = None                            # 标题 BM25，按需构建
        self._title_docs: list[dict] = []

    @property
    def store(self):
        """惰性加载向量库：只有真正走向量检索时才付加载模型/索引成本。"""
        if self._store is None:
            from app.index.store_faiss import load_index
            self._store = load_index(self.cfg)
        return self._store

    def _ensure_bm25(self):
        """惰性构建 BM25 索引：对全语料分词、统计 IDF，约几秒，仅执行一次。"""
        if self._bm25 is None:
            from rank_bm25 import BM25Okapi
            # 每条 chunk 的 text 字段用 tokenize 分词后作为 BM25 的文档表示
            corpus = [tokenize(c["text"]) for c in self.chunks]
            self._bm25 = BM25Okapi(corpus)
        return self._bm25

    def _ensure_title_bm25(self):
        """构建标题 BM25；每个文档只保留一个代表块，索引很短。"""
        if self._title_bm25 is None:
            from rank_bm25 import BM25Okapi
            docs: dict[str, dict] = {}
            for c in self.chunks:
                docs.setdefault(c["doc_id"], c)
            self._title_docs = list(docs.values())
            corpus = [tokenize(str(c.get("doc_title") or "")) for c in self._title_docs]
            self._title_bm25 = BM25Okapi(corpus)
        return self._title_bm25

    def title_candidates(self, query: str, filters: dict | None = None,
                         limit: int = 2, min_score: float = 12.0) -> list[str]:
        """标题 BM25 高置信文档；同标题只保留最新年份。"""
        title_bm = self._ensure_title_bm25()
        scores = title_bm.get_scores(tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        filters = filters or {}
        candidates: list[tuple[float, dict]] = []
        for idx in order:
            score = float(scores[idx])
            if score < min_score:
                break
            doc = self._title_docs[idx]
            if filters.get("city_in") and doc.get("city") not in filters["city_in"]:
                continue
            year_gte = filters.get("year_gte")
            if year_gte and (doc.get("year") or 0) < int(year_gte):
                continue
            candidates.append((score, doc))
        if not candidates:
            return []
        # 标题分数接近时优先最新文件，避免 2013 旧办法压过 2023 现行办法。
        best_score = candidates[0][0]
        close = [x for x in candidates if x[0] >= best_score * 0.8]
        newest = max(close, key=lambda x: (x[1].get("year") or 0, x[0]))
        ordered = [newest] + [x for x in candidates if x[1]["doc_id"] != newest[1]["doc_id"]]
        doc_ids = [doc["doc_id"] for _, doc in ordered]
        return dedupe_docs_newest(doc_ids, self.by_doc)[:limit]

    def chunk_scores(self, query: str):
        """正文 BM25 对全量块的分数，用于同一文档内选块。"""
        return self._ensure_bm25().get_scores(tokenize(query))

    def vector_search(self, query: str, k: int) -> list[str]:
        """向量检索，返回按嵌入距离排序的 chunk_id 列表（距离计算在 store 内部完成）。"""
        return [cid for cid, _ in self.store.search(query, k)]

    def bm25_search(self, query: str, k: int) -> list[str]:
        """BM25 检索：get_scores 给全语料打分，取 top-k。
        过滤掉 score<=0 的块（完全不含查询词），避免把无关内容塞进融合。"""
        bm25 = self._ensure_bm25()
        # rank_bm25 返回 shape=(N,) 的 numpy 数组，每个元素是该文档的 BM25 分数
        scores = bm25.get_scores(tokenize(query))
        # 按分数降序取前 k 个下标
        order = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        # 只保留 score>0 的文档（至少包含一个查询词），并按下标映射到 chunk_id
        return [self.chunks[i]["chunk_id"] for i in order if scores[i] > 0]

    def retrieve(self, query: str, filters: dict | None = None,
                 use_vector: bool = True, use_bm25: bool = True,
                 use_rerank: bool | None = None, top_k: int | None = None,
                 keep_stages: bool = False) -> dict:
        """混合检索主流程：向量 + BM25 -> RRF 融合 -> 元数据过滤 -> 可选精排 -> top-k。

        参数:
            query: 用户原始查询字符串
            filters: 元数据过滤条件（如按城市、险种等字段筛选）
            use_vector: 是否启用向量检索（消融实验开关）
            use_bm25: 是否启用 BM25 检索（消融实验开关）
            use_rerank: 是否启用精排；None 时自动根据 cfg.rerank_model 是否配置决定
            top_k: 最终返回的文档数量；None 时使用 cfg.final_top_k
            keep_stages: 为 True 时返回每个阶段的中间结果，用于排查召回问题

        返回: dict，包含 final_ids、final（文档块列表）、各阶段 id 等
        """
        cfg = self.cfg
        rerank_ready = rerank_available(cfg.rerank_model)
        if use_rerank is None:
            use_rerank = rerank_ready                 # 模型路径不存在时自动关闭精排
        top_k = top_k or cfg.final_top_k
        stages: dict = {}                             # 仅在 keep_stages=True 时有意义

        # ── 第一阶段：多路独立检索 ──
        rank_lists: list[list[str]] = []              # 每路是一个按相关性排序的 id 列表
        if use_vector:
            stages["vector"] = self.vector_search(query, cfg.vector_top_k)
            rank_lists.append(stages["vector"])
        if use_bm25:
            stages["bm25"] = self.bm25_search(query, cfg.bm25_top_k)
            rank_lists.append(stages["bm25"])

        # ── 第二阶段：RRF 融合 ──
        # 只用排名计算分数，避免向量距离与 BM25 分值量纲不同的问题
        fused = rrf_fuse(rank_lists, cfg.rrf_k)
        cand_ids = [cid for cid, _ in fused]          # 按融合得分排序的候选 id 列表
        stages["fused"] = cand_ids

        # ── 第三阶段：元数据过滤 ──
        # 先过滤再精排：能少算几次 Cross-Encoder，也避免被非目标城市的条款挤占名额。
        # 过滤后不足 top_k 时保留城市、放宽年份补齐（见 filter_with_fallback）。
        cand_ids, relaxed_year = filter_with_fallback(cand_ids, self.chunks, filters, top_k)
        stages["filtered"] = cand_ids

        # ── 第四阶段：可选精排 ──
        # 仅在开启精排开关、有候选文档且模型已配置时才执行
        used_rerank = bool(use_rerank and cand_ids and rerank_ready)
        if used_rerank:
            cands = [(cid, self.by_id[cid]["text"]) for cid in cand_ids]      # 取出文本用于精排
            ranked = rerank(query, cands, cfg.rerank_model, cfg.embed_device)  # Cross-Encoder 重打分
            stages["rerank"] = [cid for cid, _ in ranked]
            final_ids = cap_by_document(stages["rerank"], self.by_id, top_k, cfg.doc_cap)
        else:
            final_ids = cap_by_document(cand_ids, self.by_id, top_k, cfg.doc_cap)

        # ── 组装返回结果 ──
        out = {
            "query": query,
            "filters": filters or {},
            "final_ids": final_ids,                              # 最终选中的 chunk_id 列表
            "final": [self.by_id[cid] for cid in final_ids],     # 对应的完整文档块
            "used_rerank": used_rerank,                          # 便于前端展示实际使用的策略
            "used_vector": use_vector,
            "used_bm25": use_bm25,
            "doc_cap": cfg.doc_cap,            # 0=不限；1=每个文档最多 1 个 chunk
            "relaxed_year": relaxed_year,      # 是否因候选不足放宽了年份
            "rerank_available": rerank_ready,  # 精排路径不可用时为 False
        }
        if keep_stages:
            out["stages"] = stages   # 调试/分析用：各阶段的中间状态
        return out