# 启用项目 Git hooks：pre-commit 检查暂存文件，pre-push 跑 55 项自检。
# 用法：powershell -ExecutionPolicy Bypass -File scripts\setup_hooks.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
git config core.hooksPath .githooks
Write-Host "已设置 core.hooksPath=.githooks"
Write-Host "pre-commit: python scripts/pre_commit_check.py"
Write-Host "pre-push:   python verify.py"
