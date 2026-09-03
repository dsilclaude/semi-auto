"""plan.py — 측정 조건을 '값'으로 표현한다.

여기 정의된 것들은 전부 dict/JSON 으로 왕복 가능해야 한다.
에이전트가 만드는 건 이 값이고, 코드는 고정이다.

용어
  terminal : 소자 단자의 논리 이름. "TG" / "BG" / "D" / "S".
             어떤 SMU 슬롯에 물려 있는지는 여기서 모른다(config.py 담당).
  var1     : 주 sweep 축. 각 var2 스텝마다 이 sweep 이 통째로 1회 돈다.
  var2     : 부 sweep 축(=스텝). 없으면 None.

transfer = var1 이 게이트(TG/BG), output = var1 이 D.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace, asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

# 이 시스템이 아는 단자 이름 전부. 이 목록 밖의 이름은 validate 에서 막힌다.
TERMINALS: Tuple[str, ...] = ("TG", "BG", "D", "S")

DIRECTIONS = ("single", "double")
SPACINGS = ("linear", "log")
RANGE_MODES = ("auto", "limited", "fixed")


# ---------------------------------------------------------------------------
# sweep 축
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SweepAxis:
    """한 축의 sweep 조건. EasyEXPERT 의 VAR1/VAR2 칸과 1:1 대응."""

    terminal: str
    start: float
    stop: float
    points: int = 101
    direction: str = "single"   # single=편도 | double=왕복(start→stop→start)
    spacing: str = "linear"     # linear | log
    compliance: float = 1e-3    # [A]
    step: Optional[float] = None  # 지정하면 points 대신 이걸로 환산
    label: str = ""             # 표기용 (Vg, Vd 등). 비면 terminal 로 대체

    # --- 파생값 -----------------------------------------------------------
    @property
    def n_steps(self) -> int:
        """No of Step. step 이 지정되면 그걸로 환산, 아니면 points 그대로."""
        if self.step:
            return int(round(abs(self.stop - self.start) / abs(self.step))) + 1
        return int(self.points)

    @property
    def n_measured(self) -> int:
        """실제로 장비가 뱉는 포인트 수. double 이면 2배."""
        return self.n_steps if self.direction == "single" else 2 * self.n_steps

    def values(self) -> List[float]:
        """스텝 전압 리스트(편도 기준). start > stop 이면 내림차순."""
        n = self.n_steps
        if n <= 1:
            return [float(self.start)]
        if self.spacing == "log":
            if self.start <= 0 or self.stop <= 0:
                raise ValueError("log spacing 은 start/stop 이 모두 양수여야 함")
            lo, hi = math.log10(self.start), math.log10(self.stop)
            return [10 ** (lo + (hi - lo) * k / (n - 1)) for k in range(n)]
        return [self.start + (self.stop - self.start) * k / (n - 1) for k in range(n)]

    @property
    def span(self) -> Tuple[float, float]:
        """이 축이 훑는 전압 구간 (min, max)."""
        return (min(self.start, self.stop), max(self.start, self.stop))

    @property
    def name(self) -> str:
        return self.label or f"V{self.terminal}"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "SweepAxis":
        return SweepAxis(**{k: v for k, v in d.items() if k in SweepAxis.__annotations__})


# ---------------------------------------------------------------------------
# 부속 설정 (거의 안 바뀌는 값들 — 기본값이 곧 현재 셋업)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Timing:
    hold: float = 1.0        # [s] var2 스텝마다 sweep 시작 전 대기
    delay: float = 0.0       # [s]
    step_delay: float = 0.0  # [s] 스텝별 측정 지연
    auto_abort: bool = False  # False = "Sweep CONTINUE AT ANY"
    post: str = "START"      # 측정 후 출력 복귀 위치: START | STOP


@dataclass(frozen=True)
class Adc:
    adc_type: str = "HRADC"  # HRADC(고분해능) | HSADC(고속)
    mode: str = "AUTO"
    n: int = 10              # 적분 계수 (HRADC AUTO 기본 6 → 저노이즈 지향 10)
    auto_zero: bool = False  # OFF = 적분시간 절반


@dataclass(frozen=True)
class RangeSpec:
    """Set Ranging Mode. 채널별 '측정 전류 레인지'."""
    mode: str = "limited"     # auto | limited | fixed
    range: str = "1 nA"       # "1 nA","10 nA","100 nA","1 uA",...


DEFAULT_RANGES: Dict[str, RangeSpec] = {t: RangeSpec() for t in TERMINALS}


# ---------------------------------------------------------------------------
# IVPlan
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IVPlan:
    """한 번의 I-V 측정을 완전히 기술하는 값.

    이 객체 하나가 결과 CSV 옆에 JSON 으로 같이 저장된다.
    에이전트가 조건을 만들기 시작하면 이게 유일한 실험 기록이다.
    """

    kind: str                       # "transfer" | "output"
    var1: SweepAxis
    var2: Optional[SweepAxis] = None
    constants: Dict[str, float] = field(default_factory=dict)      # terminal -> V
    compliances: Dict[str, float] = field(default_factory=dict)    # terminal -> A
    timing: Timing = field(default_factory=Timing)
    adc: Adc = field(default_factory=Adc)
    ranges: Dict[str, RangeSpec] = field(default_factory=lambda: dict(DEFAULT_RANGES))
    label: str = ""
    note: str = ""                  # 왜 이 조건인지(에이전트 근거). 기록용.

    # --- 파생값 -----------------------------------------------------------
    @property
    def n_points(self) -> int:
        """총 측정 포인트 수 = var1 포인트 × var2 스텝."""
        n2 = self.var2.n_steps if self.var2 else 1
        return self.var1.n_measured * n2

    @property
    def active_terminals(self) -> List[str]:
        """이번 plan 이 실제로 전압을 인가하는 단자."""
        act = [self.var1.terminal]
        if self.var2:
            act.append(self.var2.terminal)
        act += [t for t in self.constants if t not in act]
        return act

    def terminal_spans(self, all_terminals: Iterable[str] = TERMINALS
                       ) -> Dict[str, Tuple[float, float]]:
        """단자별 전압 구간 (min, max).

        미지정 단자는 0 V 로 본다 — 안 쓰는 게이트를 띄우지 말고 접지하라는
        규칙과 같은 가정. 실제 배선이 다르면 여기가 아니라 config 에서 고칠 것.
        """
        spans: Dict[str, Tuple[float, float]] = {t: (0.0, 0.0) for t in all_terminals}
        for t, v in self.constants.items():
            spans[t] = (float(v), float(v))
        for ax in (self.var1, self.var2):
            if ax is not None:
                spans[ax.terminal] = ax.span
        return spans

    def compliance_of(self, terminal: str) -> Optional[float]:
        """단자별 컴플라이언스. sweep 축은 축의 값이 우선한다."""
        if self.var1.terminal == terminal:
            return self.var1.compliance
        if self.var2 is not None and self.var2.terminal == terminal:
            return self.var2.compliance
        return self.compliances.get(terminal)

    # --- 직렬화 -----------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "var1": self.var1.to_dict(),
            "var2": self.var2.to_dict() if self.var2 else None,
            "constants": dict(self.constants),
            "compliances": dict(self.compliances),
            "timing": asdict(self.timing),
            "adc": asdict(self.adc),
            "ranges": {k: asdict(v) for k, v in self.ranges.items()},
            "label": self.label,
            "note": self.note,
        }

    @staticmethod
    def from_dict(d: dict) -> "IVPlan":
        return IVPlan(
            kind=d["kind"],
            var1=SweepAxis.from_dict(d["var1"]),
            var2=SweepAxis.from_dict(d["var2"]) if d.get("var2") else None,
            constants=dict(d.get("constants", {})),
            compliances=dict(d.get("compliances", {})),
            timing=Timing(**d.get("timing", {})),
            adc=Adc(**d.get("adc", {})),
            ranges={k: RangeSpec(**v) for k, v in d.get("ranges", {}).items()}
                   or dict(DEFAULT_RANGES),
            label=d.get("label", ""),
            note=d.get("note", ""),
        )

    def describe(self) -> str:
        v1 = self.var1
        s = (f"{self.kind}: {v1.name} {v1.start}→{v1.stop} V "
             f"{v1.n_steps}pt {v1.direction} (comp {v1.compliance:g} A)")
        if self.var2:
            v2 = self.var2
            s += f" × {v2.name} {v2.start}→{v2.stop} V {v2.n_steps}step"
        if self.constants:
            s += " | const " + ", ".join(f"{k}={v:g}" for k, v in self.constants.items())
        return s + f" | {self.n_points} pts"


# ---------------------------------------------------------------------------
# 생성 헬퍼 — 사람이 손으로 만들 때, 그리고 시드 plan 을 만들 때 쓴다
# ---------------------------------------------------------------------------
def transfer(gate: str, start: float, stop: float, points: int = 101, *,
             vd: float = 0.1, source: str = "S", drain: str = "D",
             other_gate: Optional[str] = None, other_gate_v: float = 0.0,
             direction: str = "double", gate_compliance: float = 1e-6,
             drain_compliance: float = 1e-3,
             source_compliance: Optional[float] = None,
             label: str = "", note: str = "") -> IVPlan:
    """transfer(Id-Vg) plan 하나.

    other_gate 를 주면 그 게이트를 DC 로 고정한다. dual gate 에서
    "안 쓰는 게이트는 띄우지 말고 0 V" 규칙 때문에 기본이 0 V 고정이다.
    """
    consts = {source: 0.0, drain: float(vd)}
    if other_gate:
        consts[other_gate] = float(other_gate_v)
    return IVPlan(
        kind="transfer",
        var1=SweepAxis(terminal=gate, start=start, stop=stop, points=points,
                       direction=direction, compliance=gate_compliance,
                       label=f"V{gate}"),
        constants=consts,
        # 소스는 드레인+게이트 전류를 다 받으므로 드레인보다 넉넉해야 한다.
        # 다만 경계가 더 낮으면 그쪽을 따라야 하므로 밖에서 지정할 수 있게 둔다.
        compliances={drain: drain_compliance,
                     source: (max(drain_compliance * 10, 1e-2)
                              if source_compliance is None else source_compliance)},
        label=label or f"transfer_{gate}",
        note=note,
    )


def output(drain: str = "D", start: float = 0.0, stop: float = 10.0, points: int = 81, *,
           gate: str = "BG", gate_start: float = 0.0, gate_stop: float = 20.0,
           gate_steps: int = 5, source: str = "S",
           other_gate: Optional[str] = None, other_gate_v: float = 0.0,
           direction: str = "double", drain_compliance: float = 1e-3,
           gate_compliance: float = 1e-6, label: str = "", note: str = "") -> IVPlan:
    """output(Id-Vd) plan 하나. var1=Vd sweep, var2=Vg step."""
    consts = {source: 0.0, gate: float(gate_start)}
    if other_gate:
        consts[other_gate] = float(other_gate_v)
    return IVPlan(
        kind="output",
        var1=SweepAxis(terminal=drain, start=start, stop=stop, points=points,
                       direction=direction, compliance=drain_compliance,
                       label=f"V{drain}"),
        var2=SweepAxis(terminal=gate, start=gate_start, stop=gate_stop,
                       points=gate_steps, compliance=gate_compliance,
                       label=f"V{gate}"),
        constants=consts,
        compliances={source: max(drain_compliance * 10, 1e-2)},
        label=label or f"output_{drain}",
        note=note,
    )


def with_axis(plan: IVPlan, **kw) -> IVPlan:
    """var1 의 일부 필드만 바꾼 새 plan (frozen 이라 replace 로)."""
    return replace(plan, var1=replace(plan.var1, **kw))
