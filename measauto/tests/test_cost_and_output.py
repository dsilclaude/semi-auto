"""test_cost_and_output.py — 돈과 전압, 조용히 새면 아무도 못 알아채는 둘.

여기 있는 검사는 기능이 아니라 **경계**다. 깨지면 기능이 안 되는 게 아니라,
누군가 몇 주 뒤에 청구서나 죽은 소자로 알게 된다.

  · SMU 출력   측정이 끝났는데 전압이 남아 있으면 팁이 닿은 소자에 며칠씩
               DC 바이어스가 걸린다. 실제로 그랬다.
  · LLM 호출   탐색 소자 수가 상한이 아니면 소자 수만큼 호출이 늘어난다.
               16 소자에서 4회 → 32회로 늘었던 적이 있다.
  · 페이로드   원본 CSV 를 실으면 요청 하나가 30k 토큰이 된다(실측 105,479 자).
               지표 + 다운샘플이면 900자다.
"""

from __future__ import annotations

import json
import tempfile

import numpy as np
import pandas as pd

from ..agent.policy import HeuristicPolicy, Proposal
from ..config import HardwareConfig
from ..drivers.b1500 import B1500
from ..executor import Executor, Site
from ..metrics import summarize, to_llm_payload
from ..plan import transfer
from ..replay import FakeExecutor, SimulatedDevice
from ..safety import bounds_from_stack
from ..session import Session, SessionConfig
from ..store import Store
from .fixtures import stack_with_limits


# ---------------------------------------------------------------------------
# 장비: 소자에 전압이 남지 않는다
# ---------------------------------------------------------------------------
class _Adapter:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _Inst:
    def __init__(self):
        self.writes = []
        self.adapter = _Adapter()

    def write(self, cmd):
        self.writes.append(cmd)


def test_close_turns_outputs_off_before_closing_the_session():
    """세션만 닫으면 SMU 는 마지막 값을 계속 인가한다.

    VISA Device Clear 는 측정을 중단시킬 뿐 출력을 안 내린다. 팁은
    executor.home() 이 소자에 다시 접촉시킨 채로 끝나므로, 출력이 남으면
    그대로 DC 스트레스가 된다.
    """
    inst = _Inst()
    b = B1500("GPIB0::17::INSTR")
    b.inst = inst
    b.close()

    assert inst.writes == ["CL"], f"CL 을 안 보냈다: {inst.writes}"
    assert inst.adapter.closed, "세션이 안 닫혔다"
    assert b.inst is None


def test_output_off_failure_does_not_stop_the_teardown():
    """CL 이 실패해도 뒷정리는 계속돼야 한다.

    여기서 예외가 올라가면 s300 정리까지 통째로 멈추고, 프로버가 원격 모드에
    잠긴 채 남는다. 실패는 알리되 막지는 않는다.
    """
    class _Broken(_Inst):
        def write(self, cmd):
            raise RuntimeError("GPIB 끊김")

    b = B1500("GPIB0::17::INSTR")
    b.inst = _Broken()
    b.close()                       # 예외가 새어 나오면 실패
    assert b.inst is None


def test_home_drops_the_voltage_before_it_moves():
    """원점 복귀는 출력을 내린 다음이다.

    home() 은 마지막에 소자에 **다시 접촉**시킨다. 그 전에 출력을 안 내리면
    스윕 post 값이 그대로 인가된 채 실행이 끝난다.
    """
    calls = []

    class _S300:
        def read_position(self):
            calls.append("read")
            return (0, 0, 100)

        def separate(self):
            calls.append("separate")

        def move_xy(self, x, y):
            calls.append("move")

        def contact(self):
            calls.append("contact")

        def contact_slow(self, z, **kw):
            calls.append("contact")

    class _B1500:
        def __init__(self):
            self.off = 0

        def outputs_off(self):
            calls.append("outputs_off")
            self.off += 1

    b = _B1500()
    ex = Executor(s300=_S300(), b1500=b)
    ex._z_contact = 100
    ex.home()

    assert b.off == 1, "출력을 안 내렸다"
    assert calls.index("outputs_off") < calls.index("separate"), calls


# ---------------------------------------------------------------------------
# 페이로드: 원본 CSV 는 LLM 으로 가지 않는다
# ---------------------------------------------------------------------------
def test_raw_csv_never_reaches_the_payload():
    """원본을 싣는 경로가 아예 없어야 한다.

    실측: 원본 105,479 자(≈30k 토큰) vs 다운샘플 903 자(≈258 토큰). 이력이
    턴마다 쌓이므로 5턴이면 요청 하나가 150k 토큰이 됐다. 그 400배로 얻는
    것이 없었다 — 지표는 어차피 코드가 계산하고, LLM 은 긴 배열을 눈대중으로
    읽는다. 원본은 store 가 결과 폴더에 data.csv 로 저장한다.
    """
    import measauto.metrics as metrics

    assert not hasattr(metrics, "csv_payload"), \
        "csv_payload 가 되살아났다 — 원본 CSV 를 보내는 경로다"
    assert "csv" not in to_llm_payload.__code__.co_varnames, \
        "to_llm_payload 에 csv 인자가 되살아났다"
    assert not hasattr(SessionConfig(), "send_full_csv"), \
        "send_full_csv 가 되살아났다"

    stack = stack_with_limits()
    bounds = bounds_from_stack(stack)
    plan = transfer("BG", -2, 6, points=201, vd=0.1, direction="double")
    vg = np.linspace(-2, 6, 201)
    df = pd.DataFrame({"V_BG": vg,
                       "I_D": 1e-13 + 1e-6 / (1 + np.exp(-(vg - 1.0) / 0.12)),
                       "I_BG": 1e-13, "V_D": 0.1, "V_S": 0.0})

    with tempfile.TemporaryDirectory() as tmp:
        sess = Session(FakeExecutor(SimulatedDevice()), bounds,
                       Store(tmp, make_png=False))
        payload = sess._payload(summarize(df, plan, bounds=bounds), df, plan)

    assert "data_csv" not in payload, "원본 CSV 가 페이로드에 실렸다"
    assert "curve" in payload and "metrics" in payload
    size = len(json.dumps(payload, ensure_ascii=False))
    assert size < 4000, f"페이로드가 {size}자 — 다운샘플이 아니다"


