"""examples/replay_legacy.py — 이미 측정해 둔 CSV 로 에이전트를 평가한다.

    python -m measauto.examples.replay_legacy --glob "../projects/grid_measure/results_idvd/manual*.csv"

같은 소자를 여러 번 물어봐서 status 가 갈리면 프롬프트가 부실하다는 신호다.
안전 경계에 몇 번 부딪히는지도 같이 센다 — 에이전트가 경계를 자주 건드리면
프롬프트에 경계 설명이 부족하거나 목적이 경계와 충돌한다는 뜻이다.

한계: 에이전트가 새 조건을 제안해도 그 조건으로 찍힌 과거 데이터는 없다.
그래서 여기서 세는 건 '첫 판단' 뿐이다. 다중턴 수렴 횟수는
examples/dryrun.py (시뮬레이션) 로 센다.
"""

from __future__ import annotations

import argparse
import glob as globmod
import json
from pathlib import Path

from ..agent.policy import HeuristicPolicy, default_policy
from ..plan import output, transfer
from ..replay import ReplayCase, replay_score
from ..safety import MissingLimitError, bounds_from_stack


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", required=True, help="CSV 경로 패턴")
    ap.add_argument("--kind", choices=["output", "transfer"], default="output")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--heuristic", action="store_true")
    ap.add_argument("--stack", default="sd_dualgate")
    ap.add_argument("--out", default="replay_score.json")
    args = ap.parse_args()

    roles = {"BG": 1, "D": 2, "S": 3}
    if args.kind == "output":
        # 기존 노트북의 Id-Vd 조건 그대로 (VAR1 Vd 0~10 81pt double, VAR2 Vg 10→3 15step)
        plan = output("D", 0, 10, points=81, gate="BG", gate_start=10.0,
                      gate_stop=3.0, gate_steps=15, direction="double",
                      drain_compliance=10e-3, gate_compliance=10e-3,
                      label="legacy_idvd")
    else:
        plan = transfer("BG", -0.5, 20.0, points=101, vd=0.1, direction="double",
                        label="legacy_transfer")

    paths = sorted(Path(p) for p in globmod.glob(args.glob))
    if not paths:
        raise SystemExit(f"매칭되는 파일이 없다: {args.glob}")
    cases = [ReplayCase(path=p, plan=plan, roles=roles, site=p.stem) for p in paths]

    try:
        bounds = bounds_from_stack(args.stack)
    except MissingLimitError as e:
        raise SystemExit(f"\n[안전 경계 미설정]\n{e}\n")
    policy = HeuristicPolicy() if args.heuristic else default_policy()
    print(f"policy={type(policy).__name__}, cases={len(cases)}, "
          f"repeats={args.repeats}\n")

    score = replay_score(cases, policy, bounds,
                         objective=("이 소자의 출력특성에서 포화 여부와 접촉 저항 "
                                    "영향을 본다."),
                         repeats=args.repeats)

    print("상태 분포:", score["status_counts"])
    print("경계 접촉:", score["boundary_contacts"], "/", score["n_runs"])
    if score["unstable_cases"]:
        print("결론이 갈린 케이스(프롬프트 점검 필요):", score["unstable_cases"])
    print()
    for r in score["rows"][:len(cases)]:
        print(f"  {r['case']:<24s} {r['status']:<16s} conf={r['confidence']:.2f}")
        print(f"      {r['reason'][:150]}")
        if r["proposed"]:
            print(f"      → {r['proposed']}")

    Path(args.out).write_text(
        json.dumps({k: v for k, v in score.items() if k != "rows"},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n요약 저장: {args.out}")


if __name__ == "__main__":
    main()
