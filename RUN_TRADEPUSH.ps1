$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $project ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    $systemPython = (Get-Command python -ErrorAction Stop).Source
    Write-Host "首次运行：正在创建 TradePush 独立 Python 环境..."
    & $systemPython -m venv (Join-Path $project ".venv")
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r (Join-Path $project "requirements.txt")
}

Set-Location $project
& $venvPython -m streamlit run app.py --server.port 8510 --server.address 127.0.0.1 --server.runOnSave true
