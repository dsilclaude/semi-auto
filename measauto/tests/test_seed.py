"""시드 계산 — stack 을 바꾸면 시드가 따라 움직이는가, 그리고 스텝이
SS 보다 성겨지지 않는가. 후자가 이 모듈의 존재 이유다."""

from __future__ import annotations

import copy

from ..config import DEFAULT
from ..metrics import summarize
from ..replay import SimulatedDevice, simulate
from ..safety import bounds_from_stack, validate
from ..seed import seed_transfer
from .fixtures import stack_with_limits

STACK = "sd_dualgate"


def _step(plan) -> float:
    return abs(plan.var1.stop - plan.var1.start) / (plan.var1.n_steps - 1)


def test_seed_passes_its_own_bounds():
    """계산된 시드는 항상 검증을 통과해야 한다. 아니면 자기가 만든 걸
    자기가 막는 셈이다."""
    stack = stack_with_limits(STACK)
    bounds = bounds_from_stack(stack)
    plan = seed_transfer(stack)
    errs = [v for v in validate(plan, bounds, terminals=DEFAULT.roles.keys())
            if v.severity == "error"]
    assert not errs, f"계산된 시드가 경계를 위반: {[str(e) for e in errs]}"


def test_step_is_finer_than_ss():
    """스텝이 SS 보다 성기면 SS 를 못 잰다. 이게 손으로 정하면 나던 실수다."""
    ss = 0.45
    assert _step(seed_transfer(_with_measured(vth_V=1.15, ss_V_per_dec=ss))) < ss


def test_seed_recovers_ss_of_the_device_it_was_built_for():
    """실측값으로 만든 시드는 그 소자의 SS 를 되찾아야 한다."""
    vth, ss = 1.15, 0.45
    stack = _with_measured(vth_V=vth, ss_V_per_dec=ss)
    plan = seed_transfer(stack)
    dev = SimulatedDevice(vth=vth, ss=ss, mobility=10.8, cox=33.6e-9,
                          wl=21.0 / 4.0, i_off=1e-13, ig_leak=5e-14, seed=0)
    m = summarize(simulate(plan, dev), plan, bounds=bounds_from_stack(stack))
    got = m.get("ss")
    assert got is not None, "SS 가 계산되지 않았다"
    assert abs(got - ss) / ss < 0.15, f"SS 회복 실패: {got} vs {ss}"


def _with_measured(**kw) -> dict:
    s = stack_with_limits(STACK)
    s["measured"] = {"source": "테스트", **kw}
    return s


def test_spec_values_are_structurally_unreadable():
    """설계자료 값은 코드가 읽을 수 없는 이름(_spec_reference)에 있어야 한다.

    'expected' 키로 남겨두면 언젠가 누군가 다시 읽는다. 이름을 바꿔두면
    실수로도 못 들어간다.
    """
    s = stack_with_limits(STACK)
    assert "expected" not in s, "자료값이 아직 읽을 수 있는 이름에 있다"
    assert s["_spec_reference"]["ss_V_per_dec"] == 0.270, "기록은 남아 있어야 한다"

    plan = seed_transfer(s)               # measured 없음 → blind 여야 한다
    assert "blind" in plan.note, plan.note
    # 자료값(Vth 1.55, SS 0.27)을 썼다면 시작점이 1.55-6×0.27-2 = -2.07 이었을 것.
    # blind 는 그와 무관한 고정 창에서 출발해야 한다.
    assert abs(plan.var1.start - (-2.07)) > 0.5, \
        f"자료 Vth·SS 로 계산된 값처럼 보인다: {plan.var1.start}"


def test_measured_ss_never_makes_the_step_coarser_than_before():
    """측정된 SS 는 참값의 상한이다. 그대로 되먹이면 스텝이 성겨지고 다음 SS 가
    더 부풀려지는 발산 루프가 된다. 직전 스텝보다 성겨지지 않아야 한다."""
    used = 0.05
    s = _with_measured(vth_V=1.15, ss_V_per_dec=1.0, step_V=used)
    assert _step(seed_transfer(s)) <= used + 1e-9, "직전 스텝보다 성겨졌다"


