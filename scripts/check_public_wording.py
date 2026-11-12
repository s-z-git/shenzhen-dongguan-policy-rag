"""检查公开仓库是否会带入计划性、面试或简历类措辞。

推送到公开仓库时由 .githooks/pre-push 调用。检查范围是说明文档、报告和
代码；不扫描 data/raw、data/annotation 等政策原文与评测数据，避免误伤
“求职创业补贴”“鹏城英才计划”这类正式名称。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 只匹配项目叙述中的话术，不把政策名称里的“计划”“求职”当违规
PATTERN = re.compile(
    r"简历|面试|秋招|春招|任务清单|交接文档|收口清单|路线图|里程碑|下一步|计划性"
)

SCOPE_PREFIXES = ("docs/", "data/reports/", "app/", "scripts/", "code/")
SCOPE_FILES = ("README.md", "CHANGELOG.md")
SELF = "scripts/check_public_wording.py"
# 内部 Git 流程文档不进公开仓库，不参与检查
SKIP_FILES = ("docs/GIT_WORKFLOW.md",)


def in_scope(path: str) -> bool:
    """只检查项目说明、报告和代码，不碰原始语料。"""
    if path == SELF or path in SKIP_FILES:
        return False
    if path in SCOPE_FILES:
        return True
    if path.startswith(SCOPE_PREFIXES):
        return True
    # 项目根目录的入口脚本
    return path.endswith(".py") and "/" not in path


def main() -> int:
    """返回 0 表示干净；发现违规词时打印位置并返回 1。"""
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", check=True,
    ).stdout.splitlines()
    hits = []
    for name in files:
        if not in_scope(name):
            continue
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for no, line in enumerate(text.splitlines(), 1):
            if PATTERN.search(line):
                hits.append(name + ":" + str(no) + ": " + line.strip())
    if hits:
        print("[public-wording] 发现计划性/面试/简历类措辞：")
        for hit in hits:
            print("  " + hit)
        print("[public-wording] 请改写或删除后再推送公开仓库")
        return 1
    print("[public-wording] 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
