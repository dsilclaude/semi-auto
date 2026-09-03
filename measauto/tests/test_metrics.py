"""metrics 검증 — 답을 아는 가짜 소자로 지표가 되돌아오는지 본다.

metrics 는 순수 함수라 장비 없이 검증된다. 그리고 나머지 모듈이 전부 여기에
의존하므로, 여기가 맞는지가 제일 중요하다.
"""

from __future__ import annotations

import numpy as np

from ..metrics import downsample_curve, summarize, to_llm_payload, transfer_metrics
from ..plan import transfer
from ..replay import SimulatedDevice, simulate


def approx(a, b, tol):
    return a is not None and abs(a - b) <= tol


def test_transfer_recovers_vth_and_ss():
    dev = SimulatedDevice(vth=5.0, ss=0.30, wl=1.5, i_off=2e-13, noise=0.0)
    plan = transfer("BG", -2.0, 15.0, points=171, vd=0.1, direction="single")
    df = simulate(plan, dev)
    m = summarize(df, plan)

    assert m["polarity"] == "n", m["polarity"]
    assert approx(m["vth_lin"], 5.0, 1.0), m["vth_lin"]
    assert approx(m["ss"], 0.30, 0.08), m["ss"]
    assert m["decades"] > 4, m["decades"]
    assert m["turn_on_inside_window"] is True
    assert m["is_flat"] is False


def test_flat_curve_when_vth_outside_window():
    """Vth 가 범위 밖이면 평평한 선이 나온다. 지표는 그 사실만 말하고 판정하지 않는다."""
    dev = SimulatedDevice(vth=20.0, noise=0.0)
    plan = transfer("BG", 0.0, 5.0, points=51, vd=0.1, direction="single")
    m = summarize(simulate(plan, dev), plan)
    assert m["is_flat"] is True
    assert m["turn_on_inside_window"] is False
    # 죽은 소자와 구분되는 단서는 지표 하나가 아니라 바닥 기울기다
    assert m["floor_slope"] is not None


def test_dead_device_is_not_confidently_distinguishable():
    dead = summarize(simulate(transfer("BG", 0, 5, points=51, vd=0.1,
                                       direction="single"),
                              SimulatedDevice(dead=True, noise=0.0)),
                     transfer("BG", 0, 5, points=51, vd=0.1, direction="single"))
    assert dead["is_flat"] is True


def test_negative_current_does_not_break_log():
    """산화물 TFT off 영역에서 Id 가 음수로 찍히는 건 흔하다. log10 이 깨지면 안 된다."""
    vg = np.linspace(-2, 2, 41)
    idd = np.random.default_rng(0).normal(0, 1e-13, vg.size)   # 절반쯤 음수
    m = transfer_metrics(vg, idd, np.full_like(vg, 1e-14))
    assert m["negative_id_fraction"] > 0.2
    assert m["decades"] is not None and np.isfinite(m["decades"])


def test_hysteresis_from_double_sweep():
    dev = SimulatedDevice(vth=5.0, noise=0.0)
    plan = transfer("BG", 0, 12, points=61, vd=0.1, direction="double")
    m = summarize(simulate(plan, dev), plan)
    assert m["n_branches"] == 1
    # 시뮬레이터에 드리프트가 없으니 히스테리시스는 0 근처여야 한다
    assert m["hysteresis_V"] is None or abs(m["hysteresis_V"]) < 0.5


def test_gm_peak_at_window_edge_marks_mobility_as_a_lower_bound():
    """gm 이 꺾이는 걸 못 보면 argmax 는 마지막 점을 준다 — 최고점이 아니다.

    실측에서 이걸 구분하지 않아 이동도가 계속 작게 나왔다: 같은 W=9 이
    −1~5 V 에서 10.3, −5~10 V 에서 15.0, 창 안에서 꺾였을 때 16.5 근처.
    창을 넓힌 만큼 커졌다는 건 그게 값이 아니라 하한이었다는 뜻이다.
    """
    # gm 이 위로 갈수록 오르기만 하는 곡선. 꼭대기는 창 밖에 있다.
    vg = np.linspace(0.0, 6.0, 121)
    idd = 1e-9 + 1e-7 * np.clip(vg - 1.0, 0, None) ** 2      # gm ∝ (Vg−Vth)
    m = transfer_metrics(vg, idd, np.full_like(vg, 1e-14), wl=6.0)
    assert m["gm_peak_at_edge"] is True, m["gm_peak_margin_V"]

    # 같은 소자를 꼭대기 너머까지 재면 창 안에서 꺾인다.
    vg2 = np.linspace(0.0, 20.0, 401)
    rise = np.clip(vg2 - 1.0, 0, None) ** 2
    idd2 = 1e-9 + 1e-7 * rise / (1.0 + (np.clip(vg2 - 8.0, 0, None) / 3.0) ** 2)
    m2 = transfer_metrics(vg2, idd2, np.full_like(vg2, 1e-14), wl=6.0)
    assert m2["gm_peak_at_edge"] is False, m2["gm_peak_margin_V"]
    assert m2["gm_peak_vg"] < 20.0


def test_mobility_lower_bound_flag_follows_gm_peak():
    """이동도가 없으면 하한 표시도 없어야 한다 (cox 를 모르는 흔한 경우)."""
    vg = np.linspace(0.0, 6.0, 121)
    idd = 1e-9 + 1e-7 * np.clip(vg - 1.0, 0, None) ** 2
    no_cox = transfer_metrics(vg, idd, None, wl=6.0)
    assert no_cox["mobility_cm2_Vs"] is None
    assert no_cox["mobility_is_lower_bound"] is False

    with_cox = transfer_metrics(vg, idd, None, wl=6.0, cox=3.4e-8, vd=0.1)
    assert with_cox["mobility_cm2_Vs"] is not None
    assert with_cox["mobility_is_lower_bound"] is True


def test_llm_payload_is_small():
    dev = SimulatedDevice(vth=5.0)
    plan = transfer("BG", -2, 15, points=201, vd=0.1, direction="double")
    df = simulate(plan, dev)
    payload = to_llm_payload(summarize(df, plan), downsample_curve(df, plan, 25))
    assert len(payload["curve"]["x"]) <= 25
    assert "log_ig" in payload["curve"]          # Ig 는 필수
    assert all(isinstance(v, (int, float)) for v in payload["curve"]["log_id"])
    import json
    assert len(json.dumps(payload)) < 4000, "페이로드가 너무 크다"


def test_output_metrics():
    from ..plan import output
    dev = SimulatedDevice(vth=3.0, noise=0.0)
    plan = output("D", 0, 10, points=41, gate="BG", gate_start=5, gate_stop=15,
                  gate_steps=3, direction="single")
    m = summarize(simulate(plan, dev), plan)
    assert m["n_branches"] == 3
    assert m["saturation_ratio"] is not None
    assert m["r_on_ohm"] is not None


TESTS = [v for k, v in dict(globals()).items() if k.startswith("test_")]
