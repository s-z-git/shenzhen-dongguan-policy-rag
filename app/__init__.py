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
# HF_HOME 走 huggingface 自己的默认解析（env 优先，其次 ~/.cache/huggingface），
# 这里不再写死路径：曾经写死 r"F:\HF-Cache"，对 Linux/macOS 无意义，
# 且本机 HF_HOME 早在 User 作用域有值，那行 setdefault 实际从未生效。
os.environ.setdefault("HF_ENDPOINT", "https://huggingface.co")

# 默认离线：模型已在本地缓存时启动 26s -> 3s（实测）。
# 但**只在缓存目录真的存在时才设**——无条件离线会让没有缓存的机器（如干净 CI
# runner）报 LocalEntryNotFoundError，报错文本又长得像"连不上网"，很难查。
# 用户显式设置了 HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE 时一律以用户为准。
#
# 注意：这里**不能** import huggingface_hub 来探测缓存路径。constants 模块在
# import 的瞬间就把 HF_HUB_OFFLINE 读进常量了，先 import 再设环境变量等于没设
# （曾经这样写，结果 setdefault 成功但离线始终不生效）。所以按 HF 自己的规则
# 手工推导缓存目录：HF_HUB_CACHE > HF_HOME/hub > ~/.cache/huggingface/hub。
if os.environ.get("HF_HUB_OFFLINE") is None:
    _hf_home = os.environ.get("HF_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface"
    )
    _hf_hub_cache = os.environ.get("HF_HUB_CACHE") or os.path.join(_hf_home, "hub")
    _hf_hub_cache = os.path.expanduser(os.path.expandvars(_hf_hub_cache))
    if os.path.isdir(_hf_hub_cache):
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