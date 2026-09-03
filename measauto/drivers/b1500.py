"""drivers/b1500.py — Keysight B1500A. SCPI/FLEX 문법을 아는 유일한 곳.

바깥에서 받는 건 '슬롯 번호로 쓰인 순수 dict' 하나뿐이다:

    spec = {
      "var1":   {"unit":2,"name":"Vd","direction":"double","spacing":"linear",
                 "start":0.0,"stop":10.0,"points":81,"compliance":10e-3,
                 "power_comp":None},
      "var2":   {"unit":1,"name":"Vg","start":10.0,"stop":3.0,"points":15,
                 "compliance":10e-3} 또는 None,
      "constants": {3: {"v":0.0,"compliance":0.1}, ...},   # unit -> DC 조건
      "timing": {"hold":1.0,"delay":0.0,"step_delay":0.0,
                 "auto_abort":False,"post":"START"},
      "adc":    {"adc_type":"HRADC","mode":"AUTO","n":10,"auto_zero":False},
      "ranges": {1:{"mode":"limited","range":"1 nA"}, ...},
    }

EasyEXPERT 'Measurement Setup' 화면(VAR1/VAR2/Timing/Constants/Ranging)과 1:1.
VAR2 는 장비 기능이 아니라 파이썬 외부 루프로 구현한다(스텝마다 VAR1 sweep 1회).
"""

from __future__ import annotations

import time
from typing import Dict, Optional

import pandas as pd

# --- pandas 3.0 호환 shim ----------------------------------------------------
# pandas 2.1 에서 DataFrame.applymap -> DataFrame.map 으로 이름이 바뀌고
# 3.0 에서 applymap 이 제거됐다. pymeasure 0.16 의 B1500 read_data 가 아직
# applymap 을 써서 측정 데이터 읽을 때 에러난다. 동작이 같으므로 옛 이름을
# 다시 연결한다. (라이브러리가 pandas 3 을 지원하면 삭제 가능)
if not hasattr(pd.DataFrame, "applymap"):
    pd.DataFrame.applymap = pd.DataFrame.map


class B1500Error(RuntimeError):
    pass


_SWEEP_MODE = {
    ("single", "linear"): "LINEAR_SINGLE",
    ("double", "linear"): "LINEAR_DOUBLE",
    ("single", "log"): "LOG_SINGLE",
    ("double", "log"): "LOG_DOUBLE",
}


def _sweep_mode(direction: str, spacing: str) -> str:
    key = (str(direction).lower(), str(spacing).lower())
    if key not in _SWEEP_MODE:
        raise B1500Error(f"direction/spacing 조합 오류: {direction}/{spacing}")
    return _SWEEP_MODE[key]


def _range_name(mode: str, rng: Optional[str]) -> str:
    m = str(mode).lower()
    if m == "auto":
        return "Auto Ranging"
    if m == "limited":
        return f"{rng} limited auto ranging"
    if m == "fixed":
        return f"{rng} range fixed"
    raise B1500Error(f"ranging mode 오류: {mode!r} (auto|limited|fixed)")


def _points(cfg: dict) -> int:
    if cfg.get("step"):
        return int(round(abs(cfg["stop"] - cfg["start"]) / abs(cfg["step"]))) + 1
    return int(cfg["points"])


def _values(cfg: dict):
    n = _points(cfg)
    if n <= 1:
        return [float(cfg["start"])]
    return [cfg["start"] + (cfg["stop"] - cfg["start"]) * k / (n - 1) for k in range(n)]


def _comp(value):
    """compliance -> ramp_source 인자. None 이면 '' (장비 이전 설정 유지)."""
    return "" if value in (None, "") else value


