# measauto — 자동측정 에이전트

기존 `projects/grid_measure/*.ipynb` 는 그대로 두고, 그 안에 섞여 있던
**장비 문법 / 측정 조건 / 지표 / 판단** 을 변경 주기에 따라 갈라 놓은 패키지.

## 왜 이렇게 갈랐나

`main` 을 사람이 고치는 구조면 에이전트도 코드를 고쳐야 한다. **코드 생성은
검증도 롤백도 안 된다.** `main` 이 plan(값)을 소비하는 구조면 에이전트는
dict/JSON 만 만들면 되고, 필드가 유한하니 실행 전 전수 검사가 된다.

같은 이유로 안전 경계 상수도 `safety.py` 에 박지 않고 `stacks/*.json` 에서
계산해 내려보낸다 — 웨이퍼가 바뀌어도 코드는 안 건드린다.

부수 효과: 조건이 데이터라 결과 CSV 옆에 같이 저장된다. 에이전트가 조건을
만들기 시작하면 이게 유일한 실험 기록이다.

| 층 | 변경 주기 | 형태 | 파일 |
|---|---|---|---|
| 장비 통신 (VISA/SCPI, 슬롯 매핑) | 거의 없음 | 코드 | `drivers/`, `config.py` |
| 측정 primitive | 가끔 | 코드 | `executor.py` |
| 측정 조건·대상 | 매번 | **값** | `plan.py` |
| 소자 스택·기하 | 웨이퍼마다 | **값** | `stacks/*.json` |
| 저장·분석 | 독립 | 코드 | `store.py`, `metrics.py` |

### 불변 규칙 둘

1. `drivers` 는 이 패키지의 아무것도 import 하지 않는다.
2. `agent` 는 `drivers` 를 import 하지 않는다 → 장비 없이 리플레이 가능.

그리고 **`validate` 는 `executor` 밖에 있다.** executor 는 안전하다고 가정하고
실행만 하고, 검증은 `session` 이 executor 를 부르기 직전에 건다. 한 함수에
섞으면 "검사를 통과시키려고 실행을 고치는" 유혹이 생긴다.

## 에이전트의 경계

| | 담당 |
|---|---|
| 층 정보 입력 (`stacks/*.json`) | 사람 |
| 측정 목적 (objective 한 줄) | **사람** — 데이터에서 안 나오는 유일한 정보 |
| 안전 경계 계산 | 함수 (`safety.bounds_from_stack`) |
| 시드 plan | 고정 — 관측 0개면 판단 근거가 없다 |
| 지표 계산 | 고정 함수 (`metrics.py`) |
| 관측 후 다음 plan 결정 | 에이전트 |
| 수렴/이상 판정 | 에이전트 |
| 최종 검사 | validator — 에이전트는 존재도 모른다 |

에이전트가 `safety.py` 를 수정할 수 있으면 검사받는 쪽이 검사를 고쳐 쓰는
구조가 된다. 그래서 에이전트가 낼 수 있는 건 **시드 plan 에 덮어쓸 패치**
(`agent/schema.py`) 뿐이고, 필드가 열거되어 있다.

## 지금 바로 돌려보기 (장비 없이)

```powershell
cd E:\semi-auto
.\.venv\Scripts\python.exe -m measauto.tests.run_all          # 21개 테스트
.\.venv\Scripts\python.exe -m measauto.examples.dryrun --heuristic
```

`dryrun` 은 가짜 소자를 상대로 시드 측정 → 지표 → 판단 → 다음 plan → 안전
검사 → 저장 루프를 끝까지 돈다. `--vth 9` 처럼 시드 범위(0~5 V) 밖에 두면
에이전트가 범위를 넓혀 곡선을 잡아내는 걸 볼 수 있다.
`ANTHROPIC_API_KEY` 가 있으면 `--heuristic` 을 빼고 LLM 으로 돌린다.

기존 측정 데이터로 지표를 확인:

```powershell
.\.venv\Scripts\python.exe -m measauto.examples.replay_legacy `
    --glob "projects/grid_measure/results_idvd/manual*.csv" --heuristic
