# 环境与排错

## Windows / PowerShell

如果 PowerShell 报 `conda 不是命令`，或 `profile.ps1 被禁止加载`，不要继续用裸 `python` / `pip`。直接用环境里的 Python 全路径：

```powershell
$py = "F:\CondaData\envs\rag-c8\python.exe"
& $py --version
& $py -m pip install -r requirements.txt
```

## 数据与索引路径

FAISS 的 C++ 文件 API 写不了含中文的目录，因此索引默认放在纯 ASCII 目录：

```powershell
$env:REFRAG_DATA_DIR = "F:\refrag-data"
# $env:RAG_INDEX_DIR = "F:\refrag-data\index"
# $env:RAG_RERANK_MODEL = "F:\refrag-data\models\bge-reranker-base"
```

## 模型缓存

```powershell
$env:HF_HOME = "F:\HF-Cache"
```

模型已缓存时默认离线加载；需要下载新模型时再覆盖 `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE`。

## API Key

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
$env:ZHIPU_API_KEY = "..."
```

Key 只从环境变量读取，不写入仓库。


## 会话库（2026-11-13）

- `data/memory.db` 存 `sessions` + `messages(meta_json)`；删除该文件可重置全部会话。
- 首问才落库；只打开页面不会产生空会话。
- 旧版消息启动时会回填 `conclusion`；旧消息没有 `chunk_ids/citations`，依据卡为空是预期现象，不代表新会话有问题。
- 页面 URL 的 `?s=<session_id>` 可恢复会话；换一个 id 等于新会话。

## 完整本机流程

```powershell
$py = "F:\CondaData\envs\rag-c8\python.exe"
$env:REFRAG_DATA_DIR = "F:\refrag-data"
$env:HF_HOME = "F:\HF-Cache"

& $py build_index.py
& $py verify.py
& $py ask.py "东莞本科应届生入户后能领哪些补贴"
& $py -m streamlit run app/ui/streamlit_app.py
```

## trace 时间窗统计

`stats.py` 的 `--since/--until` 支持 ISO 时间；**只给日期时，`--until` 取当天 23:59:59**。

```powershell
# 统计 2026-11-08 当天
python stats.py --since 2026-11-08 --until 2026-11-08

# 统计 2026-11-08 10:00–12:00
python stats.py --since 2026-11-08T10:00:00 --until 2026-11-08T12:00:00
```
