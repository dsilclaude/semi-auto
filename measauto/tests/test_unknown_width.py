"""W(채널 폭)를 모르는 소자에서도 스윕 범위를 찾을 수 있는가.

지금까지의 stack 은 폭마다 하나씩(sd_w9 … sd_w24) 사람이 만들어 두었다.
그래서 W 를 안다는 전제가 코드 곳곳에 조용히 깔려 있었다:

  · 정전류 Vth 의 기준 전류가 W/L 에 비례한다 → W 를 모르면 1 로 가정하고
    계산되어 값이 어긋난다. 그런데 **범위 탐색 전체가 그 값 위에 얹혀 있었다.**
  · 첫 창이 (-1, 5) V 로 못박혀 있었다 → 이 웨이퍼의 n형 소자가 +1.5 V 에서
    켜진다는 것을 이미 안다는 뜻이다. 모르는 소자에는 쓸 수 없는 전제다.

여기서 검증하는 것은 **W 를 몰라도 창을 제대로 찾는가**이다.
"""

from __future__ import annotations

import copy

import numpy as np

from ..metrics import ONSET_DECADES, transfer_metrics
from ..safety import bounds_from_stack, validate
from ..config import DEFAULT
from ..seed import BLIND_HALF_MAX_V, blind_window, seed_transfer
from .fixtures import stack_with_limits

STACK = "sd_dualgate"


def _curve(vth: float, *, w_scale: float = 1.0, lo=-5.0, hi=10.0, n=301):
    """n형 transfer 곡선. w_scale 은 폭에 비례하는 전류 배율."""
    vg = np.linspace(lo, hi, n)
    sub = 10.0 ** ((vg - vth) / 0.3)              # 문턱하 (SS = 0.3 V/dec)
    idd = w_scale * (1e-13 + 1e-9 * np.minimum(sub, 1.0 + np.clip(vg - vth, 0, None)))
    return vg, idd


# ---------------------------------------------------------------------------
# turn-on 기준이 W 에 의존하지 않는가
# ---------------------------------------------------------------------------
def test_turn_on_is_the_same_whether_or_not_width_is_known():
    """같은 곡선인데 W 를 알고 모르고에 따라 turn-on 이 달라지면 안 된다.

    vth_cc 는 W/L 배만큼 어긋난다(그래서 vth_cc_wl_known 으로 표시한다).
    범위 탐색이 쓰는 turn_on_V 는 그 영향을 받지 않아야 한다.
    """
    vg, idd = _curve(2.0)
    known = transfer_metrics(vg, idd, wl=6.0)
    unknown = transfer_metrics(vg, idd, wl=None)

    assert known["vth_cc_wl_known"] is True
    assert unknown["vth_cc_wl_known"] is False
    # 기준 전류가 6배 다르니 vth_cc 자체는 어긋난다 — 그게 표시하는 이유다
    assert known["vth_cc"] != unknown["vth_cc"]
    # W 무관 기준은 같아야 한다
    assert abs(known["vth_onset_V"] - unknown["vth_onset_V"]) < 1e-6
    # 그리고 실제 Vth 근처를 가리켜야 한다
    assert abs(unknown["turn_on_V"] - 2.0) < 1.0, unknown["turn_on_V"]
    assert "W 무관" in unknown["turn_on_basis"]


def test_turn_on_is_the_same_across_widths():
    """폭이 4배 다른 두 소자(전류가 4배)에서 turn-on 이 같게 나와야 한다."""
    narrow = transfer_metrics(*_curve(2.0, w_scale=1.0), wl=None)
    wide = transfer_metrics(*_curve(2.0, w_scale=4.0), wl=None)
    assert abs(narrow["turn_on_V"] - wide["turn_on_V"]) < 0.05, (
        narrow["turn_on_V"], wide["turn_on_V"])


def test_onset_reference_rides_on_the_measured_floor():
    """기준이 절대 전류가 아니라 '바닥에서 몇 decade 위'여야 W 와 무관해진다."""
    vg, idd = _curve(2.0, w_scale=7.0)
    m = transfer_metrics(vg, idd, wl=None)
    assert abs(m["vth_onset_ref_A"] / m["id_off"] - 10.0 ** ONSET_DECADES) < 1e-6


# ---------------------------------------------------------------------------
# turn-on 을 못 찾았을 때 어느 쪽으로 넓힐지
# ---------------------------------------------------------------------------
def test_subthreshold_tail_points_upward():
    """turn-on 이 창 바로 위면 문턱하 꼬리가 창 끝에 걸린다. 그 가파른
    기울기가 '위쪽으로 넓혀라'의 근거다."""
    m = transfer_metrics(*_curve(5.5, lo=-5.0, hi=5.0), wl=None,
                         i_range_floor=1e-11)
    # 꼬리 끝이 창에 걸렸을 뿐 전이를 다 본 게 아니다 — 그걸 구분해야 한다
    assert m["transition_captured"] is False
    # 창 전체 평균으로는 안 보이고 끝에서만 보인다 — 그게 이 지표가 있는 이유
    assert m["id_edge_trend_dec_per_V"] > 10 * abs(m["id_trend_dec_per_V"])
    assert m["turn_on_side"] == "above", m["id_edge_trend_dec_per_V"]


