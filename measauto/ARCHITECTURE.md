# measauto 구조 설명서

이 문서는 **왜 이렇게 갈랐는지 / 각 파일이 무슨 일을 하는지 / 폴더가 어떻게 생겼는지**를
설명한다. "어떻게 돌리는지"는 [README.md](README.md) 에 있다.

기준 시점: 2026-08-16 / `measauto 0.1.0` / 테스트 21개 통과.

---

## 1. 무엇을 만들려던 건가

기존 `projects/grid_measure/*.ipynb` 는 **장비 문법 · 측정 조건 · 지표 계산 · 판단**이
셀 하나에 뒤섞여 있었다. 사람이 손으로 조건을 고치며 돌릴 때는 그게 제일 빨랐다.

여기에 "측정 중에 다음 조건을 스스로 정하는" 에이전트를 붙이려니 문제가 생긴다.
**에이전트가 조건을 바꾸려면 노트북 셀을 고쳐야 하는데, 코드 생성은 검증도 롤백도
안 된다.** 무엇이 바뀌었는지 diff 로 봐야 하고, 위험한 값이 들어갔는지 실행 전에
확인할 방법이 없다.

그래서 구조를 뒤집었다.

> **`main` 이 plan(값)을 소비하는 구조면, 에이전트는 dict/JSON 만 만들면 된다.
> 필드가 유한하니 실행 전 전수 검사가 된다.**

이 한 문장이 패키지 전체의 설계 근거다. 나머지는 전부 여기서 파생된다.

### 파생 ① — 모듈을 '기능'이 아니라 '변경 주기'로 자른다

보통은 `io.py` / `analysis.py` / `plot.py` 처럼 기능으로 자른다. 여기서는
**얼마나 자주 바뀌는가**로 잘랐다. 자주 바뀌는 것과 안 바뀌는 것이 한 파일에 있으면,
조건 하나 바꾸려고 장비 코드를 건드리게 되기 때문이다.

| 층 | 변경 주기 | 형태 | 위치 |
|---|---|---|---|
| 장비 통신 (VISA/SCPI, 슬롯 매핑) | 거의 없음 | 코드 | `drivers/`, `config.py` |
| 측정 primitive | 가끔 | 코드 | `executor.py` |
| 측정 조건·대상 | **매번** | **값** | `plan.py` (객체), 스크립트의 시드 |
| 소자 스택·기하 | 웨이퍼마다 | **값** | `stacks/*.json` |
| 저장·분석 | 독립 | 코드 | `store.py`, `metrics.py` |

### 파생 ② — 안전 경계도 코드에 박지 않는다

`safety.py` 에 `V_MAX = 30` 이라고 쓰면 웨이퍼가 바뀔 때마다 코드를 고쳐야 한다.
대신 **층 두께와 유전율을 `stacks/*.json` 에 적고 경계를 계산**한다. 웨이퍼가 바뀌면
json 만 갈아끼운다.

### 파생 ③ — 검증은 실행 밖에 둔다

`validate` 는 `executor` 안에 없다. `executor` 는 "이미 안전하다"고 가정하고 실행만
하고, 검증은 `session` 이 `executor` 를 부르기 **직전에** 건다.

> 한 함수에 섞으면 "검사를 통과시키려고 실행을 고치는" 유혹이 생긴다.

### 파생 ④ — 지표는 계산하고, 판단은 LLM 이 한다

`metrics.py` 는 Vth·SS·decade 를 **정확히 계산**하되 **판정하지 않는다**.
"죽은 소자다 / 범위를 넓혀야 한다"는 `agent/` 의 몫이다.

이유: 범위 탐색 단계에서는 **예외가 예외가 아니다.** 첫 스윕의 평평한 선이 죽은
소자인지, Vth 가 범위 밖인지, 팁이 안 닿았는지는 그 데이터만으로 안 갈라진다.
규칙으로 가르려 하면 곡선이 이미 보이는 걸 전제하게 되어 정작 초반엔 계산조차 안 된다.

### 부수 효과 — 조건이 데이터라서 기록이 남는다

조건이 값이므로 결과 CSV 옆에 `plan.json` 을 그대로 떨궈둘 수 있다.
**에이전트가 조건을 만들기 시작하면 이게 유일한 실험 기록이다.**

---

## 2. 폴더 구조

### 저장소 전체

```
E:\semi-auto\
├── measauto/          ← 이 문서의 대상. 자동측정 패키지
├── projects/          기존 측정 프로젝트 (노트북)
│   ├── grid_measure/    grid_measure_idvd.ipynb, _fast, _config, _pulsed …
│   └── old/
├── examples/          장비별 예제 (b1500 / s300 / vna)
├── utils/             좌표 CSV (grid_2x4, grid_4x4, grid_5x4)
├── docs/              매뉴얼 PDF, 인수인계 자료, 세션 기록
├── requirements.txt
└── README.md
```

`measauto` 는 기존 노트북을 **대체하지 않는다.** 노트북은 그대로 두고, 그 안에 섞여
있던 것을 갈라 담은 별도 패키지다.

### measauto 내부

