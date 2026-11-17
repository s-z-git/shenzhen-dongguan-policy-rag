# Faithfulness 交叉抽检（2026-11-06）

## 样本

- 来源：`data/eval_report.v3_v1.1.json`
- 抽样：fact / multi / stale 各取前 10 条非拒答回答，共 30 条。
- 这是固定样本，不是随机抽样；结论不能外推为“整体 100% 忠实”。
- 输入：`data/annotation/faithfulness_tasks.jsonl`（query + answer + 本轮检索 sources + grounding）。

## 结果

两个独立 DeepSeek 子代理 A/B 逐条对照 sources 判断：

| 判定 | A | B | 一致 |
|---|---:|---:|---:|
| supported | 30 | 30 | **30/30** |
| partial | 0 | 0 | 0 |
| unsupported | 0 | 0 | 0 |

- 两个代理对 30/30 条判定完全一致，没有需要人工裁决的冲突。
- 未发现无依据的金额、比例、期限、对象、条件或因果关系。
- 所有“资料未载明 X”类回答，在 sources 确实未载明时被判为正确的保守回答。

## 与机械核验的交叉点

机械 grounding 在“公司发工资是否需要工资台账”这条上标了一个未溯源数字 `2`；两名 LLM 审核员都判 supported。用户确认 **`2` 是分点/上下文编号，不是数量断言**。

处理：

- 扩展 `_CTX_LABEL_RE`，排除 `资料1/2`、`资料2至资料4`、`资料1、2` 等上下文标签；
- 保留并补测 `2. 是` 这类分点编号过滤；
- 重算 v1.1：`n_numbers` **262 → 261**，`number_grounded_rate` **0.9733 → 0.9770**；
- 新增回归：`资料1/2 不作为数字断言`、`分点编号不作为数字断言`。

位置记录（供追溯）：

- 任务：`data/annotation/faithfulness_tasks.jsonl` 第 6 行；
- 报告：`data/eval_report.v3_v1.1.json` 第 7 条记录（0-based index 6），原 `records[6].grounding.numbers.ungrounded == ["2"]`；
- 操作清单：`data/reports/manual_check_2026-11-06.md` 第 2 节。

## 口径限制

- 这是 **LLM 交叉审核**，不是人工金标。
- 样本是每类前 10 条，可能偏向早期问题；不能替代全量 68 条人工抽检。
- 建议明天人工只看：
  1. 上述 `2` 的机械核验点；
  2. 随机再抽 5 条 stale 回答，确认“废止/失效”判断没有过度推断。

## 产物

- `data/annotation/faithfulness_agentA.jsonl`
- `data/annotation/faithfulness_agentB.jsonl`
- `data/annotation/faithfulness_merged.jsonl`
- `data/reports/faithfulness_audit.md`（本文件）
