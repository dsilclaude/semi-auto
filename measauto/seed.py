"""seed.py — 첫 조건의 **폴백**을 소자 스펙에서 계산한다.

[여기는 판단하는 곳이 아니다]
어디를 스윕할지, 어느 쪽으로 얼마나 넓힐지, turn-on 위로 어디까지 갈지는
**에이전트가 정한다.** 이 모듈은 에이전트를 못 부를 때(API 실패, --no-agent-seed)
쓰이는 안전한 출발점을 만들 뿐이다. 아래 상수들은 **폴백 기본값이지 정책이
아니다** — 이 값들이 측정의 성패를 좌우한다면 그건 에이전트가 안 불린 것이다.

다만 두 가지는 판단이 아니라 **측정이 성립하기 위한 조건**이라 여기 남는다:
  · 스텝 ≤ SS/4        — 이보다 성기면 SS 라는 값 자체가 안 나온다
  · turn-on 아래 여유   — floor 를 못 보면 Ioff·SS 를 계산할 수 없다


시드를 손으로 적어 두면 stack.json 을 갈아끼워도 따라오지 않는다. 그러면
웨이퍼가 바뀔 때 경계만 움직이고 측정 조건은 그대로여서, 둘이 어긋난 채로
측정이 나간다. 여기서 stack 하나로부터 경계와 시드를 함께 만든다.

무엇을 어디서 가져오나
  · 상한/하한  ← safety.bounds_from_stack (유전막 두께·ε 에서 계산된 벽)
  · 시작/끝/스텝 ← stack["expected"] (설계자료의 Vth·SS·특성화 범위)

핵심은 **스텝을 SS 에서 정하는 것**이다. SS[V/decade] 는 turn-on 이 얼마나
좁은 전압 구간에서 끝나는지를 말해준다. SS=0.27 이면 7 decade 전이가 약 2 V
안에서 끝나므로, 0.5 V 스텝으로는 그 구간에 점이 3~4개밖에 안 들어가고
SS 가 0.44 로 부풀려진다(실측 검증). 스텝을 SS 의 분수로 잡으면 이 실수가
구조적으로 안 나온다.

expected 가 없는 stack 이면 경계 전체를 성기게 훑는 blind survey 로 떨어진다.
어느 쪽으로 만들어졌는지는 plan.note 에 남으므로 결과 폴더에서 확인할 수 있다.

이 모듈은 plan 과 safety 만 import 한다. 장비도 pandas 도 모른다.
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Sequence, Tuple

from dataclasses import replace

from .plan import DEFAULT_RANGES, IVPlan, RangeSpec, output, transfer
from .safety import Bounds, bounds_from_stack, load_stack

# 기본값 — 왜 이 숫자인지는 각 주석에.
# 스텝 = SS / 이 값. 이름 그대로 **서브스레숄드 decade 당 점 수**다.
# 1.5 로 두었더니 6 decade 전이에 9점밖에 안 들어가 SS 회귀가 5점으로 이뤄졌고,
# 그 값을 믿을 수 없다는 지적이 실측에서 나왔다. SS 를 회귀로 뽑으려면 한
# decade 안에 최소 몇 점은 있어야 한다. 4 면 6 decade 에 24점 → 회귀 창을
# 넉넉히 잡을 수 있다.
# 대가: 점이 늘면 스윕 시간이 늘고 그만큼 바이어스 노출도 길어진다. 범위를
# 좁히는 것으로 상쇄해야 한다(실측 turn-on 을 알면 에이전트가 좁힌다).
SS_POINTS_PER_DECADE = 4.0
OFF_DECADES = 6.0            # turn-on 아래로 몇 decade 를 봐야 floor 가 잡히나
OFF_PAD_V = 2.0              # 그 아래 여유 [V]. floor 평탄도를 보려면 필요.
# turn-on **위로** 얼마나 갈 것인가 — 폴백 기본값. 진짜 답(이동도를 보려면
# gm 꼭대기를 넘겨야 한다 등)은 에이전트가 gm_peak_at_edge 를 보고 정한다.
# 예전에는 경계(hi_wall)까지 열었는데, 그건 limits.json 에 사람이 넣어 둔
# 운용 창이 있을 때만 좁게 유지됐다. 그게 없는 stack 에서는 절연파괴 벽까지
# 열려 -3 ~ 28.5 V / 631 점 같은 창이 나왔다. 폴백이 그렇게 크면 안 된다.
ON_SPAN_MIN_V = 3.0          # turn-on 위로 최소 이만큼
ON_SPAN_SS_MULT = 10.0       # 또는 SS 의 이 배 (가파른 소자일수록 좁아도 된다)
WALL_MARGIN = 0.95           # 경계에 딱 붙지 않는다 (검증 오차·반올림 여유)
MAX_POINTS_MARGIN = 0.9      # max_points 상한에도 여유
# 게이트 컴플라이언스 = 경계 상한의 이 비율. 경계 상한 자체가 이미 '보호를 위해
# 낮게 잡은 값'이므로 더 조이면 정상 신호까지 클램프되어 곡선이 왜곡된다. 1.0 = 상한 그대로.
GATE_COMPLIANCE_FRAC = 1.0
# 드레인 컴플라이언스 = 기대 Ion × 이 값 (경계로 클립).
# 10 배면 관례적인 보호 여유다. 이보다 크게 잡으면 단락·접촉불량 때 흐르는
# 전류를 못 막고, 작게 잡으면 정상 on 전류가 잘려 곡선이 평평해진다.
DRAIN_HEADROOM = 10.0
# --- 첫 소자(실측이 없을 때) --------------------------------------------------
# **좁게 시작해서 넓혀 간다.** 경계 전체를 한 번에 훑지 않는다.
#
# 두 번 틀렸던 곳이다. 처음에는 SS 물리 하한에 맞춰 경계 전체를 0.015 V 스텝으로
# 잡았고(3600점 × 민감 레인지 = 소자당 36분), 그다음엔 스텝만 성기게 했다.
# 둘 다 '한 번에 넓게'라는 점이 같았다. 그건 소자에 필요 이상의 전압을 처음부터
# 거는 것이라, 무엇이 나올지 모르는 상태에서 할 일이 아니다.
#
# 옳은 순서는 좁은 창으로 시작해 turn-on 이 안 보이면 ×1.5 로 넓히는 것이다.
# Ig 가 안전 센서다 — 지수적 상승·정역 불일치가 보이면 넓히지 말고 멈춘다.
# 넓히는 것은 **정찰 소자에서만** 한다(scout). 나머지 소자는 거기서 찾은 범위로
# 한 번씩만 잰다. 그래야 반복 스트레스가 한 소자에 몰리고 나머지는 깨끗하다.
# **중심은 0 V 다.** 예전에는 (-1, 5) 로 못박아 두었는데, 그건 이 웨이퍼의
# n형 소자가 +1.5 V 근처에서 켜진다는 것을 이미 안다는 뜻이었다. 소자를 모르는
# 상태에서는 쓸 수 없는 가정이다 — p형이거나 공핍형이면 창의 대부분이 헛물이고,
# 넓혀 갈 때도 치우침이 그대로 커진다. 0 V 는 소자가 바이어스 없이 놓인 점이고
# 극성을 전제하지 않는 유일한 중심이다.
#
# 폭은 안전 창의 일부만 쓴다. 절대값으로 박아두면 유전막이 바뀌어 경계가
# 달라져도 첫 창이 그대로여서, 두꺼운 막에서는 턱없이 좁고 얇은 막에서는
# 위험하게 넓어진다. 비율로 두면 기술이 바뀌어도 따라온다.
BLIND_FRACTION = 0.15         # 안전 창의 이 비율만 처음에 연다
BLIND_HALF_MIN_V = 2.0        # 그래도 이보다는 넓게 (floor 를 봐야 한다)
BLIND_HALF_MAX_V = 5.0        # 그래도 이보다는 좁게 (모르는 소자에 큰 전압 금지)
BLIND_STEP_V = 0.05           # 창이 좁으니 촘촘해도 점이 얼마 안 된다
# 정찰 스윕의 측정 레인지. 가장 민감한 레인지는 점당 시간이 크게 늘어나므로,
# 위치를 찾는 동안은 중간 레인지를 쓰고 정밀 측정에서만 연다.
BLIND_RANGE = "100 pA"

# B1517A HRSMU 가 고를 수 있는 측정 전류 레인지 (아래로 갈수록 분해능이 좋고 느리다).
# 'limited auto ranging' 은 여기 적은 레인지보다 **아래로는 안 내려간다**.
# 기본값이 1 nA 면 그 레인지의 노이즈(≈ 수 pA)가 바닥이 되어, 산화물 TFT 의
# 진짜 off (1e-13 이하)를 아예 못 본다 — 실측에서 Ioff 가 5e-12 에 고정됐고
# 게이트를 더 내려도 안 내려갔다. 소자가 아니라 레인지가 만든 바닥이었다.
CURRENT_RANGES = [
    (1e-11, "10 pA"), (1e-10, "100 pA"), (1e-9, "1 nA"), (1e-8, "10 nA"),
    (1e-7, "100 nA"), (1e-6, "1 uA"), (1e-5, "10 uA"), (1e-4, "100 uA"),
    (1e-3, "1 mA"), (1e-2, "10 mA"), (1e-1, "100 mA"),
]
RANGE_HEADROOM = 10.0        # 기대 최소전류의 이 배까지 내려가는 레인지를 고른다


def range_for(i_min: float) -> str:
    """이 정도 전류를 보려면 어느 레인지까지 열어야 하나.

    i_min 은 '보고 싶은 가장 작은 전류'다. 그보다 한참 위에서 레인지를 막으면
    그 레인지의 노이즈가 측정 바닥이 된다.
    """
    target = abs(i_min) / RANGE_HEADROOM
    for lim, name in CURRENT_RANGES:
        if lim >= target:
            return name
    return CURRENT_RANGES[-1][1]


def blind_window(lo_wall: float, hi_wall: float, *,
                 fraction: float = BLIND_FRACTION,
                 half_min: float = BLIND_HALF_MIN_V,
                 half_max: float = BLIND_HALF_MAX_V) -> Tuple[float, float]:
    """실측이 하나도 없을 때의 첫 창. **0 V 를 중심으로 안전 창의 일부만.**

    소자를 모르면 turn-on 이 어디인지도, n형인지 p형인지도 모른다. 그 상태에서
    한쪽으로 치우친 창을 잡으면 절반이 헛물일 뿐 아니라, 넓혀 갈 때 그 치우침이
    같이 커진다. 0 을 중심으로 두면 어느 극성이든 같은 비용으로 훑는다.

    경계가 0 을 품지 않는 특수한 배선이면(예: 게이트에 오프셋이 걸린 셋업)
    0 중심이 성립하지 않으므로 경계 안쪽으로 접어 넣는다.
    """
    span = max(hi_wall - lo_wall, 0.0)
    half = min(max(span * fraction / 2.0, half_min), half_max)
    lo, hi = max(-half, lo_wall), min(half, hi_wall)
    if hi <= lo:                      # 0 이 경계 밖 — 경계 쪽으로 붙인다
        if lo_wall > 0:
            lo, hi = lo_wall, min(lo_wall + 2 * half, hi_wall)
        else:
            lo, hi = max(hi_wall - 2 * half, lo_wall), hi_wall
    return lo, hi


def _gate_window(bounds: Bounds, gate: str, constants: Dict[str, float]
                 ) -> Tuple[float, float]:
    """이 게이트가 실제로 걸을 수 있는 전압 구간 (lo, hi).

    대향 단자가 DC 로 고정돼 있으므로 |Vg - Vother| ≤ L 을 Vg 구간으로 푼다.
    운용 창(windows)이 있으면 그것과도 교집합.
    """
    lo, hi = -math.inf, math.inf
    for other, v in constants.items():
        lim = bounds.limit_for(gate, other)
        if lim is None:
            continue
        lo = max(lo, v - lim)
        hi = min(hi, v + lim)

    win = bounds.windows.get(gate)
    if win:
        lo, hi = max(lo, win[0]), min(hi, win[1])

    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        raise ValueError(
            f"{gate} 의 허용 전압 구간을 정할 수 없다 (lo={lo}, hi={hi}). "
            f"stack 의 gates/limits 를 확인할 것.")
    return lo, hi


def _apply_margin(lo: float, hi: float, margin: float) -> Tuple[float, float]:
    """경계에서 margin 만큼 안쪽으로. 0 을 중심으로 줄인다."""
    return lo * margin, hi * margin


def _device_facts(stack: dict) -> dict:
    """시드 계산에 쓸 소자 값. **실측만 쓴다. 설계자료값은 안 본다.**

    자료의 Vth·SS·Ion 은 다른 로트·다른 조건의 값일 수 있고, W 스플릿 소자에서는
    아예 유추다. 실제로 이 웨이퍼에서 Vth 는 0.3~0.5 V 낮고 SS 는 1.4~1.9 배
    나빴다 — 네 소자 모두 같은 방향이라 산포가 아니라 계통 차이였다.
    그 값으로 조건을 잡으면 틀린 전제 위에 측정을 쌓게 된다.

    그래서 stack["measured"] 만 읽는다. 없으면 아무것도 모르는 상태이고,
    seed_transfer 가 blind 경로로 간다(물리 상한만 가지고 훑는다).
    """
    meas = stack.get("measured") or {}
    out = {k: v for k, v in meas.items()
           if v is not None and not str(k).startswith("_") and k != "source"}
    if out:
        out["_basis"] = f"실측 ({meas.get('source', '출처 미기재')})"
    return out


def seed_output(stack, *, gate: str = "BG", source: str = "S", drain: str = "D",
                gate_steps: int = 5, points: int = 81,
                wall_margin: float = WALL_MARGIN,
                gate_compliance_frac: float = GATE_COMPLIANCE_FRAC,
                direction: str = "single",
                measured_vth: Optional[float] = None,
                bounds: Optional[Bounds] = None) -> IVPlan:
    """output(Id-Vd) 시드. 접촉저항(선형 기울기)과 포화·λ 를 보기 위한 조건.

    transfer 와 다른 점이 둘이다.

    · **Vd 범위**는 SS 가 아니라 '포화에 들어가는가'가 정한다. 포화는 대략
      Vd > Vg − Vth 에서 시작하므로, 가장 높은 게이트 스텝에서도 포화 구간이
      남도록 Vd 상한을 잡아야 λ 를 뽑을 수 있다. 경계(|VD−VS|)가 그 상한이다.
    · **게이트 스텝**은 turn-on 위에서 고르게 놓는다. Vth 아래 스텝은 곡선이
      바닥에 붙어 정보가 없고, 스텝마다 게이트를 그 전압에 붙들고 Vd 를 훑으므로
      스트레스만 쌓인다. 그래서 실측 Vth 가 있으면 그걸 쓰는 편이 훨씬 낫다.

    전류가 transfer(Vd 0.1 V)보다 두 자릿수 커지므로 드레인 컴플라이언스와
    측정 레인지도 그에 맞춰 다시 잡는다.
    """
    stack_d = stack if isinstance(stack, dict) else load_stack(stack)
    bnd = bounds or bounds_from_stack(stack_d)
    exp = _device_facts(stack_d)

    # --- Vd 범위: D-S 경계와 자료 특성화 상한 중 낮은 쪽 ---------------------
    ds_lim = bnd.limit_for(drain, source)
    vd_max = ds_lim * wall_margin if ds_lim else 10.0
    vds = exp.get("characterized_vd_V")
    if vds:
        vd_max = min(vd_max, float(max(vds)))
    vd_max = math.floor(vd_max * 100) / 100

    # --- 게이트 스텝: turn-on 위에서 고르게 -----------------------------------
    vth = measured_vth if measured_vth is not None else exp.get("vth_V")
    g_lo, g_hi = _apply_margin(*_gate_window(bnd, gate, {source: 0.0, drain: vd_max}),
                               wall_margin)
    rng = exp.get("characterized_vg_range_V")
    if rng:
        g_hi = min(g_hi, float(rng[1]))
    # 첫 스텝은 turn-on 바로 위(과전압 1 V), 마지막은 허용 상한.
    g_start = (float(vth) + 1.0) if vth is not None else max(g_lo, 0.0)
    g_start = math.ceil(min(max(g_start, g_lo), g_hi) * 100) / 100
    g_stop = math.floor(g_hi * 100) / 100
    if g_stop <= g_start:
        raise ValueError(f"게이트 스텝 범위가 비었다 ({g_start}~{g_stop})")

    # --- 전류: Vd 가 커지면 transfer 조건보다 훨씬 많이 흐른다 ----------------
    i_on = exp.get("i_on_A_at_vd10_vg20") or exp.get("i_on_A_at_vd0p1_vg20")
    d_lim = bnd.i_compliance_max.get(drain)
    drain_comp = min(float(d_lim), float(i_on) * DRAIN_HEADROOM) if (i_on and d_lim) \
        else (d_lim or 1e-3)
    s_lim = bnd.i_compliance_max.get(source)
    source_comp = max(drain_comp * 10, 1e-2)
    if s_lim is not None:
        source_comp = min(source_comp, float(s_lim))
    g_lim = bnd.i_compliance_max.get(gate)
    gate_comp = g_lim * gate_compliance_frac if g_lim else 1e-7

    ranges = dict(DEFAULT_RANGES)
    ranges[drain] = RangeSpec(mode="limited", range=range_for(drain_comp / 1e4))

    note = (f"output: Vd 0~{vd_max:g} V (경계 |V{drain}-V{source}| 안), "
            f"{gate} {g_start:g}~{g_stop:g} V {gate_steps}스텝. "
            f"게이트 시작은 turn-on(Vth"
            + (f" 실측 {measured_vth:g}" if measured_vth is not None
               else f" 기대 {vth:g}" if vth is not None else " 미상")
            + ") 위로 잡아 바닥에 붙는 스텝을 없앴다. "
              "포화 구간이 남아야 λ 를 뽑을 수 있다. "
            + f"stack={stack_d.get('name', '?')}")

    plan = output(drain, start=0.0, stop=vd_max, points=points,
                  gate=gate, gate_start=g_start, gate_stop=g_stop,
                  gate_steps=gate_steps, source=source, direction=direction,
                  drain_compliance=drain_comp, gate_compliance=gate_comp,
                  label=f"seed_output_{drain}", note=note)
    return replace(plan, ranges=ranges,
                   compliances={**plan.compliances, source: source_comp})


def seed_transfer(stack, *, gate: str = "BG", source: str = "S", drain: str = "D",
                  ss_points_per_decade: float = SS_POINTS_PER_DECADE,
                  off_decades: float = OFF_DECADES, off_pad_V: float = OFF_PAD_V,
                  wall_margin: float = WALL_MARGIN,
                  max_points_margin: float = MAX_POINTS_MARGIN,
                  gate_compliance_frac: float = GATE_COMPLIANCE_FRAC,
                  drain_headroom: float = DRAIN_HEADROOM,
                  direction: str = "double",
                  vd: Optional[float] = None,
                  bounds: Optional[Bounds] = None) -> IVPlan:
    """stack(이름 또는 dict) → transfer 시드 plan.

    반환된 plan 은 반드시 validate 를 다시 태울 것 — 여기서 경계를 참고하긴
    하지만, 검사는 여전히 session 이 executor 직전에 거는 것이 원칙이다.
    """
    stack_d = stack if isinstance(stack, dict) else load_stack(stack)
    bnd = bounds or bounds_from_stack(stack_d)
    exp = _device_facts(stack_d)

    # --- Vd : 선형영역 transfer 의 관례값. 낮을수록 채널 스트레스가 작다.
    # (Vd ≪ Vg−Vth 여야 선형영역 이동도 공식이 성립한다)
    if vd is None:
        vd = float(exp.get("vd_V") or 0.1)

    constants = {source: 0.0, drain: float(vd)}
    lo_wall, hi_wall = _apply_margin(*_gate_window(bnd, gate, constants), wall_margin)

    vth = exp.get("vth_V")
    ss = exp.get("ss_V_per_dec")

    if vth is not None and ss is not None and ss > 0:
        # --- 실측 기반 -----------------------------------------------------
        vth, ss = float(vth), float(ss)

        # 시작: turn-on 아래로 off_decades 만큼 + 여유. floor 를 봐야
        #       'off 가 진짜 off 인지'와 floor_slope 가 계산된다.
        start = max(vth - off_decades * ss - off_pad_V, lo_wall)
        # 끝: turn-on 위로 필요한 만큼만. 경계까지 여는 것은 '필요'가 아니라
        #     '가능'일 뿐이고, 모르는 소자에 큰 전압을 거는 이유가 안 된다.
        #     gm 이 안 꺾였으면 다음 소자에서 이 끝을 늘린다.
        stop = min(vth + max(ON_SPAN_MIN_V, ON_SPAN_SS_MULT * ss), hi_wall)

        step = ss / ss_points_per_decade          # ← 이 줄이 이 모듈의 요점
        basis = (f"{exp['_basis']} 기반: Vth={vth:g} V, SS={ss:g} V/dec "
                 f"→ 스텝 {step:.3g} V (= SS/{ss_points_per_decade:g})")

        # 발산 방지: 측정된 SS 는 **참값의 상한**이다. 스텝이 성길수록 SS 가
        # 부풀려지므로, 그 값을 그대로 믿고 스텝을 늘리면 다음 SS 가 더 부풀려지고
        # 스텝이 또 늘어난다. 그래서 **그 SS 를 만들어낸 스텝보다 성겨지지 않게**
        # 잠근다. 이전 측정이 그 스텝으로 SS 를 뽑아냈으니 유지는 항상 안전하다.
        prev_step = exp.get("step_V")
        if prev_step and step > float(prev_step):
            step = float(prev_step)
            basis += (f" — 단 직전 측정 스텝 {float(prev_step):g} V 보다 성겨지지"
                      f" 않게 잠금(측정 SS 는 참값의 상한이라 되먹이면 발산한다)")
    else:
        # --- 아무것도 모름: 경계 전체를, 물리 하한이 허용하는 만큼 촘촘하게 ---
        # 자료값을 안 쓰므로 turn-on 이 어디인지 모른다. 놓치지 않으려면 경계
        # 전체를 훑어야 하고, SS 가 얼마든 분해되려면 스텝을 물리 하한
        # (60 mV/dec)에 맞춰야 한다. 이 한 번이 이후 모든 소자의 근거가 되므로
        # 여기서 아끼면 뒤가 전부 흔들린다.
        start, stop = blind_window(lo_wall, hi_wall)
        step = BLIND_STEP_V
        basis = (f"blind 폴백: 소자 실측이 없다. **0 V 중심**의 좁은 창 "
                 f"{start:g}~{stop:g} V 로 시작한다 — 경계({lo_wall:g}~{hi_wall:g} V) "
                 f"전체를 처음부터 걸지 않고, 극성(n/p)도 전제하지 않는다. "
                 f"turn-on 이 이 창에 없으면 **다음 소자에서** 관측된 기울기 방향으로 "
                 f"넓힌다(같은 소자를 다시 재지 않는다). "
                 f"※ 이 창은 에이전트가 조건을 못 정했을 때의 폴백이다 — 보통은 "
                 f"에이전트가 목적과 경계를 보고 정한다")

    # 부동소수점 꼬리를 자른다 — 기록·재현·눈으로 읽기 위해. 안쪽으로만 민다.
    start, stop = math.ceil(start * 100) / 100, math.floor(stop * 100) / 100

    if stop <= start:
        raise ValueError(f"시드 범위가 비었다 (start={start}, stop={stop})")

    # --- 점 수: 스텝에서 환산하되 max_points 안으로 -------------------------
    span = stop - start
    points = int(round(span / step)) + 1
    cap = int(bnd.max_points * max_points_margin)
    if direction == "double":
        cap //= 2                                  # 왕복은 실측 점이 2배
    if points > cap:
        points = max(cap, 2)
        basis += f" / max_points 때문에 {points}점으로 제한"

    # --- 컴플라이언스 -------------------------------------------------------
    g_lim = bnd.i_compliance_max.get(gate)
    gate_comp = g_lim * gate_compliance_frac if g_lim else 1e-7

    # 실측 Ion 이 있으면 그 10배로 조인다. 없으면 경계 상한을 그대로 쓴다 —
    # 그 상한은 사람이 limits.json 에 넣은 보호선이라 근거가 있다(자료 추정이
    # 아니다). 첫 소자는 이 상한이 유일한 보호이므로 limits 를 잘 잡아둘 것.
    d_lim = bnd.i_compliance_max.get(drain)
    i_on = exp.get("i_on_A_at_vd0p1_vg20") or exp.get("i_on_A")
    if i_on and d_lim:
        drain_comp = min(float(d_lim), float(i_on) * drain_headroom)
    else:
        drain_comp = d_lim or 1e-3

    # 소스는 드레인+게이트 전류를 다 받으므로 넉넉해야 하지만, 경계가 더 낮으면
    # 그쪽이 이긴다. plan.transfer 의 기본값(1e-2 하한)을 그냥 쓰면 경계를 넘는다.
    s_lim = bnd.i_compliance_max.get(source)
    source_comp = max(drain_comp * 10, 1e-2)
    if s_lim is not None:
        source_comp = min(source_comp, float(s_lim))

    # --- 측정 레인지 --------------------------------------------------------
    # 'limited' 는 적은 레인지보다 아래로 안 내려간다. 너무 위에서 막으면 그
    # 레인지의 노이즈가 곧 측정 바닥이 되어 진짜 off 를 못 본다(실측에서 1 nA 로
    # 막혀 Ioff 가 5e-12 에 고정됐다). 반대로 가장 민감한 레인지는 점당 시간이
    # 크게 늘어난다. 그래서 목적에 따라 가른다:
    #   실측 있음(좁고 정밀한 측정) → 가장 민감한 레인지. 점이 적어 감당된다.
    #   실측 없음(넓은 위치 찾기)   → 중간 레인지. off 값은 어차피 참고용이다.
    drain_range = CURRENT_RANGES[0][1] if vth is not None else BLIND_RANGE
    ranges = dict(DEFAULT_RANGES)
    ranges[drain] = RangeSpec(mode="limited", range=drain_range)

    note = (f"{basis}. 범위 {start:g}~{stop:g} V 는 "
            f"경계 [{lo_wall:g}, {hi_wall:g}] 안. "
            f"드레인 측정 레인지 {drain_range} "
            + ("(가장 민감한 레인지 — off 바닥이 레인지 노이즈에 막히지 않게)"
               if vth is not None else
               "(위치 찾기용 중간 레인지 — 가장 민감한 레인지는 점당 시간이 크게 "
               "늘어 넓은 스윕에 안 맞는다. 정밀 off 는 다음 소자에서)")
            + ". "
            f"stack={stack_d.get('name', '?')}")

    plan = transfer(gate, start=start, stop=stop, points=points, vd=vd,
                    source=source, drain=drain, direction=direction,
                    gate_compliance=gate_comp, drain_compliance=drain_comp,
                    source_compliance=source_comp,
                    label=f"seed_{gate}_from_stack", note=note)
    return replace(plan, ranges=ranges)