```
measauto/
├── __init__.py           공개 API + 설계 원칙 요약
├── README.md             왜 이렇게 갈랐나 + 실행법
├── ARCHITECTURE.md       ← 이 문서
│
├── config.py             이 셋업만의 사실 (VISA 주소, 단자↔SMU 매핑)
├── plan.py               측정 조건을 '값'으로              [값의 정의]
├── frame.py              장비 출력 → 단자 이름 기준 정규화
├── safety.py             stack.json → 경계 계산 + plan 검사  [검증]
├── metrics.py            데이터 → 지표 (판정 안 함)          [분석]
├── executor.py           plan → 장비 동작                   [실행]
├── session.py            area 단위 운용 루프                 [조립]
├── store.py              결과 + 조건 + 지표 저장             [기록]
├── replay.py             장비 없이 돌려보고 세는 도구         [검증]
│
├── drivers/              장비 문법을 아는 유일한 곳
│   ├── __init__.py
│   ├── b1500.py            Keysight B1500A (FLEX/SCPI)
│   └── s300.py             Cascade S300 프로버 (Nucleus)
│
├── agent/                관측 → 다음 plan
│   ├── __init__.py
│   ├── schema.py           에이전트가 낼 수 있는 답의 문법
│   ├── prompt.py           LLM 에 넘길 문맥 조립
│   └── policy.py           LLM / 규칙 기반 구현
│
├── stacks/               소자 스택 (웨이퍼마다 바뀌는 값)
│   └── sd_dualgate.json
│
├── examples/             진입점 스크립트
│   ├── dryrun.py           장비 없이 전체 루프
│   ├── run_area.py         실제 장비로 area 하나
│   └── replay_legacy.py    과거 CSV 로 에이전트 평가
│
└── tests/                pytest 없이 도는 테스트 21개
    ├── run_all.py
    ├── test_safety.py      (6)
    ├── test_metrics.py     (7)
    └── test_loop.py        (8)
```

### 의존 방향

```
        examples/  (진입점)
             │
          session ──────────────┐
         ╱   │   ╲              │
   safety  executor  store     agent/
      │       │        │      (policy·prompt·schema)
      │    drivers/    │         │
      │    b1500·s300  │         │
      └───────┴────────┴─────────┘
                 plan · frame · metrics
                     (순수 계층)
```

**아래로만 의존한다.** `plan`/`frame`/`metrics`/`safety` 는 장비를 모르고,
`drivers` 는 위층을 모른다.

---

## 3. 불변 규칙 셋

깨지면 설계가 무너지는 것들이다. 코드를 고칠 때 이것부터 확인할 것.

### ① `drivers/` 는 measauto 의 아무것도 import 하지 않는다

`drivers/b1500.py` 는 `IVPlan` 을 모른다. **슬롯 번호로 쓰인 순수 dict** 만 받는다.
단자 이름(`"BG"`) → 채널 번호(`1`) 변환은 `executor.plan_to_spec()` 이 유일하게 한다.

이유: 배선이 바뀌면 `config.roles` 만 고치면 되고, 드라이버는 그대로다.

### ② `agent/` 는 `drivers/` 를 import 하지 않는다

에이전트 층은 장비 없이 과거 데이터만으로 돌아가야 한다. **그게 이 층의 유일한 검증
수단**이기 때문이다 (`replay.py`, `examples/replay_legacy.py`).

### ③ 에이전트는 `safety` 를 수정할 수 없고 validator 의 존재도 모른다

프롬프트에 "경계를 넘으면 실행되지 않고 되돌아온다"까지만 알려준다.
검사받는 쪽이 검사를 고쳐 쓰는 구조가 되면 안 된다.

---

## 4. 파일별 역할

### 4-1. `plan.py` — 측정 조건을 값으로

**패키지의 중심.** 여기 정의된 것은 전부 dict/JSON 으로 왕복 가능해야 한다.

| 타입 | 역할 |
|---|---|
| `SweepAxis` | 한 축의 sweep 조건. **EasyEXPERT 의 VAR1/VAR2 칸과 1:1** |
| `Timing` | hold / delay / step_delay / auto_abort / post |
| `Adc` | HRADC·HSADC, 적분 계수, auto_zero |
| `RangeSpec` | 채널별 측정 전류 레인지 (auto / limited / fixed) |
| `IVPlan` | 위를 다 묶은 **한 번의 측정을 완전히 기술하는 값** |

용어 정리:

- `terminal` — 소자 단자의 **논리 이름** (`"TG"`, `"BG"`, `"D"`, `"S"`).
  어느 SMU 슬롯에 물렸는지는 여기서 모른다 (`config.py` 담당).
- `var1` — 주 sweep 축. `var2` 스텝마다 이 sweep 이 통째로 1회 돈다.
- `var2` — 부 sweep 축(스텝). 없으면 `None`.
- `kind` — `var1` 이 게이트면 `"transfer"`, D 면 `"output"`.

파생 속성이 계산을 대신해준다: `n_steps`(step 지정 시 환산), `n_measured`(double 이면 2배),
`n_points`(var1 × var2), `terminal_spans()`(단자별 전압 구간 — **검증의 입력**),
`compliance_of()`.

생성 헬퍼 `transfer()` / `output()` 이 사람이 쓰는 입구다. `transfer()` 는
`other_gate` 를 주면 그 게이트를 DC 고정한다 — *"안 쓰는 게이트는 띄우지 말고 0 V"*
규칙 때문에 기본이 0 V 고정이다.

### 4-2. `config.py` — 이 셋업만의 사실

배선이 바뀔 때만 고친다. **단자 이름 ↔ SMU 채널 매핑이 사는 유일한 곳.**

```python
roles = {"BG": 1, "D": 2, "S": 3}       # DEFAULT — 현재 셋업 (SMU 3개)
DUAL_GATE = HardwareConfig(roles={"BG": 1, "D": 2, "S": 3, "TG": 4})
```