class B1500:
    """B1500A 래퍼. 연결 · 정리 · staircase sweep 만 한다."""

    def __init__(self, address: str, visa_library: str = "",
                 timeout_ms: int = 300000):
        self.address = address
        self.visa_library = visa_library
        # NI-488.2 는 타임아웃을 이산 단계(10/30/100/300/1000초)로 '올림'한다.
        # 600000(10분)으로 적으면 실제로는 1000초(16.7분)가 걸린다. 300000 은
        # 정확히 300초로 적용된다.
        self.timeout_ms = timeout_ms
        self.inst = None

    # --- 연결 -------------------------------------------------------------
    def connect(self):
        from pymeasure.instruments.agilent import AgilentB1500

        # 최신 pymeasure(0.16+)의 AgilentB1500 는 read/write_termination 을
        # 내부에서 "\r\n" 으로 자동 설정하므로 여기서 넘기면 중복 인자 에러.
        kw = {"timeout": self.timeout_ms}
        if self.visa_library:
            kw["visa_library"] = self.visa_library
        b = AgilentB1500(self.address, **kw)

        # 연결 직후 청소: 이전 세션/통신오류(NCIC 등)로 남은 입력버퍼·에러큐를 비운다.
        b.clear()
        b.write("*CLS")
        n = self._drain_errors(b)
        if n:
            print(f"[b1500] 묵은 에러 {n}개 제거함")

        b.initialize_all_smus()
        b.data_format(21, mode=1)   # SMU 초기화 후 호출
        self.inst = b
        return self

    def close(self):
        try:
            if self.inst is not None:
                self.inst.adapter.close()
        except Exception as e:              # 세션 정리는 실패해도 진행
            print(f"[b1500] close 중 예외(무시 가능): {e}")
        finally:
            self.inst = None

    def idn(self) -> str:
        return self.inst.ask("*IDN?")

    def modules(self) -> str:
        return self.inst.ask("UNT?")

    # --- 상태 -------------------------------------------------------------
    @staticmethod
    def _drain_errors(b, max_reads: int = 500) -> int:
        """FLEX 에러큐를 +0('No Error') 나올 때까지 비운다.

        *CLS 로는 이 큐가 안 비워질 수 있어, 이전 통신오류(NCIC 등)로 쌓인
        +100 backlog 를 직접 제거한다."""
        n = 0
        for _ in range(max_reads):
            resp = b.ask("ERRX?")
            if resp.split(",")[0].strip() in ("0", "+0"):
                return n
            n += 1
        return n

    def _wait(self, timeout_s: float = 120, poll: float = 0.2) -> float:
        """측정이 끝날 때까지 GPIB serial poll(STB)로 기다린다.

        pymeasure 의 check_idle() 을 쓰면 안 된다. 그건 XE 측정이 도는 도중에
        '*OPC?' 를 써넣는데, B1500 은 측정 중 들어온 질의를 제때 처리하지 못해
        응답이 오지 않는다. 증상: 에러큐는 +0 으로 깨끗하고 데이터도 안 나온 채
        VISA 타임아웃까지 매달림. serial poll 은 출력버퍼/입력큐를 건드리지
        않아 측정을 방해하지 않는다. STB bit4(0x10)=MAV 가 서면 완료."""
        c = self.inst.adapter.connection
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            if c.read_stb() & 0x10:
                return time.time() - t0
            time.sleep(poll)
        raise B1500Error(
            f"측정이 {timeout_s}s 안에 끝나지 않음 (마지막 STB=0x{c.read_stb():02X})")

    def check(self) -> dict:
        """통신/준비 상태 확인. 값만 돌려주고 판정은 위층에서."""
        return {
            "idn": self.idn(),
            "opc": self.inst.ask("*OPC?").strip(),
            "modules": self.modules(),
        }

    # --- 측정 -------------------------------------------------------------
    # 측정 레인지가 낮을수록 점당 적분·정착 시간이 길어진다. 실측에서 1 nA →
    # 10 pA 로 두 decade 내리자 점당 시간이 몇 배가 됐다(3600점이 18분).
    RANGE_SLOWDOWN = {"10 pA": 4.0, "100 pA": 2.0, "1 nA": 1.0}

    @classmethod
    def expected_seconds(cls, npts: int, hold: float = 0.0,
                         meas_range: Optional[str] = None) -> float:
        """이 측정이 대략 몇 초 걸릴지. 타임아웃을 여기에 맞춰 잡는다.

        실측 기준: 236점 13.7s, 298점 ~30s (1 nA 레인지) → 점당 0.06~0.1s.
        여유를 줘서 점당 0.15s 로 보고, 민감한 레인지면 그만큼 곱한다.
        이보다 오래 걸리면 정상이 아니므로 빨리 실패하는 편이 낫다 —
        넉넉하게 잡아두면 아무 소식 없이 십수 분을 매달린다(실제로 그랬다).
        """
        slow = 1.0
        for name, factor in cls.RANGE_SLOWDOWN.items():
            if meas_range and name in meas_range:
                slow = factor
                break
        return 20.0 + hold + npts * 0.15 * slow

    def sweep(self, spec: dict, *, timeout_s: Optional[float] = None) -> pd.DataFrame:
        """staircase sweep 실행. spec 은 모듈 docstring 형식.

        timeout_s : 측정 완료 대기 상한. 안 주면 점 수에서 추정한다.
        반환: DataFrame. VAR2 사용 시 맨 앞에 VAR2 값 컬럼이 붙고
              스텝별 결과가 세로로 이어붙는다.
        """
        if self.inst is None:
            raise B1500Error("connect() 를 먼저 호출할 것")
        b = self.inst

        var1 = spec["var1"]
        var2 = spec.get("var2")
        timing = spec.get("timing", {})
        adc = spec.get("adc", {})
        ranges = spec.get("ranges", {}) or {}
        constants: Dict[int, dict] = {int(k): v for k, v in
                                      (spec.get("constants") or {}).items()}

        sweep_ch = int(var1["unit"])
        const_chs = [ch for ch in constants if ch != sweep_ch]
        active = [sweep_ch] + const_chs
        smus = {ch: getattr(b, f"smu{ch}") for ch in active}

        mode = _sweep_mode(var1.get("direction", "single"), var1.get("spacing", "linear"))
        nop = _points(var1)
        npts = nop if "SINGLE" in mode else 2 * nop

        if var2:
            var2_ch = int(var2["unit"])
            if var2_ch not in const_chs:
                raise B1500Error(
                    f"VAR2 unit(SMU{var2_ch}) 은 constants 에 있어야 한다")
            v2_vals = _values(var2)
        else:
            var2_ch, v2_vals = None, [None]

        frames = []
        for v2 in v2_vals:
            # --- 측정 모드/채널 (MM) ---
            b.meas_mode("STAIRCASE_SWEEP", *[smus[ch] for ch in active])
            for ch in active:
                s = smus[ch]
                s.enable()
                s.adc_type = adc.get("adc_type", "HRADC")
                s.meas_op_mode = "COMPLIANCE_SIDE"

            # --- Ranging Mode (RI) ---
            for ch in active:
                rc = ranges.get(ch) or ranges.get(str(ch))
                if rc:
                    smus[ch].meas_range_current = _range_name(rc["mode"], rc.get("range"))

            # --- ADC / Timing ---
            b.adc_setup(adc.get("adc_type", "HRADC"), adc.get("mode", "AUTO"),
                        adc.get("n", 10))
            b.adc_auto_zero = adc.get("auto_zero", False)
            b.sweep_timing(timing.get("hold", 0.0), timing.get("delay", 0.0),
                           step_delay=timing.get("step_delay", 0.0))
            b.sweep_auto_abort(timing.get("auto_abort", False),
                               post=timing.get("post", "START"))

            # --- VAR1 sweep 소스 (WV) ---
            pcomp = "" if var1.get("power_comp") in (None, "") else var1["power_comp"]
            smus[sweep_ch].staircase_sweep_source(
                "VOLTAGE", mode, "Auto Ranging",
                var1["start"], var1["stop"], nop, var1["compliance"], pcomp,
            )

            # --- Constants (DV). VAR2 채널이면 이번 스텝 값으로 덮어씀 ---
            for ch in const_chs:
                if ch == var2_ch:
                    val = v2
                    comp = var2.get("compliance", constants[ch].get("compliance"))
                else:
                    val = constants[ch]["v"]
                    comp = constants[ch].get("compliance")
                smus[ch].ramp_source("VOLTAGE", "Auto Ranging", val, _comp(comp),
                                     stepsize=0.1, pause=20e-3)

            # --- 실행 ---
            b.check_errors()
            b.clear_buffer()
            b.clear_timer()
            b.send_trigger()

            budget = timeout_s or self.expected_seconds(npts, timing.get("hold", 0.0))
            self._wait(timeout_s=budget)
            # 데이터 읽기에도 같은 상한을 건다. 측정은 끝났다고 하는데 데이터가
            # 안 오는 경우가 있고(점 수 불일치 등), 그때 VISA 기본 타임아웃으로
            # 두면 또 몇 분을 기다린다.
            conn = b.adapter.connection
            prev, conn.timeout = conn.timeout, int(budget * 1000)
            try:
                data = b.read_data(npts)
            finally:
                conn.timeout = prev

            if var2_ch is not None:
                data.insert(0, f"VAR2 SMU{var2_ch} {var2.get('name','V')} (V)", v2)
            frames.append(data)

        # 모든 const SMU 0 V 복귀
        for ch in const_chs:
            smus[ch].ramp_source("VOLTAGE", "Auto Ranging", 0,
                                 _comp(constants[ch].get("compliance")),
                                 stepsize=0.1, pause=20e-3)

        return pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
