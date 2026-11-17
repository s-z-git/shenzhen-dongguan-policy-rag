# Clause-level gold 交叉标注审计（2026-11-06）

## 范围

- 题目：`data/annotation/clause_gold_tasks.jsonl`，24 条 fact 题。
- 标注员：两个独立 DeepSeek 子代理 A/B，只能从每题 `golden_docs` 内选 chunk。
- 原始输出：
  - `data/annotation/clause_gold_agentA.jsonl`
  - `data/annotation/clause_gold_agentB.jsonl`
- 合并结果：`data/eval_gold.clause.jsonl`（24 条）
- 用户裁决：`data/annotation/clause_gold_resolved.jsonl`

## 结果

- 24 条中 **23 条 chunk 集合完全一致**。
- 剩余 1 条“城乡居民养老保险...困难人员是不是按120元交？”由用户裁决：**B 正确，#1 已覆盖 120 元档位**，按 B 收录。
- 因此正式 clause gold 为 **24 条，全部已确认**，没有未决冲突。
- 24 条中：
  - 8 条 evidence 直接采用某个代理的原文摘录（A 4 条、B 4 条）；
  - 16 条因为代理 evidence 不是 chunk 原文的逐字子串，改为“chunk 首 100 字自动摘录”，`evidence_source=auto`。
  - **chunk ID 本身由双代理一致确认或用户裁决**；evidence 只作定位提示。

## 用户裁决记录

题目：

> 城乡居民养老保险每年缴费有几个档次可以选，困难人员是不是按120元交？

- A：2 个 chunk（#1 + #2）。
- B：1 个 chunk（#1），confidence 1.0。
- 用户判断：**#1 已覆盖答案，B 对**。
- 处理：按 B 的 #1 收录；裁决记录见 `data/annotation/clause_gold_resolved.jsonl`。

## 口径限制

- 这是 **LLM 交叉标注 + 用户裁决** 的 clause-level gold，不是法律意义上的全量金标。
- 24 条只覆盖 fact 子集，不能代表全部 73 条。
- 报告 clause Recall 时必须写明 `n=24, LLM cross-checked + user adjudication`。

## 产物

- `data/eval_gold.clause.jsonl`（24 条，正式诊断用）
- `data/annotation/clause_gold_resolved.jsonl`（1 条用户裁决记录）
- `data/reports/clause_gold_audit.md`（本文件）