def test_already_conducting_points_downward_not_upward():
    """**부호만 보면 정확히 반대로 넓힌다.**

    창 전체가 문턱 위면 Id ∝ (Vg−Vth) 라 전류가 Vg 와 함께 오르지만,
    turn-on 은 창 **아래**에 있다. 문턱하 꼬리(가파름)와 문턱 위(완만함)를
    기울기 크기로 갈라야 방향이 맞는다.
    """
    m = transfer_metrics(*_curve(-25.0, lo=-5.0, hi=5.0), wl=None,
                         i_range_floor=1e-11)
    assert m["id_trend_dec_per_V"] > 0          # 전류는 오르는데
    assert m["id_state"] == "conducting"
    assert m["turn_on_side"] == "below", m["id_trend_dec_per_V"]   # 방향은 아래


def test_far_off_floor_admits_it_knows_nothing():
    """turn-on 이 한참 멀면 문턱하 꼬리가 측정 바닥 아래라 곡선은 진짜로
    평평하다. 그때 방향을 지어내면 엉뚱한 쪽으로 넓히게 된다 — 모른다고
    말해야 0 V 중심 대칭 확장으로 떨어진다."""
    m = transfer_metrics(*_curve(25.0, lo=-5.0, hi=5.0), wl=None,
                         i_range_floor=1e-11)
    assert m["id_state"] == "off_floor"
    assert m["turn_on_side"] is None, m["id_trend_dec_per_V"]


def test_noise_does_not_invent_a_direction():
    """진짜 평평한 바닥에서 방향을 지어내면 엉뚱한 쪽으로 넓히게 된다."""
    vg = np.linspace(-5, 5, 201)
    idd = np.random.default_rng(0).normal(1e-13, 2e-15, vg.size)
    m = transfer_metrics(vg, idd, wl=None, i_range_floor=1e-11)
    assert m["turn_on_side"] is None, m["id_trend_dec_per_V"]


def test_turn_on_inside_window_needs_no_width():
    vg, idd = _curve(2.0, lo=-5.0, hi=10.0)
    assert transfer_metrics(vg, idd, wl=None)["turn_on_inside_window"] is True


# ---------------------------------------------------------------------------
# 첫 창이 극성을 전제하지 않는가
# ---------------------------------------------------------------------------
def test_blind_window_is_centred_on_zero():
    """(-1, 5) 같은 치우친 창은 'n형이고 양의 Vth' 를 이미 안다는 뜻이다."""
    lo, hi = blind_window(-20.0, 20.0)
    assert abs(lo + hi) < 1e-9, (lo, hi)      # 0 중심
    assert hi <= BLIND_HALF_MAX_V


def test_blind_window_follows_the_envelope():
    """유전막이 바뀌어 경계가 달라지면 첫 창도 따라와야 한다.
    절대값으로 박아두면 두꺼운 막에서는 좁고 얇은 막에서는 위험해진다."""
    tight = blind_window(-8.0, 8.0)
    wide = blind_window(-60.0, 60.0)
    assert (wide[1] - wide[0]) > (tight[1] - tight[0])
    # 그래도 상한은 지킨다 — 모르는 소자에 큰 전압을 걸지 않는다
    assert wide[1] <= BLIND_HALF_MAX_V


def test_blind_window_stays_inside_a_one_sided_envelope():
    """경계가 0 을 품지 않는 배선에서도 창이 경계 안에 있어야 한다."""
    lo, hi = blind_window(2.0, 30.0)
    assert 2.0 <= lo < hi <= 30.0


# ---------------------------------------------------------------------------
# geometry 가 없는 stack 이 끝까지 도는가
# ---------------------------------------------------------------------------
def _without_geometry() -> dict:
    s = copy.deepcopy(stack_with_limits(STACK))
    s.pop("geometry", None)
    return s


def test_seed_works_without_geometry():
    """W 를 모른다고 시드 계산이 멈추면 안 된다 — 전압 창은 유전막이 정하지
    폭이 정하지 않는다."""
    stack = _without_geometry()
    bounds = bounds_from_stack(stack)
    assert bounds.wl_ratio is None
    plan = seed_transfer(stack)
    errs = [v for v in validate(plan, bounds, terminals=DEFAULT.roles.keys())
            if v.severity == "error"]
    assert not errs, [str(e) for e in errs]
    assert plan.var1.start < 0 < plan.var1.stop, plan.describe()


def test_seed_window_does_not_depend_on_width():
    """폭만 다른 두 stack 이 같은 전압 창을 내야 한다. 전압은 유전막 문제다."""
    a, b = _without_geometry(), _without_geometry()
    a["geometry"] = {"W_um": 9.0, "L_um": 4.0}
    b["geometry"] = {"W_um": 24.0, "L_um": 4.0}
    pa, pb = seed_transfer(a), seed_transfer(b)
    assert (pa.var1.start, pa.var1.stop) == (pb.var1.start, pb.var1.stop)


TESTS = [
    test_turn_on_is_the_same_whether_or_not_width_is_known,
    test_turn_on_is_the_same_across_widths,
    test_onset_reference_rides_on_the_measured_floor,
    test_subthreshold_tail_points_upward,
    test_already_conducting_points_downward_not_upward,
    test_far_off_floor_admits_it_knows_nothing,
    test_noise_does_not_invent_a_direction,
    test_turn_on_inside_window_needs_no_width,
    test_blind_window_is_centred_on_zero,
    test_blind_window_follows_the_envelope,
    test_blind_window_stays_inside_a_one_sided_envelope,
    test_seed_works_without_geometry,
    test_seed_window_does_not_depend_on_width,
]
