"""
入口：解析政策文件 -> 条款块 -> FAISS 索引

用法：python build_index.py
"""
from __future__ import annotations

from app.config import DEFAULT
from app.pipeline import RAGPipeline


def main() -> None:
    """解析 + 建索引入口（幂等，重跑会覆盖 chunks 与索引）。
    解析 + 建索引入口（幂等，重跑会覆盖 chunks 与索引）。"""
    pipe = RAGPipeline.build(DEFAULT, verbose=True)
    print("[done] chunks -> " + str(DEFAULT.chunks_path))
    print("[done] index  -> " + str(DEFAULT.index_dir))
    print("[done] 语料 " + str(len(pipe.chunks)) + " 条款块")


if __name__ == "__main__":
    main()