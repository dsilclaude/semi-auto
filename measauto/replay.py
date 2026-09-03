"""replay.py — 장비 없이 돌려보고 세는 도구.

검증 방식 두 가지.

1) 리플레이 (과거 데이터 1턴 평가)
   실제로 측정한 CSV 를 metrics 에 통과시키고, 그 관측을 에이전트에게 보여준
   뒤 무엇을 하겠다고 하는지 센다. 같은 소자에서 반복했을 때 결론이 갈리면
   프롬프트가 부실하다는 신호다.
   ※ 완전한 다중턴 리플레이는 불가능하다 — 에이전트가 새 조건을 제안하면
     그 조건으로 찍힌 데이터가 과거에는 없다. 그래서 여기서는 '첫 판단' 만 센다.

2) 시뮬레이션 (다중턴 루프 전체)
   간단한 TFT 모델로 plan 에 반응하는 가짜 소자를 만든다. 에이전트가 범위를
   넓히면 곡선이 실제로 나타나므로, '몇 턴에 수렴 / 경계 몇 번 접촉' 을
   끝까지 셀 수 있다. 모델이 단순하다는 한계는 있지만, 루프·검증·저장 경로가
   제대로 물려 있는지는 이걸로 다 드러난다.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from .agent.policy import Policy, Proposal
from .executor import Site
from .frame import load_legacy_csv
from .metrics import csv_payload, downsample_curve, summarize, to_llm_payload
from .plan import IVPlan
from .safety import Bounds, validate


# ---------------------------------------------------------------------------
# 1) 과거 CSV 리플레이
# ---------------------------------------------------------------------------
@dataclass
class ReplayCase:
    path: Path
    plan: IVPlan                    # 그 데이터를 만든 조건
    roles: Dict[str, int]
    site: str = "replay"
    constants: Optional[Dict[str, float]] = None


def replay_once(case: ReplayCase, policy: Policy, bounds: Bounds, *,
                objective: str, send_full_csv: bool = True,
                csv_max_rows: Optional[int] = None) -> dict:
    """CSV 한 장 → 지표 → 에이전트 판단 1회.

    send_full_csv 는 session 쪽 설정과 맞춰야 한다. 두 경로가 다른 형태를
    보내면 리플레이 점수가 실제 운용을 대변하지 못한다.
    """
    df = load_legacy_csv(case.path, case.roles, case.constants or case.plan.constants)
    m = summarize(df, case.plan, bounds=bounds)
    if send_full_csv:
        payload = to_llm_payload(m, csv=csv_payload(df, max_rows=csv_max_rows))
    else:
        payload = to_llm_payload(m, downsample_curve(df, case.plan))

    ctx = {
        "objective": objective,
        "bounds_text": bounds.describe(),
        "seed_text": case.plan.describe(),
        "seed_plan": case.plan,
        "device_text": f"site={case.site} (리플레이: {case.path.name})",
        "prior_devices": None,
        "ig_abort_A": bounds.ig_abort_A,
    }
    history = [{"iteration": 0, "plan": case.plan.describe(),
                "plan_obj": case.plan, "payload": payload}]
    prop: Proposal = policy.propose(ctx, history)

    viol: List[str] = []
    if prop.plan is not None:
        viol = [str(v) for v in validate(prop.plan, bounds) if v.severity == "error"]

    return {
        "case": case.path.name,
        "status": prop.status,
        "reason": prop.reason,
        "confidence": prop.confidence,
        "proposed": prop.plan.describe() if prop.plan else None,
        "violations": viol,
        "metrics": m,
    }


def replay_score(cases: Sequence[ReplayCase], policy: Policy, bounds: Bounds, *,
                 objective: str, repeats: int = 3,
                 send_full_csv: bool = True,
                 csv_max_rows: Optional[int] = None) -> dict:
    """여러 케이스 × 반복 → 상태 분포와 경계 접촉 횟수.

    같은 케이스에서 status 가 갈리면 프롬프트가 부실하다는 신호다.
    """
    rows, per_case = [], {}
    for c in cases:
        statuses = []
        for _ in range(repeats):
            r = replay_once(c, policy, bounds, objective=objective,
                            send_full_csv=send_full_csv,
                            csv_max_rows=csv_max_rows)
            rows.append(r)
            statuses.append(r["status"])
        per_case[c.path.name] = Counter(statuses)

    return {
        "n_runs": len(rows),
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "boundary_contacts": sum(1 for r in rows if r["violations"]),
        "unstable_cases": [k for k, v in per_case.items() if len(v) > 1],
        "per_case": {k: dict(v) for k, v in per_case.items()},
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# 2) 시뮬레이션
# ---------------------------------------------------------------------------
@dataclass
class SimulatedDevice:
    """아주 단순한 n형 TFT. 루프를 끝까지 돌려보기 위한 것이지 모델 연구가 아니다."""
    vth: float = 3.0
    ss: float = 0.25            # [V/decade]
    mobility: float = 10.0      # [cm²/Vs]
    cox: float = 33.6e-9        # [F/cm²]
    wl: float = 1.5             # W/L
    i_off: float = 2e-13        # [A]
    ig_leak: float = 5e-14      # [A] 기저 게이트 누설
    ig_break_V: float = 1e9     # 이 |Vg| 를 넘으면 Ig 가 지수적으로 상승
    noise: float = 0.15         # 상대 노이즈
    dead: bool = False
    seed: int = 0

    def id_of(self, vg: np.ndarray, vd: float) -> np.ndarray:
        """문턱 위(드리프트) + 아래(지수) + 바닥을 더한다.

        문턱 위는 Vd 가 작으면 선형, Vd ≥ 과전압이면 포화로 자동 전환되고,
        문턱 아래는 log10 기울기가 정확히 1/SS 다. 그래서 회복해야 할 Vth 와
        SS 가 파라미터 그대로 되돌아온다 — 지표 검증용 픽스처의 조건.
        """
        vg = np.asarray(vg, float)
        if self.dead:
            return np.zeros_like(vg)
        k = self.mobility * self.cox * self.wl
        ov = np.maximum(vg - self.vth, 0.0)
        vd_eff = np.minimum(abs(vd), ov)                 # Vd ≥ 과전압이면 포화
        above = k * (vd_eff * ov - 0.5 * vd_eff ** 2)
        i_th = k * abs(vd) * self.ss                     # 문턱에서의 전류 스케일
        sub = i_th * np.power(10.0, np.clip(np.minimum(vg - self.vth, 0.0) / self.ss,
                                            -30, 0))
        return above + sub + self.i_off

    def ig_of(self, vg: np.ndarray) -> np.ndarray:
        base = np.full_like(vg, self.ig_leak)
        over = np.abs(vg) - self.ig_break_V
        return base + np.where(over > 0, self.ig_leak * np.power(10.0, over), 0.0)


def simulate(plan: IVPlan, dev: SimulatedDevice) -> pd.DataFrame:
    """plan 에 반응하는 가짜 측정 데이터. 컬럼은 이미 정규화된 형태."""
    rng = np.random.default_rng(dev.seed)
    gate = plan.var1.terminal
    v = np.array(plan.var1.values(), float)
    if plan.var1.direction == "double":
        v = np.concatenate([v, v[::-1]])

    steps = plan.var2.values() if plan.var2 else [None]
    frames = []
    for s in steps:
        consts = dict(plan.constants)
        if plan.var2 is not None and s is not None:
            consts[plan.var2.terminal] = s
        vd = consts.get("D", 0.1)

        if plan.kind == "transfer":
            idd = dev.id_of(v, vd)
            igg = dev.ig_of(v)
            cols = {f"V_{gate}": v, "I_D": idd, f"I_{gate}": igg}
        else:  # output: var1=Vd, var2=Vg
            vg = consts.get(plan.var2.terminal if plan.var2 else "BG", 0.0)
            idd = np.array([dev.id_of(np.array([vg]), float(x))[0] for x in v])
            cols = {f"V_{gate}": v, "I_D": idd,
                    "I_BG": dev.ig_of(np.full_like(v, vg))}

        # 노이즈 + 바닥. off 영역에서 음수로 찍히는 것까지 재현한다.
        n = rng.normal(0.0, dev.i_off * dev.noise * 3, size=idd.shape)
        cols["I_D"] = idd * (1 + rng.normal(0, dev.noise * 0.05, idd.shape)) + n
        comp = plan.compliance_of("D")
        if comp:
            cols["I_D"] = np.clip(cols["I_D"], -comp, comp)

        g = pd.DataFrame(cols)
        for t, val in consts.items():
            g[f"V_{t}"] = float(val)
        if s is not None:
            g.insert(0, "step", float(s))
        frames.append(g)

    return pd.concat(frames, ignore_index=True)


class FakeExecutor:
    """Executor 와 같은 겉면. 이동은 흉내만 내고 측정은 simulate 로."""

    def __init__(self, device: SimulatedDevice, config=None):
        from .config import DEFAULT
        self.device = device
        self.cfg = config or DEFAULT
        self.moves: List[Site] = []

    def connect(self):
        return self

    def close(self, local: bool = True):
        pass

    def check(self) -> dict:
        return {"s300": "fake", "b1500": "fake"}

    def set_reference(self):
        return (0.0, 0.0, 0.0)

    def goto(self, site: Site):
        self.moves.append(site)
        return (site.x, site.y, 0.0)

    def home(self):
        return (0.0, 0.0, 0.0)

    def measure(self, plan: IVPlan) -> pd.DataFrame:
        self.cfg.require(plan.active_terminals)
        return simulate(plan, self.device)

    def run(self, site: Site, plan: IVPlan) -> pd.DataFrame:
        self.goto(site)
        return self.measure(plan)