```

## 실제 장비로

```powershell
# 먼저 검증만 (장비 안 엶)
.\.venv\Scripts\python.exe -m measauto.examples.run_area --coords utils\grid_4x4.csv --dry
# 원점 등록 + 실행 (사람이 첫 소자에 팁 contact 시킨 상태에서)
.\.venv\Scripts\python.exe -m measauto.examples.run_area --coords utils\grid_4x4.csv --set-reference
```

코드 실행 전에 Nucleus UI 에서 사람이 끝내둬야 하는 것(코드가 대체하지 않음):
척 로드/진공 → **Alignment** → Tipping/Set Contact → 첫 소자에 팁 contact.

⚠️ `config.py` 의 기본 `roles` 는 지금 셋업(SMU 3개: BG/D/S)이다. dual gate 를
다 물리려면 SMU 가 4개 필요하다(슬롯 1이 WGFMU 라 SMU 번호가 한 칸 밀려 있음).
배선되지 않은 단자를 plan 이 건드리면 `executor` 가 막는다.

## LLM 에 무엇을 어떤 형태로 넘기나

원본 배열 통째로는 안 넘긴다. 201점 × 3열이면 6~8k 토큰이고 이력까지 쌓이면
5턴에 30k 를 넘는다. 비용보다 문제는 긴 배열 중간 정보를 잘 못 본다는 것,
그리고 SS 같은 걸 시키면 회귀가 아니라 눈대중으로 답한다는 것이다 —
0.22 를 0.3 이라 해도 틀린 걸 알아챌 방법이 없다.

**지표(정확히 계산) + 다운샘플 곡선(20~30점)** 의 조합으로 간다. 실측 payload
크기는 900자 안쪽이다.

> ⚠️ **현재 기본값은 `SessionConfig.send_full_csv = True` 라 위 설명과 다르게
> 측정 CSV 원본 전체가 그대로 간다.** 두 방식을 직접 비교해보려고 켜 둔 것이다.
> 실측 (`results_idvd/subsite_1.csv`, 91 KB / 2,430 행):
>
> | 설정 | payload | 대략 토큰 |
> |---|---|---|
> | `send_full_csv=True` (`data_csv`) | 105,479 자 | ≈ 30,100 |
> | `send_full_csv=False` (`curve`) | 903 자 | ≈ 258 |
>
> 이력이 턴마다 쌓이므로 5턴이면 요청 하나가 **≈150k 토큰**이다. 비교가 끝나면
> `send_full_csv=False` 로 되돌리는 것을 권한다. `csv_max_rows` 로 행 수만 줄일
> 수도 있다(균등 추출).

```json
{"metrics": {"vth_cc": 2.1, "ss": 0.18, "ss_points": 5, "decades": 5.4,
             "floor_slope": 0.02, "gm_peak_vg": 4.6, "ig_max": 4e-12,
             "compliance_hit": false, "id_norm": 7.8e-7},
 "curve": {"x_name": "VBG", "x": [-2, -1.6, ...],
           "log_id": [-12.1, ...], "log_ig": [-12.4, ...], "id_sign": [1, ...]}}
