# reasoning_effort A/B 报告（2026-11-08）

## 结论

这次 A/B 证明 `none` 能解决延迟大头，但还不能无条件下沉为产品默认：

- **延迟**：mean 28.81s → 9.59s（-66.7%），P95 53.77s → 12.56s（-76.6%）；reasoning token 占 output 从 91.32% → 0%。
- **质量**：L1 要点覆盖 0.8736 → 0.8516（-2.20pp，满足“下降 ≤3pp”）；但数字可溯源率 0.9785 → 0.9429（-3.56pp），引用命中率 -1.47pp、引用精度 -1.97pp，stale 分层 L1 下降 6.98pp，且多出 2 条完全无引用的回答。
- **判定**：因“引用/数字不降”不成立，**产品默认保持 `default`**；`RAG_REASONING_EFFORT=none` 仅作为显式快速模式。等后续 UI 做开关时，必须标注“更快，但数字核验和 stale 题可能下降”。

`none` 并非全面变差：multi L1 反而从 0.8947 升到 0.9298（+3.51pp），引用条款级可验证率从 0.8202 升到 0.8810（+6.08pp），无条款号引用比例从 17.54% 降到 10.12%。问题集中在数值保真、fact/stale 和引用覆盖率，按任务书门槛不能忽略。

## 两列数字

同一套 73 条 v3 gold，其他参数相同，只切换 `RAG_REASONING_EFFORT`。L1 为 `glm-4.5-air` 分别裁判两轮，各 68 题 / 182 个要点，0 失败；`none` 有 1 题答案与 `default` 完全相同，命中同答案缓存。

| 指标 | default（产品默认） | none（快速模式） | 变化 |
|---|---:|---:|---:|
| Recall@5 | 0.9559 | 0.9706 | +1.47pp |
| MRR | 0.8002 | 0.7934 | -0.69pp |
| clause Recall@5 | 0.9167 | 0.9167 | +0.00pp |
| L1 要点覆盖 | 0.8736 | 0.8516 | -2.20pp |
| L1 整题全中 | 0.7353 | 0.7353 | +0.00pp |
| L1 fact | 0.9024 | 0.8659 | -3.65pp |
| L1 multi | 0.8947 | 0.9298 | +3.51pp |
| L1 stale | 0.7907 | 0.7209 | -6.98pp |
| 引用命中率 | 0.8824 | 0.8676 | -1.47pp |
| 引用精度 | 0.7015 | 0.6818 | -1.97pp |
| 引用条款级可验证率 | 0.8202 | 0.8810 | +6.08pp |
| 无引用回答数 | 0 | 2 | +2 |
| 数字可溯源率 | 0.9785 | 0.9429 | -3.56pp |
| 硬边界拒答 | 5/5 | 5/5 | 0 |
| mean 延迟 | 28.81s | 9.59s | -19.22s |
| P50 延迟 | 24.15s | 9.79s | -14.37s |
| P95 延迟 | 53.77s | 12.56s | -41.20s |
| output tokens（73 题合计） | 216312 | 16927 | -199385 |
| reasoning token 占比 | 91.32% | 0.00% | -91.32pp |

## 复现命令

```powershell
$py = "python"
$env:REFRAG_DATA_DIR = "F:\refrag-data"
$env:HF_HOME = "F:\HF-Cache"

$env:RAG_REASONING_EFFORT = "default"
& $py run_eval.py --gold-file data/eval_gold.reviewed.v3.jsonl `
  --report-out data/eval_report.reasoning_default.json

$env:RAG_REASONING_EFFORT = "none"
& $py run_eval.py --gold-file data/eval_gold.reviewed.v3.jsonl `
  --report-out data/eval_report.reasoning_none.json

# 本机 glm-5.3 余额不足（HTTP 429），两轮统一改用 glm-4.5-air；
# 主裁判失败时降级到 glm-4-flash。本次两轮均未触发降级。
& $py judge_l1.py --report data/eval_report.reasoning_default.json `
  --gold data/eval_gold.reviewed.v3.jsonl `
  --out data/l1_report.reasoning_default.json `
  --model glm-4.5-air --fallback-model glm-4-flash

& $py judge_l1.py --report data/eval_report.reasoning_none.json `
  --gold data/eval_gold.reviewed.v3.jsonl `
  --out data/l1_report.reasoning_none.json `
  --model glm-4.5-air --fallback-model glm-4-flash
```

## 口径与限制

- P50/P95 直接取 `records[].latency_ms`（`pipeline.ask()` 端到端），73 条含 boundary；与 `aggregate_retrieval` 的 P95 一致。
- reasoning token 占比 = `sum(output_token_details.reasoning) / sum(output_tokens)`，来自报告内 `records[].usage`。
- 这是同一套 73 条 gold 上的单次重跑，不是 blind holdout；模型生成存在波动，不能把小差异当成稳定结论。
- `data/eval_report.reasoning_*.json` 与 `data/l1_report.reasoning_*.json` 是本地评测产物，按 `.gitignore` 不入库；本 md 入库。
