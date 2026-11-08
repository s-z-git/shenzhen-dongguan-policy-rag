"""
app.generate.verify —— 生成后核验：引用真实性 + 数字可溯源性

对应"答案质量三层"的前两层：
    L1 引用核验：回答里的 [出处: 文件名（条款号）] 是否真的对应本轮检索到的条款
    L2 数字核验：回答里的金额 / 年限 / 比例，是否能在被引条款原文里找到

为什么必须单独做一层：prompt 只能"要求"模型写出处，不能保证它写的是真的。
used_citations() 会把模型写的出处原样返回；评测里假标题匹配不上任何 chunk，
会被当成"没写引用"而不是"编造引用"——这个错误在现有指标里是隐形的。

边界（写进 README，主动说明）：
    本模块只能证明"出处真实存在、条款号对得上"，
    不能证明"这段原文支持这个结论"。后者需要 entailment/NLI，本项目不做。

纯规则、不调 LLM、单次 < 5ms。
"""
from __future__ import annotations

import difflib
import re

from app.generate.generator import CITE_RE

NL = chr(10)

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}

# 数字 + 单位。前后各加断言，避免把 "12080" 里的 "2080" 也算一个数字
_NUM_RE = re.compile(
    r"(?<![\d.])(\d{1,9}(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(亿元|万元|元|%|％|个月|月|年|天|日|次|倍|周岁|岁|人|份|工作日)?")

# 公文文号：深人社规〔2017〕6号 —— 里面的年份和序号是标识符，不是要核对的数字
_DOC_NO_RE = re.compile(r"[〔\[【]\s*(?:19|20)\d{2}\s*[〕\]】]\s*\d{1,4}\s*号|第\s*\d{1,4}\s*号")
# 上下文标签：模型会说「资料2至资料4」，这是指代不是数量
# 上下文标签：资料2至资料4 / 资料1/2 / 资料1、2 都不是待核对的数量
_CTX_LABEL_RE = re.compile(
    r"资料\s*\d+(?:\s*(?:至|到|~|～|-|—|/|、)\s*(?:资料\s*)?\d+)*")
# 「资料1（2013）」「（2020）」这类是上下文标签/年份，不是数量
_PAREN_YEAR_RE = re.compile(r"[（(]\s*(?:19|20)\d{2}\s*[）)]")
# 中文数字 + 单位：三年 / 五万元 / 十一人
_CN_NUM_RE = re.compile(
    r"([一二三四五六七八九十百零〇两]{1,6})\s*"
    r"(亿元|万元|元|%|％|个月|月|年|天|日|倍|周岁|岁|人|份|个)")
# 「百分之二十二」= 22%
_PCT_CN_RE = re.compile(r"百分之\s*([一二三四五六七八九十百零〇两0-9]{1,4})")

# 条款号：第X条 / 第X章 / 第X节 / 第X款 / （一） / 一、 / 3.
_CLAUSE_RE = re.compile(r"第\s*([一二三四五六七八九十百零〇两0-9]{1,6})\s*([条章节款项目])")
_PAREN_NUM_RE = re.compile(r"[（(]\s*([一二三四五六七八九十百零〇两0-9]{1,4})\s*[）)]")
_DUN_RE = re.compile(r"([一二三四五六七八九十]{1,3})\s*、")
_DOT_RE = re.compile(r"(?:^|[\s，,；;：:])?(\d{1,3})\s*[.．、]")


