"""
app.ingest.loader —— 原始文件 -> (元数据, 正文)

对应 DESIGN.md 的 app/ingest/loader.py。

政策文件是本项目自己采集的 Markdown，文件头是手写 `key: value`（不是标准 YAML），
所以自己解析，不引入 pyyaml 依赖。
"""
from __future__ import annotations

import re
from pathlib import Path

# 一级标题，如 `# 一级标题`
_H1 = re.compile(r"^\s*#\s+")
# “附件2.转发...pdf”这类只指向附件的行，不是正文内容
_NOISE = re.compile(r"^\s*(附件\s*\d*\s*[\.、]?\s*.*\.(pdf|docx?|xlsx?|zip|rar))\s*$", re.I)


def split_front_matter(text: str) -> tuple[dict, str]:
    """拆出文件头。返回 (meta, body)；没有文件头时返回 ({}, 原文)。"""
    text = (text or "").lstrip("\ufeff")
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    # 文件头必须以 `---` 开头，且至少有一行正文；如果不是，就认为没有文件头
    if not lines or lines[0].strip() != "---":
        return {}, text
    # 文件头结束标记是 `---`，之后是正文
    for i in range(1, len(lines)):
        if lines[i].strip() == "---": # 文件头结束标记
            meta: dict = {}
            for ln in lines[1:i]: # 解析文件头的每一行
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    meta[k.strip()] = v.strip() # 去掉前后空格
            return meta, chr(10).join(lines[i + 1:])
    return {}, text


def clean_body(body: str, doc_title: str = "") -> str:
    """去掉重复的一级标题、附件指针行、多余空行，以及与标题完全重复的首行。"""
    keep = []
    for ln in body.splitlines():
        if _H1.match(ln):
            continue
        # “附件2.xxx.pdf”这类只是指向附件的行，不是正文内容
        if _NOISE.match(ln):
            continue
        keep.append(ln.rstrip()) # 去掉行尾空格
    lines = chr(10).join(keep).splitlines()
    # 去掉与标题完全重复的首行
    while lines and lines[0].strip() in ("", doc_title):
        lines.pop(0) # 去掉首行

    txt = chr(10).join(lines) # 重新拼接成文本
    txt = re.sub(chr(10) + "{3,}", chr(10) + chr(10), txt) # 连续空行压缩成一个
    return txt.strip()


def load_documents(raw_dir: Path) -> list[dict]:
    """读取目录下所有 .md，返回 [{doc_id, meta, body}]（body 已清洗）。"""
    files = sorted(Path(raw_dir).glob("*.md"))
    if not files:
        raise FileNotFoundError("找不到政策文件: " + str(raw_dir))
    docs = []
    for f in files:
        meta, body = split_front_matter(f.read_text(encoding="utf-8"))
        title = meta.get("doc_title") or f.stem # 优先使用文件头的 doc_title，没有就用文件名
        docs.append({"doc_id": f.stem, "meta": meta, "body": clean_body(body, title)})
    return docs