> ⚠️ `smuN` 은 **장착 슬롯 번호가 아니다.** 실측 확인 결과 `smu1→슬롯2`, `smu2→슬롯3`,
> `smu3→슬롯4`, `smu4` 없음 — 슬롯 1 이 B1530A WGFMU 라 SMU 번호가 한 칸 밀린다.
> **dual gate 를 다 물리려면 SMU 가 4개 필요하다.** 지금은 3개라 TG 를 못 쓴다.

`require()` 가 배선 안 된 단자를 쓰는 plan 을 막는다. VISA 라이브러리를
`nivisa64.dll` 로 못박은 이유도 여기 주석에 있다 (Keysight VISA 로 붙으면
`VI_ERROR_RSRC_NFOUND`).

### 4-3. `frame.py` — 컬럼 정규화

B1500(pymeasure)이 주는 컬럼은 **슬롯 기준**이다:
`"SMU2 Current Measurement (A)"`, `"VAR2 SMU1 Vg (V)"`.
슬롯 번호는 배선이 바뀌면 바뀌므로 **분석 코드가 알면 안 된다.**

여기서 한 번만 갈아끼우면 이후로는 단자 이름만 본다:
`V_TG, V_BG, V_D, V_S, I_TG, I_BG, I_D, I_S, step`

| 함수 | 역할 |
|---|---|
| `canonicalize(df, roles)` | 슬롯 컬럼 → 단자 컬럼. 매핑 안 되는 건 그대로 둔다 |
| `fill_constants(df, constants)` | 측정 안 된 DC 고정 단자의 전압 컬럼을 채운다 |
| `load_legacy_csv(path, roles, constants)` | 기존 노트북 CSV 를 그대로 읽는 입구 |

**pandas 말고는 아무것도 import 하지 않는다** — 장비 없이 과거 CSV 로 `metrics` 를
단위테스트할 수 있어야 하므로.

`fill_constants` 가 필요한 이유: B1500 은 sweep/측정 채널만 전압을 돌려주므로
0 V 로 잡아둔 소스는 컬럼이 아예 없다. 분석에서 "V_S 가 없다"로 갈리지 않게 채운다.

### 4-4. `safety.py` — 경계 계산과 검사

**순수 함수만 있다. 장비도 pandas 도 모른다.**

#### 물리 (`series_dielectric`)

유전막은 전압이 아니라 **필드**에 반응한다. `E = V/t_ox` 이고 파괴 메커니즘
(qEλ → impact ionization → avalanche)이 E 에만 의존한다. 직렬 스택은 전속밀도 D 가
연속이라 **ε 작은 층에 필드가 몰린다.**

```
V = Σ E_i t_i = D · Σ(t_i/ε_i)
층 i 의 한계  V_i,max = E_i,max · ε_i · Σ(t_j/ε_j)
스택 한계     V_max = min_i V_i,max
```

SD 웨이퍼 BG (SiNx 50 nm/ε7 + SiO₂ 75 nm/ε3.9, E_max 3 MV/cm) 검산:

```
Σ t/ε = 50/7 + 75/3.9 = 26.374 nm
SiO2 한계 = 3 MV/cm × 3.9 × 26.374 nm = 30.9 V   ← 율속
SiNx 한계 = 3 MV/cm × 7.0 × 26.374 nm = 55.4 V
ε_eff = 125/26.374 = 4.74     (SmartSPICE EPSI = 4.74 와 일치)
C_BG  = ε0·ε_eff/125 nm = 33.6 nF/cm²
```

W/L 은 수평 방향(전류 크기)이라 대체 불가다. 수직 필드의 경로 길이는 `t_ox` 뿐이다.

#### `Bounds` 와 `bounds_from_stack()`

json → `Bounds`. 담기는 것: 단자쌍 전압 상한, 단자별 컴플라이언스 상한, 운용 창,
`max_points`, Ig 경고/중단 문턱, **계산 근거(`derived`)**, 기하(`geometry`).

`derived` 를 같이 들고 다니는 이유는 검산 때문이다 — `describe()` 가 율속층과 ε_eff 를
같이 찍어줘서 손계산과 바로 대조된다.

#### `validate(plan, bounds)`

**필드가 유한하니 전수 검사가 된다 — 이게 plan 을 값으로 둔 이유다.**
5가지를 본다:

1. **형식** — kind, terminal, direction, spacing, 포인트 수, compliance,
   var1/var2 단자 충돌
2. **규모** — `n_points > max_points` (누적 스트레스 시간)
3. **전압** — 단자쌍 최악조건. `worst = max(|amax−bmin|, |amin−bmax|)`.
   두 축이 독립이라고 보는데, 한 plan 에서 동시에 훑는 축은 var1/var2 뿐이고
   그 둘은 실제로 독립이므로 과대평가가 아니다
4. **운용 창** — `VTG ∈ [−5, 20]` 같은 것
5. **컴플라이언스** — 단자별 상한

`Violation` 은 `error` / `warn` 두 등급이다. `assert_valid()` 는 error 만 예외로 올린다.

### 4-5. `metrics.py` — 지표 (판정 없음)

입력은 정규화된 DataFrame + `IVPlan`, 출력은 dict. **장비도 파일도 에이전트도 모른다.**

| 함수 | 역할 |
|---|---|
| `split_branches` | var2 스텝별 / 왕복 방향별로 곡선을 쪼갠다 |
| `transfer_metrics` | 편도 transfer 곡선 하나 → 지표 |
| `output_metrics` | 편도 output 곡선 하나 → 지표 |
| `summarize` | 최상위. branch 별 + 대표 branch 지표를 평평하게 |
| `downsample_curve` | 대표 곡선을 20~30점으로 |
| `csv_payload` | 측정 CSV 원본 전체를 텍스트로 (다운샘플의 반대편) |
| `to_llm_payload` | 지표 + (곡선 \| CSV) → LLM 페이로드 |
| `jsonable` | numpy·nan 을 순수 파이썬으로 (저장 전 필수) |

