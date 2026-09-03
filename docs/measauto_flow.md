# measauto 실행 순서도

`measauto/examples/run_area.py` → `Session.run_area` → `Session.run_site` 의 실제 분기를
그대로 옮긴 것. 세 장으로 나눈다.

| 그림 | 내용 | 파일 |
|---|---|---|
| A | 실행 전체 — 준비 · 안전 검사 · 소자 순회 · 마무리 | [PNG](flow/measauto_flow_a_overview.png) · [SVG](flow/measauto_flow_a_overview.svg) |
| B | 시드 결정 — 이 소자의 조건을 무엇을 근거로 정하는가 | [PNG](flow/measauto_flow_b_seed.png) · [SVG](flow/measauto_flow_b_seed.svg) |
| C | 반복 루프 — 소자 하나 안에서 도는 루프 | [PNG](flow/measauto_flow_c_iteration.png) · [SVG](flow/measauto_flow_c_iteration.svg) |

그림을 고치려면 `docs/flow/src/` 의 스크립트를 고치고 다시 돌린다 (matplotlib 만 있으면 된다).

```
cd docs/flow/src && python dia_a.py && python dia_b.py && python dia_c.py
```

---

## A. 실행 전체 (`run_area.py` + `Session.run_area`)

![A. 실행 전체](flow/measauto_flow_a_overview.png)

## B. 시드 결정 (`_seed_for` → `_baseline_for` → `_extend_past_gm_peak`)

초안의 `Generate Seed Conditions` 한 칸이 실제로는 이만큼이다. 조정이 **소자 사이**에서
일어나는 게 이 설계의 핵심이라 접어두면 안 된다 — 같은 소자를 다시 재면 트래핑이 쌓인다.

![B. 시드 결정](flow/measauto_flow_b_seed.png)

## C. 반복 루프 (`Session.run_site`)

초안의 오른쪽 판. 빠져 있던 것: 반복 상한, 위반 재시도 상한, 측정 예외, 첫 회차와
이후 회차의 차이, status 가 7가지라는 점, 저장 시점.

![C. 반복 루프](flow/measauto_flow_c_iteration.png)

---
## 초안에서 채운 것

| 초안 | 실제 |
|---|---|
| `Calibration Device?` 의 No 분기 없음 | 확정 plan(locked)으로 1회만 측정, 에이전트 호출 없음 |
| `More Devices?` 의 Yes 분기 없음 | 다음 소자로 되돌아감 |
| 루프 종료 조건이 verdict 뿐 | `max_iters` 상한, 측정 예외, 7가지 status |
| 위반 되돌리기가 무한 | `max_retry_on_violation` 상한 → `out_of_bounds`, 재시도도 iteration 을 소비 |
| `Move Probe, Run Sweep` 한 칸 | 첫 회차만 프로버 이동(`ex.run`), 이후는 같은 자리 재측정(`ex.measure`) |
| 소자 간 학습 경로 없음 | `prior_devices` → 다음 소자 기준안 (창 확대 / 실측 반영 / gm 꼭대기 연장) |
| 마무리 없음 | 요약 · `home`/`close` (finally) · 리포트는 장비 닫은 뒤 |
| 시작부 없음 | 경계 계산 · 기준안 검증 · `--dry` · 연결 실패 |

초안 오른쪽 아래 `Verdict = propose new conditions?` 의 Yes 상자가 `Send violation back` 으로
잘못 붙어 있다 — 그 자리는 "제안된 조건으로 교체"다. 위반 되돌리기는 위쪽 경로다.

---

## 부록 — 같은 그림의 mermaid 원본

draw.io 로 옮겨 손으로 편집하려면 **Arrange ▸ Insert ▸ Advanced ▸ Mermaid** 에 붙여넣는다.

### A. 실행 전체 (`run_area.py` + `Session.run_area`)

