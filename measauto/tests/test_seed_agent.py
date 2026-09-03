"""에이전트가 첫 조건을 정하는 경로 — LLM 없이 뒷단만 검증한다.

목적이 바뀌면 시드가 달라져야 한다는 것 자체는 LLM 이 판단하므로 여기서 못 본다.
여기서 보는 것은 '에이전트가 낸 값이 어떻게 다뤄지는가'다:
  · 정상 제안이면 반영되는가
  · 경계를 넘으면 기준안으로 되돌아가는가
  · 호출이 실패해도 측정이 멈추지 않는가
"""

from __future__ import annotations

from ..agent.policy import HeuristicPolicy, Proposal
from ..agent.prompt import build_seed_message
from ..agent.schema import PlanPatch, apply_patch
from ..config import DEFAULT
from ..executor import Site
from ..plan import transfer
from ..safety import bounds_from_stack, validate
from ..seed import seed_transfer
from .fixtures import stack_with_limits


class _FakePolicy:
    """propose_seed 만 흉내낸다."""

    def __init__(self, patch=None, status="propose", boom=False):
        self.patch, self.status, self.boom = patch, status, boom

    def propose_seed(self, ctx):
        if self.boom:
            raise RuntimeError("API 실패 흉내")
        if self.status != "propose":
            return Proposal(self.status, "못 정하겠다", 0.2, source="llm")
        plan = apply_patch(ctx["seed_plan"], self.patch, note="에이전트가 정함")
        return Proposal("propose", "목적이 음바이어스 거동을 요구한다", 0.8,
                        plan=plan, patch=self.patch, source="llm")


def _setup():
    # 실측이 있는 상태를 가정한다 — 그래야 기준안이 좁게 잡히고, 에이전트가
    # 넓히거나 좁히는 것을 구분해서 볼 수 있다. 실측이 없으면 기준안이 이미
    # 경계 전체(blind)라 '넓힌다'가 성립하지 않는다.
    stack = stack_with_limits()
    stack["measured"] = {"source": "테스트", "vth_V": 1.15,
                         "ss_V_per_dec": 0.45, "step_V": 0.11}
    return stack, bounds_from_stack(stack), seed_transfer(stack)


def _agent_seed(policy, baseline, bounds):
    """run_area._agent_seed 와 같은 규칙(폴백 포함)을 축약한 것."""
    try:
        prop = policy.propose_seed({"objective": "x", "bounds_text": "",
                                    "seed_text": "", "seed_plan": baseline})
    except Exception:
        return baseline
    if prop.status != "propose" or prop.plan is None:
        return baseline
    errs = [v for v in validate(prop.plan, bounds, terminals=DEFAULT.roles.keys())
            if v.severity == "error"]
    return baseline if errs else prop.plan


def test_agent_can_widen_the_negative_side():
    """목적이 음의 영역을 요구하면 넓힐 수 있어야 한다."""
    _, bounds, base = _setup()
    wide = PlanPatch(var1_start=-18.0, var1_points=210)
    got = _agent_seed(_FakePolicy(wide), base, bounds)
    assert got.var1.start == -18.0
    assert got.var1.start < base.var1.start, "넓어지지 않았다"


def test_out_of_bounds_proposal_falls_back():
    """경계를 넘는 제안은 반영되지 않고 기준안이 남는다."""
    _, bounds, base = _setup()
    got = _agent_seed(_FakePolicy(PlanPatch(var1_stop=200.0)), base, bounds)
    assert got.var1.stop == base.var1.stop, "경계를 넘는 제안이 반영됐다"


def test_api_failure_does_not_stop_measurement():
    """호출이 실패해도 결정론적 기준안으로 계속 간다."""
    _, bounds, base = _setup()
    assert _agent_seed(_FakePolicy(boom=True), base, bounds) is base


def test_needs_human_falls_back():
    _, bounds, base = _setup()
    assert _agent_seed(_FakePolicy(status="needs_human"), base, bounds) is base