transfer 지표: `id_on/id_off/decades`, `polarity`, `gm_peak`, `vth_lin`(선형 외삽),
`vth_cc`(정전류 기준, W/L 정규화), `ss`(**눈대중이 아니라 슬라이딩 창 회귀**),
`floor_slope`(off 바닥 기울기), `ig_max/ig_over_id/ig_slope`, `compliance_hit`,
`mobility_cm2_Vs`, 그리고 사실 관찰 `is_flat` / `turn_on_inside_window`.

output 지표: `saturation_ratio`, `r_on_ohm`, `crowding_index`(접촉 저항 지배),
`kink_index`.

세부 설계 두 개:

- **SS 는 창을 줄여서라도 값을 낸다.** 성긴 survey 에서는 문턱하 구간에 점이 몇 개
  안 들어온다. 5→4→3 점으로 내려가며 시도하되 **`ss_points` 에 몇 점으로 쟀는지
  같이 남긴다** — 그래야 거친 값인 걸 안다.
- **대표 branch = on 전류 최대.** 판단이 아니라 "가장 정보가 많은 곡선" 선택이다.

#### LLM 에 넘기는 형태

원본 배열 통째로는 안 넘긴다. 201점 × 3열이면 6~8k 토큰이고 이력까지 쌓이면 5턴에
30k 를 넘는다. **비용보다 문제는 긴 배열 중간 정보를 잘 못 본다는 것**, 그리고 SS 같은
걸 시키면 회귀가 아니라 눈대중으로 답한다는 것이다 — 0.22 를 0.3 이라 해도 틀린 걸
알아챌 방법이 없다.

→ **지표(정확히 계산) + 다운샘플 곡선(20~30점)**. 실측 payload 는 900자 안쪽.

```json
{"metrics": {"vth_cc": 2.1, "ss": 0.18, "ss_points": 5, "decades": 5.4,
             "floor_slope": 0.02, "ig_max": 4e-12, "compliance_hit": false},
 "curve": {"x_name": "VBG", "x": [-2, -1.6, ...],
           "log_id": [-12.1, ...], "log_ig": [-12.4, ...], "id_sign": [1, ...]}}
```

- **Ig 필수** — 게이트 누설 판단은 Id 만으로 안 되고, t_ox 를 모를 때 경계를 올릴
  근거가 Ig 뿐이다.
- **전류는 log10** — 8 decade 에 걸쳐 있어 선형으로 쓰면 off 영역이 전부
  `0.00000000000012` 꼴이라 구분이 안 된다. 로그면 −12.9, −6.5 로 거리가 균등해진다.
  Vg 는 선형.
- **함정** — 노이즈로 Id 가 음수면 log10 이 깨진다. 산화물 TFT off 영역에서 실제로
  자주 나오니 `|I| + 1e-15` 로 받고 부호는 `id_sign` 에 따로.

#### 원본 CSV 를 통째로 보내는 선택지 (현재 기본값)

`SessionConfig.send_full_csv = True` 면 다운샘플 대신 **`csv_payload()` 가 만든 CSV
원본 전체**가 `data_csv` 로 실린다. 두 방식을 실측 비교하려고 켜 둔 상태다.

`results_idvd/subsite_1.csv` (91 KB / 2,430 행) 기준:

| 설정 | payload | 대략 토큰 | 5턴 누적 |
|---|---|---|---|
| `send_full_csv=True` | 105,479 자 | ≈ 30,100 | ≈ 150k |
| `send_full_csv=False` | 903 자 | ≈ 258 | ≈ 1.3k |

**어느 쪽이든 `metrics` 는 항상 같이 간다.** 배열이 있다고 LLM 에게 회귀를 시키면
눈대중 답이 오고 틀려도 알아챌 방법이 없으므로, 프롬프트가 *"vth·ss·decades 를 데이터에서
다시 읽지 말고 metrics 값을 쓰라"* 고 명시한다.

`replay_once()` / `replay_score()` 에도 같은 인자가 있다 — **두 경로가 다른 형태를
보내면 리플레이 점수가 실제 운용을 대변하지 못한다.**

### 4-6. `executor.py` — plan → 장비 동작

**plan 이 이미 안전하다고 가정한다.** 검증은 `session` 이 걸어준다.

| 요소 | 역할 |
|---|---|
| `Site` | 측정 지점. **원점 기준 상대좌표 [µm]** |
| `load_sites` | 좌표 CSV/XLSX → `Site` 목록 |
| `plan_to_spec` | **IVPlan(단자) → dict(SMU 채널).** 드라이버가 단자를 모르게 하는 유일한 지점 |
| `Executor` | 장비 두 대를 묶어 "한 지점에서 한 plan" 제공 |

`Executor` 메서드: `connect/close/check`, `set_reference`, `goto`(분리→XY이동→접촉),
`home`, `measure`(이동 없이 측정), `run`(이동 후 측정).

두 가지가 중요하다.

- **`b1500`/`s300` 을 주입할 수 있게 열어 뒀다.** 그래서 `replay.FakeExecutor` 로
  갈아끼워 장비 없이 전체 루프를 돌 수 있다.
- **`home()` 은 원점에 컨택된 상태로 끝낸다.** 다음 실행에서 첫 소자(0,0)를 곧바로
  측정하는 전제가 그대로 성립하도록.

### 4-7. `session.py` — 운용 루프 (조립 지점)

