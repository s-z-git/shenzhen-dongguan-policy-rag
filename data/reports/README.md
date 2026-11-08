# reports 索引

## 当前口径（P4，2026-11-09）

| 文件 | 用途 |
|---|---|
| `latency_budget.md` | 分阶段 P50/P95、TTFT 口径、纯规则查询分析对照、优化顺序 |
| `reasoning_ab.md` | reasoning default/none 的 73 条 A/B，两列质量与延迟 |
| `streaming_ttft.md` | 5 条样本的生成首字/端到端首字/总耗时 |
| `ttft_sample.jsonl` | TTFT 复现样本问题 |

## 历史评测（保留原数字，不随当前代码改写）

| 文件 | 口径 |
|---|---|
| `v1.1_report.md` | v1.1 正式报告；0.9853 等数字是历史口径 |
| `v3_final_report.md` | v1.0 收口，1.000 是旧口径 |
| `v3_after_fix.md` | 修复拒答/引用匹配后的第一轮对照 |
| `v2_eval_report.md` | v2 prompt 早期对照 |
| `prompt_ab_report.md` | v1/v2 prompt A/B |
| `repeat_summary.md` | 同代码三轮重复评测；MRR 0.8331–0.8343 |

## 专项审计

- `clause_gold_audit.md`：24 条 clause gold 审计
- `faithfulness_audit.md`：引用/数字忠实度抽检
- `multi_doc_select_report.md`、`multi_point_audit.md`、`multi_full_diagnosis.md`、`multi_retrieval_fix_trial.md`：multi 检索专项
- `rank_probe_report.md`：rank 分布探测
- `strict_l1_v3.md`、`l1_spotcheck.md`、`l1_spotcheck_summary.md`：L1 抽检
- `manual_check_2026-11-06.md`：人工核验记录

原则：历史报告只追加说明，不把旧数字改成当前数字。
