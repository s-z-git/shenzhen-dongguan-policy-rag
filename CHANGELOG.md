# CHANGELOG

## 2026-11-13 — UI 改造：会话 + 四层答案

- 会话：`sessions` + `messages(meta_json)`；首问才落库，`?s=` 刷新恢复，多轮消息全量回放。
- 答案：时效条 → 结论 → 要点 → 依据；依据默认折叠；`[出处]` 转 `[n]` 角标；同文件同条款去重。
- 特殊态：超范围 / 资料不足 / 错误状态卡；拒答判定从「有无检索结果」改为「有无引用」。
- 旧消息：启动时回填 `conclusion`，避免 `short_history` 退回整段正文。
- 文件区：探针 → `scripts/experiments/`，历史文档移出公开快照；`verify.py` 等入口脚本不动。
- 检索 / prompt / 评测口径未改；`verify.py` 4583 块通过。

## 2026-11-12 — 拒答判定修复

- 修复 `is_refusal()`：只要正文带可核验出处，就不再判为整题拒答。2026-11-09 报告里 8 条非 boundary 误判（fact 2 / multi 2 / stale 4）全部带 1 条引用，5 条 boundary 全是 0 引用。
- `verify.py` 新增 8 条真实误判样本的回归断言，自检从 93 项增加到 94 项。

## 2026-11-09 — P4 发布收尾

- README 首屏重写：一句话定位、架构图、4 张 UI 截图、default/none 核心数字、技术栈、3 行快速开始。
- 新增 `docs/architecture.png`、`docs/images/ui-*.png`、`docs/troubleshooting.md`。
- 新增 `CHANGELOG.md`，把版本历史移出 README 首屏；DESIGN、代码导读、数据目录和 Git 流程同步到当前口径。
- 公开快照更新为 P4；About 改为 current default/none 口径，不再使用旧 0.985。

## 2026-11-09 — 架构审计整改

- `ask()` 改为复用 `_retrieve()`，消除同步/流式检索编排分叉。
- `stats.py` 增加 `--since/--until`，延迟报告可用固定时间窗复现。
- README 合并重复快速开始、移走本机路径说明、标注 v3 boundary 5/5 与扩展 12/12。
- `latency_budget.md` 增加滚动样本快照说明；`streaming_ttft.md` 标注样本含 1 条拒答、1 条 stale。
- 修复 `stats.py` 日期型 `--until`：只给日期时覆盖当天 23:59:59；新增 `scripts/test_stats_time.py`。
- 新增 `scripts/test_citation_view.py`（5 项纯函数测试）和 `scripts/check_stream_consistency.py`（3/3 检索一致性）。
- public 仓库不发布内部 Git 流程文档。

## 2026-11-08 — P0-P3

- P0：新增 `RAG_REASONING_EFFORT`，完成 reasoning default/none A/B；产品默认保持 default，none 作为快速模式。
- P1：Streamlit 改为先渲染后加载；增加快速/高质量开关、示例、一句话结论、核验徽章、出处卡片。
- P2：新增 `ask_stream()`、`build_answer_prompt()`、`stream_answer()`；UI 使用 status + 流式输出，核验在流结束后执行。
- P3：新增延迟预算、纯规则查询分析对照、ask/ask_stream 检索一致性检查；删除 Milvus 空壳。

## v1.1 — 2026-11-06

- 增加原问题直检锚点、`year_lte`、clause-level 指标、硬/安全边界集、warmup 与配置可移植性。
- 正式报告：`data/reports/v1.1_report.md`。

## v1.0 — 2026-11-03

- 73 条 v3 全量评测、clean clone、引用高亮验收完成。
- 历史报告：`data/reports/v3_final_report.md`。
- v1.0 的 Recall@5 1.000 是旧口径；修复拒答/引用匹配后口径变严，v1.1 为 0.9853（67/68），不是代码退化。
