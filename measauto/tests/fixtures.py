"""테스트용 안전 경계 값.

stacks/*.limits.json 은 비어 있다 — 그 값들은 설계자료에 없어서 공정 담당이
채우기 전까지 비워 두는 것이 이 프로젝트의 규칙이다(sd_dualgate.limits.json 참고).

테스트가 검증하는 것은 **계산이 맞는가**이지 그 값이 옳은가가 아니다.
그래서 여기서 값을 명시적으로 넣고 "만약 e_max = 3.0 이라면 V_bd = 30.86"
을 확인한다. 이 숫자들은 테스트 픽스처일 뿐 권장값이 아니다.
"""

from __future__ import annotations

from ..safety import load_stack, merge_limits

TEST_LIMITS = {
    "gates": {
        "BG": {"e_max_MV_per_cm": 3.0, "v_user_max": None},
        "TG": {"e_max_MV_per_cm": 3.0, "v_user_max": 55.0},
    },
    "limits": {
        "pair_v_max": {"D-S": 20.0},
        "i_compliance_A": {"TG": 1.0e-6, "BG": 1.0e-6, "D": 1.0e-3, "S": 1.0e-1},
        "ig_warn_A": 1.0e-10,
        "ig_abort_A": 1.0e-9,
        "max_points": 4000,
        "tg_window_V": [-5.0, 20.0],
    },
    "provenance": {"source": "tests/fixtures.py — 테스트 픽스처", "approved_by": None},
}


def stack_with_limits(name: str = "sd_dualgate") -> dict:
    """stack + 테스트용 경계값 (순수 절연파괴 기준).

    실제 limits.json 의 characterized_envelope 정책은 떼어낸다 — 그 정책은
    별도 테스트에서 확인하고, 여기서는 물리 계산만 검증한다.
    """
    s = merge_limits(load_stack(name), TEST_LIMITS)
    for k in ("policy", "characterized_gate"):
        s.get("limits", {}).pop(k, None)
    return s