# ---------------------------------------------------------------------------
# 루프: 탐색 소자 수는 상한이다
# ---------------------------------------------------------------------------
class _CountingPolicy(HeuristicPolicy):
    """호출을 세고, 수렴 여부를 시험이 정한다."""

    def __init__(self, *, converge: bool):
        super().__init__()
        self.converge = converge
        self.calls = 0

    def propose_seed(self, ctx):
        self.calls += 1
        return super().propose_seed(ctx)

    def propose(self, ctx, history):
        self.calls += 1
        if self.converge:
            return Proposal("converged", "충분히 봤다", 0.9, source="test")
        return Proposal("propose", "더 봐야 한다", 0.5,
                        plan=ctx["seed_plan"], source="test")


def _run(*, converge: bool, calib: int, n_sites: int = 8):
    stack = stack_with_limits()
    cfg = HardwareConfig(roles={"BG": 1, "D": 2, "S": 3})
    ex = FakeExecutor(SimulatedDevice(vth=2.0, noise=0.0), config=cfg)
    seed = transfer("BG", -1, 5, points=61, vd=0.1, direction="single")
    pol = _CountingPolicy(converge=converge)

    with tempfile.TemporaryDirectory() as tmp:
        sess = Session(ex, bounds_from_stack(stack), Store(tmp, make_png=False),
                       pol, SessionConfig(max_iters=1, calibration_sites=calib),
                       stack=stack)
        res = sess.run_area([Site(str(i + 1), 0, -300 * i, area="A")
                             for i in range(n_sites)], seed)
    return res, pol.calls


def test_calibration_budget_is_a_ceiling_not_a_floor():
    """탐색 소자 수 안에 조건을 못 잡으면 **멈춘다.**

    예전 조건은 `i < calibration_sites or locked is None` 이었다. 뒤쪽 절이
    있어서 확정 plan 을 못 얻으면 남은 소자가 전부 계속 에이전트를 불렀다 —
    8 소자에서 4회로 끝날 것이 16회가 됐고, 16 소자면 32회였다.

    비용만의 문제가 아니다. 조건이 안 정해진 채로 남은 소자를 계속 찍으면
    서로 비교할 수 없는 곡선만 쌓이고, 소자는 이미 스트레스를 받는다.
    안 재는 편이 낫다.
    """
    res, calls = _run(converge=False, calib=2, n_sites=8)

    assert len(res) == 2, f"{len(res)}개를 측정했다 — 탐색 상한을 넘겼다"
    assert calls == 4, f"에이전트를 {calls}번 불렀다 (기대: 2소자 × 2회)"


def test_locked_plan_covers_the_rest_without_calling_the_agent():
    """수렴하면 나머지 소자는 확정 plan 으로 돌고 호출이 없다."""
    res, calls = _run(converge=True, calib=2, n_sites=8)

    assert len(res) == 8, f"{len(res)}개만 측정했다"
    assert calls == 4, f"에이전트를 {calls}번 불렀다 (기대: 탐색 2소자 × 2회)"
    assert all(r.iterations == 1 for r in res)
    # 확정된 조건이 나머지에 그대로 적용됐는가
    windows = {(r.final_plan.var1.start, r.final_plan.var1.stop)
               for r in res if r.final_plan}
    assert len(windows) == 1, f"조건이 갈렸다: {windows}"


def test_zero_calibration_sites_never_calls_the_agent():
    """탐색 0개 = 시드를 그대로 쓴다. 멈추는 것이 아니다."""
    res, calls = _run(converge=False, calib=0, n_sites=4)

    assert len(res) == 4, f"{len(res)}개만 측정했다"
    assert calls == 0, f"에이전트를 {calls}번 불렀다 — 탐색 0개인데"


TESTS = [
    test_close_turns_outputs_off_before_closing_the_session,
    test_output_off_failure_does_not_stop_the_teardown,
    test_home_drops_the_voltage_before_it_moves,
    test_raw_csv_never_reaches_the_payload,
    test_calibration_budget_is_a_ceiling_not_a_floor,
    test_locked_plan_covers_the_rest_without_calling_the_agent,
    test_zero_calibration_sites_never_calls_the_agent,
]
