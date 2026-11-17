# v1.0 收口评测报告（2026-11-03）

> 状态：项目一冻结版。代码提交 `927bb1e`，tag `v1.0`。
>
> 2026-11-06 修复后重跑见 `data/reports/v3_after_fix.md`；本报告保留为 v1.0 历史口径。
> 本报告是 2026-11-03 全量重跑的最终口径；11-01/11-02 的报告保留为历史记录，不覆盖。

## 1. 评测口径

- 评测集：`data/eval_gold.reviewed.v3.jsonl`，73 条（fact 28 / multi 25 / stale 15 / boundary 5）。
- 召回分母：n=68（boundary 5 条不计召回）。
- 回答 prompt：v2。
- multi：`run_eval.py` 按 `type=multi` 显式启用文档级选块；普通 CLI 默认仍关闭（方案 B）。
- L1 裁判：glm-4.5-air，gold 用 v3 strict 口径；boundary 不参与 L1。

## 2. 端到端结果

| 指标 | 最终值 | 说明 |
|---|---:|---|
| Recall@5 | **1.000**（95% CI 1.000–1.000） | n=68 |
| MRR | **0.8387**（95% CI 0.775–0.900） | n=68 |
| golden_coverage | **0.9706** | 多文档题平均覆盖比例 |
| full_recall | **0.9412（64/68）** | 全部 golden 都找到 |
| 拒答准确率 | **1.000（5/5）** | boundary 全对 |
| 引用文件级匹配率 | **1.000（230/230）** | 未匹配 0 条 |
| 引用条款级可验证率 | **0.7957（183/230）** | 47 条 no_clause；条款冲突 0 条 |
| 数字可溯源率 | **0.9815（266/271）** | 被引原文中可核对 |
| P95 延迟 | **51.43 s** | mean 30.06 s，LLM 调用主导 |

## 3. L1 答案要点覆盖率

| 指标 | 最终值 | 说明 |
|---|---:|---|
| L1 要点覆盖 | **0.8626（157/182）** | 题均覆盖 0.8740，95% CI 0.8167–0.9265 |
| L1 整题全中 | **0.7353（50/68）** | 95% CI 0.6324–0.8382 |

| 类型 | n | Recall@5 | MRR | golden_coverage | full_recall | L1 要点 | L1 整题全中 |
|---|---:|---:|---:|---:|---:|---:|---:|
| fact | 28 | 1.000 | 0.842 | 1.000 | 1.000 | 71/82（86.59%） | 21/28（75.00%） |
| multi | 25 | 1.000 | 0.848 | 0.920 | 0.840 | 54/57（94.74%） | 22/25（88.00%） |
| stale | 15 | 1.000 | 0.817 | 1.000 | 1.000 | 32/43（74.42%） | 7/15（46.67%） |
| boundary | 5 | — | — | — | — | — | 拒答 5/5 |

## 4. 必须保留的负面结论

1. **multi 本次是 54/57（94.74%）**，低于 11-02 子集重跑的 56/57（98.25%）。全量正式数字以本报告为准，不能拿最好那次宣传。
2. **引用条款级可验证率降到 0.7957**。原因是多主题模式会带回政策解读/全文块，这些块没有条款号；引用仍做到文件级 230/230 匹配，未匹配和条款冲突均为 0。
3. **L1 v3 strict 不能和旧 83 点口径直接比较**。本报告只使用审计后的 57 个 multi 要点。
4. 评测集是自建集，boundary 只有 5 条；拒答 5/5 的置信区间很宽。

## 5. 可复现命令

```powershell
python run_eval.py --gold-file data/eval_gold.reviewed.v3.jsonl --ablation-only --report-out data/eval_ablation.json
python run_eval.py --gold-file data/eval_gold.reviewed.v3.jsonl --no-ablation --prompt-version v2 --report-out data/eval_report.v3_final.json
python judge_l1.py --report data/eval_report.v3_final.json `
  --gold data/eval_gold.reviewed.v3.jsonl `
  --out data/l1_report_v3_final.json --cache data/l1_cache.jsonl `
  --model glm-4.5-air --fallback-model= --workers 4 --timeout 60
```

注意：2026-11-06 起默认已切到 `data/eval_gold.reviewed.v3.jsonl`（73 条）；要跑旧 9 条需显式传 `--gold-file data/eval_gold.jsonl`。

## 6. clean clone 与演示验收

- clean clone 先执行 `python build_index.py`（生成 4583 块），再执行 `python verify.py`：**68 项全 PASS**（2026-11-06 修复后为 77 项）。
- 引用定位截图：`docs/images/citation_highlight.png`。
- T5 验收时修复了 Streamlit rerun 后回答消失的问题：最近一次结果存入 `session_state`，切换引用不再丢回答。
- 已实测 43 号第二条、41 号第二条、42 号第二条/第五条，文档、条款路径和 `<mark>` 高亮均正确。

## 7. 产物

- 源码与文档：commit `927bb1e`，tag `v1.0`
- 逐题报告（不入库）：`data/eval_report.v3_final.json`
- L1 逐题报告（不入库）：`data/l1_report_v3_final.json`
- 消融汇总（入库）：`data/eval_ablation.json`
- 引用高亮截图（入库）：`docs/images/citation_highlight.png`
