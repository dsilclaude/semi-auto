"""safety 검증 — 손으로 계산한 값과 대조한다.

여기 숫자들은 임의로 정한 게 아니라 SD 웨이퍼 층 테이블에서 나온 값이고,
ε_eff=4.74 는 SmartSPICE EPSI 와, C_BG=33.6 nF/cm² 는 W=6 소자의
µ≈10.8 cm²/Vs 와 각각 독립적으로 맞는다. 이 테스트가 깨지면 계산이 틀린 것이다.
"""

from __future__ import annotations

from ..plan import transfer, output
from ..safety import (MissingLimitError, bounds_from_stack, load_stack,
                      series_dielectric, validate)
from .fixtures import stack_with_limits


def approx(a, b, tol=0.02):
    return abs(a - b) <= tol * abs(b)


def test_bg_stack():
    stack = stack_with_limits()
    d = series_dielectric(stack["gates"]["BG"]["dielectric"], 3.0)
    assert approx(d["sum_t_over_eps_nm"], 26.374), d["sum_t_over_eps_nm"]
    assert approx(d["v_breakdown_V"], 30.86), d["v_breakdown_V"]
    assert d["limiting_layer"] == "SiO2"      # ε 작은 층에 필드가 몰린다
    assert approx(d["eps_eff"], 4.74), d["eps_eff"]
    assert approx(d["c_nF_per_cm2"], 33.6), d["c_nF_per_cm2"]


def test_tg_stack():
    stack = stack_with_limits()
    d = series_dielectric(stack["gates"]["TG"]["dielectric"], 3.0)
    assert approx(d["v_breakdown_V"], 60.0), d["v_breakdown_V"]
    assert approx(d["eps_eff"], 3.9)


def test_bounds_pairs():
    b = bounds_from_stack(stack_with_limits())
    # BG 는 파괴 계산값(30.86 → 내림 30), TG 는 운용 한계 55 가 이긴다
    assert b.limit_for("BG", "S") == 30.0
    assert b.limit_for("BG", "D") == 30.0
    assert b.limit_for("TG", "S") == 55.0
    assert b.limit_for("TG", "D") == 55.0
    # 게이트끼리는 두 유전막 직렬이라 각각보다 관대하다
    assert 85 <= b.limit_for("TG", "BG") <= 95, b.limit_for("TG", "BG")
    assert b.limit_for("D", "S") == 20.0
    assert b.wl_ratio == 1.5


def test_validate_catches_output_case():
    """output 에서 Vd=10, V_BG=-20 이면 |V_BG-V_D| 가 정확히 30 → 경계에 붙는다."""
    b = bounds_from_stack(stack_with_limits())
    ok = output("D", 0, 10, points=41, gate="BG", gate_start=-20, gate_stop=0,
                gate_steps=5)
    errs = [v for v in validate(ok, b, terminals=("TG", "BG", "D", "S"))
            if v.severity == "error"]
    assert not errs, errs

    over = output("D", 0, 11, points=41, gate="BG", gate_start=-20, gate_stop=0,
                  gate_steps=5)
    codes = [v.code for v in validate(over, b, terminals=("TG", "BG", "D", "S"))]
    assert "pair_voltage" in codes


def test_validate_window_and_points():
    b = bounds_from_stack(stack_with_limits())
    # TG 운용 창은 -5 ~ +20 V 비대칭 (TG 는 중앙만 덮으므로)
    bad = transfer("TG", -10, 10, points=51, vd=0.1, other_gate="BG")
    codes = [v.code for v in validate(bad, b, terminals=("TG", "BG", "D", "S"))]
    assert "window" in codes

    huge = transfer("BG", 0, 20, points=5000, vd=0.1)
    codes = [v.code for v in validate(huge, b, terminals=("BG", "D", "S"))]
    assert "max_points" in codes


def test_validate_compliance():
    b = bounds_from_stack(stack_with_limits())
    p = transfer("BG", 0, 20, points=51, vd=0.1, gate_compliance=1e-3)  # 상한 1 µA
    codes = [v.code for v in validate(p, b, terminals=("BG", "D", "S"))]
    assert "compliance" in codes


def test_materials_supply_e_max_without_per_wafer_input():
    """스택에 내압을 안 적어도 재료표에서 와야 한다 — 이게 보편성의 핵심이다.

    설계자료는 재료 이름과 두께만 준다. 내압은 웨이퍼가 아니라 재료에 딸린
    값이므로 웨이퍼마다 확인받지 않아도 된다.
    """
    raw = load_stack("sd_dualgate")
    assert raw["gates"]["BG"].get("e_max_MV_per_cm") is None, "스택엔 내압이 없다"
    b = bounds_from_stack(raw)
    assert b.derived["BG"]["v_breakdown_V"] is not None, "재료표에서 안 왔다"
    assert b.derived["BG"]["limiting_layer"] == "SiO2"


