$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $project ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw ".venv not found. Run RUN_TRADEPUSH.ps1 first."
}

Set-Location $project

if ($args.Count -eq 1) {
    & $python collect_data.py reconstruct $args[0]
    exit $LASTEXITCODE
}

if ($args.Count -eq 2) {
    & $python collect_data.py reconstruct-range --start $args[0] --end $args[1]
    exit $LASTEXITCODE
}

Write-Host "Usage:"
Write-Host "  .\BACKFILL_HISTORY.ps1 2026-06-22"
Write-Host "  .\BACKFILL_HISTORY.ps1 2026-06-01 2026-06-22"
exit 1