**검증과 실행을 잇는 유일한 곳.** `executor` 를 부르기 직전에 `validate` 를 건다.

`run_site()` 의 한 턴:

```
validate(plan) ──위반──> 에이전트에게 되돌려 보냄 (max_retry_on_violation 회)
      │통과
   측정 (it==0 이면 이동 포함, 이후엔 제자리)
      │
   summarize → to_llm_payload
      │
   policy.propose()  →  Proposal(status, patch)
      │
   store.save(데이터·조건·지표·제안 전부)
      │
   status=="propose" 면 patch 적용해 다음 턴, 아니면 종료
```

`run_area()` 는 **앞의 `calibration_sites` 개만 에이전트로 탐색**하고, 수렴한 plan 을
`locked` 로 잡아 나머지 소자엔 그대로 한 번씩 적용한다.

> **탐색 자체가 소자를 오염시킨다.** 소자마다 탐색하면 스트레스가 전수에 쌓인다.

부수 효과 하나: **iteration 횟수 자체가 스크리닝 지표다.** LLM 호출이 몰리는 소자가
곧 이상 소자다. `index.csv` 의 `iteration` 열을 세면 바로 보인다.

`SessionConfig` 로 조절: `objective`(**사람이 정하는 유일한 정보**), `max_iters`,
`calibration_sites`, `max_retry_on_violation`, `min_confidence`, `curve_points`,
`send_full_csv`, `csv_max_rows`.

관측을 어떤 형태로 넘길지는 `Session._payload()` 한 곳에서 갈린다 (4-5 참고).

### 4-8. `store.py` — 기록

```
results_agent/
  index.csv                    ← 모든 측정 한 줄씩 (status, vth, ss, iteration…)
  A/d1/iter00/
      data.csv       정규화된 측정 데이터
      plan.json      이 데이터를 만든 조건 전부 + site + timestamp
      metrics.json   지표 + 에이전트 판단/근거 + LLM 에 실제로 넘긴 payload
      curve.png      로그용 그림
```

`metrics.json` 에 **LLM payload 까지 넣는 이유**: 나중에 "왜 이렇게 판단했지"를 되짚을 때
에이전트가 실제로 본 것이 무엇인지 알아야 한다.

**PNG 는 판단 입력이 아니라 로그용이다.** 이미지는 픽셀→축 매핑이라 2.1인지 2.4인지 못
가르고, 로그축에서 −12 와 −13 은 몇 픽셀 차이라 Ioff 판정에 특히 취약하다. 토큰도
1~1.5k 로 다운샘플 배열보다 비싸고, 렌더링 스타일이 판단에 개입하는데 그 영향은 추적이
불가능하다. 그래도 **사람이** 나중에 볼 때는 곡선 한 장이 압도적으로 빠르다.

### 4-9. `replay.py` — 장비 없이 검증

검증 방식이 두 가지다.

**① 리플레이 (과거 CSV, 1턴 평가)** — `ReplayCase`, `replay_once`, `replay_score`.
실제 측정한 CSV 를 지표로 만들어 에이전트에게 보여주고 무엇을 하겠다는지 센다.
**같은 소자에서 반복했을 때 결론이 갈리면 프롬프트가 부실하다는 신호다.**

> 한계: 에이전트가 새 조건을 제안해도 그 조건으로 찍힌 과거 데이터는 없다.
> 그래서 여기서 세는 건 '첫 판단' 뿐이다.

**② 시뮬레이션 (다중턴 전체)** — `SimulatedDevice`, `simulate`, `FakeExecutor`.
plan 에 반응하는 가짜 TFT. 에이전트가 범위를 넓히면 곡선이 실제로 나타나므로
"몇 턴에 수렴 / 경계 몇 번 접촉"을 끝까지 셀 수 있다.

`SimulatedDevice` 는 문턱 위(드리프트) + 아래(지수) + 바닥을 더하는데,
**문턱 아래 log10 기울기가 정확히 1/SS 다.** 그래서 넣은 Vth·SS 가 지표로 그대로
되돌아온다 — 지표 검증용 픽스처의 조건이다. off 영역에서 전류가 음수로 찍히는 것까지
재현한다.

### 4-10. `agent/schema.py` — 답의 문법

에이전트는 `IVPlan` 전체를 새로 쓰지 않는다. **시드 plan 에 덮어쓸 패치만** 낸다.

1. 필드가 유한해야 실행 전 전수 검사가 된다. timing/ADC/range 같은 거의 안 바뀌는 값을
   매번 다시 쓰게 하면 검사 표면이 무한히 넓어진다.
2. 코드 생성은 검증도 롤백도 안 된다. dict 는 둘 다 된다.

`PlanPatch` 필드는 12개뿐이다: `kind`, `var1_start/stop/points/direction/compliance`,
`var2_terminal/start/stop/points/compliance`, `constants`, `drain_compliance`.
**`None` 은 "그대로 둔다"는 뜻.**

`STATUSES` 는 열린 문장이 아니라 열거값이다 — `propose`, `converged`, `dead`, `leaky`,
`out_of_bounds`, `polarity_anomaly`, `needs_human`. 구조화해서 받아야 session 이 분기할
수 있고 리플레이 평가에서 셀 수 있다.

`PROPOSAL_SCHEMA` 는 structured outputs 용 JSON Schema (`additionalProperties: false`,
전 필드 `required`). `apply_patch()` 로 만든 plan도 **반드시 다시 validate 를 탄다.**

### 4-11. `agent/prompt.py` — 문맥 조립

`SYSTEM_PROMPT` 에 들어 있는 것: 받는 것/돌려주는 것, status 의 뜻, **범위 탐색 원칙**,
dual gate 주의, 경계에 대한 태도, reason 작성 요령.

