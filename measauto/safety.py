"""safety.py — 안전 경계를 stack.json 에서 '계산'하고, plan 을 검사한다.

경계 상수를 코드에 박지 않는 이유: 웨이퍼가 바뀌면 값이 바뀌지 코드가 바뀌지
않는다. stack.json 만 갈아끼우면 경계가 따라 움직인다.

물리
  유전막은 전압이 아니라 필드에 반응한다.  E = V / t_ox
  직렬 스택은 전속밀도 D 가 연속이라  E_i = D / ε_i  →  ε 작은 층에 필드가 몰린다.
  전체 전압       V = Σ E_i t_i = D · Σ (t_i / ε_i)
  층 i 의 한계    V_i,max = E_i,max · ε_i · Σ (t_j / ε_j)
  스택 한계       V_max = min_i V_i,max

  검산 (SD 웨이퍼 BG = SiNx 50 nm/ε7 + SiO2 75 nm/ε3.9):
      Σ t/ε = 50/7 + 75/3.9 = 26.374 nm
      SiO2 한계 = 3 MV/cm × 3.9 × 26.374 nm = 30.9 V   ← 율속
      SiNx 한계 = 3 MV/cm × 7.0 × 26.374 nm = 55.4 V
      ε_eff = 125 / 26.374 = 4.74   (SmartSPICE EPSI = 4.74 와 일치)
      C_BG  = ε0·ε_eff / 125 nm    = 33.6 nF/cm²

  W/L 은 수평 방향(전류 크기)이라 대체 불가. 수직 필드의 경로 길이는 t_ox 뿐이다.

이 모듈은 순수 함수만 담는다. 장비도 pandas 도 모른다.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .materials import UnknownMaterialError, resolve_layers
from .plan import IVPlan, TERMINALS, DIRECTIONS, SPACINGS, RANGE_MODES

EPS0_F_PER_CM = 8.8541878128e-14   # 진공 유전율 [F/cm]
STACK_DIR = Path(__file__).with_name("stacks")

Pair = Tuple[str, str]


def _pair(a: str, b: str) -> Pair:
    return (a, b) if a <= b else (b, a)


# ---------------------------------------------------------------------------
# 유전막 계산
# ---------------------------------------------------------------------------
def series_dielectric(layers: Sequence[dict],
                      e_max_MV_per_cm: Optional[float] = None) -> dict:
    """직렬 유전막 스택의 등가 유전율/커패시턴스, 그리고 (알면) 절연파괴 전압.

    layers : [{"material":..,"t_nm":..,"eps_r":..}, ...]
    e_max  : 절연내압 [MV/cm]. **모르면 None** — 그러면 파괴전압 없이
             ε_eff 와 C 만 돌려준다. C 는 이동도 환산에 쓰이므로 내압을
             몰라도 계산할 수 있어야 한다(두께·ε 만으로 정해진다).
    """
    if not layers:
        raise ValueError("dielectric layers 가 비어 있음")

    t_total_nm = sum(float(L["t_nm"]) for L in layers)
    sum_t_eps_nm = sum(float(L["t_nm"]) / float(L["eps_r"]) for L in layers)
    sum_t_eps_cm = sum_t_eps_nm * 1e-7

    per_layer = []
    for L in layers:
        # 층마다 내압이 다르다(SiNx 와 SiO2 는 같지 않다). 인자로 준 값이 있으면
        # 전 층에 적용하고, 없으면 층에 붙어 있는 값을 쓴다(materials 가 채워준다).
        e_i = e_max_MV_per_cm if e_max_MV_per_cm is not None else L.get("e_max_MV_per_cm")
        # 이 층이 자기 E_max 에 먼저 도달할 때의 전체 인가전압
        v_i = (float(e_i) * 1e6 * float(L["eps_r"]) * sum_t_eps_cm
               if e_i is not None else None)
        per_layer.append({
            "material": L.get("material", "?"),
            "t_nm": float(L["t_nm"]),
            "eps_r": float(L["eps_r"]),
            "e_max_MV_per_cm": None if e_i is None else float(e_i),
            "v_limit_V": v_i,
        })

    known = [d for d in per_layer if d["v_limit_V"] is not None]
    # 하나라도 모르면 '최소'를 말할 수 없다 — 모르는 층이 율속일 수 있다.
    limiting = min(known, key=lambda d: d["v_limit_V"]) if len(known) == len(per_layer) else None
    eps_eff = t_total_nm / sum_t_eps_nm
    c = EPS0_F_PER_CM * eps_eff / (t_total_nm * 1e-7)

    return {
        "v_breakdown_V": limiting["v_limit_V"] if limiting else None,
        "limiting_layer": limiting["material"] if limiting else None,
        "eps_eff": eps_eff,
        "c_F_per_cm2": c,
        "c_nF_per_cm2": c * 1e9,
        "t_total_nm": t_total_nm,
        "sum_t_over_eps_nm": sum_t_eps_nm,
        "per_layer": per_layer,
    }


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Bounds:
    """plan 을 검사할 때 쓰는 경계값 묶음. stack.json 에서 계산되어 나온다."""

    stack_name: str
    pair_v_max: Dict[Pair, float]            # |V_a - V_b| 상한 [V]
    i_compliance_max: Dict[str, float]       # 단자별 컴플라이언스 상한 [A]
    windows: Dict[str, Tuple[float, float]]  # 단자별 운용 전압 창 (선택)
    max_points: int
    ig_warn_A: float
    ig_abort_A: float
    derived: Dict[str, dict] = field(default_factory=dict)   # 계산 근거(=검산용)
    geometry: Dict[str, float] = field(default_factory=dict)
    # **이 웨이퍼에서 실제로 잰 값만** 들어온다 (stack["measured"]).
    # 설계자료의 기대값은 절대 여기 들어오지 않는다 — 다른 로트·다른 조건일 수
    # 있고 W 스플릿 소자에서는 유추라, 틀린 전제 위에 측정을 쌓게 된다.
    # 자료는 stack["_spec_reference"] 에 기록만 되어 있고 어떤 코드도 읽지 않는다.
    measured: Dict[str, object] = field(default_factory=dict)

    def limit_for(self, a: str, b: str) -> Optional[float]:
        return self.pair_v_max.get(_pair(a, b))

    def cox_of(self, gate: str) -> Optional[float]:
        d = self.derived.get(gate)
        return d["c_F_per_cm2"] if d else None

    @property
    def wl_ratio(self) -> Optional[float]:
        w, l = self.geometry.get("W_um"), self.geometry.get("L_um")
        return (w / l) if (w and l) else None

    def describe(self) -> str:
        lines = [f"[bounds] stack={self.stack_name}"]
        for (a, b), v in sorted(self.pair_v_max.items()):
            lines.append(f"  |V{a} - V{b}| ≤ {v:g} V")
        for t, i in sorted(self.i_compliance_max.items()):
            lines.append(f"  I({t}) compliance ≤ {i:g} A")
        for t, (lo, hi) in sorted(self.windows.items()):
            lines.append(f"  V{t} ∈ [{lo:g}, {hi:g}] V (운용 창)")
        lines.append(f"  points ≤ {self.max_points},  Ig abort ≥ {self.ig_abort_A:g} A")
        for g, d in sorted(self.derived.items()):
            if d.get("eps_eff") is None:
                continue
            head = f"  · {g}:"
            if d.get("v_breakdown_V") is not None:
                head += f" V_bd={d['v_breakdown_V']:.1f} V (율속층 {d['limiting_layer']})"
            applied = d.get("v_applied_V")
            # 물리 한계보다 좁혀졌으면 그 사실과 이유를 같이 보여준다.
            if applied is not None and (d.get("v_breakdown_V") is None
                                        or abs(applied - d["v_breakdown_V"]) > 0.05):
                head += f" → 적용 {applied:g} V"
            lines.append(f"{head}, ε_eff={d['eps_eff']:.2f}, "
                         f"C={d['c_nF_per_cm2']:.1f} nF/cm²")
            if d.get("basis"):
                lines.append(f"      {d['basis']}")
        prov = getattr(self, "_provenance", None) or {}
        if prov.get("source"):
            lines.append(f"  근거: {prov['source']}"
                         + (f" / 승인: {prov['approved_by']}"
                            if prov.get("approved_by") else " / **미승인**"))
        return "\n".join(lines)


def load_stack(name_or_path: str | Path) -> dict:
    """stacks/<name>.json 을 읽고, 옆에 <name>.limits.json 이 있으면 합친다.

    두 파일로 나눈 이유: <name>.json 은 설계자료 전사본이고, 안전 경계
    (절연내압·컴플라이언스 상한·운용 창)는 자료에 없는 값이라 섞으면 어디까지가
    자료이고 어디부터가 가정인지 구분이 안 된다. limits 쪽은 공정 담당이 채운다.

    합칠 때 null 은 '안 채워짐'이므로 무시한다 — 즉 limits 파일이 통째로 비어
    있으면 stack 은 경계 없이 남고, bounds_from_stack 이 거기서 멈춘다.
    """
    p = Path(name_or_path)
    if not p.exists():
        cand = STACK_DIR / (p.name if p.suffix == ".json" else f"{p.name}.json")
        if not cand.exists():
            raise FileNotFoundError(f"stack 을 찾을 수 없음: {name_or_path}")
        p = cand
    # utf-8-sig 로 읽는다. 편집기·PowerShell 이 BOM 을 붙이는 일이 흔한데,
    # 순수 utf-8 로 읽으면 첫 글자에서 JSONDecodeError 가 난다.
    with open(p, "r", encoding="utf-8-sig") as f:
        stack = json.load(f)

    lim_path = p.with_name(f"{p.stem}.limits.json")
    if lim_path.exists():
        with open(lim_path, "r", encoding="utf-8-sig") as f:
            merge_limits(stack, json.load(f))
        stack.setdefault("_limits_file", str(lim_path))
    return stack


def merge_limits(stack: dict, lim: dict) -> dict:
    """limits 문서를 stack 에 덮어쓴다. null 은 '안 채워짐'이라 건너뛴다."""
    for gate, g in (lim.get("gates") or {}).items():
        if gate not in stack.get("gates", {}):
            continue
        for k, v in (g or {}).items():
            if v is not None:
                stack["gates"][gate][k] = v

    dst = stack.setdefault("limits", {})
    for k, v in (lim.get("limits") or {}).items():
        if v is None or v == {} or v == []:
            continue
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            dst[k].update({kk: vv for kk, vv in v.items() if vv is not None})
        else:
            dst[k] = v

    # 최상위에 적는 정책 키들도 limits 안으로 옮긴다 (bounds_from_stack 이 여기서 읽는다)
    for k in ("policy", "characterized_gate"):
        if lim.get(k) is not None:
            dst[k] = lim[k]

    if lim.get("provenance"):
        stack["_limits_provenance"] = lim["provenance"]
    return stack


def bounds_from_stack(stack: dict | str | Path, *, derate: float = 1.0,
                      round_down_V: float = 1.0) -> Bounds:
    """stack.json → Bounds.

    derate       : 계산된 파괴전압에 곱하는 여유 계수 (기본 1.0 = 계산값 그대로).
    round_down_V : 이 단위로 내림. 1.0 이면 30.86 V → 30 V.
    """
    if not isinstance(stack, dict):
        stack = load_stack(stack)

    derived: Dict[str, dict] = {}
    pair_v_max: Dict[Pair, float] = {}

    def _clip(v: float) -> float:
        v = v * derate
        if round_down_V > 0:
            v = math.floor(v / round_down_V) * round_down_V
        return v

    limits_in = stack.get("limits") or {}
    policy = limits_in.get("policy")
    exp = stack.get("expected") or {}

    field_basis = limits_in.get("field_basis")

    # --- 게이트 ↔ 대향 단자 -------------------------------------------------
    # 내압은 재료 물성이라 materials.json 에서 온다. 층마다 다를 수 있고,
    # 층에 직접 적힌 값이 있으면 그쪽이 이긴다(그 웨이퍼만의 실측값).
    for gate, g in stack.get("gates", {}).items():
        layers = _resolve(g["dielectric"], gate, field_basis)
        d = series_dielectric(layers, g.get("e_max_MV_per_cm"))

        v_lim = _clip(d["v_breakdown_V"])
        why = [f"절연파괴 계산 (율속층 {d['limiting_layer']})"]

        # 아래는 '더 좁히는' 조건들. 물리 한계보다 낮은 쪽이 항상 이긴다.
        v_user = g.get("v_user_max")
        if v_user is not None and float(v_user) < v_lim:
            v_lim = float(v_user)
            why.append(f"운용 한계 {v_user:g} V")

        env = _envelope_limit(stack, gate, policy, limits_in, exp)
        if env is not None and env < v_lim:
            v_lim = env
            why.append(f"자료 특성화 범위 {env:g} V")

        d["v_applied_V"] = v_lim
        d["v_user_max"] = v_user
        d["basis"] = " → ".join(why)
        derived[gate] = d
        for other in g.get("opposing", []):
            pair_v_max[_pair(gate, other)] = v_lim

    # --- 묶인 게이트 --------------------------------------------------------
    # 레이아웃에서 두 게이트가 한 패드로 나오면 전기적으로 같은 노드다.
    # 그러면 같은 전압이 두 유전막에 동시에 걸리므로, 그 노드의 상한은
    # 구성원 중 가장 낮은 쪽이 정한다. 안 묶어두면 더 약한 막이 검사를 통째로
    # 비껴간다 — 지금 셋업은 BG(30.9 V)가 TG(60 V)보다 낮아 우연히 맞았을 뿐이다.
    for group in stack.get("tied_gates") or []:
        members = [g for g in group if g in derived]
        if len(members) < 2:
            continue
        lim = min(derived[g]["v_applied_V"] for g in members
                  if derived[g].get("v_applied_V") is not None)
        node = members[0]                      # 대표 이름 (config.roles 의 그 단자)
        others = {o for g in members for o in stack["gates"][g].get("opposing", [])}
        for other in others:
            pair_v_max[_pair(node, other)] = lim
        for g in members[1:]:                  # 묶인 나머지는 별도 노드가 아니다
            for other in stack["gates"][g].get("opposing", []):
                pair_v_max.pop(_pair(g, other), None)
            pair_v_max.pop(_pair(node, g), None)   # 같은 노드끼리는 ΔV=0
        derived[node]["tied_with"] = members[1:]
        derived[node]["v_applied_V"] = lim
        derived[node]["basis"] += (
            f" | {'+'.join(members)} 가 한 노드로 묶여 있어 가장 낮은 쪽({lim:g} V)이 지배")
        # 묶인 게이트는 채널을 양쪽에서 제어하므로 결합 용량이 병렬로 더해진다.
        # 다만 TG 가 채널 일부만 덮으면 구간마다 값이 다르다 — 그래서 '참고값'이다.
        cs = [derived[g].get("c_F_per_cm2") for g in members]
        if all(c is not None for c in cs):
            derived[node]["c_parallel_F_per_cm2"] = sum(cs)

    # --- 게이트 ↔ 게이트 ----------------------------------------------------
    gg = stack.get("gate_gate")
    if gg:
        a, b = gg["pair"]
        if gg.get("v_max") is not None:
            v = float(gg["v_max"])
            derived["gate_gate"] = {"v_applied_V": v, "source": "stack.json 명시값"}
        else:
            # 채널이 완전히 꺼진 최악의 경우 = 두 유전막 직렬
            layers = (_resolve(stack["gates"][a]["dielectric"], a, field_basis)
                      + _resolve(stack["gates"][b]["dielectric"], b, field_basis))
            d = series_dielectric(layers)
            v = _clip(d["v_breakdown_V"])
            d["v_applied_V"] = v
            d["source"] = "두 유전막 직렬 계산 (채널 off 최악조건)"
            derived["gate_gate"] = d
        pair_v_max[_pair(a, b)] = v

    # --- stack.json 이 직접 못 박은 pair (D-S 등) ---------------------------
    limits = stack.get("limits", {})
    for key, v in (limits.get("pair_v_max") or {}).items():
        a, b = key.split("-")
        pair_v_max[_pair(a.strip(), b.strip())] = float(v)

    windows = {t: (float(lo), float(hi))
               for t, (lo, hi) in _iter_windows(limits)}

    b = Bounds(
        stack_name=stack.get("name", "unnamed"),
        pair_v_max=pair_v_max,
        i_compliance_max={k: float(v) for k, v in (limits.get("i_compliance_A") or {}).items()},
        windows=windows,
        max_points=int(_num_or(limits.get("max_points"), 10000)),
        ig_warn_A=float(_num_or(limits.get("ig_warn_A"), 1e-10)),
        ig_abort_A=float(_num_or(limits.get("ig_abort_A"), 1e-9)),
        derived=derived,
        geometry={k: float(v) for k, v in (stack.get("geometry") or {}).items()},
        measured=dict(stack.get("measured") or {}),
    )
    object.__setattr__(b, "_provenance", stack.get("_limits_provenance") or {})
    return b


class MissingLimitError(RuntimeError):
    """안전 경계 값이 안 채워졌다. 경계 없이 도는 것보다 멈추는 편이 낫다."""


def _resolve(layers, gate: str, field_basis):
    """층에 재료 물성(ε, 내압)을 붙인다. 모르는 재료면 멈춘다."""
    try:
        return resolve_layers(layers, field_basis=field_basis)
    except UnknownMaterialError as e:
        raise MissingLimitError(f"[{gate}] {e}") from e


def _envelope_limit(stack: dict, gate: str, policy, limits_in: dict,
                    _unused: dict = None) -> Optional[float]:
    """절연내압을 모를 때의 대안 상한 — **설계자료가 실제로 측정한 범위**.

    내압은 증착 조건에서 나오는 물리량이라 추정할 수 없다. 대신 자료가
    "이 범위에서 측정했다"고 보여준 구간은 소자가 그 안에서 살아 동작한다는
    문서화된 실적이다. 그걸 넘지 않는 것은 추정이 아니라 보수적 선택이다.

    특성화된 게이트(limits.characterized_gate)에만 적용한다 — 자료가 그 게이트를
    쓸어서 측정했기 때문이다. 다른 게이트에 같은 범위를 갖다 붙이면 근거 없는
    확장이 되므로 None 을 돌려주고, 그 게이트는 경계 없이 남는다(사용하려면
    config.roles 단계에서 막히거나, 내압을 채워야 한다).
    """
    if policy != "characterized_envelope":
        return None
    if gate != limits_in.get("characterized_gate"):
        return None
    # 운용 창은 **limits.json** 에서 읽는다. 자료의 expected 블록에서 읽지 않는다.
    # 이건 소자 거동 추정치가 아니라 '어디까지 걸겠다'는 운영 결정이고, 그런
    # 결정은 사람이 provenance 와 함께 적는 파일에 있어야 한다.
    rng = limits_in.get("operating_envelope_V")
    if not rng or len(rng) != 2:
        return None
    return float(max(abs(float(rng[0])), abs(float(rng[1]))))


def _num_or(v, default):
    """null 이면 기본값. limits 가 비어 있을 때 int(None) 로 죽지 않게."""
    return default if v is None else v


def _iter_windows(limits: dict):
    """limits 안의 '<terminal 소문자>_window_V' 항목을 (terminal, (lo,hi)) 로."""
    for k, v in limits.items():
        if k.endswith("_window_V") and isinstance(v, (list, tuple)) and len(v) == 2:
            yield k[: -len("_window_V")].upper(), v


# ---------------------------------------------------------------------------
# 검사
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    severity: str = "error"    # error | warn

    def __str__(self) -> str:
        return f"[{self.severity}:{self.code}] {self.message}"


def validate(plan: IVPlan, bounds: Bounds, *,
             terminals: Iterable[str] = TERMINALS) -> List[Violation]:
    """plan 을 경계에 대고 전수 검사한다. 반환 = 위반 목록(비면 통과).

    필드가 유한하니 전수 검사가 된다 — 이게 plan 을 '값'으로 둔 이유다.
    에이전트는 이 함수의 존재를 모른다. 검사받는 쪽이 검사를 고쳐 쓰면 안 되니까.
    """
    v: List[Violation] = []
    terms = set(terminals)

    # --- 1. 형식 -----------------------------------------------------------
    if plan.kind not in ("transfer", "output"):
        v.append(Violation("kind", f"알 수 없는 kind: {plan.kind!r}"))

    axes = [("var1", plan.var1)] + ([("var2", plan.var2)] if plan.var2 else [])
    for tag, ax in axes:
        if ax.terminal not in terms:
            v.append(Violation("terminal", f"{tag}: 모르는 단자 {ax.terminal!r}"))
        if ax.direction not in DIRECTIONS:
            v.append(Violation("direction", f"{tag}: direction={ax.direction!r}"))
        if ax.spacing not in SPACINGS:
            v.append(Violation("spacing", f"{tag}: spacing={ax.spacing!r}"))
        if ax.n_steps < 2:
            v.append(Violation("points", f"{tag}: 포인트 수가 {ax.n_steps} 개"))
        if ax.compliance is None or ax.compliance <= 0:
            v.append(Violation("compliance", f"{tag}: compliance 가 {ax.compliance!r}"))

    for t in plan.constants:
        if t not in terms:
            v.append(Violation("terminal", f"constants: 모르는 단자 {t!r}"))

    if plan.var2 and plan.var2.terminal == plan.var1.terminal:
        v.append(Violation("axis_clash",
                           f"var1 과 var2 가 같은 단자({plan.var1.terminal})를 쓴다"))
    if plan.var2 and plan.var2.terminal in plan.constants:
        # var2 채널은 DC 로 먼저 잡아두고 스텝마다 덮어쓰는 구조라, constants 에
        # 있는 것 자체는 정상이다(드라이버가 요구한다). 다만 거기 적힌 값이
        # var2 시작값과 다르면 사람이 다른 의도로 넣은 것이므로 알려준다.
        const_v = float(plan.constants[plan.var2.terminal])
        if abs(const_v - float(plan.var2.start)) > 1e-9:
            v.append(Violation(
                "axis_shadow",
                f"constants[{plan.var2.terminal}]={const_v:g} 가 var2 시작값 "
                f"{plan.var2.start:g} 과 다르다 — 스텝 값이 덮어쓰므로 무시된다",
                severity="warn"))

    for t, r in plan.ranges.items():
        if r.mode not in RANGE_MODES:
            v.append(Violation("range_mode", f"ranges[{t}].mode={r.mode!r}"))

    if plan.kind == "transfer" and plan.var1.terminal in ("D", "S"):
        v.append(Violation("kind_axis",
                           f"transfer 인데 var1 이 {plan.var1.terminal} 이다", severity="warn"))
    if plan.kind == "output" and plan.var1.terminal != "D":
        v.append(Violation("kind_axis",
                           f"output 인데 var1 이 {plan.var1.terminal} 이다", severity="warn"))

    # --- 2. 규모 -----------------------------------------------------------
    if plan.n_points > bounds.max_points:
        v.append(Violation("max_points",
                           f"총 {plan.n_points} 포인트 > 상한 {bounds.max_points}. "
                           f"누적 스트레스 시간이 길어진다"))

    # --- 3. 전압 (단자쌍) ---------------------------------------------------
    spans = plan.terminal_spans(terms)
    for (a, b), lim in bounds.pair_v_max.items():
        if a not in spans or b not in spans:
            continue
        amin, amax = spans[a]
        bmin, bmax = spans[b]
        # 최악조건: 두 축이 독립이라고 본다. 한 plan 에서 동시에 훑는 축은
        # var1/var2 뿐이고 그 둘은 실제로 독립이므로 과대평가가 아니다.
        worst = max(abs(amax - bmin), abs(amin - bmax))
        if worst > lim + 1e-9:
            v.append(Violation(
                "pair_voltage",
                f"|V{a} - V{b}| 최악 {worst:.2f} V > 한계 {lim:g} V "
                f"(V{a}∈[{amin:g},{amax:g}], V{b}∈[{bmin:g},{bmax:g}])"))

    # --- 3b. 경계가 아예 없는 단자 -------------------------------------------
    # 내압 미확인 게이트(예: 자료에 특성화 곡선이 없는 TG)는 pair_v_max 가
    # 안 만들어진다. 그러면 위 검사가 그 단자를 통째로 건너뛰어 '검사를 통과한 것처럼'
    # 보인다. 구멍을 남기느니 막는다 — 쓰려면 경계를 채워야 한다.
    active = [t for t in plan.active_terminals if t in terms]
    for t in active:
        others = [o for o in active if o != t]
        if others and all(bounds.limit_for(t, o) is None for o in others):
            v.append(Violation(
                "unbounded_terminal",
                f"{t} 에 대한 전압 상한이 정의되어 있지 않다 → 전압 검사가 "
                f"이 단자를 건너뛴다. stack 의 limits 에 경계를 채울 것"))

    # --- 4. 운용 창 ---------------------------------------------------------
    for t, (lo, hi) in bounds.windows.items():
        if t not in spans:
            continue
        tmin, tmax = spans[t]
        if tmin < lo - 1e-9 or tmax > hi + 1e-9:
            v.append(Violation(
                "window",
                f"V{t} ∈ [{tmin:g},{tmax:g}] 가 운용 창 [{lo:g},{hi:g}] 밖"))

    # --- 5. 분해능 (스텝 vs SS) ---------------------------------------------
    # 누가 조건을 만들었든(사람·seed.py·에이전트) 여기서 한 번 걸린다.
    # SS[V/decade] 보다 스텝이 성기면 turn-on 구간에 점이 몇 개 안 들어가고,
    # SS 가 부풀려진 채 '그럴듯한 숫자'로 나온다. 실측: 스텝 0.5 V / SS 0.27
    # → 0.44 로 보고됨(62% 과대). 값이 안 나오는 게 아니라 틀리게 나오는 것이
    # 위험해서 경고로 남긴다. (성긴 survey 자체는 정당한 전략이라 error 는 아님)
    # 실측 SS 가 있을 때만 검사한다. 자료의 기대 SS 로는 검사하지 않는다 —
    # 그 값이 틀리면 멀쩡한 조건에 경고가 붙고 틀린 조건이 통과한다.
    ss = bounds.measured.get("ss_V_per_dec")
    if plan.kind == "transfer" and ss:
        lo, hi = plan.var1.span
        n = plan.var1.n_steps
        if n > 1:
            step = (hi - lo) / (n - 1)
            if step > float(ss):
                v.append(Violation(
                    "resolution",
                    f"스텝 {step:.3g} V 가 실측 SS {float(ss):g} V/dec 보다 성기다 "
                    f"→ SS 가 과대평가된다. 점을 "
                    f"{int(round((hi - lo) / (float(ss) / 1.5))) + 1}개 이상으로 "
                    f"(스텝 ≤ {float(ss) / 1.5:.3g} V) 잡을 것",
                    severity="warn"))

    # --- 6. 컴플라이언스 ----------------------------------------------------
    for t in terms:
        c = plan.compliance_of(t)
        lim = bounds.i_compliance_max.get(t)
        if c is not None and lim is not None and c > lim * (1 + 1e-9):
            v.append(Violation(
                "compliance",
                f"I({t}) compliance {c:g} A > 상한 {lim:g} A"))

    return v


def assert_valid(plan: IVPlan, bounds: Bounds) -> None:
    """위반이 하나라도 있으면 예외. session 이 executor 를 부르기 직전에 쓴다."""
    vs = [x for x in validate(plan, bounds) if x.severity == "error"]
    if vs:
        raise SafetyError("plan 이 안전 경계를 위반함:\n  " + "\n  ".join(map(str, vs)), vs)


class SafetyError(RuntimeError):
    def __init__(self, message: str, violations: List[Violation]):
        super().__init__(message)
        self.violations = violations