def test_coarse_proposal_is_flagged_not_silently_accepted():
    """에이전트가 성기게 잡으면 분해능 경고가 붙어야 한다(값은 통과시키되 기록)."""
    _, bounds, base = _setup()
    coarse = _agent_seed(_FakePolicy(PlanPatch(var1_points=20)), base, bounds)
    codes = [v.code for v in validate(coarse, bounds,
                                      terminals=DEFAULT.roles.keys())]
    assert "resolution" in codes


def test_prior_devices_reach_the_seed_prompt():
    """소자당 1회면 조정은 소자 사이에서만 일어난다. 앞 소자 결과가 시드
    프롬프트에 안 들어가면 그 조정 자체가 성립하지 않는다.
    (실제로 한동안 안 들어가 있었고, 소자 2·3·4 가 매번 스펙만 보고 처음부터
    다시 정하고 있었다)"""
    msg = build_seed_message(
        objective="Vth, SS", bounds_text="", baseline_text="",
        prior_devices=[{"site": "1", "vth_cc": 1.09, "ss": 0.499}])
    assert "1.09" in msg and "0.499" in msg, "앞 소자 지표가 빠졌다"
    assert "앞 소자" in msg


def test_fixed_direction_is_told_to_the_agent():
    """사람이 방향을 못 박았으면 그 전제를 알려야 한다. 모르면 실행되지도 않을
    계획(single)을 놓고 스트레스를 계산한다."""
    msg = build_seed_message(
        objective="Vth", bounds_text="", baseline_text="",
        fixed_text="스윕 방향 = double (왕복이라 실측 점 수가 2배가 된다)")
    assert "고정한 것" in msg and "double" in msg


def test_no_prior_devices_says_so():
    msg = build_seed_message(objective="Vth", bounds_text="", baseline_text="")
    assert "앞 소자" not in msg


def test_spec_expected_values_are_not_in_the_prompt():
    """설계자료의 기대 Vth·SS 를 프롬프트에 넣지 않는다.

    이 웨이퍼에서 계통적으로 틀렸고(실측 Vth 1.05~1.27 vs 자료 1.55), W 스플릿
    소자에서는 아예 유추값이다. 넣어두면 에이전트가 매 판단의 기준점으로 삼아
    틀린 앵커가 된다.
    """
    from ..config import HardwareConfig
    from ..replay import FakeExecutor, SimulatedDevice
    from ..safety import bounds_from_stack
    from ..session import Session, SessionConfig
    from ..store import Store
    from .fixtures import stack_with_limits
    import tempfile

    stack = stack_with_limits()
    assert stack["_spec_reference"]["vth_V"] == 1.55, "이 테스트의 전제"

    cfg = HardwareConfig(roles={"BG": 1, "D": 2, "S": 3})
    with tempfile.TemporaryDirectory() as tmp:
        sess = Session(FakeExecutor(SimulatedDevice(), config=cfg),
                       bounds_from_stack(stack), Store(tmp, make_png=False),
                       HeuristicPolicy(), SessionConfig())
        ctx = sess._ctx(Site("d1", 0, 0, area="A"),
                        transfer("BG", 0, 5, points=41, vd=0.1))
    assert "expected_text" not in ctx or not ctx.get("expected_text"), \
        "기대값이 ctx 에 실렸다"

    msg = build_seed_message(objective="Vth", bounds_text=ctx["bounds_text"],
                             baseline_text=ctx["seed_text"],
                             device_text=ctx["device_text"],
                             terminals_text=ctx["terminals_text"])
    assert "1.55" not in msg and "0.27" not in msg, "기대값이 프롬프트에 새어나갔다"


def test_heuristic_returns_baseline():
    """규칙 기반은 목적을 못 읽으므로 기준안을 그대로 쓴다."""
    _, _, base = _setup()
    prop = HeuristicPolicy().propose_seed({"seed_plan": base})
    assert prop.plan is base


TESTS = [v for k, v in dict(globals()).items() if k.startswith("test_")]
