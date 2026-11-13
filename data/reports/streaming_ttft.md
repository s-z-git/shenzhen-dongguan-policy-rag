# 流式输出 TTFT 报告（2026-11-08）

## 口径

- **生成首字**：从调用 `llm.stream()` 到第一个非空 token，不含查询分析和检索。
- **端到端首字**：从 `ask_stream()` 开始（含分析 + 检索）到第一个非空 token。
- 快速模式：`RAG_REASONING_EFFORT=none`。
- 样本：`data/reports/ttft_sample.jsonl` 的 5 条真实问题，CPU 精排开启，warm pipeline，单次运行。
- 样本构成：含 1 条 boundary 拒答、1 条 stale；生成首字统计包含拒答首字，不能只按知识问答解读。

## 快速模式实测

| 指标 | P50 | P95 | 均值 |
|---|---:|---:|---:|
| 生成首字 | **715.55ms** | **977.74ms** | 770.54ms |
| 端到端首字 | **6780.16ms** | **9596.37ms** | 7049.00ms |
| 总耗时 | **7157.28ms** | **9597.63ms** | 7658.54ms |

分阶段 P50：analysis 846ms、retrieval 4884ms、generation 1237ms、grounding 0.94ms。

结论：**生成阶段 TTFT < 1s**；端到端首字仍受检索/精排约 5s 限制。UI 用 `st.status` 覆盖检索等待，避免这段时间白屏。

## 高质量模式单样本

同一问题（深圳失业保险金标准）：

| 指标 | 值 |
|---|---:|
| 生成首字 | 5065ms |
| 端到端首字 | 12732ms |
| 总耗时 | 13057ms |
| reasoning token 占比 | 89.5% |

## 对照口径

- **同题集对照（73 条）**：P0 A/B 的 default mean 28.81s → none 9.59s，即 **33.3%**，满足“总耗时不超过改造前 60%”。
- **流式路径量级（5 条样本）**：mean 7.66s、P50 7.16s。这个数字只说明流式链路量级，不能和 73 条的 28.81s 直接相除。

## 复现样本

5 条问题已提交到 `data/reports/ttft_sample.jsonl`：

1. 深圳失业保险金标准是多少？
2. 东莞市最低工资标准是多少？
3. 东莞本科应届生入户后能领哪些补贴？
4. 深圳新引进博士人才生活补贴的标准是多少
5. 怎么做红烧肉

复跑时逐条调用 `ask_stream(..., sink=sink, trace_path=<local-trace>)`，再执行：

```powershell
$env:REFRAG_DATA_DIR = "F:\refrag-data"
$env:RAG_REASONING_EFFORT = "none"
& "python" stats.py --path <local-trace>
```

trace 字段：`first_token_ms`（生成首字）、`ttft_ms`（端到端首字）、`timings_ms.generation`、`usage`。
