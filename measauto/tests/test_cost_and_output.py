"""test_cost_and_output.py — 돈과 전압, 조용히 새면 아무도 못 알아채는 둘.

여기 있는 검사는 기능이 아니라 **경계**다. 깨지면 기능이 안 되는 게 아니라,
누군가 몇 주 뒤에 청구서나 죽은 소자로 알게 된다.

  · SMU 출력   측정이 끝났는데 전압이 남아 있으면 팁이 닿은 소자에 며칠씩
               DC 바이어스가 걸린다. 실제로 그랬다.
  · 버스 조회  물린 버스에 VISA 호출을 던지면 드라이버 안에서 막힌다.
               Ctrl+C 도 안 먹고 프로세스를 죽여도 안 풀린다(실측).
  · LLM 호출   탐색 소자 수가 상한이 아니면 소자 수만큼 호출이 늘어난다.
               16 소자에서 4회 → 32회로 늘었던 적이 있다.
  · 페이로드   원본 CSV 를 실으면 요청 하나가 30k 토큰이 된다(실측 105,479 자).
               지표 + 다운샘플이면 900자다.
"""

from __future__ import annotations

import json
import tempfile
import time

import numpy as np
import pandas as pd

from ..agent.policy import HeuristicPolicy, Proposal
from ..config import HardwareConfig
from ..drivers.b1500 import B1500
from ..executor import BusTimeout, Executor, Site, list_visa_resources
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
class _Connection:
    def __init__(self):
        self.ren = []

    def control_ren(self, mode):
        self.ren.append(mode)


class _Adapter:
    def __init__(self):
        self.closed = False
        self.connection = _Connection()

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


def test_close_returns_the_mainframe_to_local():
    """세션을 닫을 때 GTL 을 보내는가. 그리고 **CL 이 먼저인가.**

    ⚠️ 이 검사는 '보내는지'만 본다. 실장비에서 **효과가 없다는 것**이
    2026-09-10 에 확인됐다 — GTL 도, REN 을 내려도 FlexGUI 의 RMT 가 안
    꺼진다. 사람이 본체에서 Tools > Go to Local & Close 를 눌러야 한다
    (drivers/b1500.set_local 의 기록). 표준 버스 예의라 남겨둔 것뿐이니,
    이 검사가 통과한다고 장비가 로컬로 돌아갔다는 뜻은 아니다.

    순서는 진짜로 중요하다 — 출력을 내린(CL) 뒤에 보내야 한다.
    """
    inst = _Inst()
    b = B1500("GPIB0::17::INSTR")
    b.inst = inst
    b.close()

    assert inst.adapter.connection.ren == [6], \
        f"GTL(VI_GPIB_REN_ADDRESS_GTL=6)을 안 보냈다: {inst.adapter.connection.ren}"
    assert inst.writes == ["CL"], "CL 이 GTL 보다 먼저여야 한다"


