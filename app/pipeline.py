"""
app.pipeline —— 端到端编排

把四个模块串起来，对外只暴露三个动作：
    pipe = RAGPipeline.build()     # 离线：解析 458 份 -> 条款块 -> FAISS 索引
    pipe = RAGPipeline.load()      # 在线：加载已有索引
    r = pipe.ask("东莞本科应届生入户后能领哪些补贴")

这样 CLI、Streamlit、评测脚本用的是同一条链路 —— 不会出现"UI 里能跑、评测跑不通"。
"""
from __future__ import annotations

import re
import time

from app.config import DEFAULT, Config
from app.generate import (analyze_query, answer, check_grounding, filters_from_analysis,
                          rewrite_query, staleness_notes, stream_answer)
from app.ingest import build_corpus, citation, load_chunks, save_chunks
from app.index import build_index, load_index
from app.retrieve import HybridRetriever, cap_by_document, rrf_fuse, tokenize
from app.retrieve.doc_select import dedupe_docs_newest, pick_section_chunk
from app.retrieve.rerank import rerank, rerank_available
from app.observability import TraceRecorder

NL = chr(10)   # 换行符常量


def longest_common_substring(a: str, b: str) -> int:
    """最长公共子串长度；用来识别“同一政策的追问”。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def policy_name(title: str) -> str:
    """从正式标题里提取《政策名》，去掉发文机关等长前缀。"""
    matches = re.findall(r"《([^》]+)》", title or "")
    name = max(matches, key=len) if matches else (title or "")
    name = re.sub(r"政策(解读|咨询)$", "", name)
    return re.sub(r"[\s《》]", "", name)


def is_eligibility_question(question: str) -> bool:
    """是否在问“能不能申请/是否符合条件”。"""
    words = ["能不能申请", "能申请", "可以申请", "能否申请", "是否符合条件", "符合条件吗"]
    return any(word in (question or "") for word in words)


def titles_are_distinct(titles: list[str], threshold: int = 6) -> bool:
    """子问题命中的政策名是否不同；同一政策的不同段落不启用文档级选块。"""
    names = [policy_name(t) for t in titles]
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if longest_common_substring(names[i], names[j]) >= threshold:
                return False
    return True


def subs_are_distinct(subs: list[str], threshold: float = 0.45) -> bool:
    """子问题主题是否足够不同；高度重合或同一政策追问时不启用文档级选块。"""
    token_sets = [set(tokenize(s)) for s in subs]
    for i in range(len(token_sets)):
        for j in range(i + 1, len(token_sets)):
            union = token_sets[i] | token_sets[j]
            if not union:
                return False
            overlap = len(token_sets[i] & token_sets[j]) / len(union)
            if overlap >= threshold:
                return False
            if longest_common_substring(subs[i], subs[j]) >= 4:
                return False
    return True


def dedupe_sub_questions(question: str, subs: list[str] | None, limit: int = 3) -> list[str]:
    """子问题清洗：去空、去重、去掉与原问题相同的，最多保留 limit 条（保持原顺序）。"""
    out: list[str] = []
    q = (question or "").strip()
    for s in subs or []:
        s = str(s or "").strip()
        if not s or s == q or s in out:
            continue
        out.append(s)
        if len(out) >= limit:
            break
    return out


def ensure_sub_question_coverage(final_ids: list[str], must_ids: list[str],
                                 top_k: int) -> list[str]:
    """保证每个子问题的头部块进入 final；替换非 must 槽位，避免互相覆盖。"""
    must_set = set(must_ids)
    for must in must_ids:
        if must in final_ids:
            continue
        if len(final_ids) < top_k:
            final_ids.append(must)
            continue
        for idx, cid in enumerate(final_ids):
            if cid not in must_set:
                final_ids[idx] = must
                break
    return final_ids


class RAGPipeline:
    """端到端 RAG 管线：编排 ingest → index → retrieve → generate 四大模块。

    只对外暴露三个操作：
    - build():  离线构建全量索引（解析 PDF + 嵌入编码 + FAISS 建库）
    - load():   在线加载已有索引（秒级启动，跳过解析/编码）
    - ask():    执行一次完整问答（查询分析 → 检索 → 生成 → 核验）
    """

    def __init__(self, chunks: list[dict] | None = None,
                 retriever: HybridRetriever | None = None,
                 cfg: Config | None = None):
        """chunks / retriever 为 None 表示尚未 build 或 load（调用 ask 会直接报错，避免误用）。"""
        self.cfg = cfg or DEFAULT
        self.chunks = chunks
        self.retriever = retriever

    # ---------------- 构建 / 加载 ----------------
    @classmethod
    def build(cls, cfg: Config | None = None, verbose: bool = True) -> "RAGPipeline":
        """离线构建：解析 PDF → 条款切分 → 向量编码 → FAISS 索引 → 落盘。

        完整流程约 2-3 分钟（CPU 编码约 4500 块），之后用 load() 秒级启动。
        """
        cfg = cfg or DEFAULT
        cfg.ensure_dirs()                                               # 确保数据目录存在
        # ① 文档摄取：解析 PDF → 提取条款文本 → 生成 chunk 列表
        chunks, docs = build_corpus(cfg, verbose=verbose)
        # ② 持久化：chunks 存为 JSONL，方便 load() 时直接读取
        save_chunks(chunks, cfg.chunks_path)
        # ③ 建向量索引：编码嵌入 + FAISS 索引构建
        store = build_index(chunks, cfg, verbose=verbose)
        # 将建好的索引注入 retriever 的 store 属性，避免 load 时重复编码
        return cls(chunks=chunks, retriever=HybridRetriever(chunks, cfg, store=store), cfg=cfg)

    @classmethod
    def load(cls, cfg: Config | None = None) -> "RAGPipeline":
        """在线加载：读 chunks.jsonl + FAISS 索引，秒级完成无需编码。"""
        cfg = cfg or DEFAULT
        chunks = load_chunks(cfg.chunks_path)                           # 从磁盘读取 chunks
        store = load_index(cfg)                                         # 加载 FAISS 索引（只读内存映射）
        return cls(chunks=chunks, retriever=HybridRetriever(chunks, cfg, store=store), cfg=cfg)

    def warmup(self, top_k: int = 2) -> dict:
        """预加载 BM25 与精排模型，降低首问延迟；失败不阻断主流程。"""
        if self.retriever is None:
            raise RuntimeError("检索器未初始化")
        return self.retriever.retrieve(
            "政策补贴", use_rerank=rerank_available(self.cfg.rerank_model), top_k=top_k)

    # ---------------- 问答 ----------------
    def _retrieve_multi(self, question: str, subs: list[str] | None, filters: dict,
                        use_rerank: bool | None, top_k: int | None,
                        use_doc_select: bool | None = None) -> dict:
        """多子问题检索：各自粗排 → RRF 合并 → 用原始问题统一精排一次。

        为什么要合并后再精排：子问题只负责「扩大召回面」；
        「哪条最能回答用户原本的问题」仍应由原问题打分决定，所以精排的 query 用 question。
        """
        cfg = self.cfg
        top_k = top_k or cfg.final_top_k
        uniq = dedupe_sub_questions(question, subs)
        if not uniq:                      # 没拆出子问题（或子问题就是原问题）→ 走单查询
            res = self.retriever.retrieve(question, filters=filters, use_rerank=use_rerank,
                                          top_k=top_k, keep_stages=True)
            res["n_sub_questions"] = 1
            return res

        rerank_ready = rerank_available(cfg.rerank_model)
        do_rerank = rerank_ready if use_rerank is None else bool(use_rerank) and rerank_ready
        want_doc_select = cfg.multi_doc_select if use_doc_select is None else use_doc_select
        # 原问题直检作为锚点：子问题只负责扩大召回，不能把原问题直接命中的文档挤掉。
        base = self.retriever.retrieve(question, filters=filters, use_rerank=False,
                                       top_k=cfg.vector_top_k, keep_stages=True)
        base_ids = base["final_ids"]
        rank_lists, per_sub = [], {}
        top_titles: list[str] = []
        relaxed = bool(base.get("relaxed_year"))
        for s in uniq:
            # 子问题只做粗排（不精排），避免 N 次 Cross-Encoder 推理
            r = self.retriever.retrieve(s, filters=filters, use_rerank=False,
                                        top_k=cfg.vector_top_k, keep_stages=True)
            per_sub[s] = r["final_ids"]
            rank_lists.append(r["final_ids"])
            relaxed = relaxed or bool(r.get("relaxed_year"))
            if r["final_ids"]:
                top_doc = self.retriever.by_id[r["final_ids"][0]]["doc_id"]
                top_titles.append(self.retriever.by_doc[top_doc][0].get("doc_title") or "")

        # 原问题直检作为额外一路参与 RRF：子问题负责扩召回，原问题负责保底相关性。
        rank_lists.insert(0, base_ids)

        if (want_doc_select and do_rerank
                and question.count("？") + question.count("?") >= 2
                and len(uniq) >= 2 and subs_are_distinct(uniq)
                and titles_are_distinct(top_titles)):
            return self._retrieve_multi_doc_select(question, uniq, filters, top_k)

        merged = [cid for cid, _ in rrf_fuse(rank_lists, cfg.rrf_k)]
        merged = merged[:cfg.vector_top_k * 2]      # 最多 40 条，控制精排耗时

        used_rerank = bool(do_rerank and merged)
        stages = {"sub": per_sub, "base": base_ids, "merged": merged}
        if used_rerank:
            cands = [(cid, self.retriever.by_id[cid]["text"]) for cid in merged]
            ranked = rerank(question, cands, cfg.rerank_model, cfg.embed_device)
            stages["rerank"] = [cid for cid, _ in ranked]
            candidate_ids = stages["rerank"]
        else:
            candidate_ids = merged

        # 先按 doc_cap 选最终 top-k，再补齐“每个子问题至少 1 条”。
        # 顺序不能反：否则保底替换会重新引入同一文档的多个 chunk，doc_cap 失效。
        final_ids = cap_by_document(candidate_ids, self.retriever.by_id, top_k, cfg.doc_cap)
        # 子问题保底只用于“精排后”的路径：不精排时 RRF 合并本身已经保留了各子问题的头部结果
        if used_rerank and top_k >= len(uniq):
            must_ids: list[str] = []
            # 默认 top-k 足够时，优先保留原问题直检的第一条。
            if base_ids and top_k > len(uniq):
                must_ids.append(base_ids[0])
            for ids in rank_lists:
                if not ids:
                    continue
                must = ids[0]
                if cfg.doc_cap > 0:
                    must_doc = self.retriever.by_id[must]["doc_id"]
                    if any(self.retriever.by_id[x]["doc_id"] == must_doc for x in final_ids):
                        continue
                if must not in must_ids:
                    must_ids.append(must)
            final_ids = ensure_sub_question_coverage(final_ids, must_ids, top_k)

        # 资格类问题补一个“补贴对象/补贴条件”主文块，避免只召回申请材料或补贴标准。
        if is_eligibility_question(question) and final_ids:
            scores = self.retriever.chunk_scores(question)
            for cid in list(final_ids):
                did = self.retriever.by_id[cid]["doc_id"]
                doc_chunks = self.retriever.by_doc[did]
                if not any(("补贴对象" in c["text"] or "补贴条件" in c["text"])
                           for c in doc_chunks):
                    continue
                extra = pick_section_chunk(question, doc_chunks, scores,
                                           self.retriever._chunk_index)
                extra_id = extra["chunk_id"]
                if extra_id not in final_ids:
                    for i, old_id in enumerate(final_ids):
                        if self.retriever.by_id[old_id]["doc_id"] == did:
                            final_ids[i] = extra_id
                            break
                    break

        return {
            "query": question,
            "filters": filters or {},
            "final_ids": final_ids,
            "final": [self.retriever.by_id[cid] for cid in final_ids],
            "used_rerank": used_rerank,
            "used_vector": True,
            "used_bm25": True,
            "doc_cap": cfg.doc_cap,
            "relaxed_year": relaxed,
            "n_sub_questions": len(uniq),
            "stages": stages,
        }

    def _retrieve_multi_doc_select(self, question: str, subs: list[str], filters: dict,
                                   top_k: int) -> dict:
        """多子问题检索：文档级去重 + 按问题类型选主文块。

        每个子问题取正文 top3 文档和标题 BM25 top2 文档，合并后按
        “对象/条件/标准/期限/项目/险种”选块；最后用原问题统一精排，
        并保证每个子问题至少有一个高置信块进入上下文。
        """
        cfg = self.cfg
        target_k = top_k + 1      # 多主题模式多给 1 条证据，避免两个子问题互相挤掉。
        selected_ids: list[str] = []
        priority_ids: list[str] = []
        per_sub: dict[str, dict] = {}
        relaxed = False
        for sub in subs:
            r = self.retriever.retrieve(sub, filters=filters, use_rerank=False,
                                        top_k=cfg.vector_top_k, keep_stages=True)
            relaxed = relaxed or bool(r.get("relaxed_year"))
            body_order: list[str] = []
            for cid in r["final_ids"]:
                did = self.retriever.by_id[cid]["doc_id"]
                if did not in body_order:
                    body_order.append(did)
            body_docs = dedupe_docs_newest(body_order, self.retriever.by_doc)[:3]
            title_docs = self.retriever.title_candidates(sub, filters=filters,
                                                         limit=2, min_score=12.0)
            docs: list[str] = []
            for did in body_docs + title_docs:
                if did not in docs:
                    docs.append(did)
            body_scores = self.retriever.chunk_scores(sub)
            chunk_by_doc: dict[str, dict] = {}
            for did in docs:
                chunk = pick_section_chunk(sub, self.retriever.by_doc[did], body_scores,
                                           self.retriever._chunk_index)
                chunk_by_doc[did] = chunk
                cid = chunk["chunk_id"]
                if cid not in selected_ids:
                    selected_ids.append(cid)
            for did in body_docs[:1] + title_docs[:1] + body_docs[1:3]:
                chunk = chunk_by_doc.get(did)
                if chunk and chunk["chunk_id"] not in priority_ids:
                    priority_ids.append(chunk["chunk_id"])
            per_sub[sub] = {"body_docs": body_docs, "title_docs": title_docs}

        base = self.retriever.retrieve(question, filters=filters, use_rerank=True,
                                       top_k=top_k, keep_stages=True)
        relaxed = relaxed or bool(base.get("relaxed_year"))
        if base["final_ids"]:
            base_top = base["final_ids"][0]
            if base_top not in priority_ids:
                priority_ids.insert(0, base_top)
        candidates: list[str] = []
        for cid in selected_ids + base["final_ids"]:
            if cid not in candidates:
                candidates.append(cid)
        ranked = rerank(question,
                        [(cid, self.retriever.by_id[cid]["text"]) for cid in candidates],
                        cfg.rerank_model, cfg.embed_device)
        final_ids: list[str] = []
        seen_docs: set[str] = set()
        for cid in priority_ids:
            did = self.retriever.by_id[cid]["doc_id"]
            if cid in final_ids or did in seen_docs:
                continue
            final_ids.append(cid)
            seen_docs.add(did)
            if len(final_ids) >= target_k:
                break
        for cid, _ in ranked:
            if len(final_ids) >= target_k:
                break
            did = self.retriever.by_id[cid]["doc_id"]
            if cid in final_ids or did in seen_docs:
                continue
            final_ids.append(cid)
            seen_docs.add(did)
        if len(final_ids) < target_k:
            for cid in base["final_ids"]:
                if len(final_ids) >= target_k:
                    break
                if cid not in final_ids:
                    final_ids.append(cid)
        return {
            "query": question,
            "filters": filters or {},
            "final_ids": final_ids,
            "final": [self.retriever.by_id[cid] for cid in final_ids],
            "used_rerank": True,
            "used_vector": True,
            "used_bm25": True,
            "doc_cap": cfg.doc_cap,
            "relaxed_year": relaxed,
            "n_sub_questions": len(subs),
            "stages": {"sub": per_sub, "base": base["final_ids"], "selected": selected_ids,
                       "priority": priority_ids, "rerank": [cid for cid, _ in ranked]},
        }

    def _retrieve(self, question: str, use_rerank: bool | None, top_k: int | None,
                  use_llm_analysis: bool, use_sub_questions: bool,
                  use_doc_select: bool | None, history: list[dict] | None,
                  trace: TraceRecorder) -> tuple[str, dict, dict, dict]:
        """ask_stream 的检索前置：查询改写 → 分析 → 混合检索，不触碰 ask()。"""
        if self.retriever is None:
            raise RuntimeError("检索器未初始化：请用 RAGPipeline.build() 或 RAGPipeline.load() 构造实例")
        search_question = rewrite_query(question, history, self.cfg) if history else question
        trace.set("search_question", search_question)
        trace.set("used_history", bool(history))

        t0 = time.perf_counter()
        analysis = analyze_query(search_question, self.cfg, use_llm=use_llm_analysis)
        filters = filters_from_analysis(analysis)
        trace.set("sub_questions", analysis.get("sub_questions") or [])
        trace.mark("analysis", (time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        if use_sub_questions:
            res = self._retrieve_multi(search_question, analysis.get("sub_questions"), filters,
                                       use_rerank, top_k, use_doc_select)
        else:
            res = self.retriever.retrieve(search_question, filters=filters,
                                          use_rerank=use_rerank, top_k=top_k,
                                          keep_stages=True)
            res["n_sub_questions"] = 1
        trace.mark("retrieval", (time.perf_counter() - t0) * 1000)
        return search_question, analysis, filters, res

    def ask(self, question: str, use_rerank: bool | None = None, top_k: int | None = None,
            use_llm_analysis: bool = True, use_sub_questions: bool = True,
            use_doc_select: bool | None = None,
            history: list[dict] | None = None, session_id: str | None = None,
            trace_path=None) -> dict:
        """一次完整问答：查询分析 → 检索 → 生成 → 核验 → 组装返回。

        参数:
            question:        用户原始问题
            use_rerank:      是否启用精排，None 时自动根据配置决定
            top_k:           最终返回文档数，None 时使用配置默认值
            use_llm_analysis: 是否用 LLM 做查询分析（结构化过滤 + 子问题拆分）
            use_sub_questions: 是否把拆出的子问题分别检索再合并（关闭则只用主查询）
            history:          最近对话消息；有值时先做 query rewrite
            session_id:       会话 id，仅用于 trace/多轮
            trace_path:       覆盖默认 trace 路径；None 时用 cfg.trace_path

        返回: dict，包含 answer、citations、notes、grounding、
              chunks、sources、stages 等完整字段
        """
        trace = TraceRecorder(question, self.cfg.answer_prompt_version, session_id)
        # 同步与流式共用同一份检索编排，避免两条入口未来分叉。
        search_question, an, filters, res = self._retrieve(
            question, use_rerank, top_k, use_llm_analysis, use_sub_questions,
            use_doc_select, history, trace,
        )

        # ── 第三步：生成回答 ──
        # 将 top-k 条款拼为上下文，调用 DeepSeek 生成带出处的回答
        t0 = time.perf_counter()
        out = answer(search_question, res["final"], self.cfg)
        trace.mark("generation", (time.perf_counter() - t0) * 1000)
        # 年份限定过严导致放宽时，必须告诉用户：回答里可能混入更早年份的文件
        if res.get("relaxed_year"):
            out.setdefault("notes", []).append(
                "符合你所提年份条件的条款不足 " + str(top_k or self.cfg.final_top_k)
                + " 条，以下回答可能包含更早年份的文件，请注意时效。")
        # ── 第四步：生成后核验 ──
        # 检查回答中引用的文件/条款号是否真实存在、数字是否与原文一致
        t0 = time.perf_counter()
        grounding = check_grounding(out.get("answer", ""), res["final"], search_question)
        trace.mark("grounding", (time.perf_counter() - t0) * 1000)
        # ── 组装完整返回 ──
        # 把检索中间态一并返回：CLI 排查、UI 展示、评测取 id 都用得上
        out.update({
            "grounding": grounding,                              # 出处核验结果（用于评估幻觉率）
            "question": question,
            "search_question": search_question,                 # 多轮改写后的检索问题
            "used_history": bool(history),
            "trace_id": trace.trace_id,
            "analysis": an,                                     # 查询分析的结构化输出
            "filters": filters,                                 # 实际使用的检索过滤条件
            "chunks": res["final"],                             # 最终传给 LLM 的条款块列表
            "sources": [citation(c) for c in res["final"]],     # 人类可读的出处描述
            "used_rerank": res["used_rerank"],                  # 是否实际调用了精排
            "n_sub_questions": res.get("n_sub_questions", 1),   # 实际参与检索的子问题数
            "doc_cap": res.get("doc_cap", self.cfg.doc_cap),      # 0=不限；1=每文档最多 1 chunk
            "relaxed_year": bool(res.get("relaxed_year")),      # 是否因候选不足放宽了年份
            "stages": res.get("stages", {}),                    # 检索各阶段的 id 列表（调试用）
        })
        # trace 只存摘要字段，不把完整 grounding items 写进去，避免日志过大
        ci = grounding.get("citations") or {}
        nu = grounding.get("numbers") or {}
        trace.set("filters", filters)
        trace.set("n_sub_questions", out.get("n_sub_questions", 1))
        trace.set("used_rerank", bool(out.get("used_rerank")))
        trace.set("doc_cap", out.get("doc_cap", self.cfg.doc_cap))
        trace.set("retrieved_chunks", [c["chunk_id"] for c in res["final"]])
        trace.set("retrieved_docs", [c["doc_id"] for c in res["final"]])
        trace.set("answer_chars", len(out.get("answer") or ""))
        trace.set("refused", bool(out.get("refused")))
        trace.set("citations_total", ci.get("total", 0))
        trace.set("citations_verified", ci.get("verified", 0))
        trace.set("citations_no_clause", ci.get("no_clause", 0))
        trace.set("citations_conflict", ci.get("clause_conflict", 0))
        trace.set("numbers_total", nu.get("total", 0))
        trace.set("numbers_grounded", nu.get("grounded", 0))
        trace.set("usage", out.get("usage") or {})
        if self.cfg.trace_enabled:
            trace.finish(trace_path or self.cfg.trace_path)
        return out

    def ask_stream(self, question: str, use_rerank: bool | None = None,
                   top_k: int | None = None, use_llm_analysis: bool = True,
                   use_sub_questions: bool = True, use_doc_select: bool | None = None,
                   history: list[dict] | None = None, session_id: str | None = None,
                   trace_path=None, sink: dict | None = None):
        """流式问答：检索同步完成，返回 token 迭代器；完整结果写入 sink["result"]。"""
        sink = sink if sink is not None else {}
        started = time.perf_counter()
        trace = TraceRecorder(question, self.cfg.answer_prompt_version, session_id)

        try:
            search_question, analysis, filters, res = self._retrieve(
                question, use_rerank, top_k, use_llm_analysis, use_sub_questions,
                use_doc_select, history, trace,
            )
        except Exception as exc:
            trace.set("error", str(exc))
            if self.cfg.trace_enabled:
                trace.finish(trace_path or self.cfg.trace_path)
            result = {
                "answer": "调用失败：" + str(exc), "citations": [], "notes": [],
                "refused": False, "chunks": [], "grounding": {}, "analysis": {},
                "question": question, "search_question": question, "trace_id": trace.trace_id,
                "error": str(exc), "elapsed_ms": (time.perf_counter() - started) * 1000,
            }
            sink.update({"result": result, "error": str(exc), "trace_id": trace.trace_id})
            return iter(())

        chunks = res["final"]
        notes = staleness_notes(chunks, self.cfg)
        if res.get("relaxed_year"):
            notes.append(
                "符合你所提年份条件的条款不足 " + str(top_k or self.cfg.final_top_k)
                + " 条，以下回答可能包含更早年份的文件，请注意时效。")
        sink.update({
            "question": question,
            "search_question": search_question,
            "used_history": bool(history),
            "trace_id": trace.trace_id,
            "analysis": analysis,
            "filters": filters,
            "chunks": chunks,
            "sources": [citation(c) for c in chunks],
            "used_rerank": res["used_rerank"],
            "n_sub_questions": res.get("n_sub_questions", 1),
            "doc_cap": res.get("doc_cap", self.cfg.doc_cap),
            "relaxed_year": bool(res.get("relaxed_year")),
            "stages": res.get("stages", {}),
            "notes": notes,
            "will_refuse": not chunks,
            "first_token_ms": None,
            "ttft_ms": None,
        })

        def finish_result() -> dict:
            """流结束后统一核验、组装返回并写 trace。"""
            text = sink.get("answer") or ""
            t0 = time.perf_counter()
            grounding = check_grounding(text, chunks, search_question)
            trace.mark("grounding", (time.perf_counter() - t0) * 1000)
            elapsed_ms = (time.perf_counter() - started) * 1000
            ci = grounding.get("citations") or {}
            nu = grounding.get("numbers") or {}
            result = {
                "answer": text,
                "citations": sink.get("citations") or [],
                "notes": sink.get("notes") or [],
                "refused": bool(sink.get("refused")),
                "prompt": sink.get("prompt") or "",
                "prompt_version": self.cfg.answer_prompt_version,
                "usage": sink.get("usage") or {},
                "grounding": grounding,
                "question": question,
                "search_question": search_question,
                "used_history": bool(history),
                "trace_id": trace.trace_id,
                "analysis": analysis,
                "filters": filters,
                "chunks": chunks,
                "sources": [citation(c) for c in chunks],
                "used_rerank": res["used_rerank"],
                "n_sub_questions": res.get("n_sub_questions", 1),
                "doc_cap": res.get("doc_cap", self.cfg.doc_cap),
                "relaxed_year": bool(res.get("relaxed_year")),
                "stages": res.get("stages", {}),
                "first_token_ms": sink.get("first_token_ms"),
                "ttft_ms": sink.get("ttft_ms"),
                "elapsed_ms": elapsed_ms,
            }
            if sink.get("error"):
                result["error"] = sink["error"]
            sink["result"] = result
            sink["grounding"] = grounding
            sink["elapsed_ms"] = elapsed_ms
            trace.set("filters", filters)
            trace.set("n_sub_questions", result["n_sub_questions"])
            trace.set("used_rerank", bool(result["used_rerank"]))
            trace.set("doc_cap", result["doc_cap"])
            trace.set("retrieved_chunks", [c["chunk_id"] for c in chunks])
            trace.set("retrieved_docs", [c["doc_id"] for c in chunks])
            trace.set("answer_chars", len(text))
            trace.set("refused", result["refused"])
            trace.set("citations_total", ci.get("total", 0))
            trace.set("citations_verified", ci.get("verified", 0))
            trace.set("citations_no_clause", ci.get("no_clause", 0))
            trace.set("citations_conflict", ci.get("clause_conflict", 0))
            trace.set("numbers_total", nu.get("total", 0))
            trace.set("numbers_grounded", nu.get("grounded", 0))
            trace.set("usage", result["usage"])
            trace.set("first_token_ms", result["first_token_ms"])
            trace.set("ttft_ms", result["ttft_ms"])
            if self.cfg.trace_enabled:
                trace.finish(trace_path or self.cfg.trace_path)
            return result

        if not chunks:
            for _ in stream_answer(search_question, chunks, self.cfg, sink=sink):
                pass
            finish_result()
            return iter(())

        def token_stream():
            generation_started = time.perf_counter()
            try:
                for token in stream_answer(search_question, chunks, self.cfg, sink=sink):
                    if sink.get("first_token_ms") is None and token:
                        first_token = (time.perf_counter() - generation_started) * 1000
                        sink["first_token_ms"] = round(first_token, 2)
                        sink["ttft_ms"] = round((time.perf_counter() - started) * 1000, 2)
                        trace.mark("first_token", first_token)
                        trace.set("first_token_ms", sink["first_token_ms"])
                        trace.set("ttft_ms", sink["ttft_ms"])
                    yield token
            except Exception as exc:
                sink["error"] = str(exc)
                trace.set("error", str(exc))
            trace.mark("generation", (time.perf_counter() - generation_started) * 1000)
            finish_result()

        return token_stream()
