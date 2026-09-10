# 자동측정 UI 를 띄운다.
#
#   .\run_ui.ps1
#
# 처음 한 번은 PySide6 가 필요하다 — 없으면 여기서 알아서 설치한다.
# 조작은 전부 화면에서 한다 (스택 / 좌표 / 조건 / 실행·중단).

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    throw "가상환경을 못 찾았다: $PSScriptRoot\.venv"
}

& $py -c "import PySide6" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PySide6 가 없어서 설치합니다 (한 번만)..." -ForegroundColor Yellow
    & $py -m pip install PySide6
}

& $py measauto_ui.py @args
