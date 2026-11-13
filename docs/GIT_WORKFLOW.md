# Git 提交与回滚流程

## 每次提交前

```powershell
python verify.py
python scripts/pre_commit_check.py
python scripts/test_citation_view.py
git diff --cached --check
# 只改检索/流式时再跑：
python scripts/check_stream_consistency.py
```

## 提交粒度

- 一个小功能 / 一个明确修复 = 一个提交。
- 不要把所有实验产物和大体积 JSON 混进代码提交。
- 提交信息前缀：`feat` / `fix` / `docs` / `test` / `refactor` / `chore`。

示例：

```powershell
git add app/memory.py scripts/setup_hooks.ps1
git commit -m "feat(memory): add SQLite session store"
```

## 里程碑打 tag

```powershell
git tag -a v0.4-memory -m "minimal multi-turn memory"
```

当前开发主线：P0-P4（2026-11-09），私有 `origin` 保留完整历史；公开仓库 `public/main` 使用单 commit 快照。v1.0/v1.1 只作为历史口径。

## 公开快照

```powershell
git checkout --orphan public-snapshot
git add -A
git ls-files | Select-String -Pattern "交接文档|AGENTS|DESIGN\.md|收口清单|W1-任务|读代码自测|^code/C8/|^docs/chapter8/|^docs/chapter9/|^docs/handover/|改造任务书"
git commit -m "Public release: <说明>"
git push --force public public-snapshot:main
git checkout main
git branch -D public-snapshot
```

敏感扫描必须无输出。不要对 public 使用 `git add -f`。

本地私有文档统一放 `docs/handover/`；`.playwright-cli/`、`__pycache__/`、`data/*.db.bak-*` 不入库。

## 回滚

未推送、保留改动：

```powershell
git reset --soft HEAD~1
```

未推送、彻底丢弃（危险，确认后再用）：

```powershell
git reset --hard <commit>
```

已推送、安全回滚：

```powershell
git revert <commit>
```

## 大文件边界

以下文件不入库，保留本地：

- `data/eval_report*.json`
- `data/l1_report*.json`
- `data/rank_probe_*.json`
- `data/l1_cache.jsonl`
- `data/traces/`
- `data/memory.db`

需要证据时提交 Markdown 总结（`data/reports/`），不提交逐题大 JSON。
