# W=12 / L=4 소자 4점 측정
#   (0,0) → (0,-300) → (1250,-300) → (1250,0)
#
# 소자당 1회만, 왕복(dual) 스윕으로 찍는다.
# 첫 소자는 turn-on 위치를 몰라 넓게 훑고, 그 결과로 2~4번은 좁혀서 잰다.
# 끝나면 에이전트가 쓴 리포트가 결과 폴더에 report.md 로 저장된다.
#
#   .\run_w12.ps1            실측
#   .\run_w12.ps1 --dry      장비 안 열고 조건 검증만
#
# 추가 인자는 그대로 전달된다 (--objective "..." 등).

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw "가상환경을 못 찾았다: $PSScriptRoot\.venv"
}

.\.venv\Scripts\python.exe -m measauto.examples.run_area `
    --stack sd_w12 `
    --coords utils\w12_4pt.csv `
    --calib 4 `
    --direction double `
    --report `
    --set-reference `
    @args