```

- **Ig 필수** — 게이트 누설 판단은 Id 만으로 안 되고, t_ox 를 모를 때 경계를
  올릴 근거가 Ig 뿐이다.
- **로그로 주는 이유** — 데이터가 8 decade 에 걸쳐 있어 선형 소수점으로 쓰면
  off 영역이 전부 `0.00000000000012` 꼴이라 구분이 안 된다. 로그면 −12.9,
  −6.5 로 값 사이 거리가 균등해져 floor 평탄도와 decade 수가 숫자만 봐도
  드러난다. 사람이 transfer 를 항상 로그축으로 그리는 것과 같은 이유. Vg 는 선형.
- **함정** — 노이즈로 Id 가 음수로 찍히면 log10 이 깨진다. 산화물 TFT off
  영역에서 실제로 자주 나오니 `|I| + 1e-15` 로 받고 부호는 `id_sign` 에 따로.
- **이미지는 판단 입력이 아니다** — 픽셀→축 매핑이라 2.1인지 2.4인지 못 가르고,
  로그축에서 −12와 −13은 몇 픽셀 차이라 Ioff 판정이 특히 취약하다. 토큰도
  1~1.5k 로 다운샘플 배열보다 비싸고, 렌더링 스타일이 판단에 개입하는데 그
  영향은 추적이 불가능하다. 다만 PNG 를 `store` 에 같이 떨궈두면 나중에 사람이
  "왜 이렇게 판단했지"를 볼 때 훨씬 빠르다 — **로그용이지 입력용이 아니다.**

## 안전 경계가 계산되는 방식

유전막은 전압이 아니라 필드에 반응한다. `E = V/t_ox` 이고 파괴 메커니즘
(qEλ → impact ionization → avalanche)이 E 에만 의존한다. 직렬 스택은 D 가
연속이라 ε 작은 층에 필드가 몰린다.

SD 웨이퍼 BG (SiNx 50 nm/ε7 + SiO₂ 75 nm/ε3.9, E_max 3 MV/cm):

```
Σ t/ε = 50/7 + 75/3.9 = 26.374 nm
SiO2 한계 = 3 MV/cm × 3.9 × 26.374 nm = 30.9 V   ← 율속
SiNx 한계 = 3 MV/cm × 7.0 × 26.374 nm = 55.4 V
ε_eff = 125 / 26.374 = 4.74        (SmartSPICE EPSI = 4.74 와 일치)
C_BG  = ε0·ε_eff / 125 nm = 33.6 nF/cm²  (W=6 소자에서 µ ≈ 10.8 cm²/Vs)
```

→ `|V_BG − V_S|, |V_BG − V_D| ≤ 30 V`. TG 는 SiO₂ 200 nm 단일이라 파괴는 60 V
지만 **운용 한계 55 V** 가 이긴다(파괴가 아니라 "얼마나 밀고 싶은지"가 정한다).
게이트끼리는 두 유전막 직렬이라 ≈ 90 V 로 더 관대하다.

설계값 3~4 MV/cm 가 진성 한계의 1/3인 건 국소 결함 집중과 TDDB(시간 누적)
때문이다. **스윕 반복은 그 시간을 적립하는 것**이라 "지난번에 괜찮았으니
괜찮다"가 성립하지 않는다. 그래서 `max_points` 와 소자당 iteration 상한이
경계에 같이 들어 있다.

이 숫자들은 `tests/test_safety.py` 가 손계산과 대조한다. 깨지면 계산이 틀린 것.

## 만드는 순서 (지금 어디까지 와 있나)

1. `plan.py` + `drivers/b1500.py` — ✅ `grid_measure_config` 의 파라미터화가
   거의 그대로 `IVPlan` 이 됐다.
2. `metrics.py` — ✅ 장비 없이 기존 CSV 로 검증됨. 제일 오래 걸리고 나머지가
   전부 여기 의존한다.
3. `safety.py` + `executor.py` + `store.py` — ✅ **여기까지가 반자동.**
   이 상태로 몇 개 측정해보는 게 중요하다.
4. `agent/` — 뼈대 완성. 3단계에서 손으로 plan 을 고친 이력이 곧 프롬프트 예시다.

`sampling` 은 아직 `IVPlan` 에 넣지 않았다. transfer/output 만으로 인터페이스를
굳히고 세 번째 use case 때 가른다.

## 검증

- `tests/run_all.py` — 21개. 안전 경계 손계산 대조, 답을 아는 가짜 소자로
  Vth/SS 회복, 음수 전류에서 log10 안 깨지는지, 패치가 지정 필드만 건드리는지,
  경계 넘는 plan 이 실제로 막히는지.
- `examples/dryrun.py` — 다중턴 수렴 횟수 / 경계 접촉 횟수를 끝까지 센다.
- `examples/replay_legacy.py` — 과거 CSV 로 '첫 판단' 을 반복 측정.
  같은 소자에서 결론이 갈리면 프롬프트가 부실하다는 신호다.

**재현성 주의**: Claude Opus 5 는 `temperature`/`top_p` 를 받지 않는다(400).
"temperature 0 고정" 은 이 모델에서 불가능하므로, 리플레이는 `effort` 를
고정하고 여러 번 돌려 **분포**로 본다.

## dual gate (SD 웨이퍼) 메모

- **커플링**: ΔVth_BG ≈ −0.51·ΔV_TG. TG 범위는 절연파괴가 아니라 얼마나 밀고
  싶은지가 정한다(60 V 여유가 있어도 ±15면 충분).
- **비대칭**: BG 4 / TG 2 / offset 1 µm 이라 TG 는 중앙만 덮는다.
  V_TG<0 이면 중앙이 율속 → 기울기대로 밀리고, V_TG>0 이면 offset 이 율속 →
  이동이 금방 포화. 그래서 창이 −5 ~ +20 V 비대칭이다(`stacks` 의
  `tg_window_V`).
- **순서**: ① BG transfer, TG=0 (앵커) ② TG transfer, **BG 는 켜둔 채**
  (BG=0 이면 offset 이 안 열려 잘못된 곡선 → "범위가 좁아서"로 오해하면 위험)
  ③ 2D 맵으로 커플링 기울기 실측.
- 안 쓰는 게이트는 띄우지 말고 0 V. TG 스텝 자체가 bias stress 라 0에서
  바깥으로 번갈아(0, +5, −5, +10, −10…) 가고 카나리아를 끼운다.

## area 단위 운용

탐색 자체가 소자를 오염시킨다. 소자마다 탐색하지 말고 area 당 1~2개로
캘리브레이션한 뒤 나머지엔 확정 plan 을 적용한다 (`SessionConfig.calibration_sites`).
`prior_devices` 가 프롬프트에 누적되면 3번째 소자쯤엔 한 턴에 끝난다.

**iteration 횟수 자체가 스크리닝 지표다** — LLM 호출이 몰리는 곳이 곧 이상
소자다. `results/index.csv` 의 `iteration` 열을 세면 바로 보인다.

## 결과 레이아웃

```
results_agent/
  index.csv                      ← 모든 측정 한 줄씩 (status, vth, ss, iteration…)
  A/d1/iter00/
      data.csv        정규화된 측정 데이터 (V_BG, I_D, I_BG, …)
      plan.json       이 데이터를 만든 조건 전부
      metrics.json    지표 + 에이전트 판단/근거 + LLM 에 실제로 넘긴 payload
      curve.png       로그용 그림
```