def test_unknown_material_stops_instead_of_guessing():
    """재료표에 없는 재료면 통상값을 지어내지 말고 멈춰야 한다."""
    import copy
    s = copy.deepcopy(load_stack("sd_dualgate"))
    s["gates"]["BG"]["dielectric"] = [
        {"material": "Unobtainium", "t_nm": 100.0}]     # eps_r 도 없다
    try:
        bounds_from_stack(s)
    except MissingLimitError as e:
        assert "Unobtainium" in str(e)
    else:
        raise AssertionError("모르는 재료인데 bounds 가 만들어졌다")


def test_per_layer_e_max_is_used():
    """층마다 내압이 다르면 그걸 반영해야 한다(SiNx 와 SiO2 는 같지 않다)."""
    layers = [{"material": "SiNx", "t_nm": 50.0, "eps_r": 7.0,
               "e_max_MV_per_cm": 3.0},
              {"material": "SiO2", "t_nm": 75.0, "eps_r": 3.9,
               "e_max_MV_per_cm": 1.0}]                  # SiO2 를 일부러 약하게
    d = series_dielectric(layers)
    assert d["limiting_layer"] == "SiO2"
    assert approx(d["v_breakdown_V"], 10.29), d["v_breakdown_V"]


def test_characterized_envelope_narrows_but_never_widens():
    """자료가 특성화 범위를 주면 물리 한계보다 좁히기만 한다."""
    b = bounds_from_stack(load_stack("sd_dualgate"))     # policy = 특성화 범위
    assert b.derived["BG"]["v_breakdown_V"] > 30.0        # 물리 한계는 그대로 계산되고
    assert b.limit_for("BG", "S") == 20.0                 # 적용은 자료 범위로 좁혀진다
    assert b.limit_for("D", "S") == 10.0


def test_tied_gates_take_the_lower_limit():
    """레이아웃에서 두 게이트가 한 패드로 묶이면 같은 전압이 두 막에 걸린다.
    안 묶어두면 더 약한 막이 검사를 통째로 비껴간다."""
    import copy
    s = copy.deepcopy(load_stack("sd_w21"))
    assert s.get("tied_gates"), "이 스택은 묶인 게이트를 선언해야 한다"
    b = bounds_from_stack(s)
    # BG(30.9 V) 가 TG(60 V) 보다 낮으니 BG 가 지배한다
    assert b.derived["BG"].get("tied_with") == ["TG"]
    assert b.limit_for("BG", "S") <= b.derived["TG"]["v_applied_V"]
    # 같은 노드끼리는 ΔV=0 이므로 pair 가 남아 있으면 안 된다
    assert b.limit_for("BG", "TG") is None

    # TG 막을 아주 약하게 만들면 그쪽이 지배해야 한다
    s2 = copy.deepcopy(s)
    s2["gates"]["TG"]["dielectric"] = [
        {"material": "SiO2", "t_nm": 10.0, "eps_r": 3.9}]
    s2["limits"].pop("policy", None)          # 특성화 범위 좁힘을 빼고 물리만 본다
    b2 = bounds_from_stack(s2)
    assert b2.limit_for("BG", "S") < b.derived["BG"]["v_breakdown_V"], \
        "약한 TG 막이 상한을 못 끌어내렸다"


def test_unbounded_terminal_is_refused():
    """경계가 없는 단자를 쓰는 plan 은 조용히 통과하면 안 된다."""
    import copy
    s = copy.deepcopy(load_stack("sd_dualgate"))
    s["gates"].pop("TG")                                  # TG 경계를 만들 근거를 없앤다
    s["gate_gate"] = None
    b = bounds_from_stack(s)
    p = transfer("TG", 0, 10, points=101, vd=0.1)
    codes = [v.code for v in validate(p, b, terminals=("TG", "BG", "D", "S"))]
    assert "unbounded_terminal" in codes


def test_limits_file_is_merged():
    """limits 를 채우면 stack 에 실제로 반영되는가."""
    s = stack_with_limits()
    assert s["gates"]["BG"]["e_max_MV_per_cm"] == 3.0
    assert s["limits"]["max_points"] == 4000


TESTS = [v for k, v in dict(globals()).items() if k.startswith("test_")]
