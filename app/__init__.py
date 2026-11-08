"""
深莞人社政策智能问答系统（RAG）

模块划分与 DESIGN.md §3 一一对应：
    app/ingest/     正文抽取 + 条款切分 + 元数据
    app/index/      向量化 + 向量库（FAISS / Milvus 同一协议）
    app/retrieve/   混合检索 + RRF + 元数据过滤 + 精排
    app/generate/   查询分析 + Prompt + DeepSeek + 引用 + 时效提示
    app/eval/       评测指标 + 消融实验
    app/ui/         Streamlit

入口脚本在项目根目录：build_index.py / ask.py / run_eval.py / verify.py
"""
import io
import os
import sys

# ---- 环境引导：必须在导入 transformers / huggingface 之前执行 ----------------
os.environ.setdefault("HF_HOME", r"F:\HF-Cache")
os.environ.setdefault("HF_ENDPOINT", "https://huggingface.co")
# 模型已缓存 -> 默认离线（启动 26s -> 3s，实测）；要下载新模型时设 HF_HUB_OFFLINE=0
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# 控制台按 UTF-8 输出（Windows 默认 GBK，打印中文/emoji 会报 UnicodeEncodeError）
for _stream in (sys.stdout, sys.stderr):
    # 类型上 sys.stdout/err 是 TextIO，只有真实的 TextIOWrapper 才有 reconfigure
    if isinstance(_stream, io.TextIOWrapper):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

__version__ = "0.1.0"