def test_local_failure_does_not_stop_the_teardown():
    """어댑터가 REN 제어를 안 받아도 세션은 닫혀야 한다."""
    inst = _Inst()

    def _boom(mode):
        raise RuntimeError("이 어댑터는 REN 제어를 지원하지 않는다")

    inst.adapter.connection.control_ren = _boom
    b = B1500("GPIB0::17::INSTR")
    b.inst = inst
    b.close()
    assert inst.adapter.closed, "GTL 이 실패했다고 세션까지 안 닫혔다"
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
# 버스: 물려 있어도 매달리지 않는다
# ---------------------------------------------------------------------------
class _FakePyvisa:
    """pyvisa 를 가로채는 문맥관리자. list_resources 의 행동을 바꿔 끼운다."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self._saved = None

    def __enter__(self):
        import sys
        import types
        self._saved = sys.modules.get("pyvisa")
        mod = types.ModuleType("pyvisa")
        beh = self.behaviour

        class _RM:
            def __init__(self, *a, **k):
                if beh == "boom":
                    raise OSError("nivisa64.dll 없음")

            def list_resources(self):
                if beh == "hang":
                    time.sleep(3600)
                if beh == "empty":
                    return ()
                return ("GPIB0::17::INSTR", "GPIB0::28::INSTR")

        mod.ResourceManager = _RM
        sys.modules["pyvisa"] = mod
        return self

    def __exit__(self, *exc):
        import sys
        if self._saved is None:
            sys.modules.pop("pyvisa", None)
        else:
            sys.modules["pyvisa"] = self._saved
        return False


def test_a_wedged_bus_gives_up_instead_of_hanging():
    """물린 버스에서는 **매달리기 전에 포기**해야 한다.

    실측 2026-09-10: 진단 스크립트가 list_resources() 에서 막혔고, Ctrl+C 도
    콘솔 닫기도 안 먹었으며, 강제 종료해도 커널 호출에서 못 빠져나와 좀비로
    남았다. 어댑터 USB 를 재연결해야 풀렸다. 한 번 매달리면 손쓸 방법이
    없으므로 시간 제한이 유일한 방어다.
    """
    with _FakePyvisa("hang"):
        t0 = time.time()
        try:
            list_visa_resources("", timeout_s=0.5)
        except BusTimeout as e:
            assert "USB 를 뽑았다" in str(e), "무엇을 하면 되는지 안 알려준다"
        else:
            raise AssertionError("매달렸어야 하는데 값이 돌아왔다")
        dt = time.time() - t0
        assert dt < 3.0, f"{dt:.1f}s 나 붙들고 있었다"


def test_measurement_path_also_gives_up():
    """UI/CLI 가 타는 Executor.connect 도 같은 방어를 받는다.

    여기가 안 막히면 측정 시작을 눌렀을 때 워커 스레드가 통째로 굳고,
    중단 버튼도 소용이 없다(중단은 소자 사이에서 걸리는데 첫 소자에
    들어가지도 못한다).
    """
    cfg = HardwareConfig(bus_scan_timeout_s=0.5)
    with _FakePyvisa("hang"):
        t0 = time.time()
        try:
            Executor(cfg).connect()
        except BusTimeout:
            pass
        else:
            raise AssertionError("connect 가 통과했다")
        assert time.time() - t0 < 3.0


def test_missing_instrument_still_says_what_to_do():
    """버스는 멀쩡한데 장비가 없을 때의 안내가 그대로 남아 있는가."""
    with _FakePyvisa("empty"):
        try:
            Executor(HardwareConfig()).connect()
        except ConnectionError as e:
            assert "GPIB 버스에서 장비를 못 찾았다" in str(e)
            assert "팁은 접촉된 자리 그대로다" in str(e), "안전 상태를 안 알려준다"
        else:
            raise AssertionError("connect 가 통과했다")


def test_visa_failure_is_not_reported_as_a_timeout():
    """VISA 자체를 못 열면 그 원인이 그대로 와야 한다.

    타임아웃으로 뭉뚱그리면 USB 를 재연결하러 가는데 실은 dll 이 없는 것이다.
    """
    with _FakePyvisa("boom"):
        try:
            list_visa_resources("", timeout_s=0.5)
        except BusTimeout:
            raise AssertionError("원래 원인이 타임아웃으로 가려졌다")
        except ConnectionError as e:
            assert "VISA 를 열 수 없다" in str(e)
        else:
            raise AssertionError("통과했다")


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
    test_a_wedged_bus_gives_up_instead_of_hanging,
    test_measurement_path_also_gives_up,
    test_missing_instrument_still_says_what_to_do,
    test_visa_failure_is_not_reported_as_a_timeout,
    test_close_turns_outputs_off_before_closing_the_session,
    test_close_returns_the_mainframe_to_local,
    test_local_failure_does_not_stop_the_teardown,
    test_output_off_failure_does_not_stop_the_teardown,
    test_home_drops_the_voltage_before_it_moves,
    test_raw_csv_never_reaches_the_payload,
    test_calibration_budget_is_a_ceiling_not_a_floor,
    test_locked_plan_covers_the_rest_without_calling_the_agent,
    test_zero_calibration_sites_never_calls_the_agent,
]
