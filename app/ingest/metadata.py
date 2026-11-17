"""
app.ingest.metadata —— 元数据组装 / 校验 / 落盘

对应 DESIGN.md 的 app/ingest/metadata.py 与 §4.6（元数据设计）。

每个 chunk 都带这些字段（后面检索、过滤、引用、时效提示全靠它们）：
    chunk_id / doc_id / city / issuer / year / doc_title /
    publish_date / source_url / clause_path / clause_no / text / chars
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import DEFAULT, Config
from app.ingest.clause_splitter import chunk_body
from app.ingest.loader import load_documents

NL = chr(10)


def build_chunks(meta: dict, body: str, doc_id: str, cfg: Config | None = None) -> list[dict]:
    """一份文档 -> 若干 chunk 记录。"""
    cfg = cfg or DEFAULT
    # 标题缺省用 doc_id；年份转成 int，失败统一记 0
    title = meta.get("doc_title") or doc_id
    year = meta.get("year", "")
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = 0

    out = []
    # 对正文切出的每一块 (条款路径, 文本) 组装一条完整 chunk 记录
    for i, (path, text) in enumerate(chunk_body(body, cfg)):
        clause_path = path or "全文"                # 无条款路径时记作“全文”
        out.append({
            "chunk_id": doc_id + "#" + str(i),      # 文档内唯一：doc_id + 序号
            "doc_id": doc_id,
            "city": meta.get("city", ""),
            "issuer": meta.get("issuer", ""),
            "year": year,
            "doc_title": title,
            "publish_date": meta.get("publish_date", ""),
            "source_url": meta.get("source_url", ""),
            "clause_path": clause_path,
            # 取最细一级编号（如 第八条 / （一） / 3．）作为“条款号”，用于出处展示
            "clause_no": clause_path.split(" > ")[-1] if path else "",
            "text": text,
            "chars": len(text),
            "ord": i,
        })
    return out


def build_corpus(cfg: Config | None = None, verbose: bool = True) -> tuple[list[dict], list[dict]]:
    """整个 raw_dir -> (chunks, docs)。"""
    cfg = cfg or DEFAULT
    docs = load_documents(cfg.raw_dir)              # 加载全部原始文档
    chunks, doc_stats = [], []
    # 遍历每份文档，切块并汇总每份文档的统计
    for d in docs:
        cs = build_chunks(d["meta"], d["body"], d["doc_id"], cfg)
        chunks.extend(cs)
        doc_stats.append({
            "doc_id": d["doc_id"], "body_chars": len(d["body"]), "n_chunks": len(cs),
            "city": d["meta"].get("city", ""), "year": d["meta"].get("year", ""),
        })
    # verbose 时输出整体校验统计信息
    if verbose:
        v = validate_chunks(chunks)
        print("[ingest] 文档 " + str(len(docs)) + " 份 -> 条款块 " + str(len(chunks)) + " 个")
        print("[ingest] 块长度: 平均 " + str(round(v["avg_chars"])) + " / 最短 "
              + str(v["min_chars"]) + " / 最长 " + str(v["max_chars"]))
        print("[ingest] 超上限块: " + str(v["n_over_max"]))
    return chunks, doc_stats


def citation(chunk: dict) -> str:
    """统一出处格式：文件名 + 条款号（DESIGN 的硬要求）。"""
    # 有条款号就拼“文件名（条款路径）”，否则只给文件名
    if chunk.get("clause_no"):
        return chunk["doc_title"] + "（" + chunk["clause_path"] + "）"
    return chunk["doc_title"]


def save_chunks(chunks: list[dict], path: Path | str) -> None:
    """按 JSONL 落盘（一行一个 chunk），方便用文本工具 diff 和抽查。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)     # 确保父目录存在
    # 每行写入一个 JSON 序列化后的 chunk，UTF-8 且保留中文可读
    with p.open("w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps(c, ensure_ascii=False) + NL)


def load_chunks(path: Path | str) -> list[dict]:
    """读回 JSONL。当前 4583 块约 7MB，常驻内存没问题。"""
    # 逐行解析，跳过空行
    with Path(path).open(encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def validate_chunks(chunks: list[dict], max_chars: int = 700) -> dict:
    """把“看起来没问题”变成可断言的数字 —— verify.py 直接用它。"""
    # 按字符数排序，便于统计分位数/极值
    lens = sorted(c["chars"] for c in chunks)
    n = len(lens) or 1                              # 防除零
    problems = []
    # 检查 chunk_id 是否重复
    ids = set()
    dup = 0
    for c in chunks:
        if c["chunk_id"] in ids:
            dup += 1
        ids.add(c["chunk_id"])
    # 检查 city 合法性与 clause_path 是否为空
    bad_city = sum(1 for c in chunks if c["city"] not in ("深圳", "东莞"))
    empty_path = sum(1 for c in chunks if not c.get("clause_path"))
    # 逐项收集问题描述
    if dup:
        problems.append("chunk_id 重复 " + str(dup) + " 个")
    if bad_city:
        problems.append("city 非法 " + str(bad_city) + " 个")
    if empty_path:
        problems.append("clause_path 为空 " + str(empty_path) + " 个")
    over = sum(1 for x in lens if x > max_chars)
    if over:
        problems.append("超过 " + str(max_chars) + " 字的块 " + str(over) + " 个")
    # 汇总成可读的统计字典
    return {
        "n": len(chunks),
        "avg_chars": sum(lens) / n,
        "median_chars": lens[n // 2] if lens else 0,
        "min_chars": lens[0] if lens else 0,
        "max_chars": lens[-1] if lens else 0,
        "n_over_max": over,
        "n_dup_id": dup,
        "n_bad_city": bad_city,
        "n_empty_clause": empty_path,
        "problems": problems,
    }