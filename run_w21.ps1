# W=21 / L=4 소자 4점 측정
#   (0,0) → (0,-300) → (1250,-300) → (1250,0)
#
# 소자당 1회만, 왕복(dual) 스윕으로 찍는다.
# 조건은 에이전트가 소자마다 정한다 — 앞 소자에서 본 것이 다음 소자의 근거가 된다.
# 끝나면 에이전트가 쓴 리포트가 결과 폴더에 report.md 로 저장된다.
#
#   .\run_w21.ps1            실측
#   .\run_w21.ps1 --dry      장비 안 열고 조건 검증만
#
# 추가 인자는 그대로 전달된다 (--objective "..." 등).

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw "가상환경을 못 찾았다: $PSScriptRoot\.venv"
}

.\.venv\Scripts\python.exe -m measauto.examples.run_area `
    --stack sd_w21 `
    --coords utils\w21_4pt.csv `
    --calib 4 `
    --direction double `
    --report `
    --set-reference `
    @args
