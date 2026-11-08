# 延迟预算与检索口径（2026-11-08）

## 1. 当前分阶段耗时

> 数据快照：2026-11-08 本地 trace。`--last 200` 是滚动样本，后续 trace 增长后会漂移；固定窗口请用 `stats.py --since/--until`。

`python stats.py --last 200`（本地 trace，混合包含旧 default 与新 none/流式记录）：

| 阶段 | P50 | P95 |
|---|---:|---:|
| analysis | 1723.54ms | 4470.64ms |
| retrieval | 6409.78ms | 9533.43ms |
| generation | 2949.37ms | 34235.38ms |
| grounding | 0.61ms | 2.87ms |
| total | 12358.28ms | 46854.54ms |

这 200 条的 mean total 为 17722.36ms；reasoning token 占 output 的 84.08%。这是混合口径，不能直接当作 P0 产品模式或 P2 流式模式的单一指标。

P0 A/B 的 default 73 条口径中，reasoning token 占 output **91.32%**；任务书早期记录为约 91.6%。

## 2. P2 流式 TTFT 口径

必须区分两个数：

| 指标 | P50 | P95 | 含义 |
|---|---:|---:|---|
| 生成阶段首字 | **715.55ms** | **977.74ms** | 从调用 `llm.stream()` 到第一个非空 token，不含分析/检索 |
| 端到端首字 | **6780.16ms** | **9596.37ms** | 从 `ask_stream()` 开始（含分析+检索）到第一个非空 token |
| 流式总耗时 | **7157.28ms** | **9597.63ms** | 5 条快速模式样本，mean 7658.54ms |

**P2 达成的是生成阶段首字 <1s；端到端首字仍约 6.8s**，瓶颈是检索/精排约 4.9s。UI 用 `st.status` 覆盖检索等待，避免用户看到白屏。

高质量模式单样本：生成首字 5065ms、端到端首字 12732ms、总耗时 13057ms。

## 3. 优化顺序（不能反）

1. **关思考**：只改 `reasoning_effort`，不改检索、切分、gold；召回不变，只影响生成延迟/质量。
2. **流式 + 阶段进度**：只改 UI 呈现，不改检索；先让用户看到检索状态，再逐字生成。
3. **精排/检索优化**：会改变 retrieved_chunks，必须重跑评测并做 A/B；不能在未验证前默认改 top-k 或关闭精排。

前两步是 P0/P2 的范围；第三步属于 P3 之后的新实验，不能把 P2 的 TTFT 数字当成“端到端 3 秒已完成”。

## 4. 纯规则查询分析对照列

新增开关：`run_eval.py --no-llm-analysis`。本次运行参数：

```text
RAG_REASONING_EFFORT=none
--no-llm-analysis --no-ablation
```

同一套 73 条 gold，检索指标如下（旧报告数字不改）：

| 指标 | LLM 查询分析（P0 none） | 纯规则查询分析（新增） | 变化 |
|---|---:|---:|---:|
| Recall@5 | 0.9706 | 0.9706 | 0 |
| MRR | 0.7934 | **0.8289** | +3.55pp |
| clause Recall@5 | 0.9167 | **1.0000** | +8.33pp |
| clause MRR | 0.7722 | **0.8556** | +8.34pp |
| golden_coverage | 0.9559 | 0.9338 | -2.21pp |
| full_recall | 0.9412 | 0.8971 | -4.41pp |
| citation_verify_rate | 0.8810 | 0.8773 | -0.37pp |
| number_grounded_rate | 0.9429 | 0.9270 | -1.59pp |
| mean / P95 延迟 | 9.59s / 12.56s | 8.98s / 12.45s | 略降 |

解读：纯规则查询分析在这套 gold 上提高了排序（MRR/clause），但降低了 golden coverage/full recall 和数字可溯源率。因此它只能作为对照列，不能直接替代默认 LLM 查询分析。

报告：`data/eval_report.no_llm_analysis.json`（本地生成物，不入库）。

## 5. 成本结论

按 v1.1/任务书记录：RAG 一次问答平均 **4215 tokens**，其中 **91.6% 是 reasoning token**；这是它比 NL2SQL 慢约 **13 倍**的主要原因。P0 关思考后 output token 大幅下降，P2 流式改善的是首字体感，不是检索本身的延迟。

## 6. 复现

```powershell
$env:REFRAG_DATA_DIR = "F:\refrag-data"
python stats.py --last 200

$env:RAG_REASONING_EFFORT = "none"
python run_eval.py --gold-file data/eval_gold.reviewed.v3.jsonl `
  --no-llm-analysis --no-ablation `
  --report-out data/eval_report.no_llm_analysis.json
```