범위 탐색 원칙이 핵심이다.

- **허용 범위를 알 때** → 그 범위 전체를 성기게 한 번 훑는다. **소자를 상하게 하는 건
  스윕 폭이 아니라 누적 스트레스 시간**이므로, 50점짜리 넓은 survey 한 번이 좁은 스윕
  대여섯 번보다 덜 해롭다. 이 한 방으로 극성·대략적 Vth·전류 스케일·생사가 한꺼번에 나온다.
- **모를 때** → 좁게 시작해 ×1.5 로 넓힌다. **Ig 가 안전 센서다** — 지수적 상승, 노이즈
  증가, 정/역 불일치, 낮은 Vg 로 돌아왔을 때 베이스라인 미복귀 중 하나라도 보이면 멈춘다.
- 확실하지 않으면 `dead` 로 단정하기보다 범위를 넓혀 한 번 더 보거나 `needs_human`.

`build_user_message()` = 문맥 블록(목적·경계·시드·소자·앞선 소자들) + 이력 블록
(턴마다 조건/관측/그때의 판단/경계 위반) + `"다음 행동을 정해라."`

### 4-12. `agent/policy.py` — 구현 두 가지

`propose(ctx, history) -> Proposal` 이 인터페이스의 전부다. session 은 LLM 인지 규칙인지
구분하지 않는다.

**`LLMPolicy`** — 모델 `claude-opus-5`, system prompt 에 `cache_control` 적용,
`output_config.format` 으로 JSON Schema 강제, 안전 분류기 거절 시 서버측 fallback 시도.
`stop_reason == "refusal"` 이면 `needs_human` 으로 떨어뜨린다.

> 재현성 메모: Claude Opus 5 는 `temperature`/`top_p` 를 받지 않는다(400).
> "temperature 0 고정"이 불가능하므로 리플레이 평가는 `effort` 를 고정하고
> 여러 번 돌려 **분포**로 본다.

**`HeuristicPolicy`** — LLM 없이 파이프라인을 끝까지 돌리기 위한 최소 구현.
Ig 가 abort 문턱을 넘으면 `leaky`, decade 가 충분하고 turn-on 이 창 안이면 `converged`,
아니면 **중심 유지하고 폭만 ×1.5 확장**. 3회 확장해도 안 잡히면 `needs_human`.

> 이건 "판정 규칙이 낫다"는 주장이 아니다. 배선 점검이나 스모크 테스트용이고,
> 실제로 애매한 케이스를 잘 못 가른다 — 그게 LLM 을 쓰는 이유였다.

> ⚠️ **`default_policy()` 는 현재 자격증명을 확인하지 않는다.** `anthropic` 패키지가
> 깔려 있기만 하면 `LLMPolicy` 를 고르므로, 키가 없으면 첫 호출에서 `TypeError` 로
> 죽는다. `dryrun` 은 `--heuristic` 으로 피할 수 있지만 `run_area.py` 에는 우회로가
> 없다. → 고칠 것 (7장).

### 4-13. `drivers/b1500.py` — B1500A

**SCPI/FLEX 문법을 아는 유일한 곳.** 받는 것은 슬롯 번호로 쓰인 순수 dict 하나.
EasyEXPERT 'Measurement Setup' 화면(VAR1/VAR2/Timing/Constants/Ranging)과 1:1 대응.

**VAR2 는 장비 기능이 아니라 파이썬 외부 루프로 구현한다** — 스텝마다 VAR1 sweep 1회를
돌리고 결과 앞에 VAR2 값 컬럼을 붙여 세로로 이어붙인다.

노트북에서 피 흘려 얻은 것 세 개가 그대로 들어 있다.

- **`pandas.applymap` shim** — pandas 3.0 에서 제거됐는데 pymeasure 0.16 의
  `read_data` 가 아직 쓴다. 동작이 같으므로 옛 이름을 다시 연결한다.
- **`_wait()` 는 serial poll(STB) 로 기다린다.** pymeasure 의 `check_idle()` 을 쓰면
  안 된다 — 그건 XE 측정 중에 `*OPC?` 를 써넣는데 B1500 이 측정 중 질의를 처리하지 못해
  응답이 안 온다. 증상: 에러큐는 깨끗하고 데이터도 안 나온 채 VISA 타임아웃까지 매달림.
  serial poll 은 출력버퍼/입력큐를 안 건드린다. STB bit4(0x10)=MAV 가 서면 완료.
- **`_drain_errors()`** — `*CLS` 로는 FLEX 에러큐가 안 비워질 수 있어, 이전 통신오류(NCIC)로
  쌓인 backlog 를 `ERRX?` 로 직접 제거한다.
- **타임아웃 300000 ms** — NI-488.2 는 타임아웃을 이산 단계(10/30/100/300/1000초)로
  **올림**한다. 600000 으로 적으면 실제로는 1000초가 걸린다.

### 4-14. `drivers/s300.py` — Cascade S300 프로버

GPIB 문법을 아는 유일한 곳. `$:set:resp on` 을 **먼저** 켜야 이후 명령이 `COMPLETE`
응답을 줘서 타임아웃 없이 완료를 확인할 수 있다 (이게 빠지면 모든 action 명령이 타임아웃).

축 방향(실측): **z 증가 = 척 위(팁 쪽), z 감소 = 척 아래.**

**코드가 대체하지 않는 준비 단계** — Nucleus UI 에서 사람이 먼저 끝내야 한다:
① 척 로드/진공 ② **Alignment** (안 하면 좌표가 통째로 어긋난다) ③ Tipping/Set Contact
④ 첫 소자에 팁 contact.

