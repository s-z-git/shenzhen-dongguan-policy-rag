# 人工复核清单（2026-11-06）

## 1. Clause gold 冲突：已按用户裁决关闭

- 题目：`城乡居民养老保险每年缴费有几个档次可以选，困难人员是不是按120元交？`
- 用户判断：**B 正确，#1 已覆盖 120 元档位。**
- 处理：
  - 按 B 的 `#1` 收录进 `data/eval_gold.clause.jsonl`；
  - 裁决记录：`data/annotation/clause_gold_resolved.jsonl`；
  - clause gold 变为 **24 条**；
  - clause Recall **0.9583**、clause MRR **0.8139**、coverage **0.9583**。
- 审计：`data/reports/clause_gold_audit.md`。

## 2. Faithfulness 里的数字 `2`：已确认是分点编号

- 用户判断：**`2` 指分点编号，不是数量断言。**
- 处理：
  - 扩展 `_CTX_LABEL_RE`，把 `资料1/2`、`资料2至资料4`、`资料1、2` 等上下文标签排除；
  - 保留并补测 `2. 是` 这类分点编号过滤；
  - 重算 v1.1 数字核验：`n_numbers` **262 → 261**，`number_grounded_rate` **0.9733 → 0.9770**；
  - 新增回归：
    - `资料1/2 不作为数字断言`；
    - `分点编号不作为数字断言`。
- 位置记录（供追溯）：
  - 原题目/回答：`data/annotation/faithfulness_tasks.jsonl` 第 6 行；
  - 原报告记录：`data/eval_report.v3_v1.1.json` 第 7 条（0-based index 6），`records[6].grounding.numbers.ungrounded` 原为 `["2"]`；
  - 审计说明：`data/reports/faithfulness_audit.md` 的「与机械核验的交叉点」。

## 3. 目前没有未决冲突

- Faithfulness 30/30 两个代理一致 supported；没有 A/B 冲突。
- 硬边界 12/12；safety 4/4 安全部分回答。