```mermaid
flowchart TD
    S(["시작"]) --> IN[/"stack, objective, 좌표 CSV<br/>calib, max_iters"/]
    IN --> B["bounds_from_stack<br/>안전 경계 계산"]
    B --> BQ{"한계값이 다 있나?"}
    BQ -->|"MissingLimitError"| X1(["중단 — 한계를 채우고 다시"])
    BQ -->|"있다"| SD["seed_transfer<br/>기준안 계산 (stack 기반)"]
    SD --> V0{"기준안이 경계 안인가?"}
    V0 -->|"위반"| X2(["중단"])
    V0 -->|"통과"| DRY{"--dry ?"}
    DRY -->|"예"| X3(["검증만 하고 종료<br/>장비를 열지 않음"])
    DRY -->|"아니오"| CN["장비 연결 · check<br/>--set-reference 면 원점 등록"]
    CN --> CNQ{"연결 성공?"}
    CNQ -->|"실패"| X4(["중단"])
    CNQ -->|"성공"| NEXT["소자 i 선택"]

    NEXT --> CAL{"i < calibration_sites<br/>또는 locked 없음?"}
    CAL -->|"예 — 탐색 소자"| SEED["시드 결정 (그림 B)"]
    CAL -->|"아니오 — 나머지 소자"| FIXED["확정 plan(locked) 그대로<br/>에이전트 호출 없음"]
    SEED --> ITER["반복 루프 (그림 C)"]
    FIXED --> ITER

    ITER --> WASCAL{"탐색 소자였나?"}
    WASCAL -->|"예"| PRIOR["prior_devices += summary_row<br/>Vth · SS · turn-on 창 안 여부<br/>gm 꼭대기 창 끝 여부 · Ig"]
    PRIOR --> LOCKQ{"status == converged ?"}
    LOCKQ -->|"예"| LOCK["locked = final_plan<br/>이 area 조건 확정"]
    LOCKQ -->|"아니오"| MORE
    LOCK --> MORE{"남은 소자 있나?"}
    WASCAL -->|"아니오"| MORE
    MORE -->|"예"| NEXT
    MORE -->|"아니오"| SUMM["요약 출력<br/>소자별 status / iter / Vth / SS"]
    SUMM --> HOME["home → close<br/>팁을 원점 contact 상태로 (finally)"]
    HOME --> RPT{"--report ?"}
    RPT -->|"예"| REPORT["report.md 생성<br/>장비를 닫은 뒤에 한다"]
    RPT -->|"아니오"| E(["끝"])
    REPORT --> E

    PRIOR -.->|"다음 소자의 기준안 근거<br/>(소자를 바꿔가며 범위를 좁힌다)"| SEED

    classDef term fill:#d5e8d4,stroke:#82b366
    classDef dec fill:#ffe6cc,stroke:#d79b00
    class S,E,X1,X2,X3,X4 term
    class BQ,V0,DRY,CNQ,CAL,WASCAL,LOCKQ,MORE,RPT dec
```

---

### B. 시드 결정 (`_seed_for` → `_baseline_for` → `_extend_past_gm_peak`)

초안의 `Generate Seed Conditions` 한 칸이 실제로는 이만큼이다. 조정이 **소자 사이**에서
일어나는 게 이 설계의 핵심이라 접어두면 안 된다 — 같은 소자를 다시 재면 트래핑이 쌓인다.

```mermaid
flowchart TD
    S(["시드 결정 시작"]) --> P0{"앞 소자 결과가 있나?"}
    P0 -->|"없다 (첫 소자)"| BASE["stack 기준안 그대로"]
    P0 -->|"있다"| FOUND{"turn-on 을 창 안에서 본 소자가 있나?"}

    FOUND -->|"아니오"| WIDE["중심 유지한 채 창 ×widen_factor<br/>점 수도 같은 비율로 (스텝 유지)"]
    WIDE --> WQ{"넓힌 창이 경계 안?"}
    WQ -->|"위반"| BASE
    WQ -->|"통과"| CAND["기준안 = 넓힌 창"]

    FOUND -->|"예"| REFINE["실측 Vth·SS 중앙값을 stack.measured 에 넣고<br/>seed_transfer 재계산 (좁고 촘촘하게)"]
    REFINE --> GMQ{"앞 소자 전부 gm 꼭대기가 창 끝?<br/>(이동도가 하한)"}
    GMQ -->|"예"| EXT["켜지는 쪽 끝만 창 폭의 50% 연장<br/>turn-on 쪽 끝은 그대로"]
    EXT --> EQ{"연장한 창이 경계 안?"}
    EQ -->|"위반"| CAND
    EQ -->|"통과"| CAND
    GMQ -->|"아니오"| CAND

    BASE --> AG{"adapt_seed_per_site 이고<br/>policy 에 propose_seed 가 있나?"}
    CAND --> AG
    AG -->|"아니오"| DIR
    AG -->|"예"| ASK["에이전트에게 조건을 묻는다<br/>목적 · 경계 · 소자 스펙 · prior_devices"]
    ASK --> AR{"응답"}
    AR -->|"호출 실패 / 타임아웃"| KEEP["기준안 유지"]
    AR -->|"propose 아님"| KEEP
    AR -->|"propose"| AV{"제안이 경계 안?"}
    AV -->|"위반"| KEEP
    AV -->|"통과"| USE["plan = 에이전트 제안"]
    KEEP --> DIR
    USE --> DIR

    DIR{"force_direction 이 지정됐나?"} -->|"예"| FD["스윕 방향 강제<br/>에이전트 판단보다 우선"]
    DIR -->|"아니오"| OUT
    FD --> OUT(["이 소자의 plan 확정"])

    classDef term fill:#d5e8d4,stroke:#82b366
    classDef dec fill:#ffe6cc,stroke:#d79b00
    class S,OUT term
    class P0,FOUND,WQ,GMQ,EQ,AG,AR,AV,DIR dec
```

