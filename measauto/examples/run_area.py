"""examples/run_area.py — 실제 장비로 area 하나를 돈다.

    python -m measauto.examples.run_area --coords ../utils/grid_4x4.csv

⚠️ 실행 전에 Nucleus UI 에서 사람이 끝내둬야 하는 것 (코드가 대체하지 않는다)
   1. 척 로드 / 진공 ON / 소자 세팅
   2. Alignment (2-point align) — 안 하면 좌표가 통째로 어긋난다
   3. Tipping / Set Contact — 팁 contact 높이 등록
   4. 첫 소자에 팁을 직접 contact 시킨 상태로 둘 것 → --set-reference 로 원점 등록

--dry 를 주면 장비를 열지 않고 조건 검증까지만 한다. 처음엔 이걸로 확인할 것.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..agent.policy import default_policy
from ..config import DEFAULT
from ..executor import Executor, load_sites
from ..safety import MissingLimitError, bounds_from_stack, load_stack, validate
from ..seed import seed_transfer
from ..session import Session, SessionConfig
from ..store import Store


# 목적은 '무엇을 얻고 싶은가' 만 적는다. 소자 사실(배선·묶인 게이트 등)은
# stack 에서 나와 device_text 로 따로 전달되므로 여기 쓰지 않는다 —
# 섞어 두면 stack 을 바꿔도 목적 문장이 옛 사실을 계속 주장한다.
# 소자 스펙 시트에 들어가는 다섯 항목. 왕복 스윕을 강제하는 근거가 여기 있어야
# 한다 — 목적에 히스테리시스가 없으면 에이전트는 편도를 제안하고(맞는 판단이다)
# 우리가 매번 덮어쓰게 된다. 강제하는 것과 목적이 어긋나면 안 된다.
DEFAULT_OBJECTIVE = (
    "BG transfer 에서 소자 스펙 시트 다섯 항목을 얻는다: "
    "Vth, SS, 전계효과 이동도, on/off 비, 히스테리시스. "
    "히스테리시스는 정/역 방향 차이이므로 왕복(double) 스윕이 필요하다.")


def build_seed(stack: str, gate: str = "BG"):
    """시드 조건을 stack 에서 계산한다 (measauto.seed 참고).

    손으로 적어 두면 stack.json 을 갈아끼워도 따라오지 않아 경계와 조건이
    어긋난다. 스텝은 특히 손으로 정하면 안 된다 — SS 보다 성기면 SS 가
    부풀려진 채로 그럴듯하게 나온다.

    stack 에 expected 가 없으면 경계 전체를 성기게 훑는 survey 로 떨어진다.
    """
    return seed_transfer(stack, gate=gate)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coords", required=True, help="Subsite Name,X Position,Y Position CSV")
    ap.add_argument("--out", default="results_agent")
    ap.add_argument("--stack", default="sd_dualgate")
    ap.add_argument("--gate", default="BG", help="sweep 할 게이트 단자")
    ap.add_argument("--area", default="A")
    ap.add_argument("--calib", type=int, default=2, help="area 당 탐색할 소자 수")
    # 기본 1 — 같은 소자를 반복 측정하면 전하 트래핑이 쌓여 값이 흔들린다.
    # 실측: 같은 소자 Vth 가 5.6 → 10.4 V 로 밀렸다가 30분 뒤 5.5 V 로 회복.
    # 그 상태에서는 소자 특성이 아니라 스트레스 이력을 재게 된다.
    # 조건을 다듬는 것은 '측정 전'에 하는 일이라 시드 단계로 옮겼다(에이전트가
    # 스펙과 목적을 보고 정한다). 소자를 건드려 가며 찾지 않는다.
    ap.add_argument("--max-iters", type=int, default=1,
                    help="소자당 측정 횟수 상한. 기본 1 (트래핑 누적 방지). "
                         "2 이상으로 두면 에이전트가 관측을 보고 조건을 고쳐 "
                         "다시 측정할 수 있지만 그만큼 스트레스가 쌓인다")
    ap.add_argument("--set-reference", action="store_true",
                    help="지금 접촉된 위치를 원점으로 등록한다")
    ap.add_argument("--dry", action="store_true", help="장비를 열지 않고 검증만")
    ap.add_argument("--objective", default=DEFAULT_OBJECTIVE,
                    help="측정 목적 한 줄. 시드 범위와 수렴 판단이 여기서 갈린다")
    ap.add_argument("--no-agent-seed", action="store_true",
                    help="조건을 에이전트에게 묻지 않고 계산된 기준안을 쓴다")
    ap.add_argument("--direction", choices=["single", "double"],
                    help="스윕 방향을 못 박는다. 주면 에이전트 판단보다 우선한다")
    ap.add_argument("--report", action="store_true",
                    help="끝나고 에이전트가 쓴 리포트를 저장한다 (API 호출)")
    args = ap.parse_args()

    objective = args.objective
    try:
        bounds = bounds_from_stack(args.stack)
        seed = build_seed(args.stack, gate=args.gate)
    except MissingLimitError as e:
        raise SystemExit(f"\n[안전 경계 미설정]\n{e}\n")
    sites = load_sites(args.coords, area=args.area)
    print(f"목적: {objective}\n")

    print(bounds.describe())
    print(f"\n기준안: {seed.describe()}")
    print(f"  근거: {seed.note}")

    # 시드는 Session 이 소자마다 정한다(앞 소자 결과를 근거로). 여기서는
    # 기준안만 보여주고 넘긴다.
    policy = default_policy()
    print(f"소자 {len(sites)}개, 탐색 {args.calib}개 → 나머지는 확정 plan\n")

    viol = validate(seed, bounds, terminals=DEFAULT.roles.keys())
    for v in viol:
        print(" ", v)
    if any(v.severity == "error" for v in viol):
        raise SystemExit("시드 조건이 안전 경계를 위반한다. 고치고 다시.")
    if args.dry:
        print("\n[dry] 여기까지. 장비는 열지 않았다.")
        return

    try:
        ex = Executor(DEFAULT).connect()
    except ConnectionError as e:
        raise SystemExit(f"\n[장비 연결 실패]\n{e}\n")
    try:
        print("장비 상태:", ex.check())
        if args.set_reference:
            # set_reference 는 '0,0 으로 만들기 직전' 위치를 돌려준다. 그대로
            # 찍으면 원점이 그 좌표로 등록된 것처럼 읽히므로 뜻을 적어 준다.
            x, y, z = ex.set_reference()
            print(f"원점 등록: 직전 좌표계의 ({x:g}, {y:g}) 지점을 (0, 0) 으로, "
                  f"contact 높이 z={z:g} 로 등록했다"
                  + ("  ← 이전 원점과 같은 자리" if x == 0 and y == 0 else
                     f"  ← 이전 원점에서 X {x:+g}, Y {y:+g} µm 떨어진 자리"))

        store = Store(args.out)
        sess = Session(
            executor=ex, bounds=bounds, store=store,
            policy=policy, stack=load_stack(args.stack),
            config=SessionConfig(
                objective=objective,
                max_iters=args.max_iters, calibration_sites=args.calib,
                adapt_seed_per_site=not args.no_agent_seed,
                force_direction=args.direction),
        )
        results = sess.run_area(sites, seed)

        print("\n=== 요약 ===")
        for r in results:
            m = r.metrics or {}
            print(f"  {r.site.name:6s} {r.status:16s} iter={r.iterations} "
                  f"vth={m.get('vth_cc')} ss={m.get('ss')}")
        print(f"\n결과: {Path(args.out).resolve()}")
    finally:
        try:
            ex.home()       # 원점에 컨택된 상태로 끝내야 다음 실행 전제가 성립
        finally:
            ex.close()

    # 리포트는 장비를 닫은 뒤에 만든다 — 실패해도 팁은 이미 안전한 자리에 있다.
    if args.report:
        _write_report(args.out, store.run_id)


def _write_report(out: str, run_id: str) -> None:
    from ..report import analyze, load_run, render
    path = Path(out) / run_id / "report.md"
    try:
        recs = load_run(out, run_id)
        text = render(recs)
        try:
            text += "\n\n---\n\n## 분석\n\n" + analyze(recs)
        except Exception as e:
            text += f"\n\n(서술 분석 실패: {type(e).__name__}: {e})"
            print(f"[report] 서술 분석 실패 ({type(e).__name__}) — 표만 저장한다")
        path.write_text(text, encoding="utf-8")
        print(f"리포트: {path.resolve()}")
    except Exception as e:
        print(f"[report] 생성 실패: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