def test_blind_sweep_starts_narrow_not_at_the_wall():
    """실측이 없을 때 경계 전체를 처음부터 걸면 안 된다.

    무엇이 나올지 모르는 소자에 필요 이상의 전압을 거는 것이다. 좁은 창으로
    시작해 turn-on 이 안 보이면 넓혀 가는 것이 옳은 순서다.
    (두 번 틀렸던 곳 — 경계 전체를 훑게 만들어 소자당 36분이 걸렸다)
    """
    stack = stack_with_limits(STACK)
    plan = seed_transfer(stack)
    bounds = bounds_from_stack(stack)
    wall = bounds.limit_for("BG", "S")
    assert "blind" in plan.note

    span = plan.var1.stop - plan.var1.start
    assert span <= wall * 0.5, f"{span:g} V 는 경계({wall:g} V) 대비 너무 넓다"
    assert plan.var1.stop <= wall * 0.5, "처음부터 상한 근처까지 간다"
    # 창이 좁으니 점도 적어야 한다
    assert plan.n_points <= bounds.max_points * 0.2, plan.n_points


def test_blind_sweep_uses_a_faster_range_than_the_refined_one():
    """넓은 스윕에 가장 민감한 레인지를 쓰면 점당 시간이 크게 늘어난다.
    정밀 측정(좁은 범위)에서만 열어야 한다."""
    blind = seed_transfer(stack_with_limits(STACK))
    fine = seed_transfer(_with_measured(vth_V=1.15, ss_V_per_dec=0.45))
    assert blind.ranges["D"].range != fine.ranges["D"].range
    assert fine.ranges["D"].range == "10 pA", fine.ranges["D"].range


def test_enough_points_inside_the_subthreshold_transition():
    """SS 를 회귀로 뽑으려면 전이 구간에 점이 충분해야 한다.
    실측에서 5점 회귀가 나와 SS 를 못 믿겠다는 지적이 있었다."""
    ss = 0.45
    step = _step(seed_transfer(_with_measured(vth_V=1.15, ss_V_per_dec=ss)))
    # 6 decade 전이 폭 ≈ 6×SS. 그 안에 최소 20점은 들어와야 한다.
    assert (6 * ss) / step >= 20, f"전이 구간 점 수 {(6*ss)/step:.0f} 개로 부족"


def test_seed_follows_measured_ss():
    """실측 SS 를 절반으로 줄이면 스텝도 절반이어야 한다 — 실측이 시드를 끈다."""
    a = _step(seed_transfer(_with_measured(vth_V=1.15, ss_V_per_dec=0.40)))
    b = _step(seed_transfer(_with_measured(vth_V=1.15, ss_V_per_dec=0.20)))
    assert 0.4 < b / a < 0.6, f"스텝이 SS 를 따라가지 않는다 (비율 {b/a:.2f})"


def test_seed_respects_narrower_wall():
    """유전막을 얇게 만들면 경계가 낮아지고 시드도 따라 낮아져야 한다."""
    s1 = stack_with_limits(STACK)
    s2 = copy.deepcopy(s1)
    for layer in s2["gates"]["BG"]["dielectric"]:
        layer["t_nm"] /= 10.0                     # 경계가 대폭 낮아진다
    p1, p2 = seed_transfer(s1), seed_transfer(s2)
    assert p2.var1.stop < p1.var1.stop, "경계가 낮아졌는데 시드가 그대로다"
    errs = [v for v in validate(p2, bounds_from_stack(s2),
                                terminals=DEFAULT.roles.keys())
            if v.severity == "error"]
    assert not errs, f"좁은 경계에서 시드가 위반: {[str(e) for e in errs]}"


def test_blind_survey_without_expected():
    """expected 가 없으면 경계 전체를 훑되, 여전히 경계 안이어야 한다."""
    s = copy.deepcopy(stack_with_limits(STACK))
    s.pop("expected", None)
    plan = seed_transfer(s)
    assert "blind" in plan.note
    errs = [v for v in validate(plan, bounds_from_stack(s),
                                terminals=DEFAULT.roles.keys())
            if v.severity == "error"]
    assert not errs, f"blind survey 가 경계를 위반: {[str(e) for e in errs]}"


def test_points_stay_under_max():
    """SS 가 아주 작아도 max_points 를 넘지 않아야 한다 (누적 스트레스 상한)."""
    s = _with_measured(vth_V=1.15, ss_V_per_dec=0.001)   # 비현실적으로 가파른 소자
    plan = seed_transfer(s)
    assert plan.n_points <= bounds_from_stack(s).max_points


TESTS = [v for k, v in dict(globals()).items() if k.startswith("test_")]