def _cn2int(s: str) -> int | None:
    """中文数字转整数：八 -> 8，十一 -> 11，一百零八 -> 108；失败返回 None。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in _CN_DIGITS:
            cur = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            total += (cur or 1) * _CN_UNITS[ch]
            cur = 0
        else:
            return None
    return total + cur


def _clause_tokens(text: str) -> set[tuple[str, int]]:
    """从任意文本里抽出条款坐标集合，如 {('章', 3), ('条', 8)}。"""
    toks: set[tuple[str, int]] = set()
    for m in _CLAUSE_RE.finditer(text or ""):
        v = _cn2int(m.group(1))
        if v:
            toks.add((m.group(2), v))
    for m in _PAREN_NUM_RE.finditer(text or ""):
        v = _cn2int(m.group(1))
        if v:
            toks.add(("项", v))
    for m in _DUN_RE.finditer(text or ""):
        v = _cn2int(m.group(1))
        if v:
            toks.add(("项", v))
    for m in _DOT_RE.finditer(text or ""):
        v = _cn2int(m.group(1))
        if v and 0 < v < 200:
            toks.add(("项", v))
    return toks


# 标题里可能混进来的条款坐标：第X条 / （一） / 一、 / 8.
_COORD_RES = (
    re.compile(r"第\s*[一二三四五六七八九十百零〇两0-9]{1,6}\s*[条章节款项目]"),
    re.compile(r"[（(]\s*[一二三四五六七八九十百零〇两0-9]{1,4}\s*[）)]"),
    re.compile(r"(?<![0-9A-Za-z])\d{1,3}\s*[.．、]"),
    re.compile(r"[一二三四五六七八九十]{1,3}\s*、"),
)


def _norm_title(s: str) -> str:
    """标题归一化：先剥掉条款坐标，再去掉所有标点，只留汉字数字字母。

    为什么要先剥坐标：模型会把多级路径写成嵌套括号，例如
    「…办理清单（一、>（七）>8.）」。用「删括号」的正则处理不了嵌套，
    会把「一」「8」留在标题里，导致明明存在的文件被判成「未匹配」。
    """
    s = s or ""
    for pat in _COORD_RES:
        s = pat.sub("", s)
    return re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", s)


def _norm_key(s: str) -> str:
    """只去标点，不去括号内容——用于在正文里定位“一、强化最低工资标准执行”这类小标题。"""
    return re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", s or "")


def _paren_fragment(s: str) -> str:
    """取最后一个括号里的内容（如「补贴标准」），用于核对小节名。"""
    hit = re.findall(r"[（(]([^（）()]{1,40})[）)]", s or "")
    return hit[-1].strip() if hit else ""


def _clause_fragment(raw: str) -> str:
    """取出引用里“带条款信息的那一段”：括号内小标题优先，其次 第X条。"""
    parts = []
    for m in re.finditer(r"[（(]([^（）()]{1,60})[）)]", raw or ""):
        inner = m.group(1)
        if _clause_tokens(inner):
            parts.append(inner)
    if parts:
        return " ".join(parts)
    hit = _CLAUSE_RE.findall(raw or "")
    return " ".join("第" + a + b for a, b in hit)


def _frag_in_text(frag: str, text: str) -> bool:
    """引用的条款片段是否真的出现在 chunk 正文里。

    长的片段（≥3 字）直接做归一化包含判断；
    短的只认「一、」「1.」这种带分隔符的小标题编号，并且要求后面紧跟正文，
    避免「一」在「统一」「第一」里被误命中。裸「（一）」一律不放行——太容易糊弄过去。
    """
    f = (frag or "").strip()
    if not f:
        return False
    nf = _norm_key(f)
    if len(nf) >= 3:
        return nf in _norm_key(text)
    if len(nf) <= 2 and f.rstrip("）)")[-1:] in "、.．":
        body = f.strip("（）()")
        return re.search(re.escape(body) + r"\s*[\u4e00-\u9fa5]", text or "") is not None
    return False


def _section_item_in_text(frag: str, text: str) -> bool:
    """全文块里的「补贴条件第2项」「其他事项第4条」：正文没有字面“第2项”，
    但有小节名和编号。只要小节名出现、且其后 200 字内有对应编号，就算可核验。"""
    m = re.match(r"(.+?)(?:第)?([一二三四五六七八九十百零〇两0-9]{1,6})(?:项|条)", frag or "")
    if not m:
        return False
    section = _norm_key(m.group(1))
    num = _cn2int(m.group(2))
    if len(section) < 2 or not num:
        return False
    pos = (text or "").find(section)
    if pos < 0:
        return False
    tail = text[pos + len(section):pos + len(section) + 200]
    return re.search(r"(?:^|[：:；;、。\s])" + str(num) + r"\s*[.．、]", tail) is not None


def _year_of(s: str) -> int | None:
    """从文本里找 19xx / 20xx 的年份，用于区分同名不同年的文件。"""
    m = re.search(r"(19|20)\d{2}", s or "")
    return int(m.group(0)) if m else None


def _chunk_tokens(c: dict) -> set[tuple[str, int]]:
    """chunk 自身拥有的条款坐标（合并 clause_path 与 clause_no）。"""
    return _clause_tokens(str(c.get("clause_path") or "") + " "
                          + str(c.get("clause_no") or ""))


def _doc_candidates(title_text: str, chunks: list[dict]) -> list[dict]:
    """在【本轮检索结果】里找引用指向的文件；找不到就说明是编造或改写得面目全非。"""
    nt = _norm_title(title_text)
    if len(nt) < 8:                       # 太短的名字没有区分度，宁可判未匹配
        return []
    exact, fuzzy = [], []
    for c in chunks or []:
        nc = _norm_title(str(c.get("doc_title") or ""))
        if not nc:
            continue
        if nt in nc or nc in nt:
            exact.append(c)
            continue
        sm = difflib.SequenceMatcher(None, nt, nc)
        hit = sm.find_longest_match(0, len(nt), 0, len(nc))
        # 模型常给办法名加地名前缀（《深圳市XX办法》 vs 原文《XX办法》）
        if hit.size >= max(8, int(0.8 * len(nt))) or sm.ratio() >= 0.92:
            fuzzy.append(c)
    # 精确/包含匹配优先；只有没有精确候选时才用模糊匹配，避免 A/B 两份文件互相误配。
    base = exact or fuzzy
    if not base:
        return []
    if len(base) <= 1:
        return base                      # 只有一个候选：年份在标题里也无所谓
    # 同名多版本（如 2012/2021 两个条例）才用年份消歧；
    # 注意不能提前过滤——「2019年法治政府建设情况报告」的年份是标题的一部分，
    # 而 chunk 的 year 是 2020（发布年），提前过滤会把正确答案全部排除。
    year = _year_of(title_text)
    if year:
        same = [c for c in base if not c.get("year") or int(c["year"]) == year]
        if same:
            return same
    return base


def _classify(raw: str, chunks: list[dict]) -> dict:
    """单条出处定级：verified / no_clause / clause_conflict / unmatched。"""
    cands = _doc_candidates(raw, chunks)
    toks = _clause_tokens(raw)
    item = {"raw": raw[:160], "clause": "", "status": "unmatched",
            "matched_ids": [], "actual_clause": [], "ambiguous": False,
            "matched_in_text": False}
    if toks:
        item["clause"] = " ".join(k + str(v) for k, v in sorted(toks))
    if not cands:
        return item
    item["ambiguous"] = len(cands) > 1
    item["matched_ids"] = [c["chunk_id"] for c in cands[:5]]
    if not toks:
        # 括号里写的是小节名（如「补贴标准」）：只要这段文字在原文里出现，也算可验证
        frag = _paren_fragment(raw)
        if _frag_in_text(frag, " ".join(str(c.get("text") or "") for c in cands)):
            item["status"] = "verified"
            item["matched_in_text"] = True
            item["matched_ids"] = [c["chunk_id"] for c in cands[:5]]
        else:
            item["status"] = "no_clause"
        return item
    # 兼容两种粒度：模型引「第二条第一项」而块只切到「第二条」，或反过来。
    # 只要两边条款坐标互为子集就算指向同一处；完全不相交才是真冲突。
    ok = []
    for c in cands:
        ct = _chunk_tokens(c)
        # 注意 "全文" 块的坐标是空集，空集是任何集合的子集，必须单独挡掉
        if toks <= ct or (ct and ct <= toks):
            ok.append(c)
    if not ok:
        # 第二道：chunk 是“全文”（文档很短，没切出条款路径）时，
        # 模型常引用正文里的小标题，如「（一、强化最低工资标准执行）」。
        # 这时条款号对不上不代表写错——只要这段文字真的在原文里，就判通过。
        frag = _clause_fragment(raw)
        ok = [c for c in cands
              if _frag_in_text(frag, str(c.get("text") or ""))
              or _section_item_in_text(frag, str(c.get("text") or ""))]
        if ok:
            item["matched_in_text"] = True
    if ok:
        item["status"] = "verified"
        item["matched_ids"] = [c["chunk_id"] for c in ok[:5]]
    else:
        item["status"] = "clause_conflict"
        item["actual_clause"] = [str(c.get("clause_no") or "全文") for c in cands[:5]]
    return item


def _canonical_unit(unit: str) -> tuple[float, str]:
    """把单位归一化到同一量纲；返回 (倍率, 规范单位)。"""
    u = (unit or "").replace("％", "%")
    if u == "万元":
        return 1e4, "元"
    if u == "亿元":
        return 1e8, "元"
    if u == "个月":
        return 1.0, "月"
    if u == "周岁":
        return 1.0, "岁"
    if u == "日":
        return 1.0, "天"
    return 1.0, u


def _numbers(text: str) -> list[tuple[float, str, str]]:
    """抽数字并归一化：3 万元 / 30,000 元 -> (30000, 元)；同时过滤年份和列表编号。"""
    out: list[tuple[float, str, str]] = []
    seen: set[tuple[float, str]] = set()
    for m in _NUM_RE.finditer(text or ""):
        raw = m.group(1).replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        raw_unit = (m.group(2) or "").replace("％", "%")
        prev = text[max(0, m.start() - 1):m.start()]
        nxt = text[m.end():m.end() + 1]
        # 行首的 "1." "2、" 是列表编号，不是待核对的数字
        if nxt in ".．、" and v <= 30 and (not prev or prev in " \t" + NL):
            continue
        if nxt in "条章节款项":
            continue          # 「第2条」是条款引用，不是数量
        if nxt in "）)" and v <= 30:
            continue          # 「1）2）」是列表编号
        # 19xx/20xx 年 是政策年份，不是待核对的数量
        if raw_unit == "年" and 1900 <= v <= 2035 and abs(v - int(v)) < 1e-9:
            continue
        mult, unit = _canonical_unit(raw_unit)
        val = v * mult
        key = (round(val, 4), unit)
        if key in seen:
            continue
        seen.add(key)
        out.append((val, unit, m.group(0).strip()))
    # 「百分之二十二」这种写法：转成 22%
    for m in _PCT_CN_RE.finditer(text or ""):
        v = _cn2int(m.group(1))
        if not v:
            continue
        key = (round(float(v), 4), "%")
        if key in seen:
            continue
        seen.add(key)
        out.append((float(v), "%", m.group(0)))
    # 中文数字：原文写「三年」「五万元」时，回答写「3年」「50000元」也要能对上
    for m in _CN_NUM_RE.finditer(text or ""):
        v = _cn2int(m.group(1))
        if not v:
            continue
        mult, unit = _canonical_unit(m.group(2))
        val = v * mult
        key = (round(val, 4), unit)
        if key in seen:
            continue
        seen.add(key)
        out.append((val, unit, m.group(0).strip()))
    return out


def _basis(items: list[dict], chunks: list[dict]) -> tuple[list[dict], str]:
    """数字核验的比对基准：优先被引条款原文，其次本轮检索结果。"""
    ids = {i for it in items for i in it["matched_ids"]}
    cited = [c for c in (chunks or []) if c.get("chunk_id") in ids]
    if cited:
        return cited, "cited"
    return list(chunks or []), ("retrieved" if chunks else "none")


def check_grounding(answer_text: str, chunks: list[dict] | None, question: str = "") -> dict:
    """生成后核验入口：返回 {"citations": {...}, "numbers": {...}}。

    引用部分只解析回答原文里的 [出处: ...]，不读 used_citations() 的返回值——
    那个函数在解析失败时会退化成"按文件名匹配"，拿它来核对等于自己证明自己。
    """
    text = answer_text or ""
    chunks = chunks or []

    raw_cites: list[str] = []
    for blob in CITE_RE.findall(text):
        for part in re.split(r"[；;]", blob):     # 模型偶尔把两条引用塞进一个括号
            part = part.strip()
            if part:
                raw_cites.append(part)
    items = [_classify(b, chunks) for b in raw_cites]
    n_total = len(items)
    n_verified = sum(1 for i in items if i["status"] == "verified")
    citations = {
        "total": n_total,
        "verified": n_verified,
        "no_clause": sum(1 for i in items if i["status"] == "no_clause"),
        "clause_conflict": sum(1 for i in items if i["status"] == "clause_conflict"),
        "unmatched": sum(1 for i in items if i["status"] == "unmatched"),
        "ambiguous": sum(1 for i in items if i["ambiguous"]),
        "verify_rate": round(n_verified / n_total, 4) if n_total else None,
        "items": items[:20],
    }

    body = CITE_RE.sub("", text)
    body = _DOC_NO_RE.sub("", body)      # 文号里的年份/序号不是待核对的数字
    body = _CTX_LABEL_RE.sub("", body)   # 「资料2至资料4」是上下文指代
    body = _PAREN_YEAR_RE.sub("", body)  # 「资料1（2013）」里的年份是标签
    basis, basis_kind = _basis(items, chunks)
    known = {(round(v, 4), u) for v, u, _ in _numbers(" ".join(str(c.get("text") or "") for c in basis))}
    # 问题里已经出现的数字（如「我 42 岁」）是用户给的条件，不是模型的新断言
    asked = {(round(v, 4), u) for v, u, _ in _numbers(question or "")}
    found = [n for n in _numbers(body) if (round(n[0], 4), n[1]) not in asked]
    hit = [n for n in found if (round(n[0], 4), n[1]) in known]
    miss = [n for n in found if (round(n[0], 4), n[1]) not in known]
    numbers = {
        "total": len(found),
        "grounded": len(hit),
        "ungrounded": [n[2] for n in miss][:10],
        "rate": round(len(hit) / len(found), 4) if found else None,
        "basis": basis_kind,
        "low_confidence": basis_kind != "cited",
    }
    return {"citations": citations, "numbers": numbers}