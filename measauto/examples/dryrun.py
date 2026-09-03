"""examples/dryrun.py — 장비 없이 전체 루프를 한 번 돌린다.

    python -m measauto.examples.dryrun

가짜 소자(replay.SimulatedDevice)를 상대로 session 이 돌면서
  · 시드 plan 으로 첫 측정
  · 지표 계산 → 에이전트 판단 → 다음 plan
  · 안전 검사 → 저장
까지 전부 탄다. 배선하기 전에 파이프라인이 물려 있는지 확인하는 용도.

ANTHROPIC_API_KEY 가 있으면 LLM 이 판단하고, 없으면 규칙 기반으로 내려간다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..agent.policy import HeuristicPolicy, LLMPolicy, default_policy
from ..config import DEFAULT
from ..executor import Site
from ..replay import FakeExecutor, SimulatedDevice
from ..safety import MissingLimitError, bounds_from_stack, load_stack
from ..seed import seed_transfer
from ..session import Session, SessionConfig
from ..store import Store


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results_dryrun")
    ap.add_argument("--vth", type=float, default=8.0,
                    help="가짜 소자의 Vth. 시드 범위(0~5 V) 밖에 두면 "
                         "에이전트가 범위를 넓히는지 볼 수 있다")
    ap.add_argument("--dead", action="store_true", help="죽은 소자로 시뮬레이션")
    ap.add_argument("--heuristic", action="store_true", help="LLM 대신 규칙 기반")
    ap.add_argument("--sites", type=int, default=4)
    ap.add_argument("--stack", default="sd_dualgate")
    # run_area 와 같은 기본값(1). 소자당 1회만 재고, 범위를 넓히는 것은
    # 같은 소자를 반복하는 대신 다음 소자에서 한다.
    ap.add_argument("--max-iters", type=int, default=1)
    args = ap.parse_args()

    try:
        bounds = bounds_from_stack(args.stack)
    except MissingLimitError as e:
        raise SystemExit(f"\n[안전 경계 미설정]\n{e}\n")
    print(bounds.describe(), "\n")

    dev = SimulatedDevice(vth=args.vth, dead=args.dead,
                          ig_break_V=25.0)   # 25 V 넘으면 게이트가 새기 시작
    ex = FakeExecutor(dev, config=DEFAULT)

    # 시드는 run_area 와 같은 경로로 stack 에서 계산한다. 손으로 적어 두면
    # 경계가 바뀔 때 따라오지 않아 예제만 조용히 깨진다(실제로 그랬다).
    seed = seed_transfer(args.stack)

    policy = HeuristicPolicy() if args.heuristic else _pick_policy()
    print(f"policy = {type(policy).__name__}\n")

    sess = Session(
        executor=ex, bounds=bounds, store=Store(args.out),
        policy=policy, stack=load_stack(args.stack),
        config=SessionConfig(
            objective="BG transfer 에서 Vth, SS, on/off 비를 얻는다.",
            max_iters=args.max_iters, calibration_sites=args.sites),
    )

    sites = [Site(name=f"d{i+1}", x=250 * i, y=0, area="A") for i in range(args.sites)]
    results = sess.run_area(sites, seed)

    print("\n=== 요약 ===")
    for r in results:
        m = r.metrics or {}
        print(f"  {r.site.name:4s} {r.status:16s} iter={r.iterations} "
              f"vth={m.get('vth_cc')} ss={m.get('ss')} decades={m.get('decades')}")
    print(f"\n결과: {Path(args.out).resolve()}")


def _pick_policy():
    try:
        p = default_policy()
        if isinstance(p, LLMPolicy):
            import anthropic  # noqa: F401
        return p
    except Exception as e:
        print(f"[dryrun] LLM 사용 불가({e}) → 규칙 기반으로 내려감")
        return HeuristicPolicy()


if __name__ == "__main__":
    main()
