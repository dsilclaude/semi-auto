# 260816 세션 기록 — GPIB 통신 진단 + measauto 스윕 범위 구조

작성: 2026-08-16 / 대상 PC: UNL_microscope (Windows 11), 프로젝트 `E:\semi-auto`

이 문서는 그날 대화에서 확인된 것을 그대로 남긴 것이다. **확인된 사실 / 배제된 가설 /
아직 안 한 것**을 구분해 적었다. 재현용 스크립트는 본문에 코드째로 넣었다
(임시 폴더에 있던 것이라 지워지면 없어진다).

---

## 0. 한 줄 요약

측정을 돌리려다 GPIB 통신이 안 되는 것을 발견했고, **PC·드라이버·어댑터는 정상임을
증명**했다. 남은 원인은 GPIB 케이블 건너편(장비 또는 케이블)이며, 마지막 확인 단계에서
어댑터 USB가 빠진 채로 중단되었다. 병행해서 `measauto` 의 스윕 범위 결정 구조를
정리하고 `anthropic` 을 설치했다(API 키는 미보유).

---

## 1. 현재 상태 (이 문서 작성 시점)

| 항목 | 상태 |
|---|---|
| GPIB 통신 | ❌ 불통. **어댑터 USB가 PC에서 분리된 상태** |
| NI GPIB-USB-HS 어댑터 | ✅ 정상 (컨트롤러 기능 검증 완료) |
| NI-VISA / 드라이버 | ✅ 정상 |
| B1500A / S300 | ❓ 미확정 — 케이블 분리 검사를 못 끝냈다 |
| `anthropic` 패키지 | ✅ 0.122.0 설치, `requirements.txt` 반영 |
| `ANTHROPIC_API_KEY` | ❌ 없음 (연구실 계정 확인 중) |
| `default_policy()` 수정 | ❌ 안 함 (아래 5-3 참고) |

---

## 2. GPIB 진단 — 시간순 기록

### 2-1. 최초 상태 (아무것도 건드리기 전)

```
NI-VISA list_resources : ('ASRL1::INSTR', 'GPIB0::17::INSTR', 'GPIB0::28::INSTR')
  B1500 GPIB0::17 -> VisaIOError: VI_ERROR_IO
  S300  GPIB0::28 -> 'Cascade Microtech, S300 Theta, 586480504, 3, 3'
```

**이 시점에 이미 B1500 만 고장 상태였다.** S300 은 정상 응답했다. 이 사실이 이후
모든 추론의 기준점이다.

### 2-2. 정밀 진단 직후 — 버스 전체가 먹통

`GPIB0::INTFC` 를 열어 속성을 읽고 `GPIB0::17` 에 `clear()`(device clear)를 보낸
직후부터 **모든 주소가 `VI_ERROR_ABORT`** 가 되고 NI-VISA 목록에서 GPIB0 이 통째로
사라졌다.

```
CIC(controller-in-charge) = 0     ← PC가 버스 제어권 없음
System controller         = 1
NI-VISA list_resources    = ('ASRL1::INSTR',)
```

> 정직하게: B1500 고장은 이 조작 이전부터 있었지만, **버스 전체가 죽은 시점은 이
> 조작 직후**다. 인과를 단정할 수는 없으나 시간적으로 인접하다.

### 2-3. 배제된 가설들 (전부 해봤고 전부 아니었다)

| 가설 | 조치 | 결과 |
|---|---|---|
| Jupyter 커널이 VISA 세션 점유 | 커널 재시작 (PID 26060 → 9324) | 변화 없음 |
| NI MAX 가 보드 점유 | NIMax.exe 종료 확인 | 변화 없음 |
| 드라이버 상태 꼬임 | USB 재연결 | 변화 없음 |
| 제어권(NCIC) 상실 | `send_ifc()` | **CIC 0→1 성공**, 그래도 통신 불가 |
| Keysight VISA 가 NI 보드 점유 | Keysight VISA 로 직접 접근 | 아님 — 아래 참고 |

**Keysight VISA 건**: 기본 RM(`visa32.dll` IVI 라우터)은 `GPIB0::11`, `GPIB1::11`,
`GPIB2::17` 을 계속 목록에 보여주지만 실제로는 못 연다
(`GPIB2::17` → `RSRC_NFOUND`, `GPIB0::11` → `TMO`). Keysight 의 `GPIB0` 은 NI 보드가
아니라 **자기 쪽 82357B 어댑터 매핑**이고 그건 꽂혀 있지 않다. 즉 그 목록은
Connection Expert 에 남은 **옛 설정 찌꺼기**이지 실시간 감지가 아니다.
→ `measauto`/노트북이 `VISA_LIB = nivisa64.dll` 를 명시하는 이유가 이것이다.

