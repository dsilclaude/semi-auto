"""executor.py — plan(값) 을 장비 동작으로 옮긴다. 그게 전부다.

executor 는 plan 이 **이미 안전하다고 가정**한다. 검증은 session 이 여기를
부르기 전에 건다(safety.assert_valid). 검사와 실행을 한 함수에 섞으면
"검사를 통과시키려고 실행을 고치는" 유혹이 생긴다.

책임
  · 단자 이름 → SMU 채널 번호 변환 (config.roles)
  · 프로버 이동: 분리 → XY 이동 → (천천히) 접촉
  · 측정 실행, 결과 컬럼 정규화
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .config import DEFAULT, HardwareConfig
from .frame import canonicalize, fill_constants
from .plan import IVPlan


@dataclass(frozen=True)
class Site:
    """측정 지점. 좌표는 원점(사람이 contact 시킨 소자) 기준 상대좌표 [µm]."""
    name: str
    x: float = 0.0
    y: float = 0.0
    area: str = "default"
    note: str = ""

    @property
    def is_origin(self) -> bool:
        return self.x == 0 and self.y == 0


def load_sites(path, area: str = "default") -> List[Site]:
    """좌표 CSV/XLSX 읽기. 컬럼: Subsite Name, X Position, Y Position, Note."""
    p = Path(path)
    df = pd.read_excel(p) if p.suffix.lower() in (".xlsx", ".xls") else pd.read_csv(p)
    return [
        Site(name=str(r["Subsite Name"]), x=float(r["X Position"]),
             y=float(r["Y Position"]), area=area, note=str(r.get("Note", "")))
        for _, r in df.iterrows()
    ]


# ---------------------------------------------------------------------------
# plan → 드라이버 spec
# ---------------------------------------------------------------------------
def plan_to_spec(plan: IVPlan, roles: Dict[str, int]) -> dict:
    """IVPlan(단자 이름) → B1500 드라이버가 받는 dict(SMU 채널 번호).

    드라이버가 단자 이름을 모르게 하는 유일한 지점.
    """
    def ch(t: str) -> int:
        if t not in roles:
            raise KeyError(f"단자 {t!r} 가 roles 에 없다: {roles}")
        return roles[t]

    var1 = {
        "unit": ch(plan.var1.terminal),
        "name": plan.var1.name,
        "direction": plan.var1.direction,
        "spacing": plan.var1.spacing,
        "start": plan.var1.start,
        "stop": plan.var1.stop,
        "points": plan.var1.n_steps,
        "compliance": plan.var1.compliance,
        "power_comp": None,
    }
    var2 = None
    if plan.var2 is not None:
        var2 = {
            "unit": ch(plan.var2.terminal),
            "name": plan.var2.name,
            "start": plan.var2.start,
            "stop": plan.var2.stop,
            "points": plan.var2.n_steps,
            "compliance": plan.var2.compliance,
        }

    constants: Dict[int, dict] = {}
    for t, v in plan.constants.items():
        constants[ch(t)] = {"v": float(v), "compliance": plan.compliances.get(t)}
    # var2 채널은 DC 로 잡아뒀다가 스텝마다 덮어써지므로 constants 에 있어야 한다
    if plan.var2 is not None and ch(plan.var2.terminal) not in constants:
        constants[ch(plan.var2.terminal)] = {
            "v": float(plan.var2.start), "compliance": plan.var2.compliance}

    ranges = {ch(t): {"mode": r.mode, "range": r.range}
              for t, r in plan.ranges.items() if t in roles}

    return {
        "var1": var1,
        "var2": var2,
        "constants": constants,
        "timing": {
            "hold": plan.timing.hold, "delay": plan.timing.delay,
            "step_delay": plan.timing.step_delay,
            "auto_abort": plan.timing.auto_abort, "post": plan.timing.post,
        },
        "adc": {
            "adc_type": plan.adc.adc_type, "mode": plan.adc.mode,
            "n": plan.adc.n, "auto_zero": plan.adc.auto_zero,
        },
        "ranges": ranges,
    }


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------
class Executor:
    """장비 두 대를 묶어 '한 지점에서 한 plan 을 돌린다'를 제공한다.

    b1500/s300 을 주입할 수 있게 열어 뒀다 — 리플레이/드라이런에서
    가짜 객체를 넣어 돌리기 위함.
    """

    def __init__(self, config: HardwareConfig = DEFAULT, *, b1500=None, s300=None):
        self.cfg = config
        self.b1500 = b1500
        self.s300 = s300
        self._z_contact: Optional[int] = None

    # --- 수명주기 ---------------------------------------------------------
    def connect(self):
        from .drivers import B1500, CascadeS300
        self._check_bus()
        if self.s300 is None:
            self.s300 = CascadeS300(self.cfg.s300_address, self.cfg.visa_library,
                                    chuck_id=self.cfg.chuck_id,
                                    timeout_ms=self.cfg.s300_timeout_ms).connect()
            self.s300.setup()
            self.s300.slow_axes()
        if self.b1500 is None:
            self.b1500 = B1500(self.cfg.b1500_address, self.cfg.visa_library,
                               timeout_ms=self.cfg.b1500_timeout_ms).connect()
        return self

    def _check_bus(self) -> None:
        """장비를 열기 전에 버스가 살아 있는지 본다.

        어댑터가 드라이버에서 빠지는 일이 잦은데(강제 종료 뒤 특히), 그대로
        열면 pyvisa 스택 추적이 그대로 튀어나와 원인이 안 보인다. 여기서
        먼저 걸러 무엇을 하면 되는지 알려준다.
        """
        import pyvisa
        try:
            rm = (pyvisa.ResourceManager(self.cfg.visa_library)
                  if self.cfg.visa_library else pyvisa.ResourceManager())
            found = rm.list_resources()
        except Exception as e:
            raise ConnectionError(f"VISA 를 열 수 없다: {type(e).__name__}: {e}") from e

        want = {self.cfg.s300_address, self.cfg.b1500_address}
        missing = sorted(want - set(found))
        if not missing:
            return
        raise ConnectionError(
            "GPIB 버스에서 장비를 못 찾았다: " + ", ".join(missing) + "\n"
            f"  지금 보이는 것: {found or '(없음)'}\n"
            "\n"
            "  GPIB0 자체가 안 보이면 어댑터가 드라이버에서 빠진 것이다.\n"
            "  → NI GPIB-USB-HS 의 USB 를 뽑았다 5초 뒤 다시 꽂으면 풀린다.\n"
            "    (장치관리자에 Present 로 보여도 그럴 수 있다)\n"
            "  주소만 안 보이면 그 장비의 전원·GPIB 케이블·주소를 확인할 것.\n"
            "\n"
            "  팁은 접촉된 자리 그대로다 — 장비를 연 적이 없으므로 안전하다.")

    def close(self, local: bool = True):
        if self.s300 is not None:
            if local:
                self.s300.set_local()
            self.s300.close()
        if self.b1500 is not None:
            self.b1500.close()

    def check(self) -> dict:
        """양쪽 장비 상태. 판정은 하지 않고 값만 모아 준다."""
        return {"s300": self.s300.check() if self.s300 else None,
                "b1500": self.b1500.check() if self.b1500 else None}

    # --- 프로버 ----------------------------------------------------------
    def set_reference(self):
        """사람이 첫 소자에 contact 시킨 현재 위치를 원점으로 등록."""
        pos = self.s300.set_reference()
        self._z_contact = int(pos[2])
        return pos

    def goto(self, site: Site) -> Optional[tuple]:
        """분리 → XY 이동 → 접촉. 원점(0,0)이면 이동하지 않는다."""
        if site.is_origin:
            return None
        z_contact = int(self.s300.read_position()[2])   # 지금 = 등록된 접촉 높이
        self.s300.separate()
        self.s300.move_xy(site.x, site.y)
        if self.cfg.slow_contact:
            self.s300.contact_slow(z_contact, step=self.cfg.z_step_um,
                                   pause=self.cfg.z_pause_s, final=self.cfg.z_final_um)
        else:
            self.s300.contact()
        time.sleep(self.cfg.settle_s)
        return self.s300.read_position()

    def home(self):
        """분리 → 원점(0,0) 복귀 → 다시 접촉.

        마지막에 원점에 컨택된 상태로 끝내야, 다음 실행에서 첫 소자(0,0)를
        곧바로 측정하는 전제가 그대로 성립한다."""
        z = self._z_contact or int(self.s300.read_position()[2])
        self.s300.separate()
        self.s300.move_xy(0, 0)
        if self.cfg.slow_contact:
            self.s300.contact_slow(z, step=self.cfg.z_step_um,
                                   pause=self.cfg.z_pause_s, final=self.cfg.z_final_um)
        else:
            self.s300.contact()
        return self.s300.read_position()

    # --- 측정 -------------------------------------------------------------
    def measure(self, plan: IVPlan) -> pd.DataFrame:
        """이동 없이 지금 접촉된 자리에서 plan 하나를 실행하고 정규화해 돌려준다."""
        self.cfg.require(plan.active_terminals)
        spec = plan_to_spec(plan, self.cfg.roles)
        # 몇 점을 재는지 알고 있으니 대기 상한도 거기에 맞춘다. 넉넉한 고정값
        # (300초)으로 두면 문제가 생겼을 때 아무 소식 없이 몇 분을 매달린다.
        rng = plan.ranges.get("D")
        rng_name = rng.range if rng else None
        budget = self.b1500.expected_seconds(plan.n_points, plan.timing.hold, rng_name)
        print(f"  측정 중… {plan.n_points}점, 레인지 {rng_name or '기본'} "
              f"(예상 {budget:.0f}s 이내)", flush=True)
        t0 = time.time()
        raw = self.b1500.sweep(spec, timeout_s=budget)
        print(f"  측정 완료 {time.time() - t0:.1f}s", flush=True)
        df = canonicalize(raw, self.cfg.roles)
        return fill_constants(df, plan.constants)

    def run(self, site: Site, plan: IVPlan) -> pd.DataFrame:
        """지점으로 이동한 뒤 측정. session 이 부르는 진입점."""
        self.goto(site)
        return self.measure(plan)
