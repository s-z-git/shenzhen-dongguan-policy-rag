"""
app.retrieve.fusion —— RRF 融合

对应 DESIGN.md 的 app/retrieve/fusion.py 与 §4.3。

score(d) = sum_i 1 / (k + rank_i(d))
为什么不用加权分数相加：两路分值量纲不同（L2 距离 vs BM25 分数），
归一化方式本身会引入调参；RRF 只用排名，无需调参、更稳健。
k=60 是论文（Cormack et al., 2009）的默认值。
"""
from __future__ import annotations


def rrf_fuse(rank_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """RRF：score(d) = sum 1/(k+rank)。只用排名，避免两路分值量纲不同带来的归一化调参。
    k 越大越平滑（默认 60，来自 Cormack et al. 2009）。

    参数:
        rank_lists: 多路检索结果，每个元素是一个按相关性从高到低排列的文档 ID 列表
        k: 平滑常数，默认 60；k 越大，各名次间的分数差异越平缓

    返回:
        按融合得分降序排列的 (文档 ID, 融合得分) 列表
    """
    # 用字典累积每个文档的融合得分
    score: dict[str, float] = {}
    for rl in rank_lists:
        # enumerate 从 1 开始计数，使第一名 rank=1（而非 0），符合 RRF 公式
        for rank, cid in enumerate(rl, start=1):
            # 每一路只贡献 1/(k+rank)：排名越靠前贡献越大，且与分值量纲无关
            # 同一文档在多个列表中都会按各自排名累加各自的分值
            score[cid] = score.get(cid, 0.0) + 1.0 / (k + rank)
    # 按得分从高到低排序，返回 (文档ID, 得分) 元组列表
    return sorted(score.items(), key=lambda kv: -kv[1])