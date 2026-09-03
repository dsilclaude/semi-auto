"""intake — 에이전트가 낸 draft 가 실제로 경계·시드까지 이어지는가.

LLM 을 부르지 않는다. 에이전트가 낼 법한 draft 를 직접 넣어 뒷단을 검증한다.
(프롬프트가 좋은지는 여기서 못 본다. 그건 실제 자료로 돌려봐야 안다.)
"""

from __future__ import annotations

from ..intake import review, to_limits, to_stack
from ..safety import bounds_from_stack, validate
from ..seed import seed_transfer

# ε 를 안 준 자료를 가정한다 — 재료표가 메워야 한다.
DRAFT = {
    "name": "test_intake",
    "description": "가짜 draft",
    "gates": [{
        "name": "BG",
        "opposing": ["S", "D"],
        "dielectric": [
            {"material": "SiNx", "t_nm": 50.0, "eps_r": None, "e_max_MV_per_cm": None},
            {"material": "SiO2", "t_nm": 75.0, "eps_r": None, "e_max_MV_per_cm": None},
        ],
    }],
    "geometry": {"W_um": 6.0, "L_um": 4.0},
    "expected": {
        "vth_V": 1.55, "ss_V_per_dec": 0.27, "i_off_A": 1e-13, "i_on_A": 1e-6,
        "characterized_vg_range_V": [-20.0, 20.0], "characterized_vd_max_V": 10.0,
    },
    "provenance": [
        {"field": "gates[0].dielectric[0].t_nm", "basis": "document",
         "quote": "GI: SiNx 50nm", "confidence": 0.95},
        {"field": "gates[0].dielectric[0].eps_r", "basis": "material_knowledge",
         "quote": None, "confidence": 0.8},
        {"field": "expected.vth_V", "basis": "assumption",
         "quote": None, "confidence": 0.4},
    ],
    "missing": ["절연내압"], "questions": ["TG 도 측정하나?"], "notes": ["단위는 nm"],
}


def test_draft_reaches_bounds_without_eps_in_document():
    """자료가 ε 를 안 줘도 재료표가 메워 경계가 나와야 한다."""
    b = bounds_from_stack(to_stack(DRAFT))
    assert b.derived["BG"]["v_breakdown_V"] is not None
    assert b.limit_for("BG", "S") is not None


def test_characterized_range_becomes_a_narrowing_policy():
    """자료가 특성화 범위를 줬으면 limits 초안에 정책으로 실려야 한다."""
    lim = to_limits(DRAFT)
    assert lim.get("policy") == "characterized_envelope"
    assert lim.get("characterized_gate") == "BG"
    assert lim["limits"]["pair_v_max"]["D-S"] == 10.0


def test_seed_follows_from_the_draft():
    """draft → 시드까지 이어지고, 그 시드는 자기 경계를 통과해야 한다."""
    stack = to_stack(DRAFT)
    plan = seed_transfer(stack)
    errs = [v for v in validate(plan, bounds_from_stack(stack),
                                terminals=("BG", "D", "S"))
            if v.severity == "error"]
    assert not errs, [str(e) for e in errs]
    step = abs(plan.var1.stop - plan.var1.start) / (plan.var1.n_steps - 1)
    assert step < DRAFT["expected"]["ss_V_per_dec"], "스텝이 SS 보다 성기다"


def test_review_surfaces_ungrounded_values():
    """근거 없는 값(assumption)은 검토 화면 앞에 나와야 한다."""
    text = review(DRAFT)
    assert "확인 필요" in text
    assert "expected.vth_V" in text
    assert "절연내압" in text        # missing 도 같이


def test_intake_records_provenance_in_the_stack():
    """근거는 stack 에 남아야 한다 — 결과 폴더에서 되짚을 수 있게."""
    s = to_stack(DRAFT)
    assert s["_intake"]["provenance"]
    assert s["_intake"]["questions"] == ["TG 도 측정하나?"]


TESTS = [v for k, v in dict(globals()).items() if k.startswith("test_")]
