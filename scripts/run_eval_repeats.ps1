# 连续跑 v2 prompt 的重复评测：run2 / run3，并各做一次 L1。
# 用法：powershell -ExecutionPolicy Bypass -File scripts\run_eval_repeats.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:HF_HOME = 'F:\HF-Cache'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = $root
$python = if ($env:RAG_PYTHON) { $env:RAG_PYTHON } else { "python" }

$runs = @(
    @{ Report = 'data/eval_report.v2_run2.json'; L1 = 'data/l1_report_v2_run2_air.json' },
    @{ Report = 'data/eval_report.v2_run3.json'; L1 = 'data/l1_report_v2_run3_air.json' }
)
foreach ($run in $runs) {
    Write-Host "[repeat] generation -> $($run.Report)"
    & $python run_eval.py --gold-file data/eval_gold.reviewed.jsonl --doc-cap 0 `
        --prompt-version v2 --report-out $run.Report --no-ablation
    if ($LASTEXITCODE -ne 0) { throw "run_eval failed: $($run.Report)" }

    Write-Host "[repeat] L1 -> $($run.L1)"
    & $python judge_l1.py --report $run.Report --out $run.L1 `
        --cache data/l1_cache.jsonl --model glm-4.5-air --fallback-model= `
        --workers 4 --timeout 60
    if ($LASTEXITCODE -ne 0) { throw "judge_l1 failed: $($run.L1)" }
}
Write-Host '[repeat] all done'