`contact_slow()` 가 팁 보호의 핵심이다. 실측 결과 `:set:cont:spee` 는 `:mov:cont` 전
구간 속도를 지배하지 않는다(25 µm/s 로 설정해도 1000 µm 를 0.28 s ≈ 3500 µm/s 로 이동).
그래서 분리높이→접촉높이 구간을 `:mov:abs` 로 25 µm 씩 잘라 올리고, **마지막 50 µm 만
`contact()` 에 맡긴다** — 등록된 접촉 높이 위로는 절대 안 올라간다.

### 4-15. `stacks/sd_dualgate.json` — 소자 스택

웨이퍼마다 바뀌는 값. 구조: `gates`(유전막 층 테이블, `e_max_MV_per_cm`, `v_user_max`),
`gate_gate`, `channel`, `geometry`, `limits`, `reference`.

`_comment_*` 키에 **왜 이 숫자인지**를 적어뒀다. 예: TG 는 절연파괴 60 V 지만
`v_user_max: 55` 로 운용 한계가 이긴다 — *"파괴가 아니라 얼마나 밀고 싶은지가 정한다."*

기하 비대칭이 중요하다: BG 4 / TG 2 / offset 1 µm 라 TG 는 채널 중앙만 덮는다.
`V_TG<0` 이면 중앙이 율속 → 커플링 기울기(ΔVth_BG ≈ −0.51·ΔV_TG)대로 밀리고,
`V_TG>0` 이면 offset 이 율속 → 이동이 금방 포화. **그래서 TG 창이 −5 ~ +20 V 비대칭이다.**

### 4-16. `examples/` — 진입점

| 스크립트 | 용도 |
|---|---|
| `dryrun.py` | 장비 없이 전체 루프. `--vth` 로 시드 범위 밖에 두면 범위 확장을 볼 수 있다 |
| `run_area.py` | 실제 장비로 area 하나. `--dry` 는 검증만, `--set-reference` 는 원점 등록 |
| `replay_legacy.py` | 과거 CSV 로 에이전트 평가. status 분포·경계 접촉·불안정 케이스 |

`run_area.build_seed()` 는 이제 숫자를 들고 있지 않고 `seed.seed_transfer(stack)` 를
부른다 (4-18).

### 4-18. `seed.py` — 시드를 스펙에서 계산

시드를 손으로 적어 두면 **stack.json 을 갈아끼워도 따라오지 않는다.** 경계만 움직이고
조건은 그대로여서, 둘이 어긋난 채로 측정이 나간다. 그래서 stack 하나에서 경계와
시드를 함께 만든다.

```
상한/하한   ← safety.bounds_from_stack   (유전막 두께·ε 에서 계산된 벽)
시작/끝/스텝 ← stack["expected"]          (설계자료의 Vth·SS·특성화 범위)
```

**요점은 스텝을 SS 에서 정하는 것이다.**

```python
step = ss / SS_POINTS_PER_DECADE          # 1.5 → SS 0.27 이면 0.18 V
```

SS[V/decade] 는 turn-on 이 얼마나 좁은 구간에서 끝나는지를 말해준다. SS=0.27 이면
7 decade 전이가 약 2 V 안에서 끝나므로, **0.5 V 스텝이면 그 구간에 점이 3~4개뿐이라
SS 가 0.44 로 부풀려진다**(실측 검증). 스텝을 SS 의 분수로 잡으면 이 실수가 구조적으로
안 나온다. `tests/test_seed.py` 가 이걸 지킨다.

나머지 규칙:

| 값 | 규칙 |
|---|---|
| `stop` | `min(경계×0.95, 특성화 범위 상한)` — 더 밀 이유가 없고 TDDB 적립이 준다 |
| `start` | `Vth − 6×SS − 2 V` (floor 를 봐야 `floor_slope` 가 계산된다), 경계로 클립 |
| `points` | 스텝에서 환산, `max_points×0.9` 안으로 (왕복이면 절반) |
| 게이트 comp | 경계 상한 × 0.1 |
| 드레인 comp | `min(경계 상한, 기대 Ion × 1000)` |
| `vd` | 특성화된 Vd 중 작은 쪽 (선형영역) |

`expected` 가 없는 stack 이면 **경계 전체를 61점으로 훑는 blind survey** 로 떨어진다.
어느 쪽으로 만들어졌는지는 `plan.note` 에 남아 결과 폴더에서 확인된다.

> `plan` 과 `safety` 만 import 한다. 반환된 plan 도 **반드시 validate 를 다시 탄다** —
> 여기서 경계를 참고하긴 하지만 검사는 여전히 session 이 건다(3-③).

### 4-19. `tests/` — 28개

`pytest` 없이 `python -m measauto.tests.run_all` 로 돈다. 각 모듈이 `TESTS` 리스트를
자동 수집한다.

**`test_safety.py` (6)** — BG/TG 스택 계산을 **손계산과 대조**(깨지면 계산이 틀린 것),
단자쌍 경계, output 케이스 검출, 운용 창·포인트 수, 컴플라이언스.

**`test_metrics.py` (7)** — 답을 아는 가짜 소자에서 **Vth/SS 회복**, Vth 가 창 밖일 때
평평한 곡선, **죽은 소자를 자신 있게 구분할 수 없음**(이게 LLM 을 쓰는 근거),
음수 전류에서 log10 안 깨짐, 왕복 히스테리시스, **payload 크기**, output 지표.

