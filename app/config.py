"""
app.config —— 全局配置（路径 / 模型 / 检索参数）

对应 DESIGN.md 的 app/config.py。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

NL = chr(10) # 换行符

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

# 为什么索引不放在项目目录下：FAISS 的 C++ 文件 API 用窄字符路径，
# **写不了含中文的目录**（实测 F:\VS Code\RAG项目实战\... 写入失败，
# 报 could not open ... No such file or directory）。
# 所以索引单独放到纯 ASCII 目录，可用环境变量 RAG_INDEX_DIR 覆盖。
REFRAG_DATA_DIR = Path(os.environ.get("REFRAG_DATA_DIR", r"F:\refrag-data"))
INDEX_DIR = Path(os.environ.get("RAG_INDEX_DIR", str(REFRAG_DATA_DIR / "index")))


def _env_int(name: str, default: str) -> int:
    """读整数环境变量；非法值回退默认，避免 import 阶段直接崩掉。"""
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def _env_bool(name: str, default: str = "1") -> bool:
    """读布尔环境变量；1/true/yes/on 视为 True。"""
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    # ---- 数据源 ----
    raw_dir: Path = DATA_DIR / "raw"

    # ---- 产物 ----
    data_dir: Path = DATA_DIR
    chunks_path: Path = DATA_DIR / "chunks.jsonl"
    index_dir: Path = INDEX_DIR
    # v3 是当前正式口径；旧 9 条评测集需显式传 --gold-file。
    eval_gold: Path = DATA_DIR / "eval_gold.reviewed.v3.jsonl"
    eval_report: Path = DATA_DIR / "eval_report.v3_final.json"

    # ---- 嵌入 / 精排 ----
    embed_model: str = os.environ.get("RAG_EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
    embed_device: str = os.environ.get("RAG_EMBED_DEVICE", "cpu")  # 本机 16GB 内存是瓶颈，先 cpu
    rerank_model: str = os.environ.get(
        "RAG_RERANK_MODEL", str(REFRAG_DATA_DIR / "models" / "bge-reranker-base"))  # "" = 关精排

    # ---- LLM ----
    llm_model: str = os.environ.get("RAG_LLM_MODEL", "deepseek-flash")
    llm_base_url: str = os.environ.get("RAG_LLM_BASE_URL", "https://api.deepseek.com")
    # v1=原始 prompt；v2=逐子问题回答 + 引用只取标题行。A/B 后默认 v2，可用 --prompt-version v1 回退。
    answer_prompt_version: str = os.environ.get("RAG_PROMPT_VERSION", "v2")
    temperature: float = 0.1
    max_tokens: int = 1500
    # "default" = 不传 reasoning_effort（产品默认，复现 v1.1 口径）；
    # "none" = 关闭思考（快速模式）。A/B 时必须显式指定环境变量。
    reasoning_effort: str = os.environ.get("RAG_REASONING_EFFORT", "default")

    # ---- 切分 ----
    chunk_max_chars: int = 700
    chunk_min_chars: int = 120
    window_overlap: int = 120

    # ---- 检索 ----
    vector_top_k: int = 20
    bm25_top_k: int = 20
    rrf_k: int = 60
    final_top_k: int = 5
    # 最终 top-k 的文档多样性上限：0=不限制，1=每个 doc 最多 1 个 chunk。
    # 默认关闭以保持旧行为；评测/演示可用 RAG_DOC_CAP=1 或 --doc-cap 1 开启。
    doc_cap: int = _env_int("RAG_DOC_CAP", "0")
    # 多子问题检索时启用“文档级选择 + 按问题类型选块”。
    # 默认 0，验证通过后再切默认；可用 RAG_MULTI_DOC_SELECT=1 做 A/B。
    multi_doc_select: bool = _env_bool("RAG_MULTI_DOC_SELECT", "0")

    # ---- trace / 会话记忆 ----
    trace_enabled: bool = _env_bool("RAG_TRACE_ENABLED", "1")
    trace_path: Path = DATA_DIR / "traces" / "ask.jsonl"
    memory_db: Path = DATA_DIR / "memory.db"

    # ---- 政策时效 ----
    stale_years: int = 3

    def ensure_dirs(self) -> None:
        """创建数据目录与索引目录（索引目录可能位于项目之外，原因见 INDEX_DIR 的注释）。
        创建数据目录与索引目录（索引目录可能位于项目之外，原因见 INDEX_DIR 的注释）。"""
        for p in (self.data_dir, self.index_dir):
            p.mkdir(parents=True, exist_ok=True)


DEFAULT = Config()