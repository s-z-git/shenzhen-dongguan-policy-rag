# data 目录说明

| 路径 | 内容 | 是否入库 |
|---|---|---|
| `data/raw/` | 458 份政策原文 Markdown | 是 |
| `data/chunks.jsonl` | 条款块（build_index.py 生成） | 否（可重建） |
| `data/eval_gold.reviewed.v3.jsonl` | **73 条 v3 strict 正式评测集（当前口径）** | 是 |
| `data/eval_gold.clause.jsonl` | **24 条 clause-level gold（23 条双代理一致 + 1 条用户裁决）** | 是 |
| `data/eval_gold.boundary_ext.jsonl` | **12 条硬边界集（越域/错误城市/提示注入等）** | 是 |
| `data/eval_gold.safety_ext.jsonl` | **4 条 safety 边界（越权/个人事务/未来政策）** | 是 |
| `data/eval_gold.reviewed.jsonl` | 73 条旧口径（multi 83 点，保留对照） | 是 |
| `data/eval_report*.json` | 逐题生成/检索报告（含 `v3_final`） | 否（大文件，可重跑） |
| `data/l1_report*.json` | L1 逐题判分报告（含 `v3_final`） | 否（大文件，可重跑） |
| `data/rank_probe_*.json` | rank 分布实验明细 | 否（大文件，可重跑） |
| `data/l1_cache.jsonl` | L1 裁判缓存 | 否（本地缓存） |
| `data/annotation/` | 标注输入/输出、clause gold 原始 A/B、faithfulness 原始 A/B | 是（小文件） |
| `data/reports/` | Markdown 总结报告 | 是 |
| `data/traces/` | 运行时 trace JSONL | 否 |
| `data/memory.db` | 本地多轮会话 SQLite | 否 |

原则：**代码、脚本、Markdown 总结、小体积标注入库；大体积逐题 JSON、缓存、trace、会话库不入库。**

当前口径（P4）：`data/reports/latency_budget.md`、`data/reports/reasoning_ab.md`、`data/reports/streaming_ttft.md`。
索引：`data/reports/README.md`。
历史口径：`data/reports/v1.1_report.md`、`data/reports/v3_final_report.md`、`data/reports/v3_after_fix.md`；历史报告不改成当前数字。
