"""提交前检查：不依赖第三方库。

检查项：
1. 暂存的 Python 文件语法可编译；
2. 暂存文件没有明显行尾空格；
3. 没有把大体积评测 JSON / 缓存 / trace 误加进暂存区。

用法：python scripts/pre_commit_check.py
"""
from __future__ import annotations

import py_compile
import subprocess
import sys
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024
FORBIDDEN_PARTS = (
    "data/eval_report.json",
    "data/eval_report.v1.json",
    "data/eval_report.v2",
    "data/l1_report",
    "data/rank_probe_",
    "data/l1_cache.jsonl",
    "data/traces/",
    "data/memory.db",
)


def staged_files() -> list[Path]:
    out = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        text=True, encoding="utf-8")
    return [Path(line) for line in out.splitlines() if line.strip()]


def main() -> int:
    files = staged_files()
    errors = []
    for path in files:
        name = path.as_posix()
        if any(part in name for part in FORBIDDEN_PARTS):
            errors.append("不应入库的大文件/缓存：" + name)
            continue
        if not path.exists():
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            errors.append("文件超过 2MB：" + name)
            continue
        if path.suffix == ".py":
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as exc:
                errors.append("语法错误：" + name + " -> " + str(exc))
        if path.suffix in {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".ps1"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(text.splitlines(), 1):
                if line.rstrip() != line:
                    errors.append("行尾空格：" + name + ":" + str(lineno))
                    break
    if errors:
        print("[pre-commit] 检查失败：")
        for e in errors:
            print(" - " + e)
        return 1
    print("[pre-commit] 通过，共检查 " + str(len(files)) + " 个暂存文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
