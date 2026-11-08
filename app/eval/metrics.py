"""
app.eval.metrics —— 评测指标

对应 DESIGN.md §4.9。

指标定义：
    Recall@K   golden 文档是否出现在最终 top-K（反映检索召回）
    MRR        golden 文档排名的倒数均值（反映排序质量）
    引用正确率  回答引用的文件命中 golden 的比例（反映生成质量）
    拒答准确率  边界型问题正确说"无法回答"的比例（反映幻觉控制）
    P95 延迟    端到端 95 分位耗时

【小样本必须报区间】
评测集只有几十条时，点估计会骗人。所以这里带 bootstrap 置信区间，
以及配对 bootstrap 差异检验 —— 差分区间跨 0 就说明"差异不显著"，
不能写成"A 比 B 好"。
"""
from __future__ import annotations


def golden_ids(item: dict) -> set[str]:
    """从评测条目中提取 golden 文档的 doc_id 集合。

    golden_docs 字段可能是 "doc_id" 或 "doc_id#clause" 格式；
    统一取 # 前的 doc_id 部分，做文档级别的匹配。
    """
    return {g.split("#")[0] for g in (item.get("golden_docs") or [])}


def _bootstrap_ci(values, n_boot: int = 2000, alpha: float = 0.05, seed: int = 0):
    """对一组值做 bootstrap 重采样，计算均值的置信区间。

    参数:
        values: 数值列表（如 0/1 命中标志或连续分数）
        n_boot: 重采样次数，默认 2000
        alpha: 显著性水平，默认 0.05 → 95% 置信区间
        seed: 随机种子，保证可复现

    返回: (lower, upper) 元组，精度到小数点后 3 位

    原理：从原始样本中有放回地抽取 n_boot 组同样大小的样本，
    计算每组样本的均值，取经验分布的 α/2 和 1-α/2 分位点作为区间。
    """
    import random
    n = len(values)
    if n == 0:
        return (0.0, 0.0)
    rnd = random.Random(seed)
    means = []
    for _ in range(n_boot):
        # 有放回抽样 n 次，计算该 bootstrap 样本的均值
        means.append(sum(values[rnd.randrange(n)] for _ in range(n)) / n)
    means.sort()
    # 取经验分布的 α/2 和 1-α/2 分位点，即 95% 置信区间
    return (round(means[max(int(alpha / 2 * n_boot), 0)], 3),
            round(means[min(int((1 - alpha / 2) * n_boot), n_boot - 1)], 3))


def paired_bootstrap_diff(a, b, n_boot: int = 2000, seed: int = 0) -> dict:
    """配对 bootstrap 差异检验：计算 d = a - b 的 95% 置信区间。

    参数:
        a, b: 两个等长列表，位置 i 对应同一条评测样本的两组结果
        n_boot: 重采样次数
        seed: 随机种子

    返回: {"diff": 均值差, "ci95": (lo, hi), "significant": bool}
          ci95 区间跨 0 → significant=False，说明差异不显著

    意义：消融实验不能只比点估计（如 0.82 vs 0.78），要看差分区间。
    如果区间包含 0，说明这点差异可能在噪声范围内，不宜声称 A 优于 B。
    """
    import random
    n = min(len(a), len(b))                              # 取较短列表长度，保证对齐
    if n == 0:
        return {"diff": 0.0, "ci95": (0.0, 0.0), "significant": False}
    d = [a[i] - b[i] for i in range(n)]                  # 逐样本差值
    rnd = random.Random(seed)
    means = []
    for _ in range(n_boot):
        means.append(sum(d[rnd.randrange(n)] for _ in range(n)) / n)
    means.sort()
    # 差分分布的 95% 区间：跨 0 就说明这点差异在噪声范围内
    lo = means[max(int(0.025 * n_boot), 0)]
    hi = means[min(int(0.975 * n_boot), n_boot - 1)]
    return {"diff": round(sum(d) / n, 3), "ci95": (round(lo, 3), round(hi, 3)),
            "significant": bool(lo > 0 or hi < 0)}       # 0 不在区间内才算显著


