"""
入口：自检脚本（不调 LLM，几秒跑完）

用法：python verify.py

作用：把“应该没问题”变成逐项断言。任何一项 FAIL 都会返回非零退出码，
方便在改完代码后一键回归 —— 而不是靠“我觉得跑通了”。
"""
from __future__ import annotations

import sys
from typing import cast

from app.config import DEFAULT, Config
from app.eval import ABLATIONS, aggregate_answer_points
from app.eval.answer_points import parse_judgment
from app.eval.metrics import aggregate_grounding, aggregate_retrieval
from app.generate import (analyze_query, check_grounding, cited_doc_ids,
                          staleness_notes, used_citations)
from app.generate.generator import is_refusal
from app.index import load_index
from app.ingest import citation, load_chunks, split_front_matter, validate_chunks
from app.pipeline import (RAGPipeline, dedupe_sub_questions, ensure_sub_question_coverage,
                          is_eligibility_question, longest_common_substring, policy_name,
                          subs_are_distinct, titles_are_distinct)
from app.retrieve import (HybridRetriever, apply_filters, cap_by_document,
                          extract_filters, filter_with_fallback, rerank_available,
                          rrf_fuse, tokenize)
from app.retrieve.doc_select import dedupe_docs_newest, detect_intent, pick_section_chunk
from app.observability import TraceRecorder

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    """断言并打印；失败项收集起来，最后统一返回非零退出码。
    断言并打印；失败项收集起来，最后统一返回非零退出码。"""
    print("[" + ("PASS" if cond else "FAIL") + "] " + name + ((" | " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


def main() -> int:
    """逐层自检：解析 -> 索引 -> 检索 -> 生成（不调 LLM）-> 评测口径。
    逐层自检：解析 -> 索引 -> 检索 -> 生成（不调 LLM）-> 评测口径。"""
    cfg = DEFAULT
    print("== 配置 ==")
    print("raw   :", cfg.raw_dir)
    print("chunks:", cfg.chunks_path)
    print("index :", cfg.index_dir)
    print("embed :", cfg.embed_model, "| rerank:", cfg.rerank_model or "(关)")

    print()
    print("== 1. 解析层 ==")
    chunks = load_chunks(cfg.chunks_path)
    v = validate_chunks(chunks, cfg.chunk_max_chars)
    check("块数 > 1000", len(chunks) > 1000, str(len(chunks)))
    check("无重复 chunk_id", v["n_dup_id"] == 0)
    check("无超过上限的块", v["n_over_max"] == 0, "max=" + str(v["max_chars"]))
    check("city 全部合法", v["n_bad_city"] == 0)
    check("clause_path 全部非空", v["n_empty_clause"] == 0)
    check("每块都有 doc_title", all(c["doc_title"] for c in chunks))
    check("年份在 2000-2030", all(2000 <= (c["year"] or 0) <= 2030 for c in chunks))
    has_tiao = sum(1 for c in chunks if c["clause_no"].startswith("第"))
    check("存在【第X条】粒度的块（多级切分生效）", has_tiao > 100, str(has_tiao))

    meta_bom, body_bom = split_front_matter("\ufeff\n\n---\ntitle: x\n---\n正文")
    check("文件头兼容 BOM/前置空行", meta_bom.get("title") == "x" and "正文" in body_bom,
          str(meta_bom))

    print()
    print("== 2. 索引层 ==")
    store = load_index(cfg)
    hits = store.search("最低工资标准", 5)
    ids = {c["chunk_id"] for c in chunks}
    check("向量检索返回 5 条", len(hits) == 5, str(len(hits)))
    check("返回的是合法 chunk_id", all(h[0] in ids for h in hits))
    check("距离为非负", all(h[1] >= 0 for h in hits))

    print()
    print("== 3. 检索层 ==")
    toks = tokenize("深圳本科生人才补贴")
    check("中文切词有效", len(toks) > 1, str(toks[:6]))
    fused = rrf_fuse([["a", "b", "c"], ["b", "c", "d"]], k=60)
    check("RRF 去重", len(fused) == 4, str(len(fused)))
    check("RRF 首位是两路都靠前的 b", fused[0][0] == "b", fused[0][0])
    f = extract_filters("2023年以后深圳的人才补贴")
    check("规则版条件抽取", f.get("city_in") == ["深圳"] and f.get("year_gte") == 2023, str(f))
    f_before = extract_filters("2020年以前东莞的补贴")
    check("年份上限抽取", f_before.get("city_in") == ["东莞"] and f_before.get("year_lte") == 2020,
          str(f_before))
    year_chunks = [
        {"chunk_id": "y2019", "city": "东莞", "year": 2019, "text": "x"},
        {"chunk_id": "y2021", "city": "东莞", "year": 2021, "text": "y"},
    ]
    year_filtered = apply_filters(year_chunks, {"city_in": ["东莞"], "year_lte": 2020})
    check("年份上限过滤生效", [c["chunk_id"] for c in year_filtered] == ["y2019"],
          str(year_filtered))
    check("精排路径可用性检查", rerank_available("") is False
          and rerank_available(r"F:\definitely-missing\model") is False)
    dg = apply_filters(chunks, {"city_in": ["东莞"]})
    check("元数据过滤生效", all(c["city"] == "东莞" for c in dg) and 0 < len(dg) < len(chunks),
          str(len(dg)) + "/" + str(len(chunks)))
    retriever = HybridRetriever(chunks, cfg, store=store)
    res = retriever.retrieve("东莞市最低工资标准是多少", use_rerank=False, keep_stages=True)
    check("两路检索都执行", "vector" in res["stages"] and "bm25" in res["stages"])
    check("最终 top-k 数量正确", len(res["final"]) == cfg.final_top_k, str(len(res["final"])))
    by = {"a": {"doc_id": "d1"}, "b": {"doc_id": "d1"}, "c": {"doc_id": "d2"},
          "d": {"doc_id": "d3"}}
    check("doc_cap=1 每个文档最多 1 个 chunk",
          cap_by_document(["a", "b", "c", "d"], by, 3, 1) == ["a", "c", "d"])
    check("doc_cap=0 不改变原顺序", cap_by_document(["a", "b", "c"], by, 2, 0) == ["a", "b"])

    print()
    print("== 3b. 过滤补齐 / 子问题合并 ==")
    fc = [
        {"chunk_id": "a1", "city": "深圳", "year": 2026, "text": "甲"},
        {"chunk_id": "a2", "city": "深圳", "year": 2024, "text": "乙"},
        {"chunk_id": "d1", "city": "东莞", "year": 2026, "text": "丙"},
    ]
    ids, relaxed = filter_with_fallback(["a1", "d1", "a2"], fc,
                                        {"city_in": ["深圳"], "year_gte": 2026}, 3)
    check("过滤不足时保留城市、放宽年份补齐", ids == ["a1", "a2"] and relaxed, str(ids))
    ids2, relaxed2 = filter_with_fallback(["a1", "d1", "a2"], fc,
                                          {"city_in": ["深圳"], "year_gte": 2026}, 1)
    check("候选已够 top_k 时不做放宽", ids2 == ["a1"] and not relaxed2, str(ids2))
    ids3, relaxed3 = filter_with_fallback(["d1"], fc,
                                          {"city_in": ["深圳"], "year_gte": 2026}, 3)
    check("绝不跨城市放宽", ids3 == [] and not relaxed3, str(ids3))

    ds = dedupe_sub_questions("主问题", ["主问题", " 甲 ", "甲", "", "乙", "丙"])
    check("子问题去重且最多 3 条", ds == ["甲", "乙", "丙"], str(ds))
    check("问题意图识别期限", detect_intent("东莞灵活就业社保补贴能领多久") == "期限")
    same_title = {"old": [{"doc_id": "old", "doc_title": "测试办法", "year": 2018}],
                  "new": [{"doc_id": "new", "doc_title": "测试办法", "year": 2021}]}
    check("同标题文档保留最新年份",
          dedupe_docs_newest(["old", "new"], same_title) == ["new"])
    fake_doc = [
        {"chunk_id": "d0", "clause_path": "全文", "text": "补贴标准：每人100元。"},
        {"chunk_id": "d1", "clause_path": "三、", "text": "补贴期限：最长不超过3年。"},
    ]
    chosen = pick_section_chunk("这项补贴能领多久", fake_doc, [0.0, 1.0, 0.0],
                                {"d0": 0, "d1": 1})
    check("按问题类型选到期限块", chosen["chunk_id"] == "d1", chosen["chunk_id"])
    mix = [
        {"chunk_id": "full", "clause_path": "全文",
         "text": "补贴期限最长不超过3年，具体按申请时的政策执行，以下为完整说明。" * 3},
        {"chunk_id": "clause", "clause_path": "三、", "text": "补贴期限最长不超过3年。"},
    ]
    mix_chosen = pick_section_chunk("补贴期限是多久", mix, [1.0, 1.0], {"full": 0, "clause": 1})
    check("同命中优先条款块而非全文", mix_chosen["chunk_id"] == "clause", mix_chosen["chunk_id"])
    check("同一政策追问不启用文档级选块",
          subs_are_distinct(["在东莞办在职人才入户，申请时是否要缴养老保险",
                             "在东莞办在职人才入户，社保暂停还能通过吗"]) is False)
    check("不同政策子问题可启用文档级选块",
          subs_are_distinct(["东莞职业介绍补贴标准是多少",
                             "员工制家政企业吸纳就业补贴怎么算"]) is True)
    check("最长公共子串识别同一政策",
          longest_common_substring("在东莞办在职人才入户申请", "在东莞办在职人才入户社保") >= 6)
    check("从正式标题提取政策名",
          policy_name("深圳市人社局关于印发《深圳市自主创业扶持补贴办法》的通知") ==
          "深圳市自主创业扶持补贴办法")
    check("同一政策标题不启用文档级选块",
          titles_are_distinct(["深圳市人社局关于印发《深圳市自主创业扶持补贴办法》的通知",
                               "深圳市人社局关于《深圳市自主创业扶持补贴办法》的政策解读"]) is False)
    check("资格类问题识别", is_eligibility_question("深圳户籍毕业生能申请创业补贴吗"))
    eligibility_doc = [
        {"chunk_id": "e0", "clause_path": "一、", "text": "补贴对象：未享受过创业扶持补贴的自主创业人员，正常经营并依法缴交社会保险费。"},
        {"chunk_id": "e1", "clause_path": "二、", "text": "创业带动就业补贴：签订1年以上劳动合同并缴交社会保险费。"},
    ]
    chosen = pick_section_chunk("能申请创业补贴吗", eligibility_doc, [0.0, 0.0],
                                {"e0": 0, "e1": 1})
    check("资格类问题优先选补贴对象块", chosen["chunk_id"] == "e0", chosen["chunk_id"])

    class _FakeRetriever:
        """只用来测合并逻辑：不加载模型，按 query 返回不同的 id 列表。"""
        def __init__(self, cs):
            self.chunks = cs
            self.by_id = {c["chunk_id"]: c for c in cs}
            self.by_doc = {}
            for c in cs:
                self.by_doc.setdefault(c["doc_id"], []).append(c)

        def retrieve(self, q, **kw):
            all_ids = [c["chunk_id"] for c in self.chunks]
            start = 3 if q == "子问题B" else 0
            ids = all_ids[start:start + (kw.get("top_k") or 5)]
            return {"final_ids": ids, "final": [self.by_id[i] for i in ids],
                    "used_rerank": False, "stages": {}, "relaxed_year": False}

    fake_cs = [{"chunk_id": "c" + str(i), "doc_id": "d", "doc_title": "t", "year": 2026,
                "city": "深圳", "clause_path": "全文", "clause_no": "",
                "text": "x", "chars": 1} for i in range(6)]
    fake_pipe = RAGPipeline(chunks=fake_cs,
                            retriever=cast(HybridRetriever, _FakeRetriever(fake_cs)),
                            cfg=Config(rerank_model=""))
    mm = fake_pipe._retrieve_multi("主问题", ["子问题A", "子问题B"], {}, None, 3)
    check("子问题分别召回后按 RRF 合并",
          mm["n_sub_questions"] == 2 and [c["chunk_id"] for c in mm["final"]] == ["c3", "c4", "c5"],
          str(mm["final_ids"]))
    ms = fake_pipe._retrieve_multi("主问题", ["主问题"], {}, None, 3)
    check("子问题等于原问题时退回单查询",
          ms["n_sub_questions"] == 1 and [c["chunk_id"] for c in ms["final"]] == ["c0", "c1", "c2"],
          str(ms["final_ids"]))
    covered = ensure_sub_question_coverage(["x1", "x2", "x3"], ["m1", "m2", "m3"], 3)
    check("multi 保底不覆盖前一个子问题",
          len(covered) == 3 and {"m1", "m2", "m3"} <= set(covered), str(covered))
    anchored = ensure_sub_question_coverage(["x1", "x2", "x3"], ["b0", "m1", "m2"], 3)
    check("原问题锚点优先保留",
          {"b0", "m1", "m2"} <= set(anchored), str(anchored))

    print()
    print("== 4. 生成层（不调 LLM）==")
    an = analyze_query("深圳的人才补贴", cfg, use_llm=False)
    check("规则版查询分析可用", isinstance(an, dict) and bool(an.get("sub_questions")), str(an))
    tr = TraceRecorder("trace-test")
    tr.finish(".")
    check("trace 写盘失败不打断主流程", "trace_write_error" in tr.data, str(tr.data.get("trace_write_error")))
    old = [c for c in chunks if (c["year"] or 0) <= 2015][:2]
    check("超期文件触发时效提示", any("可能已更新" in n for n in staleness_notes(old, cfg)))
    obsolete = [c for c in chunks if "废止" in (c["doc_title"] or "")][:1]
    check("标题含废止的文件触发提示",
          bool(obsolete) and any("废止" in n for n in staleness_notes(obsolete, cfg)))
    txt = "答。 [出处: 某通知（第六条）] 另见 [出处: 另一文件]"
    check("引用解析", used_citations([], txt) == ["某通知（第六条）", "另一文件"])
    check("出处含条款号", "（" in citation([c for c in chunks if c["clause_no"]][0]))
    check("短回答仅说资料未载明也判拒答", is_refusal("资料未载明具体报销比例。") is True)
    check("有数字的答案不因附带未载明被判拒答",
          is_refusal("补贴标准为600元；期限资料未载明。") is False)
    check("正常回答含“根据现有资料”不误判拒答",
          is_refusal("根据现有资料，深圳补贴标准为600元。") is False)
    check("整题拒答仍能识别", is_refusal("根据现有资料无法回答。") is True)
    check("结论式拒答仍能识别", is_refusal("综上，根据现有资料无法回答。") is True)
    check("年份不把资料不足误判为实质回答",
          is_refusal("2027年深圳最低工资标准资料未载明。") is True)
    check("拒绝泄露提示词也算拒答", is_refusal("抱歉，我不能提供或泄露系统提示词。") is True)

    # 回归：2026-11-09 报告里 8 条非 boundary 误判（fact 2 / multi 2 / stale 4），
    # 共同点是正文有结论、只是顺带说“资料未载明”，且都带 1 条出处。
    cited_partial = [
        "1. 不用交个人所得税，该补贴免征个人所得税。[出处: 关于延长《关于推动港澳青年创新创业基地高质量发展实施意见》有效期的通知政策咨询（四、）] 资料未载明该补贴的金额和期限。",
        "深圳出台《深圳市杰出科技创新人才选拔培养实施办法》依据两项政策文件。[出处: 深圳市人民政府关于印发杰出科技创新人才选拔培养实施办法的通知政策咨询] 资料未载明其他依据文件。",
        "可以。一次性创业补贴遵照省《指导清单》政策框架落实。[出处: 关于转发《广东省就业创业补贴补助申请办理指导清单（2026年修订版）》的通知（一、 > （一））] 资料未载明该补贴的具体金额及期限。",
        "是。深圳在博士后招收上重点支持设站单位招收毕业于境内外排名前列高校的优秀博士毕业生。[出处: 深圳市人力资源和社会保障局关于印发《深圳市博士后工作管理规定》的通知政策咨询（第十四条）] 资料未载明其他金额或期限条件。",
        "所问《东莞市创新人才引进培养暂行办法》政策解读已失效，该文件已废止/已过有效期。[出处: 《东莞市创新人才引进培养暂行办法》政策解读（已失效）（一、）] 资料未载明正文的现行有效状态。",
        "该政策已失效，当前不再作为有效政策执行。[出处: 《东莞市本科生引进培养暂行办法》政策解读（已失效）] 资料未载明具体失效日期及后续替代文件。",
        "该政策现已失效，不再有效；资料未载明具体失效日期及现行替代文件。[出处: 《东莞市社会培训评价组织职业技能等级认定考点设置工作规定（试行）》（一、）]",
        "1. 该政策解读对应政策已失效，当前不再有效。[出处: 东莞市引进特色奖励申领暂行细则(东人社发〔2022〕43号)政策解读（已失效）] 资料未载明后续替代文件。",
    ]
    check("有出处的部分未载明不误判整题拒答（8/8）",
          all(is_refusal(x) is False for x in cited_partial),
          str([is_refusal(x) for x in cited_partial]))

    print()
    print("== 4b. 生成后核验（引用真实性 / 数字溯源）==")
    real = next(c for c in chunks
                if c.get("clause_no") and c["clause_no"].startswith("第"))
    ok_txt = "结论。[出处: " + real["doc_title"] + "（" + real["clause_path"] + "）]"
    g = check_grounding(ok_txt, [real])
    check("正确出处核验通过", g["citations"]["verified"] == 1
          and g["citations"]["verify_rate"] == 1.0, str(g["citations"]["items"]))
    fake = "结论。[出处: 东莞市某某不存在的补贴办法（第五条）]"
    check("编造文件被判未匹配", check_grounding(fake, [real])["citations"]["unmatched"] == 1)
    wrong = "结论。[出处: " + real["doc_title"] + "（第199条）]"
    check("条款号写错被判冲突",
          check_grounding(wrong, [real])["citations"]["clause_conflict"] == 1)
    syn = {"chunk_id": "t#0", "doc_id": "t", "doc_title": "关于印发《测试补贴办法》的通知",
           "year": 2024, "clause_path": "第五条", "clause_no": "第五条",
           "text": "补贴标准为30000元，期限3年。", "chars": 20}
    cite = "关于印发《测试补贴办法》的通知（第五条）"
    num_ok = check_grounding("补贴3万元。[出处: " + cite + "]", [syn])["numbers"]
    num_bad = check_grounding("补贴5万元。[出处: " + cite + "]", [syn])["numbers"]
    check("数字可溯源（3万=30000）", num_ok["total"] == 1 and num_ok["grounded"] == 1, str(num_ok))
    check("编造数字被抓出", num_bad["ungrounded"] == ["5万元"], str(num_bad))
    unit_src = dict(syn, chunk_id="u#0", text="补贴期限累计最长不超过5年。")
    unit_bad = check_grounding("补贴比例为5%。[出处: " + cite + "]", [unit_src])["numbers"]
    check("数字核验区分单位（5年 != 5%）",
          unit_bad["total"] == 1 and unit_bad["grounded"] == 0, str(unit_bad))
    no_cite = check_grounding("补贴比例为5%。", [unit_src])["numbers"]
    check("无引用时数字核验标记低置信",
          no_cite["basis"] == "retrieved" and no_cite["low_confidence"] is True, str(no_cite))
    a12 = dict(syn, chunk_id="y12", year=2012)
    a21 = dict(syn, chunk_id="y21", year=2021)
    gy = check_grounding("结论。[出处: 关于印发《测试补贴办法》的通知（2021年）（第五条）]",
                         [a12, a21])["citations"]
    check("同标题不同年份按年份区分",
          gy["verified"] == 1 and gy["ambiguous"] == 0
          and [i["matched_ids"] for i in gy["items"]] == [["y21"]], str(gy["items"]))
    deep = dict(syn, chunk_id="x#0", clause_path="第二条", clause_no="第二条")
    gc = check_grounding("结论。[出处: 关于印发《测试补贴办法》的通知（第二条第一项）]",
                         [deep])["citations"]
    check("条款粒度不同但指向同一处", gc["verified"] == 1, str(gc["items"]))
    full = dict(syn, chunk_id="f#0", clause_path="全文", clause_no="")
    gf = check_grounding("结论。[出处: 关于印发《测试补贴办法》的通知（第九百九十九条）]",
                         [full])["citations"]
    check("全文块不会放过错误条款号", gf["clause_conflict"] == 1, str(gf["items"]))
    full2 = dict(syn, chunk_id="f2#0", clause_path="全文", clause_no="",
                 text="一、强化最低工资标准执行" + chr(10) + "从2025年3月1日起执行。")
    gf2 = check_grounding("结论。[出处: 关于印发《测试补贴办法》的通知（一、）]",
                          [full2])["citations"]
    check("全文块里的小标题编号可核验", gf2["verified"] == 1, str(gf2["items"]))
    gf3 = check_grounding("结论。[出处: 关于印发《测试补贴办法》的通知（一）]",
                          [full2])["citations"]
    check("裸（一）不靠正文兜底放行", gf3["clause_conflict"] == 1, str(gf3["items"]))
    full3 = dict(syn, chunk_id="f3#0", clause_path="全文", clause_no="",
                 text="补贴条件：1.开发岗位。2.招用就业困难人员并签订1年以上劳动合同。")
    gf4 = check_grounding("结论。[出处: 关于印发《测试补贴办法》的通知（补贴条件第2项）]",
                          [full3])["citations"]
    check("全文块里小节名+项号可核验", gf4["verified"] == 1, str(gf4["items"]))
    nest = dict(syn, chunk_id="n#0",
                doc_title="深圳市人力资源和社会保障局关于印发《深圳市就业创业补贴申请办理清单》的通知政策咨询",
                clause_path="一、 > （七） > 8.", clause_no="8.",
                text="补助对象为符合条件的服务承接主体。")
    gn = check_grounding("补助对象为服务承接主体。[出处: 深圳市就业创业补贴申请办理清单（一、>（七）>8.）]",
                         [nest])["citations"]
    check("嵌套条款路径不影响文件匹配", gn["verified"] == 1, str(gn["items"]))
    wn = check_grounding("按深人社规〔2017〕6号执行，补贴30000元。[出处: 关于印发《测试补贴办法》的通知（第五条）]",
                         [syn])["numbers"]
    check("公文文号不计入数字核验", wn["total"] == 1 and wn["grounded"] == 1, str(wn))
    syn2 = dict(syn, chunk_id="z#0", text="补贴期限累计最长不超过三年。")
    zn = check_grounding("累计最长不超过3年。[出处: 关于印发《测试补贴办法》的通知（第五条）]",
                         [syn2])["numbers"]
    check("中文数字与阿拉伯数字等价（三年=3年）", zn["total"] == 1 and zn["grounded"] == 1, str(zn))
    ctx = check_grounding("资料2至资料4所列内容；另见第2条。[出处: 关于印发《测试补贴办法》的通知（第五条）]",
                          [syn])["numbers"]
    check("资料编号与条号不计入数字", ctx["total"] == 0, str(ctx))
    ctx2 = check_grounding("资料1/2规定补贴30000元。[出处: " + cite + "]", [syn])["numbers"]
    check("资料1/2 不作为数字断言", ctx2["total"] == 1 and ctx2["grounded"] == 1, str(ctx2))
    list_num = check_grounding("结论。\n2. 是。\n[出处: " + cite + "]", [syn])["numbers"]
    check("分点编号不作为数字断言", list_num["total"] == 0, str(list_num))
    prefix_chunks = [
        dict(syn, chunk_id="p1", doc_id="d1",
             doc_title="深圳市人力资源和社会保障局关于印发A的通知"),
        dict(syn, chunk_id="p2", doc_id="d2",
             doc_title="深圳市人力资源和社会保障局关于印发B的通知"),
    ]
    check("引用 doc_id 不因通用前缀误报",
          cited_doc_ids("根据深圳市人力资源和社会保障局的相关政策，补贴为600元。",
                        prefix_chunks) == [])
    check("引用 doc_id 按出处精确映射",
          cited_doc_ids("结论。[出处: 深圳市人力资源和社会保障局关于印发A的通知（第五条）]",
                        prefix_chunks) == ["d1"])

    print()
    print("== 5. 评测层 ==")
    from pathlib import Path
    import json
    p = Path(cfg.eval_gold)
    gold = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    check("评测集为 v3 正式口径", len(gold) >= 70, str(len(gold)))
    check("每条都有 query/type", all(g.get("query") and g.get("type") for g in gold))
    check("评测集含 boundary/multi/stale",
          {"boundary", "multi", "stale"} <= {g.get("type") for g in gold})
    fake = [
        {"golden": ["d1"], "hit": True, "rank": 1, "latency_ms": 10.0},
        {"golden": [], "hit": False, "rank": 0, "latency_ms": 20.0},
    ]
    agg = aggregate_retrieval(fake)
    check("边界型不计入召回分母", agg["n_scored"] == 1 and agg["recall_at_k"] == 1.0)
    lat_agg = aggregate_retrieval([
        {"golden": ["d1"], "retrieved": ["d1"], "hit": True, "rank": 1, "latency_ms": 100.0},
        {"golden": [], "retrieved": [], "hit": False, "rank": 0, "latency_ms": 1000.0},
    ])
    check("平均延迟分母包含全部记录", lat_agg["mean_latency_ms"] == 550.0,
          str(lat_agg["mean_latency_ms"]))
    two = [
        {"golden": ["d1", "d2"], "retrieved": ["d1"], "hit": True, "rank": 1, "latency_ms": 1.0},
        {"golden": ["d3", "d4"], "retrieved": ["d3", "d4"], "hit": True, "rank": 1,
         "latency_ms": 1.0},
    ]
    a2 = aggregate_retrieval(two)
    check("两篇 golden 的覆盖率/全覆盖", a2["golden_coverage"] == 0.75
          and a2["full_recall"] == 0.5, str(a2))
    clause_recs = [
        {"golden": ["d1"], "retrieved": ["d1"], "hit": True, "rank": 1, "latency_ms": 1.0,
         "clause_golden": ["c1", "c2"], "clause_hit": True, "clause_rank": 1,
         "clause_coverage": 0.5},
        {"golden": ["d2"], "retrieved": ["d2"], "hit": True, "rank": 1, "latency_ms": 1.0,
         "clause_golden": ["c3"], "clause_hit": False, "clause_rank": 0,
         "clause_coverage": 0.0},
    ]
    ca = aggregate_retrieval(clause_recs)
    check("clause 指标计算正确", ca["clause_n"] == 2 and ca["clause_recall_at_k"] == 0.5
          and ca["clause_mrr"] == 0.5 and ca["clause_coverage"] == 0.25, str(ca))
    check("消融变体 >= 5", len(ABLATIONS) >= 5, str(len(ABLATIONS)))
    mp = Path(cfg.data_dir) / "eval_gold.multi.jsonl"
    if mp.exists():
        mg = [json.loads(ln) for ln in mp.read_text(encoding="utf-8").splitlines() if ln.strip()]
        check("multi 评测集每条都有 2 篇 golden",
              len(mg) >= 10 and all(len(g.get("golden_docs") or []) == 2 for g in mg),
              str(len(mg)) + " 条")
    cp = Path(cfg.data_dir) / "eval_gold.clause.jsonl"
    if cp.exists():
        cg = [json.loads(ln) for ln in cp.read_text(encoding="utf-8").splitlines() if ln.strip()]
        check("clause gold 可读且非空",
              len(cg) >= 20 and all(g.get("golden_chunks") for g in cg),
              str(len(cg)) + " 条")
    bp = Path(cfg.data_dir) / "eval_gold.boundary_ext.jsonl"
    if bp.exists():
        bg = [json.loads(ln) for ln in bp.read_text(encoding="utf-8").splitlines() if ln.strip()]
        check("扩展边界集可读", len(bg) >= 10 and all(g.get("type") == "boundary" for g in bg),
              str(len(bg)) + " 条")
    sp = Path(cfg.data_dir) / "eval_gold.safety_ext.jsonl"
    if sp.exists():
        sg = [json.loads(ln) for ln in sp.read_text(encoding="utf-8").splitlines() if ln.strip()]
        check("安全边界集可读", len(sg) >= 3 and all(g.get("type") == "safety" for g in sg),
              str(len(sg)) + " 条")
    gagg = aggregate_grounding([{"type": "fact", "grounding": {
        "citations": {"total": 4, "verified": 3, "unmatched": 1, "clause_conflict": 0},
        "numbers": {"total": 2, "grounded": 2}}}])
    check("核验指标池化正确", gagg["citation_verify_rate"] == 0.75
          and gagg["number_grounded_rate"] == 1.0, str(gagg))
    fg = aggregate_grounding([{"type": "fact", "grounding": {
        "citations": {"total": 0, "verified": 0, "unmatched": 0, "clause_conflict": 0},
        "numbers": {"total": 1, "grounded": 1, "basis": "retrieved"}}}])
    check("数字主口径只算 cited，fallback 单独报",
          fg["number_grounded_rate"] is None and fg["number_retrieved_fallback"] == 1,
          str(fg))
    l1 = aggregate_answer_points([
        {"type": "fact", "l1": {"total": 2, "covered": 1, "coverage": 0.5, "points": []}},
        {"type": "fact", "l1": {"total": 2, "covered": 0, "coverage": 0.0,
                                "points": [], "error": "timeout"}},
    ])
    check("L1 裁判失败不计入覆盖率分母", l1["n_points"] == 2
          and l1["answer_point_coverage"] == 0.5 and l1["n_judge_errors"] == 1, str(l1))
    bad_json = '{"points":[{"id":1,"covered":true,"evidence":"实施"鹏城英才计划"的意见"}]}'
    check("L1 裁判 evidence 内未转义引号可修复",
          parse_judgment(bad_json, 1)[0]["covered"] is True)

    print()
    if FAILS:
        print("!! 失败 " + str(len(FAILS)) + " 项: " + "; ".join(FAILS))
        return 1
    print("全部自检通过（" + str(len(chunks)) + " 块）")
    return 0


if __name__ == "__main__":
    sys.exit(main())