### 2-4. 결정적 증거 — NI-488.2 DLL 직접 호출

VISA 를 우회해 드라이버에 직접 물었다(`ni4882.dll`).
`ibic.exe` 는 파이프 입력을 안 받아 못 쓴다(콘솔 전용).

```
ibfind('GPIB0') -> ud=31000                     보드 열림
ibsic           -> ibsta = CMPL|CIC|ATN         제어권·ATN 라인 구동 성공
ibln (pad 1)    -> ibsta = ERR..., iberr = EBUS  명령 바이트 전송 실패
```

**해석 — 여기가 이 문서의 핵심이다.**

- `ibsic` 성공 = 어댑터가 실제로 버스 라인을 구동했다 → **어댑터 정상.** 고장 아님.
- `EBUS` = 명령 바이트 핸드셰이크 실패.
- 버스에 장비가 **아예 없으면** `ENOL`(리스너 없음)이 난다. `EBUS` 는 그게 아니라
  **뭔가 물려 있는데 핸드셰이크 라인(NRFD/NDAC)을 잡고 안 놓을 때** 나온다.

가장 흔한 원인은 **케이블은 꽂혀 있는데 전원이 꺼진 장비**다. 꺼진 장비가 NDAC 을
물면 버스 전체가 죽는다(그래서 멀쩡한 S300 도 같이 안 된다).

B1500 을 여러 번 전원 재투입해도 안 풀렸다 → **범인이 B1500 이 아닐 수 있다.**
Keysight 설정에 `주소 11` 이 남아 있는 점에 비추어, **17·28 외의 제3의 장비가
체인에 물린 채 꺼져 있는지** 확인이 필요하다.

### 2-5. 중단 지점

"GPIB 케이블을 빼라"는 지시를 **USB 쪽을 빼는 것으로** 진행하여 어댑터가 PC 에서
사라졌다.

```
ibfind('GPIB0') -> ud=-1, iberr=EDVR
Get-PnpDevice -Class GPIB -> Present = False
```

---

## 3. 다음에 할 일 (GPIB) — 여기서 이어서 하면 된다

1. **어댑터 USB 를 PC 에 다시 꽂는다.** (없으면 검사 자체가 불가)
2. 어댑터의 **24핀 IEEE-488 커넥터**(나사 2개, 장비로 가는 넓적한 쪽)에서
   **케이블만 분리**한다. 어댑터는 PC 에 꽂힌 채로 둔다.
3. 아래 `ni4882_direct.py` 를 돌린다.
   - `EBUS` 가 **사라지면** → 버스에 물린 장비 중 하나가 범인. 4번으로.
   - `EBUS` 가 **그대로면** → 어댑터의 GPIB 커넥터 또는 케이블 문제.
4. 장비를 **하나씩만** 붙이며 매번 재검사 (S300 만 → B1500 만 → …).
   붙이는 순간 `EBUS` 가 나는 그 장비가 범인.
5. 데이지체인 중간 장비의 **전원**도 전부 확인. 꺼진 채 물려 있으면 안 된다.
   특히 **주소 11 번 장비**가 있는지 확인할 것.

---

## 4. 재현용 스크립트

### 4-1. 읽기 전용 통신 점검

```python
# -*- coding: utf-8 -*-
"""VISA 라이브러리 -> 리소스 목록 -> *IDN? 만. 장비 상태를 바꾸지 않는다."""
import os
import pyvisa

VISA_LIB = r"C:\Windows\System32\nivisa64.dll"
S300_GPIB, B1500_GPIB = "GPIB0::28::INSTR", "GPIB0::17::INSTR"

for p in (VISA_LIB, r"C:\Windows\System32\visa32.dll"):
    print(f"{'OK ' if os.path.exists(p) else 'X  '} {p}")

for label, lib in (("NI-VISA", VISA_LIB), ("기본", None)):
    try:
        rm = pyvisa.ResourceManager(lib) if lib else pyvisa.ResourceManager()
        print(f"{label}: {rm.list_resources()}")
    except Exception as e:
        print(f"{label}: 실패 -> {type(e).__name__}: {e}")

rm = pyvisa.ResourceManager(VISA_LIB)
for name, addr in (("B1500", B1500_GPIB), ("S300", S300_GPIB)):
    try:
        inst = rm.open_resource(addr)
        inst.timeout = 5000
        print(f"{name} {addr}: {inst.query('*IDN?').strip()!r}")
        inst.close()
    except Exception as e:
        print(f"{name} {addr}: 실패 -> {type(e).__name__}: {e}")
```

### 4-2. NI-488.2 직접 호출 (`ni4882_direct.py`) — 진단의 핵심 도구

VISA 를 우회하므로 "어댑터 문제인지 버스 문제인지" 를 가른다.
64bit NI-488.2 는 `ibfind` 가 아니라 **`ibfindA`** 를 export 한다(주의).