def doc_equivalence(chunks: list[dict]) -> dict:
    """同一份文件被两个站点各采一次（如省级办法在深圳/东莞各有一份）时，
    把它们映射到同一个 canonical doc_id，避免「内容对了但 doc_id 不同」被算成未命中。
    """
    import hashlib
    import re
    from collections import defaultdict

    by_doc: dict[str, list[str]] = defaultdict(list)
    for c in chunks:
        by_doc[c["doc_id"]].append(c.get("text") or "")
    groups: dict[str, list[str]] = defaultdict(list)
    for doc_id, texts in by_doc.items():
        key = hashlib.md5(re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", "".join(texts)).encode()).hexdigest()
        groups[key].append(doc_id)
    out: dict[str, str] = {}
    for ids in groups.values():
        canon = sorted(ids)[0]
        for d in ids:
            out[d] = canon
    return out


def _golden_coverage(rec: dict) -> float:
    """一个 golden 集合被覆盖了多少：|retrieved ∩ golden| / |golden|。

    「一问答两件事」的样本有 2 篇 golden，只看 hit（命中任意一篇）测不出差别，
    所以单独看覆盖率。
    """
    g = set(rec.get("golden") or [])
    if not g:
        return 0.0
    return len(set(rec.get("retrieved") or []) & g) / len(g)


def aggregate_retrieval(recs: list[dict]) -> dict:
    """汇总检索侧指标：Recall@K、MRR、golden 覆盖率、P95 延迟。

    注意：边界型问题没有 golden 文档，不能计入召回率/MRR（否则分母被污染）。
    """
    # 只统计有 golden 文档的 fact 类问题，过滤掉 boundary 类型
    scored = [r for r in recs if r.get("golden")]
    n = len(scored) or 1
    covs = [_golden_coverage(r) for r in scored]                                 # 防除零
    lat = sorted(r["latency_ms"] for r in recs)         # 所有问题的延迟（含 boundary）
    # 命中标志：1.0 表示至少检索到 1 篇 golden 文档
    hits = [1.0 if r["hit"] else 0.0 for r in scored]
    # Reciprocal Rank：1/rank，未命中时 rank=0 → RR=0
    rrs = [1.0 / r["rank"] if r["rank"] else 0.0 for r in scored]
    # clause-level：只统计提供了 golden_chunks 的题
    clause_rows = [r for r in recs if r.get("clause_golden")]
    cn = len(clause_rows) or 1
    clause_hits = [1.0 if r.get("clause_hit") else 0.0 for r in clause_rows]
    clause_rrs = [1.0 / r["clause_rank"] if r.get("clause_rank") else 0.0
                  for r in clause_rows]
    clause_covs = [float(r.get("clause_coverage") or 0.0) for r in clause_rows]
    return {
        "n_all": len(recs),                              # 总问题数（含 boundary）
        "n_scored": len(scored),                         # 计入召回/MRR 的问题数
        "recall_at_k": sum(hits) / n,                    # Recall@K 点估计
        "recall_ci95": _bootstrap_ci(hits),              # Recall@K 95% Bootstrap 置信区间
        "mrr": sum(rrs) / n,                             # MRR 点估计
        "mrr_ci95": _bootstrap_ci(rrs),                  # MRR 95% Bootstrap 置信区间
        "golden_coverage": round(sum(covs) / n, 4),
        "full_recall": round(sum(1 for c in covs if c >= 1.0) / n, 4),
        "clause_n": len(clause_rows),
        "clause_recall_at_k": round(sum(clause_hits) / cn, 4) if clause_rows else None,
        "clause_recall_ci95": _bootstrap_ci(clause_hits) if clause_rows else None,
        "clause_mrr": round(sum(clause_rrs) / cn, 4) if clause_rows else None,
        "clause_mrr_ci95": _bootstrap_ci(clause_rrs) if clause_rows else None,
        "clause_coverage": round(sum(clause_covs) / cn, 4) if clause_rows else None,
        "clause_full_recall": round(
            sum(1 for c in clause_covs if c >= 1.0) / cn, 4) if clause_rows else None,
        "p95_latency_ms": lat[min(int(0.95 * (len(lat) - 1)), len(lat) - 1)] if lat else 0.0,
        "mean_latency_ms": sum(lat) / len(lat) if lat else 0.0,
    }


def aggregate_generation(recs: list[dict]) -> dict:
    """汇总生成侧指标：引用命中率、引用精度、拒答准确率。

    口径说明：
    - 只有带 golden 的 fact 类进入引用指标，boundary 单独统计拒答
    - "精度"分母是模型实际引用的文档数，不包含未引用任何文档的回答
    """
    # fact 类且有 golden 标注的条目用于引用指标
    fact = [r for r in recs if r.get("golden") and r.get("type") != "boundary"]
    # boundary 类条目用于拒答准确率
    boundary = [r for r in recs if r.get("type") == "boundary"]
    # 有引用记录的 fact 条目（用于精度计算）
    with_cites = [r for r in fact if r.get("cited_docs")]
    return {
        # 引用命中率：至少引到一篇 golden 文档的比例
        "citation_hit_rate": (sum(1 for r in fact if set(r["cited_docs"]) & set(r["golden"]))
                              / (len(fact) or 1)),
        # 引用精度：引用的文档中命中 golden 的平均比例
        "citation_precision": (sum(len(set(r["cited_docs"]) & set(r["golden"])) / len(r["cited_docs"])
                                   for r in with_cites) / (len(with_cites) or 1)),
        # 拒答准确率：boundary 类中正确拒答的比例
        "refusal_accuracy": (sum(1 for r in boundary if r.get("refused")) / len(boundary))
                            if boundary else None,
        "n_boundary": len(boundary),
    }


def aggregate_grounding(recs: list[dict]) -> dict:
    """汇总生成后核验指标：引用可验证率、未匹配率、条款冲突率、数字可溯源率。

    口径（写进 README，便于复核）：
      - 只统计非 boundary 记录：边界型本来就该拒答，不该有引用和数字；
      - 比例用"池化" sum(verified)/sum(total)，不是各条比例的均值，
        否则"写 1 条且写对"和"写 5 条错 2 条"会同权；
      - 没有引用的回答单独计数，不进分母——否则"没写引用"会被当成"引用错误"。
    """
    rows = [r for r in recs if r.get("type") != "boundary" and r.get("grounding")]
    cites = [r["grounding"].get("citations") or {} for r in rows]      # 每条回答的引用核验
    nums = [r["grounding"].get("numbers") or {} for r in rows]         # 每条回答的数字核验
    c_total = sum(c.get("total") or 0 for c in cites)                  # 总引用数
    n_total = sum(n.get("total") or 0 for n in nums)                   # 总数字数
    cited_nums = [n for n in nums if n.get("basis", "cited") == "cited"]
    fallback_nums = [n for n in nums if n.get("basis") not in (None, "cited")]
    cited_total = sum(n.get("total") or 0 for n in cited_nums)
    fallback_total = sum(n.get("total") or 0 for n in fallback_nums)

    def rate(num, den):
        """安全除法：分母为 0 时返回 None 而非报错。"""
        return round(num / den, 4) if den else None

    return {
        "n_answers_checked": len(rows),                                     # 核验的回答数
        "n_answers_without_citation": sum(1 for c in cites if not c.get("total")),  # 没有任何引用的回答
        "n_citations": c_total,                                             # 总引用条数
        "citation_verify_rate": rate(sum(c.get("verified") or 0 for c in cites), c_total),
        "citation_unmatched_rate": rate(sum(c.get("unmatched") or 0 for c in cites), c_total),
        # 文件级匹配：只要文件名能在本轮检索结果里找到；条款号缺失不算错
        "citation_file_match_rate": rate(
            c_total - sum(c.get("unmatched") or 0 for c in cites), c_total),
        # 只写文件名、没写条款号的比例；全文块本来没有条款号，属正常情况
        "citation_no_clause_rate": rate(sum(c.get("no_clause") or 0 for c in cites), c_total),
        "citation_clause_conflict_rate": rate(
            sum(c.get("clause_conflict") or 0 for c in cites), c_total),    # 条款号与实际不匹配
        "n_numbers": n_total,                                               # 总数字条数
        # 主口径只统计能落到被引条款的数字；无引用回退单独报，避免高估。
        "number_grounded_rate": rate(
            sum(n.get("grounded") or 0 for n in cited_nums), cited_total),
        "number_grounded_rate_all": rate(
            sum(n.get("grounded") or 0 for n in nums), n_total),
        "number_retrieved_fallback": fallback_total,
        "number_grounded_rate_retrieved_fallback": rate(
            sum(n.get("grounded") or 0 for n in fallback_nums), fallback_total),
    }