---

### C. 반복 루프 (`Session.run_site`)

초안의 오른쪽 판. 빠져 있던 것: 반복 상한, 위반 재시도 상한, 측정 예외, 첫 회차와
이후 회차의 차이, status 가 7가지라는 점, 저장 시점.

```mermaid
flowchart TD
    S(["소자 시작"]) --> INIT["plan = 확정 plan 또는 시드<br/>it = 0, retries = 0"]
    INIT --> ITQ{"it < max_iters ?"}
    ITQ -->|"아니오"| MAXI["status = max_iters<br/>에이전트가 더 원했다면 이유를 남긴다"]
    MAXI --> E(["소자 끝"])

    ITQ -->|"예"| VAL{"validate: error 있나?<br/>(executor 부르기 직전, 유일한 관문)"}
    VAL -->|"있다"| FIXQ{"확정 plan 이거나<br/>retries >= max_retry_on_violation ?"}
    FIXQ -->|"예"| OOB["status = out_of_bounds"]
    OOB --> E
    FIXQ -->|"아니오"| RB["retries++<br/>위반 내용을 history 에 넣어 되돌려 보냄<br/>(측정은 하지 않는다)"]
    RB --> ASK2{"에이전트 응답"}
    ASK2 -->|"propose"| NEWP["plan 교체 · it++"]
    NEWP --> ITQ
    ASK2 -->|"그 외"| STOP["status = 그 값"]
    STOP --> E

    VAL -->|"없다"| FIRST{"첫 회차인가?"}
    FIRST -->|"예"| RUN["ex.run — 프로버 이동 · contact · 스윕"]
    FIRST -->|"아니오"| MEA["ex.measure — 같은 자리에서 재측정"]
    RUN --> ERRQ{"측정 중 예외?"}
    MEA --> ERRQ
    ERRQ -->|"예"| ERR["status = error"]
    ERR --> E
    ERRQ -->|"아니오"| SUM["summarize<br/>Vth · SS · gm/이동도 · on-off · Ig · 히스테리시스"]
    SUM --> PAY["LLM 페이로드 구성<br/>full CSV 또는 25점 다운샘플 + metrics"]
    PAY --> FIX2{"확정 plan 인가?"}
    FIX2 -->|"예"| CONV["Proposal = converged<br/>LLM 호출 없음"]
    FIX2 -->|"아니오"| ASK["policy.propose<br/>호출 실패 → needs_human<br/>confidence < min_confidence → needs_human"]
    CONV --> SAVE["store.save<br/>CSV · metrics · plan · proposal · 소요시간"]
    ASK --> SAVE
    SAVE --> DEC{"status"}
    DEC -->|"propose"| NP["plan = 제안 · it++"]
    NP --> ITQ
    DEC -->|"converged"| E
    DEC -->|"dead / leaky / polarity_anomaly<br/>out_of_bounds / needs_human"| E

    classDef term fill:#d5e8d4,stroke:#82b366
    classDef dec fill:#ffe6cc,stroke:#d79b00
    class S,E term
    class ITQ,VAL,FIXQ,ASK2,FIRST,ERRQ,FIX2,DEC dec
```

---

