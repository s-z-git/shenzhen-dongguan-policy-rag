# CHANGELOG

## 2026-11-17 — A8：纯域外城市规则拒答

- `app/generate/query_analysis.py` 新增 `city_scope()`：规则词表识别域外城市（广东 21 地级市 + 直辖市 + 港澳 + 常见省会），`广东省` 省名显式剥离、不触发；混合城市（同时含深莞）不拒答。
- `pipeline._retrieve()` 在改写/分析前短路：纯域外城市 → 空检索结果 → 走既有无命中拒答路径；`ask` 与 `ask_stream` 共用，`status` 仍为 `ok`（不是 error），拒答会正常写入对话记忆。
- 结果新增 `out_of_scope_city`；`run_eval` 报告新增 `n_out_of_scope_refused`，`stats.py` 同步计数。
- A6 重跑（2026-11-17）：boundary 拒答 **1/2 → 2/2**（b02 广州由作答改为拒答；b01 香港改由规则短路），`n_out_of_scope_refused=2`，非 boundary 拒答 6/28 不变。
- **口径警告**：重跑对 30 题全部重新生成，引用类指标是另一组答案，**不与 2026-11-16 基线比较**（本文件不列第二组数）；唯一可归因的是 boundary 2/2 与 `n_out_of_scope_refused=2`。
- `generator.answer()`：无检索结果时不再先初始化 LLM 客户端——域外规则拒答在没有 `DEEPSEEK_API_KEY` 的环境也能工作。
- 测试：新增 `scripts/test_city_scope.py`（10/10）；既有 5 脚本全过；`verify.py` 4583 块通过。

## 2026-11-16 — A6 30 题基线（非 blind）

- 用冻结题集 `data/eval_gold.a6.jsonl`（30 主 + 2 追问，`acc06d7`）跑一次生成评测：`run_eval.py --gold-file ... --no-ablation`。
- 引用核验 **82.8%（77/93）**、条款级 **96.25%（77/80）**、无条款 17.2%、条款冲突 0、数字可溯源 96.59%（85/88）；`n_llm_errors=0`。
- 拒答：boundary **1/2**；与 `expect` 不一致 7/30（6 条应答却拒 + 1 条广州越域应拒却作答）。归因：f04/m07 出题超出语料、f06 检索失败、m01 检索失败、m05 语料缺口同名不同义、s04 结构性语料缺口（2026-11-17 M2 阶段探测，见 `worklog/A6-未归因-归因.md`）。
- 不报 Recall（无 `golden_docs`，报告里 `recall_at_k=0.0` / `citation_hit_rate=0.0` / `mrr=0.0` 均为占位值）；不报 P95（`run_eval` 最近秩 46,694ms vs `stats.py` 线性插值 47,604ms），只报 mean 26,847.77ms。
- Harness 独立复算 11 个数，10 个逐位一致，唯一差异是 P95 方法。
- 报告不入库：`scripts/check_public_wording.py` 会拦截报告里引用的政策原文（含"准确"等词）；副本在 `worklog/a6-report-20261116.json`，逐题归因见 `worklog/A6-result.md`，复跑命令见上。

## 2026-11-16 — LLM 调用失败不再当答案（A7）

- `generator.answer()` / `stream_answer()` 失败时不再把“调用 LLM 失败: …”当答案：返回 `status="error"` + `error_kind ∈ {balance, auth, timeout, network, other}`（402/Insufficient Balance → `balance`），`answer` 为空或仅保留已流出的部分文本。
- 流式首 token 前失败不再 `yield` 错误文本；失败分支显式 `citations=[]`，不再解析部分文本。
- `pipeline.ask()` / `ask_stream()` 在 `status=error` 时跳过 `check_grounding`（`grounding.status="skipped"`），trace 写 `status` + `error_kind`，引用/数字计数为 0。
- `run_eval.py`：错误行不进 `recs`；`summary.n_llm_errors / llm_error_rate`；报告新增 `errors`；失败占比 ≥20%（`LLM_ERROR_ABORT_RATIO`）时不产出报告并 `exit 1`。
- `stats.py`：新增 `n_llm_errors / llm_error_rate / refused_base`，拒答率只在非错误行上算（旧 trace 无 `error_kind` 视为无错误，历史数字不变）。
- `metrics.py` 三个 `aggregate_*` 入口各自剔除 `status=error`，防止从其它路径读 `records` 时污染延迟/引用指标。
- `ask.py`：生成失败的轮次不写入对话记忆，避免空 assistant 轮次进入下一轮 query rewrite。
- UI 错误卡新增 402 文案；`is_refusal()` 一行未改（实测错误文本本来就不判拒答）。
- `streamlit_app.py`：流式生成结束后补一次 `sink["error"]` 检查——原检查在开流之前，生成阶段失败时走正常分支，回放后是一条空白答案；实测坏 Key 提问修复前空白、修复后正常显示 401 错误卡。
- 新增 `scripts/test_llm_error.py`（10 条，10/10 PASS）；既有 4 脚本 + `verify.py`（4583 块）通过；真机正常提问 `status=ok`。

## 2026-11-15 — 两级引用口径（doc_kind + 条款级核验率）

- `verify.py` 新增 `build_doc_clause_index(chunks)`：全库预计算 `{doc_id: 是否有条款块}`（458 份文档，390 有 / 68 无），`RAGPipeline.__init__` 建一次，两处生产路径（`ask` / 流式）都传入。
- `_classify` 每条引用多一个 `doc_kind`（`clause_doc` / `file_only_doc`），**`status` 定级逻辑一律未改**。
- `check_grounding` 的 `citations` 汇总新增 `clause_doc` / `clause_verified` / `file_only_doc` 三个计数字段。
- `aggregate_grounding` 新增两个率：`citation_clause_verify_rate`（分母 = `clause_doc`，子集率）、`file_only_doc_citation_share`（分母 = 全部引用）。
- **分母为什么不能用召回块判**：早期版本用传入的召回块推断文档有没有条款块，召回只带回全文块 `#0`、条款块未召回时会把这份文档错标成无条款，把「召回漏了条款块」从分母里删掉，核验率虚高（`reasoning_default` 会从 95.41% 虚高到 96.89%）。已改为全库索引，并补 F3 回归测试（同时断言不传 index 会误判）。
- 三份报告重算（不改答案、不调 LLM），分母统一为「全部引用（含 unmatched）」：

  | 模式 | n | 全口径 | 文件级 | 条款级 | 无条款占比 |
  |---|---:|---:|---:|---:|---:|
  | `reasoning_default` | 228 | 82.02% | 100.00% | 95.41%（187/196） | 14.04% |
  | `reasoning_none` | 168 | 88.10% | 98.81% | 96.64%（144/149） | 10.12% |
  | `no_llm_analysis` | 163 | 87.73% | 98.77% | 95.17%（138/145） | 9.82% |

- **`citation_verify_rate` 全口径 82.02% 仍是主指标**，条款级 95.41% 是子集率，只作解释不作替代。
- README 旧标签「引用条款级可验证率 0.8202」属历史误称（0.8202 是全口径值），已改为「引用核验（全口径）」。
- 新增 `scripts/test_two_level_citation.py`（5 条）；`verify.py` 4583 块通过。

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