**`test_loop.py` (8)** — plan 왕복 직렬화, **패치가 지정 필드만 건드리는지**,
var2 신규 생성, 스키마 엄격성, 단자→채널 매핑, **배선 안 된 단자 거부**,
**범위가 넓어져 곡선이 잡히는지**, **경계 넘는 plan 이 실제로 막히는지**.

---

## 5. 한 번의 측정이 지나는 경로

```
stacks/sd_dualgate.json
   └─ safety.bounds_from_stack() ──> Bounds        (경계)

   └─ seed.seed_transfer() ─────────> IVPlan       (조건)
        (경계 + stack["expected"] 에서 계산)

Session.run_site(site, seed)
   │
   ├─ safety.validate(plan, bounds)                 ← 실행 직전 검사
   │
   ├─ Executor.run(site, plan)
   │     ├─ s300: separate → move_xy → contact_slow
   │     ├─ executor.plan_to_spec()   단자 → SMU 채널
   │     ├─ drivers.B1500.sweep(spec) VAR2 루프 × VAR1 sweep
   │     └─ frame.canonicalize + fill_constants     슬롯 → 단자 이름
   │                                                → DataFrame
   ├─ metrics.summarize(df, plan, bounds)  ────────> 지표 dict
   ├─ metrics.downsample_curve + to_llm_payload ──> payload (~900자)
   │
   ├─ agent.policy.propose(ctx, history)
   │     └─ prompt.build_user_message() → Claude → PROPOSAL_SCHEMA
   │                                     → Proposal(status, patch)
   ├─ agent.schema.apply_patch(seed, patch) ──────> 다음 IVPlan
   │
   └─ store.save(site, plan, df, metrics, proposal)
         → data.csv / plan.json / metrics.json / curve.png / index.csv

  status == "propose" 면 위로 돌아가 다시 validate 부터.
```

---

## 6. 무엇을 바꾸려면 어디를 건드리나

| 하고 싶은 것 | 고칠 곳 | 코드 수정? |
|---|---|---|
| 스윕 범위·스텝 변경 | `stacks/*.json` 의 `expected` (시드가 따라 움직인다) | ❌ |
| 웨이퍼(층 구조)가 바뀜 | `stacks/*.json` 새로 추가 → `--stack` | ❌ |
| 시드 생성 규칙 자체 | `seed.py` 상단 상수 (`SS_POINTS_PER_DECADE` 등) | 값만 |
| 안전 여유를 더 주고 싶음 | `bounds_from_stack(derate=0.8)` 또는 json 의 `v_user_max` | 값만 |
| 프로브 배선이 바뀜 | `config.py` 의 `roles` | 값만 |
| dual gate (SMU 4개) | `config.DUAL_GATE` 사용 | 값만 |
| 측정 목적을 바꿈 | `SessionConfig.objective` | 값만 |
| CSV 원본 ↔ 다운샘플 전환 | `SessionConfig.send_full_csv` / `csv_max_rows` | 값만 |
| 에이전트 판단 기준 | `agent/prompt.py` 의 `SYSTEM_PROMPT` | 프롬프트 |
| 에이전트가 낼 수 있는 필드 추가 | `agent/schema.py` (`PlanPatch` + `_PATCH_PROPS` + `apply_patch`) | ✅ |
| 새 지표 추가 | `metrics.py` + `to_llm_payload` 의 `default_keep` | ✅ |
| 새 측정 종류 (sampling 등) | `plan.py` + `executor.plan_to_spec` + `drivers/b1500.py` | ✅ |
| 장비 교체 | `drivers/` 만 | ✅ |

**표의 위쪽 절반이 값이고 아래쪽이 코드다.** 일상 운용에서 건드리는 건 위쪽이다.

---

## 7. 현재 상태와 미완성

README 의 "만드는 순서" 기준:

1. `plan.py` + `drivers/b1500.py` — ✅ `grid_measure_config` 의 파라미터화가 거의
   그대로 `IVPlan` 이 됐다
2. `metrics.py` — ✅ 장비 없이 기존 CSV 로 검증됨. 나머지가 전부 여기 의존한다
3. `safety.py` + `executor.py` + `store.py` — ✅ **여기까지가 반자동.**
   이 상태로 몇 개 측정해보는 게 중요하다
4. `agent/` — 뼈대 완성. 3단계에서 손으로 plan 을 고친 이력이 곧 프롬프트 예시다

### 알려진 미완성 / 주의

- **`default_policy()` 가 자격증명을 확인하지 않는다** (4-12). 키 없이 `run_area.py` 를
  돌리면 크래시한다. 고칠 것.
- **`ANTHROPIC_API_KEY` 미설정** — 현재 LLM 판단 불가.
- **SMU 3개** — dual gate 를 다 물리려면 4개 필요. 지금 `roles` 에 TG 가 없다.
- **`sampling` 은 아직 `IVPlan` 에 없다.** transfer/output 만으로 인터페이스를 굳히고
  세 번째 use case 때 가른다.
- **`run_area.py` 의 `objective` 가 하드코딩** — CLI 인자로 빼면 코드 수정 없이 바꿀 수 있다.
- **GPIB 통신 불통** (2026-08-16 기준). 진단 기록은
  [`docs/260816_GPIB진단_measauto_세션기록.md`](../docs/260816_GPIB진단_measauto_세션기록.md).

### 검증 명령

```powershell
cd E:\semi-auto
.\.venv\Scripts\python.exe -m measauto.tests.run_all                          # 21개
.\.venv\Scripts\python.exe -m measauto.examples.dryrun --heuristic            # 전체 루프
.\.venv\Scripts\python.exe -m measauto.examples.run_area --coords utils\grid_4x4.csv --dry
```