```python
# -*- coding: utf-8 -*-
"""NI-488.2 DLL 직접 호출 — VISA 우회. ibln 으로 주소별 리스너 확인."""
import ctypes

BITS = [(0x8000, "ERR"), (0x4000, "TIMO"), (0x2000, "END"), (0x0100, "CMPL"),
        (0x0040, "REM"), (0x0020, "CIC"), (0x0010, "ATN"), (0x0008, "TACS"),
        (0x0004, "LACS")]
IBERR = {0: "EDVR(시스템)", 1: "ECIC(제어권없음)", 2: "ENOL(리스너없음)",
         3: "EADR(주소지정오류)", 4: "EARG", 5: "ESAC", 6: "EABO(중단/타임아웃)",
         7: "ENEB(보드없음)", 11: "ECAP", 14: "EBUS(버스에러)"}


def sta_str(s):
    return "|".join(n for b, n in BITS if s & b) or "0"


dll = ctypes.windll.LoadLibrary("ni4882.dll")
ibfind = getattr(dll, "ibfindA", None) or dll.ibfind   # 64bit 는 ibfindA
ibfind.restype = ctypes.c_int
ibfind.argtypes = [ctypes.c_char_p]
dll.ThreadIbsta.restype = dll.ThreadIberr.restype = ctypes.c_int

ud = ibfind(b"GPIB0")
print(f"ibfind -> ud={ud} ibsta={sta_str(dll.ThreadIbsta())} "
      f"iberr={IBERR.get(dll.ThreadIberr())}")
if ud < 0:
    raise SystemExit("보드 열기 실패 — 어댑터가 PC 에 꽂혀 있는지 확인")

dll.ibsic(ud)                                   # Interface Clear
sta = dll.ThreadIbsta()
print(f"ibsic -> ibsta={sta_str(sta)}  CIC={'있음' if sta & 0x0020 else '없음'}")

listen = ctypes.c_short(0)
found = []
for pad in range(1, 31):
    dll.ibln(ud, pad, 0, ctypes.byref(listen))
    sta = dll.ThreadIbsta()
    if sta & 0x8000:
        err = dll.ThreadIberr()
        print(f"pad {pad}: 에러 iberr={IBERR.get(err, err)}")
        break
    if listen.value:
        print(f"pad {pad}: 리스너 있음")
        found.append(pad)
print("검출:", found or "없음")
```

실행:

```powershell
E:\semi-auto\.venv\Scripts\python.exe ni4882_direct.py
```

### 4-3. 판정표

| 결과 | 뜻 |
|---|---|
| `ibsic` 성공 (ibsta 에 CIC·ATN 비트) | 어댑터 정상 |
| `ibln` → `EBUS` | 버스에 뭔가 물려서 라인을 잡고 있음 (꺼진 장비 의심) |
| `ibln` → `ENOL` / 리스너 0 | 버스에 장비 없음 (케이블 분리 상태의 정상 반응) |
| `ibfind` → `ud=-1`, `EDVR` | 어댑터가 PC 에 없음 |

---

## 5. 그 밖에 확인된 것

### 5-1. 노트북 두 개를 동시에 돌리면 안 된다

`grid_measure_idvd.ipynb` 와 `grid_measure_idvd_fast.ipynb` 는 **같은 주소**
(`GPIB0::17`, `GPIB0::28`)를 쓴다. 커널이 둘이면 같은 장비에 세션이 두 개 붙는다.

한쪽이 `XE` 로 측정을 걸어 출력버퍼에 데이터가 찬 상태에서 다른 쪽이 `*CLS` 나
`ERRX?` 를 쏘면 B1500 의 명령 파서가 엉키고, 그대로 커널이 죽으면 장비가 데이터를
든 채 hang 된다. **이번 사고의 유력한 발단이다.**

→ **한 번에 노트북 하나만.** 바꿀 때는 쓰던 커널을 먼저 종료할 것.

### 5-2. 매번 IFC 를 보내는 게 좋다

새 프로세스마다 `CIC` 가 0 으로 돌아온다(정상 동작으로 보임). `grid_measure_config_v2.ipynb`
의 `reset_gpib_controller()` 가 연결 전에 IFC 를 보내 이를 해결하는데,
**`grid_measure_idvd.ipynb` / `_fast.ipynb` 에는 그게 빠져 있다.** 넣어두면 산발적
접속 실패가 줄어든다. (아직 안 넣었다)

### 5-3. `anthropic` 설치의 부작용 — 아직 안 고쳤다

`measauto/agent/policy.py` 의 `default_policy()` 는 **패키지 존재만 보고** LLM 을
고른다. 키가 있는지는 확인하지 않는다.

