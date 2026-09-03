"""agent/schema.py — 에이전트가 낼 수 있는 답의 문법.

에이전트는 IVPlan 전체를 새로 쓰지 않는다. **시드 plan 에 덮어쓸 패치**만 낸다.
이유 두 가지:
  1. 필드가 유한해야 실행 전 전수 검사가 된다. (timing/ADC/range 같은
     '거의 안 바뀌는' 값을 매번 다시 쓰게 하면 검사 표면이 무한히 넓어진다)
  2. 코드 생성은 검증도 롤백도 안 된다. dict 는 둘 다 된다.

status 는 열린 문장이 아니라 열거값이다. 판단을 구조화해서 받아야
session 이 분기할 수 있고, 나중에 리플레이 평가에서 셀 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Optional

from ..plan import IVPlan, SweepAxis

STATUSES = (
    "propose",            # 다음 plan 을 제안한다 (patch 필수)
    "converged",          # 이 소자는 충분히 봤다
    "dead",               # 소자가 죽었다고 본다
    "leaky",              # 게이트 누설이 지배한다
    "out_of_bounds",      # 필요한 범위가 안전 경계 밖이다
    "polarity_anomaly",   # 극성/방향이 기대와 다르다
    "needs_human",        # 데이터만으로 못 가른다. 사람이 봐야 한다
)


@dataclass(frozen=True)
class PlanPatch:
    """시드 plan 위에 덮어쓸 값들. None 인 필드는 '그대로 둔다'는 뜻."""
    kind: Optional[str] = None
    var1_start: Optional[float] = None
    var1_stop: Optional[float] = None
    var1_points: Optional[int] = None
    var1_direction: Optional[str] = None
    var1_compliance: Optional[float] = None
    var2_terminal: Optional[str] = None
    var2_start: Optional[float] = None
    var2_stop: Optional[float] = None
    var2_points: Optional[int] = None
    var2_compliance: Optional[float] = None
    constants: Optional[Dict[str, float]] = None
    drain_compliance: Optional[float] = None

    @staticmethod
    def from_dict(d: Optional[dict]) -> Optional["PlanPatch"]:
        if not d:
            return None
        known = PlanPatch.__annotations__
        out = {k: v for k, v in d.items() if k in known and v is not None}
        # constants 는 스키마가 네 단자를 전부 요구하므로, 안 건드릴 단자는
        # 값이 null 로 온다. 딕셔너리 자체는 None 이 아니라 위 필터를 통과하니
        # 안쪽도 걸러야 한다 (안 그러면 float(None) 에서 죽는다).
        if isinstance(out.get("constants"), dict):
            out["constants"] = {k: v for k, v in out["constants"].items()
                                if v is not None}
            if not out["constants"]:
                out.pop("constants")
        return PlanPatch(**out)


def apply_patch(base: IVPlan, patch: Optional[PlanPatch], *, note: str = "") -> IVPlan:
    """시드 plan + 패치 → 새 IVPlan. 여기서 만들어진 것도 반드시 validate 를 탄다."""
    if patch is None:
        return replace(base, note=note or base.note)

    v1 = base.var1
    v1 = replace(
        v1,
        start=v1.start if patch.var1_start is None else float(patch.var1_start),
        stop=v1.stop if patch.var1_stop is None else float(patch.var1_stop),
        points=v1.points if patch.var1_points is None else int(patch.var1_points),
        step=None if patch.var1_points is not None else v1.step,
        direction=v1.direction if patch.var1_direction is None else patch.var1_direction,
        compliance=(v1.compliance if patch.var1_compliance is None
                    else float(patch.var1_compliance)),
    )

    v2 = base.var2
    wants_v2 = any(x is not None for x in (
        patch.var2_terminal, patch.var2_start, patch.var2_stop,
        patch.var2_points, patch.var2_compliance))
    if wants_v2:
        if v2 is None:
            if patch.var2_terminal is None:
                raise ValueError("var2 를 새로 만들려면 var2_terminal 이 필요하다")
            v2 = SweepAxis(terminal=patch.var2_terminal,
                           start=patch.var2_start if patch.var2_start is not None else 0.0,
                           stop=patch.var2_stop if patch.var2_stop is not None else 0.0,
                           points=int(patch.var2_points or 5),
                           compliance=float(patch.var2_compliance or 1e-6),
                           label=f"V{patch.var2_terminal}")
        else:
            v2 = replace(
                v2,
                terminal=v2.terminal if patch.var2_terminal is None else patch.var2_terminal,
                start=v2.start if patch.var2_start is None else float(patch.var2_start),
                stop=v2.stop if patch.var2_stop is None else float(patch.var2_stop),
                points=v2.points if patch.var2_points is None else int(patch.var2_points),
                step=None if patch.var2_points is not None else v2.step,
                compliance=(v2.compliance if patch.var2_compliance is None
                            else float(patch.var2_compliance)),
            )

    constants = dict(base.constants)
    if patch.constants:
        # 스키마가 TG/BG/D/S 를 전부 요구하므로, 이 셋업에 없는 단자에도 값이
        # 실려 온다. 그대로 두면 validate 가 '모르는 단자'로 반려해 제안이 통째로
        # 버려진다. base 가 쓰는 단자만 남긴다 — 에이전트가 배선되지 않은 단자를
        # 새로 끌어들일 수는 없고, 쓰는 단자면 base 에 이미 들어 있다.
        known = {base.var1.terminal, *base.constants}
        if base.var2 is not None:
            known.add(base.var2.terminal)
        constants.update({k: float(v) for k, v in patch.constants.items()
                          if v is not None and k in known})

    compliances = dict(base.compliances)
    if patch.drain_compliance is not None:
        compliances["D"] = float(patch.drain_compliance)

    return replace(
        base,
        kind=patch.kind or base.kind,
        var1=v1, var2=v2,
        constants=constants, compliances=compliances,
        note=note or base.note,
    )


# ---------------------------------------------------------------------------
# structured outputs 용 JSON Schema
# ---------------------------------------------------------------------------
def _num(desc: str) -> dict:
    return {"type": ["number", "null"], "description": desc}


# [주의] nullable 필드에 enum 을 같이 주면 API 가 400 을 낸다
#   ("Enum value 'transfer' does not match declared type '['string','null']'").
# 허용값은 description 으로 알리고, 들어온 값은 apply_patch/validate 가 거른다.
_PATCH_PROPS = {
    "kind": {"type": ["string", "null"],
             "description": "측정 종류. \"transfer\" 또는 \"output\". 바꿀 때만, 보통 null."},
    "var1_start": _num("주 sweep 시작 전압 [V]"),
    "var1_stop": _num("주 sweep 끝 전압 [V]"),
    "var1_points": {"type": ["integer", "null"], "description": "주 sweep 포인트 수"},
    "var1_direction": {"type": ["string", "null"],
                       "description": "\"single\"(편도) 또는 \"double\"(왕복). "
                                      "double 이면 히스테리시스를 본다"},
    "var1_compliance": _num("주 sweep 채널 컴플라이언스 [A]"),
    "var2_terminal": {"type": ["string", "null"],
                      "description": "부 sweep(스텝) 단자. \"TG\" / \"BG\" / \"D\" / \"S\" 중 하나"},
    "var2_start": _num("부 sweep 시작 [V]"),
    "var2_stop": _num("부 sweep 끝 [V]"),
    "var2_points": {"type": ["integer", "null"], "description": "부 sweep 스텝 수"},
    "var2_compliance": _num("부 sweep 채널 컴플라이언스 [A]"),
    "constants": {
        "type": ["object", "null"],
        "description": "DC 고정 단자 전압 [V]. 안 쓰는 게이트도 띄우지 말고 0 으로.",
        "properties": {t: _num(f"{t} 전압 [V]") for t in ("TG", "BG", "D", "S")},
        "required": ["TG", "BG", "D", "S"],
        "additionalProperties": False,
    },
    "drain_compliance": _num("드레인 컴플라이언스 [A]"),
}

PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": list(STATUSES),
                   "description": "이 관측에 대한 판단"},
        "reason": {"type": "string",
                   "description": "어떤 지표/곡선 특징을 근거로 그렇게 봤는지 2~3문장"},
        "confidence": {"type": "number",
                       "description": "0~1. 낮으면 session 이 사람에게 넘긴다"},
        "patch": {
            "type": ["object", "null"],
            "description": "status=propose 일 때 다음 측정 조건. 그 외에는 null.",
            "properties": _PATCH_PROPS,
            "required": list(_PATCH_PROPS.keys()),
            "additionalProperties": False,
        },
    },
    "required": ["status", "reason", "confidence", "patch"],
    "additionalProperties": False,
}
