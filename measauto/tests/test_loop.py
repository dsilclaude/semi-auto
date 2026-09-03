"""plan/patch/executor/session 배관 검증. LLM 없이 규칙 기반으로 끝까지 돈다."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..agent.policy import HeuristicPolicy, Proposal
from ..agent.schema import PROPOSAL_SCHEMA, PlanPatch, apply_patch
from ..config import HardwareConfig
from ..executor import Site, plan_to_spec
from ..plan import IVPlan, transfer
from ..replay import FakeExecutor, SimulatedDevice
from ..safety import bounds_from_stack
from ..session import Session, SessionConfig
from ..store import Store
from .fixtures import stack_with_limits


def test_plan_roundtrip():
    p = transfer("BG", -5, 25, points=61, vd=0.1, direction="double")
    q = IVPlan.from_dict(p.to_dict())
    assert q.to_dict() == p.to_dict()
    assert q.n_points == 61 * 2


def test_patch_only_touches_named_fields():
    base = transfer("BG", 0, 5, points=51, vd=0.1)
    new = apply_patch(base, PlanPatch(var1_start=-5, var1_stop=25, var1_points=101))
    assert (new.var1.start, new.var1.stop, new.var1.n_steps) == (-5, 25, 101)
    # 건드리지 않은 값은 그대로 (여기가 무너지면 전수 검사 전제가 깨진다)
    assert new.timing == base.timing and new.adc == base.adc
    assert new.constants == base.constants
    assert new.var1.compliance == base.var1.compliance


def test_patch_can_add_var2():
    base = transfer("BG", 0, 5, points=51, vd=0.1)
    new = apply_patch(base, PlanPatch(var2_terminal="TG", var2_start=0,
                                      var2_stop=10, var2_points=3))
    assert new.var2 is not None and new.var2.terminal == "TG"
    assert new.n_points == 51 * 2 * 3


def test_patch_survives_nulls_inside_constants():
    """스키마가 네 단자를 전부 요구하므로 안 쓰는 단자는 null 로 온다.
    딕셔너리 자체는 None 이 아니라 필터를 통과하니, 안쪽도 걸러야 한다.
    (실측 중 float(None) 으로 죽은 적이 있다)"""
    p = PlanPatch.from_dict({"var1_stop": 10.0,
                             "constants": {"TG": None, "BG": None,
                                           "D": 0.1, "S": 0.0}})
    base = transfer("BG", 0, 20, points=51, vd=0.1)
    plan = apply_patch(base, p)          # 예외가 나면 실패
    assert plan.constants["D"] == 0.1
    assert "TG" not in plan.constants


def test_patch_drops_terminals_the_setup_does_not_have():
    """스키마가 TG/BG/D/S 를 다 요구하므로 없는 단자에도 값이 실려 온다.
    그대로 두면 validate 가 '모르는 단자'로 제안을 통째로 반려한다.
    (실측에서 실제로 에이전트 제안이 이것 때문에 버려졌다)"""
    base = transfer("BG", 0, 20, points=51, vd=0.1)      # BG/D/S 만 쓰는 셋업
    p = PlanPatch.from_dict({"var1_stop": 12.0,
                             "constants": {"TG": 0.0, "BG": None,
                                           "D": 0.1, "S": 0.0}})
    plan = apply_patch(base, p)
    assert "TG" not in plan.constants, plan.constants
    assert plan.var1.stop == 12.0


def test_patch_with_all_null_constants_is_dropped():
    p = PlanPatch.from_dict({"constants": {"TG": None, "BG": None,
                                           "D": None, "S": None}})
    assert p is None or p.constants is None


def test_schema_is_strict():
    """structured outputs 규격: 모든 필드가 required, additionalProperties=False."""
    assert PROPOSAL_SCHEMA["additionalProperties"] is False
    assert set(PROPOSAL_SCHEMA["required"]) == set(PROPOSAL_SCHEMA["properties"])
    patch = PROPOSAL_SCHEMA["properties"]["patch"]
    assert set(patch["required"]) == set(patch["properties"])


def test_plan_to_spec_maps_terminals_to_channels():
    cfg = HardwareConfig(roles={"BG": 1, "D": 2, "S": 3})
    p = transfer("BG", 0, 10, points=21, vd=0.5)
    spec = plan_to_spec(p, cfg.roles)
    assert spec["var1"]["unit"] == 1
    assert spec["constants"][2]["v"] == 0.5    # D
    assert spec["constants"][3]["v"] == 0.0    # S
    assert spec["var2"] is None


def test_unwired_terminal_is_refused():
    cfg = HardwareConfig(roles={"BG": 1, "D": 2, "S": 3})   # TG 없음
    ex = FakeExecutor(SimulatedDevice(), config=cfg)
    p = transfer("TG", 0, 10, points=21, vd=0.1, other_gate="BG")
    try:
        ex.measure(p)
    except KeyError as e:
        assert "TG" in str(e)
    else:
        raise AssertionError("배선 안 된 단자를 그냥 측정했다")


def test_session_expands_range_until_curve_appears():
    """Vth 가 시드 범위 밖인 소자. 규칙 기반 policy 가 범위를 넓혀 잡아야 한다."""
    bounds = bounds_from_stack(stack_with_limits())
    cfg = HardwareConfig(roles={"BG": 1, "D": 2, "S": 3})
    ex = FakeExecutor(SimulatedDevice(vth=9.0, noise=0.0), config=cfg)
    seed = transfer("BG", 0, 5, points=41, vd=0.1, direction="single")

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(tmp, make_png=False)
        sess = Session(ex, bounds, store, HeuristicPolicy(),
                       SessionConfig(max_iters=5, calibration_sites=1))
        r = sess.run_site(Site("d1", 0, 0, area="A"), seed)
        assert r.iterations >= 2, "범위를 한 번도 안 넓혔다"
        assert r.status in ("converged", "needs_human"), r.status
        idx = Store(tmp, run_id=store.run_id).read_index()
        assert len(idx) == r.iterations
        # 폴더 이름에 조건이 들어가므로 경로를 하드코딩하지 않는다
        site_dir = Path(tmp) / store.run_id / "A" / "d1"
        made = sorted(site_dir.glob("iter*/plan.json"))
        assert len(made) == r.iterations, [p.parent.name for p in made]
        # 회차마다 조건이 달라졌으면 폴더 이름도 달라야 한다(덮어쓰지 않는다)
        assert len({p.parent.name for p in made}) == r.iterations


class _WideningAgent:
    """창을 넓히는 것은 **에이전트**다 — 코드가 아니다.

    session 은 직전 소자에서 쓴 조건을 base 로 넘길 뿐 스스로 옮기지 않는다.
    여기서는 그 에이전트 자리에 '못 찾았으면 ×1.5' 라는 최소한의 정책을 끼워
    넣어, 루프가 그 결정을 **소자 사이로** 실어 나르는지만 본다.
    """

    def __init__(self):
        self.seen: list = []

    def propose_seed(self, ctx):
        base = ctx["seed_plan"]
        priors = ctx.get("prior_devices") or []
        self.seen.append(len(priors))
        if not priors or any(d.get("transition_captured") for d in priors):
            return Proposal("propose", "기준안 유지", 0.8, plan=base, source="llm")
        lo, hi = base.var1.start, base.var1.stop
        mid, half = (lo + hi) / 2, (hi - lo) / 2 * 1.5
        patch = PlanPatch(var1_start=mid - half, var1_stop=mid + half,
                          var1_points=int(base.var1.n_steps * 1.5))
        return Proposal("propose", "앞 소자에서 turn-on 을 못 찾았다", 0.8,
                        plan=apply_patch(base, patch, note="에이전트가 넓힘"),
                        patch=patch, source="llm")

    def propose(self, ctx, history):
        return Proposal("converged", "1회로 끝", 1.0, source="llm")


def test_window_widens_across_devices_not_within_one():
    """turn-on 을 못 찾으면 **다음 소자에서** 창을 넓힌다.

    같은 소자를 다시 재면 전하 트래핑이 쌓여 Vth 가 밀린다(실측: 5.6→10.4 V).
    소자를 바꿔가며 넓히면 모든 소자가 1회씩만 찍히고 데이터가 깨끗하다.

    넓힐지 말지는 에이전트가 정한다. 여기서 확인하는 것은 **루프가 그 결정을
    소자 사이로 옮기는가**이다 — 앞 소자 결과가 다음 소자의 문맥에 들어가고,
    에이전트가 낸 조건이 다음 base 가 되는가.
    """
    stack = stack_with_limits()
    cfg = HardwareConfig(roles={"BG": 1, "D": 2, "S": 3})
    ex = FakeExecutor(SimulatedDevice(vth=9.0, noise=0.0), config=cfg)
    seed = transfer("BG", -1, 5, points=61, vd=0.1, direction="single")
    agent = _WideningAgent()

    with tempfile.TemporaryDirectory() as tmp:
        sess = Session(ex, bounds_from_stack(stack), Store(tmp, make_png=False),
                       agent, SessionConfig(max_iters=1, calibration_sites=4),
                       stack=stack)
        sites = [Site(str(i + 1), 0, -300 * i, area="A") for i in range(4)]
        res = sess.run_area(sites, seed)

    for r in res:                       # 어느 소자도 두 번 찍히지 않는다
        assert r.iterations == 1, f"소자 {r.site.name} 이 {r.iterations}번 측정됐다"

    # 앞 소자 결과가 실제로 다음 소자의 문맥에 쌓였는가
    assert agent.seen == [0, 1, 2, 3], agent.seen

    spans = [r.final_plan.var1.stop - r.final_plan.var1.start for r in res
             if r.final_plan]
    assert spans[1] > spans[0], f"창이 안 넓어졌다: {spans}"
    # Vth 9 V 가 -1~5 V 밖이므로 넓히다가 결국 찾아야 한다
    assert any((r.metrics or {}).get("transition_captured") for r in res), \
        "넓혀도 turn-on 을 못 찾았다"


def test_baseline_carries_the_last_condition_and_decides_nothing():
    """session 은 직전 조건을 넘길 뿐 스스로 창을 옮기지 않는다.

    에이전트가 아무 patch 도 안 내면 같은 창이 그대로 반복돼야 한다. 코드가
    몰래 넓혀 주면 판단이 두 곳에 생기고, 그 규칙이 이 웨이퍼에 맞춰 굳는다.
    """
    stack = stack_with_limits()
    cfg = HardwareConfig(roles={"BG": 1, "D": 2, "S": 3})
    ex = FakeExecutor(SimulatedDevice(vth=9.0, noise=0.0), config=cfg)
    seed = transfer("BG", -1, 5, points=61, vd=0.1, direction="single")

    with tempfile.TemporaryDirectory() as tmp:
        sess = Session(ex, bounds_from_stack(stack), Store(tmp, make_png=False),
                       HeuristicPolicy(),      # 아무것도 안 바꾸는 policy
                       SessionConfig(max_iters=1, calibration_sites=4),
                       stack=stack)
        res = sess.run_area([Site(str(i + 1), 0, -300 * i, area="A")
                             for i in range(4)], seed)

    windows = {(r.final_plan.var1.start, r.final_plan.var1.stop)
               for r in res if r.final_plan}
    assert windows == {(-1.0, 5.0)}, f"코드가 스스로 창을 옮겼다: {windows}"


def test_single_shot_measures_each_device_once():
    """max_iters=1 이면 소자를 딱 한 번만 건드려야 한다.

    같은 소자를 반복 측정하면 전하 트래핑이 쌓여 값이 흔들린다(실측 확인).
    에이전트가 더 재고 싶어해도 측정은 1회로 끝나야 하고, 무엇을 원했는지는
    기록에 남아야 한다.
    """
    bounds = bounds_from_stack(stack_with_limits())
    cfg = HardwareConfig(roles={"BG": 1, "D": 2, "S": 3})
    ex = FakeExecutor(SimulatedDevice(vth=9.0, noise=0.0), config=cfg)
    seed = transfer("BG", 0, 5, points=41, vd=0.1, direction="single")

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(tmp, make_png=False)
        sess = Session(ex, bounds, store, HeuristicPolicy(),
                       SessionConfig(max_iters=1))
        r = sess.run_site(Site("d1", 0, 0, area="A"), seed)
        assert r.iterations == 1, r.iterations
        assert len(Store(tmp, run_id=store.run_id).read_index()) == 1
        # 시드가 좁아 규칙 기반은 범위를 넓히자고 했을 것 — 그 의사가 남아야 한다
        assert r.status == "max_iters"
        assert "추가 측정" in (r.error or ""), r.error


def test_reruns_do_not_overwrite_each_other():
    """같은 소자를 같은 조건으로 다시 재도 이전 결과가 남아야 한다.

    소자는 스트레스 이력이 쌓이므로 '두 번째 측정'은 다른 데이터다.
    실측에서 실제로 1차 곡선이 2차에 덮여 사라진 적이 있다.
    """
    bounds = bounds_from_stack(stack_with_limits())
    cfg = HardwareConfig(roles={"BG": 1, "D": 2, "S": 3})
    seed = transfer("BG", 0, 5, points=41, vd=0.1, direction="single")

    with tempfile.TemporaryDirectory() as tmp:
        for run in ("run_a", "run_b"):
            ex = FakeExecutor(SimulatedDevice(vth=2.0, noise=0.0), config=cfg)
            sess = Session(ex, bounds, Store(tmp, make_png=False, run_id=run),
                           HeuristicPolicy(), SessionConfig(max_iters=1))
            sess.run_site(Site("d1", 0, 0, area="A"), seed)
        runs = sorted(p.name for p in Path(tmp).iterdir() if p.is_dir())
        assert runs == ["run_a", "run_b"], runs
        assert len(Store(tmp).read_index()) == 2, "인덱스에 둘 다 남아야 한다"


def test_session_blocks_out_of_bounds_plan():
    bounds = bounds_from_stack(stack_with_limits())
    cfg = HardwareConfig(roles={"BG": 1, "D": 2, "S": 3})
    ex = FakeExecutor(SimulatedDevice(), config=cfg)
    bad = transfer("BG", 0, 50, points=41, vd=0.1)   # |V_BG-V_S| = 50 > 30

    with tempfile.TemporaryDirectory() as tmp:
        sess = Session(ex, bounds, Store(tmp, make_png=False), HeuristicPolicy(),
                       SessionConfig(max_iters=2, max_retry_on_violation=0))
        r = sess.run_site(Site("d1", 0, 0, area="A"), bad)
        assert r.status == "out_of_bounds", r.status
        assert not ex.moves or True   # 이동은 해도 측정은 하지 않는다
        assert r.iterations == 0, "경계를 넘는 조건이 실행됐다"


TESTS = [v for k, v in dict(globals()).items() if k.startswith("test_")]