```python
def default_policy() -> Policy:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return LLMPolicy()
    try:
        import anthropic
        return LLMPolicy()          # ← 키가 없어도 여기로 온다
    except ImportError:
        return HeuristicPolicy()
```

| | 설치 전 | 설치 후 (지금) |
|---|---|---|
| `default_policy()` | `HeuristicPolicy` | `LLMPolicy` |
| `run_area.py` | 규칙 기반으로 동작 | **첫 판단에서 크래시** |

`dryrun` 은 `--heuristic` 으로 피할 수 있지만 `run_area.py` 에는 그 옵션이 없다.
→ **자격증명이 실제로 잡히는지**를 보고 고르도록 고칠 것. 함수 주석의 원래 의도가
그것이다("API 자격증명이 보이면 LLM, 아니면 규칙 기반").

키 없이 호출하면:

```
TypeError: Could not resolve authentication method.
           Expected one of api_key, auth_token, or credentials to be set.
```

---

## 6. measauto — 스윕 범위는 어디서 정해지나

3층으로 갈라져 있다. **코드를 고치는 게 아니라 값을 고친다.**

### ① 상한 (벽) — `stacks/sd_dualgate.json`

`safety.bounds_from_stack()` 이 층 두께·ε 에서 계산한다. 웨이퍼가 바뀌면 **json 만**
고친다.

```
|VBG − VS| ≤ 30 V,  |VBG − VD| ≤ 30 V      ← BG 스윕의 실질 한계
|VD  − VS| ≤ 20 V                           ← output 스윕 한계
|VBG − VTG| ≤ 90 V,  VTG ∈ [−5, 20] V (운용 창)
I(BG) ≤ 1e-6 A,  I(D) ≤ 1e-3 A,  I(S) ≤ 0.1 A
points ≤ 4000,  Ig abort ≥ 1e-9 A
 · BG: V_bd=30.9 V (율속층 SiO2), ε_eff=4.74, C=33.6 nF/cm²
 · TG: V_bd=60.0 V (율속층 SiO2), ε_eff=3.90, C=17.3 nF/cm²
```

### ② 시작 범위 (시드) — `examples/run_area.py` 의 `build_seed()`

**사람이 정하는 곳은 여기 하나뿐이다.**

```python
transfer("BG", start=-5.0, stop=25.0, points=61, vd=0.1,
         direction="double", gate_compliance=1e-7, drain_compliance=1e-3)
#  → transfer: VBG -5.0→25.0 V 61pt double | const S=0, D=0.1 | 122 pts
```

### ③ 이후 조정 — 에이전트

지표·곡선을 보고 `PlanPatch` 로 범위를 넓히거나 좁힌다. 경계를 넘는 제안은
`validate()` 가 실행 전에 막는다.

### 범위를 잡는 원칙 (`agent/prompt.py` 에 명문화되어 있음)

- **허용 범위를 알 때** → 전체를 성기게 **한 번에** 훑는다. 소자를 상하게 하는 건
  스윕 폭이 아니라 **누적 스트레스 시간**이라, 넓은 survey 1회가 좁은 스윕 6회보다
  덜 해롭다. 현재 시드가 이 전략.
- **모를 때** → 좁게 시작해 ×1.5 씩. **Ig 가 안전 센서**다 — 지수적 상승, 노이즈
  증가, 정/역 불일치, 베이스라인 미복귀 중 하나라도 보이면 넓히지 말고 멈춘다.

### 확인 명령 (장비 없이)

```powershell
cd E:\semi-auto
.\.venv\Scripts\python.exe -m measauto.tests.run_all
.\.venv\Scripts\python.exe -m measauto.examples.run_area --coords utils\grid_4x4.csv --dry
.\.venv\Scripts\python.exe -m measauto.examples.dryrun --heuristic --vth 9
```

키가 생긴 뒤 LLM 이 실제로 범위를 넓히는지 보려면 `--vth 40` 처럼 **경계(30 V) 밖**에
두고 `out_of_bounds` 를 내는지 보는 편이 유용하다(`--vth 9` 는 시드 범위 안이라 너무 쉽다).

---

## 7. 미해결 체크리스트

- [ ] **어댑터 USB 다시 꽂기** ← 이것부터
- [ ] GPIB 케이블 분리 상태로 `ni4882_direct.py` 재실행 (3장 절차)
- [ ] 범인 장비 특정 (하나씩 붙이기 / 주소 11 확인)
- [ ] `ANTHROPIC_API_KEY` — 연구실 계정 확인 중
- [ ] `default_policy()` 를 자격증명 기준으로 수정 (5-3)
- [ ] idvd 노트북 두 개에 `reset_gpib_controller()` 추가 (5-2)
- [ ] `objective` 를 CLI 인자로 빼기 (선택)
