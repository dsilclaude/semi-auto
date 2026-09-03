# W=9 / L=4 소자 2점 측정 (Y -300 um 간격)
#
# 소자당 1회만, 왕복(dual) 스윕으로 찍는다.
# 첫 소자는 좁은 창으로 시작하고, turn-on 을 못 찾으면 다음 소자에서 넓힌다.
# 끝나면 에이전트가 쓴 리포트가 결과 폴더에 report.md 로 저장된다.
#
#   .\run_w9.ps1            실측
#   .\run_w9.ps1 --dry      장비 안 열고 조건 검증만
#
# 추가 인자는 그대로 전달된다 (--objective "..." 등).

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw "가상환경을 못 찾았다: $PSScriptRoot\.venv"
}

.\.venv\Scripts\python.exe -m measauto.examples.run_area `
    --stack sd_w9 `
    --coords utils\w9_col_300um.csv `
    --calib 2 `
    --direction double `
    --report `
    --set-reference `
    @args
