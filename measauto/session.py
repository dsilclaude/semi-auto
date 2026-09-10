"""session.py — area 단위 운용 루프.

여기가 검증(safety)과 실행(executor)을 잇는 유일한 지점이다.
executor 를 부르기 **직전에** validate 를 건다.

area 단위로 도는 이유: 탐색 자체가 소자를 오염시킨다. 소자마다 탐색하면
스트레스가 전수에 쌓인다. area 당 1~2개로 캘리브레이션한 뒤 나머지에는
확정된 plan 을 그대로 적용한다.

부수 효과 하나: iteration 횟수 자체가 스크리닝 지표다. LLM 호출이 몰리는
소자가 곧 이상 소자다. index.csv 의 iteration 열을 세면 바로 보인다.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .agent.policy import HeuristicPolicy, Policy, Proposal
from .agent.schema import PlanPatch, apply_patch
from .executor import Executor, Site
from .metrics import downsample_curve, summarize, to_llm_payload
from .plan import IVPlan, with_axis
from .safety import Bounds, validate
from .store import Store


@dataclass
class SessionConfig:
    objective: str = "이 소자의 transfer 특성에서 Vth, SS, on/off 비를 얻는다."
    max_iters: int = 5              # 소자당 측정 횟수 상한 (누적 스트레스 상한이기도 함)
    calibration_sites: int = 2      # area 당 에이전트가 탐색할 소자 수
    max_retry_on_violation: int = 2  # 경계 위반 제안을 몇 번까지 되돌려 보낼지
    min_confidence: float = 0.0     # 이보다 낮으면 needs_human 으로 넘긴다
    curve_points: int = 25          # LLM 에 넘길 다운샘플 점 수

    # --- 소자당 1회일 때의 조정 경로 -----------------------------------------
    # max_iters=1 이면 관측을 보고 조건을 고칠 기회가 소자 안에는 없다.
    # 그래서 조정을 '소자 사이'로 옮긴다 — 앞 소자에서 본 것을 근거로 다음
    # 소자의 조건을 정한다. 소자를 반복해서 건드리지 않고도 범위를 좁혀 간다.
    # 넓힐지 좁힐지, 얼마나 넓힐지는 **에이전트가 정한다.** 여기에 그 배율을
    # 두었던 적이 있는데(widen_factor), 그러면 판단이 코드와 프롬프트 두 곳에
    # 생기고 그 값이 이 웨이퍼에 맞춰 굳는다. 그래서 뺐다.
    adapt_seed_per_site: bool = True
    # 스윕 방향을 사람이 못 박고 싶을 때. None 이면 에이전트가 정한다.
    force_direction: Optional[str] = None


@dataclass
class SiteResult:
    site: Site
    status: str = "not_run"
    iterations: int = 0
    final_plan: Optional[IVPlan] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    history: List[dict] = field(default_factory=list)
    error: str = ""

    def summary_row(self) -> dict:
        """다음 소자에게 넘길 prior. 3번째 소자쯤이면 한 턴에 끝나야 정상이다."""
        m = self.metrics or {}
        p = self.final_plan
        return {
            "site": self.site.name,
            "status": self.status,
            "iterations": self.iterations,
            "sweep": [p.var1.start, p.var1.stop] if p else None,
            "points": p.var1.n_steps if p else None,
            "vth_cc": m.get("vth_cc"),
            "vth_cc_wl_known": m.get("vth_cc_wl_known"),
            "ss": m.get("ss"),
            "decades": m.get("decades"),
            # 다음 소자의 창을 넓힐지 좁힐지가 여기서 갈린다.
            # turn_on_V 는 W 를 알면 정전류 Vth, 모르면 floor 기준(W 무관)이다.
            "turn_on_V": m.get("turn_on_V"),
            "turn_on_basis": m.get("turn_on_basis"),
            "turn_on_inside_window": m.get("turn_on_inside_window"),
            # 창 안에 있다 != 전이를 다 봤다. off floor 를 못 봤으면 SS 도
            # turn-on 도 창 끝에 눌린 값이라 다음 창의 근거가 못 된다.
            "transition_captured": m.get("transition_captured"),
            # 못 찾았을 때 어느 쪽으로 넓힐지의 근거
            "turn_on_side": m.get("turn_on_side"),
            "id_edge_trend_dec_per_V": m.get("id_edge_trend_dec_per_V"),
            "id_state": m.get("id_state"),
            # 이동도는 gm 꼭대기를 넘겨야 값이 된다. 끝에 걸렸으면 하한이고,
            # 다음 소자에서 켜지는 쪽 끝을 늘릴 근거가 된다.
            "mobility_cm2_Vs": m.get("mobility_cm2_Vs"),
            "mobility_is_lower_bound": m.get("mobility_is_lower_bound"),
            "gm_peak_vg": m.get("gm_peak_vg"),
            "gm_peak_at_edge": m.get("gm_peak_at_edge"),
            "is_flat": m.get("is_flat"),
            "id_norm": m.get("id_norm"),
            "ig_max": m.get("ig_max"),
        }


class Session:
    def __init__(self, executor: Executor, bounds: Bounds, store: Store,
                 policy: Optional[Policy] = None,
                 config: Optional[SessionConfig] = None,
                 stack: Optional[dict] = None):
        self.ex = executor
        self.bounds = bounds
        self.store = store
        self.policy = policy or HeuristicPolicy()
        self.cfg = config or SessionConfig()
        self.prior_devices: List[dict] = []
        self._stack = stack
        # 직전 소자에서 실제로 쓴(=검증을 통과한) 조건. 다음 소자의 base 가 된다.
        # 여기에 판단은 없다 — 판단은 전부 에이전트가 하고, 이건 그 patch 가
        # 얹힐 바닥일 뿐이다.
        self._last_plan: Optional[IVPlan] = None

    # --- 문맥 -------------------------------------------------------------
    def _ctx(self, site: Site, seed: IVPlan) -> dict:
        g = self.bounds.geometry
        dev = f"site={site.name} (x={site.x:g}, y={site.y:g} µm)"
        if self.bounds.wl_ratio:
            dev += f", W={g.get('W_um')} µm / L={g.get('L_um')} µm"
        else:
            # W/L 을 모르면 정전류 Vth 의 기준 전류가 어긋난다(W/L=1 가정).
            # 이걸 안 알려주면 에이전트가 vth_cc 를 그대로 믿고 창을 잡는다.
            dev += ("\n**이 소자의 W/L 을 모른다.** 그래서 정전류 Vth(vth_cc)는 "
                    "W/L=1 을 가정한 값이라 절대값을 믿으면 안 된다. 대신 "
                    "turn_on_V(= vth_onset, 측정된 floor 에서 3 decade 위 — W 와 "
                    "무관)를 창의 기준으로 써라. 이동도와 id_norm 도 못 나온다. "
                    "SS·히스테리시스·on/off 비는 W 와 무관하므로 그대로 유효하다.")
        # 묶인 게이트는 곡선 해석에 직접 영향을 준다 — 덮개가 채널 일부만이면
        # 구간마다 게이트 결합이 달라 이중 turn-on(어깨)이 생길 수 있다.
        # 그걸 모르면 계면 트랩으로 오독한다.
        for node, d in (self.bounds.derived or {}).items():
            tied = d.get("tied_with")
            if not tied:
                continue
            dev += (f"\n{node} 는 {', '.join(tied)} 와 한 패드로 묶여 있다"
                    f"(전기적으로 같은 노드). 게이트 전압이 두 유전막에 동시에 걸린다.")
            if g.get("TG_len_um") and g.get("L_um"):
                dev += (f" TG 는 채널 {g['L_um']:g} µm 중 {g['TG_len_um']:g} µm 만"
                        f" 덮고 offset {g.get('TG_offset_um', 0):g} µm 이라,"
                        f" [BG만] 과 [BG+TG] 두 구간이 직렬이고 게이트 결합이 달라"
                        f" 서로 다른 Vg 에서 켜진다. 곡선의 이중 turn-on(어깨)은"
                        f" 계면 트랩보다 이 구조가 먼저 의심된다.")
        # 설계자료에서 온 기대값(Vth·SS·Ion 등)은 **프롬프트에 넣지 않는다.**
        #   · 이 웨이퍼에서 계통적으로 틀렸다 — 실측 Vth 1.05~1.27 vs 자료 1.55,
        #     SS 0.38~0.52 vs 0.270. 네 소자 모두 같은 방향이라 산포가 아니다.
        #   · W 스플릿(9/15/21/24)에서는 자료에 없는 값을 W 비로 환산한 유추다.
        #   · 넣어두면 에이전트가 매 판단에서 그 숫자를 기준점으로 삼는다
        #     ("Vth 1.55 아래로 SS 0.27 × 7 decade …"). 틀린 앵커가 된다.
        # 근거는 실측만 준다 — 첫 소자는 경계 안에서 훑고, 이후는 prior_devices.
        # (기대값은 여전히 seed.py 의 결정론적 기준안을 만드는 데는 쓰인다.
        #  거기서는 '출발점'일 뿐이고 에이전트가 관측으로 덮어쓸 수 있다.)
        return {
            "objective": self.cfg.objective,
            "bounds_text": self.bounds.describe(),
            "seed_text": seed.describe(),
            "seed_plan": seed,
            "device_text": dev,
            "terminals_text": ", ".join(sorted(self.ex.cfg.roles)),
            "prior_devices": self.prior_devices or None,
            "ig_abort_A": self.bounds.ig_abort_A,
            "fixed_text": (
                f"스윕 방향 = {self.cfg.force_direction} "
                + ("(왕복이라 실측 점 수가 제안한 점 수의 2배가 된다. "
                   "스트레스를 논할 때 이 점을 감안해라)"
                   if self.cfg.force_direction == "double" else "")
                if self.cfg.force_direction else ""),
        }

    def _baseline_for(self, base: IVPlan) -> IVPlan:
        """이번 소자의 **출발점**. 여기서는 아무것도 판단하지 않는다.

        창을 어디로 옮길지, 어느 쪽으로 얼마나 넓힐지, gm 꼭대기를 넘길지,
        turn-on 위로 어디까지 갈지 — 전부 **에이전트가 정한다.** 근거는
        prior_devices 로 넘어간다 (turn_on_V, turn_on_side, id_state,
        transition_captured, gm_peak_at_edge, mobility_is_lower_bound …).

        그 판단을 여기 코드로 옮겨 적으면 두 가지가 망가진다. 첫째, 규칙이
        두 곳에 생겨 서로 다른 답을 낸다. 둘째, 그 규칙이 이 웨이퍼에 맞춰
        굳는다 — 보편적으로 쓸 수 없게 된다.

        그래서 여기가 하는 일은 하나뿐이다: **직전 소자에서 실제로 쓴 조건을
        다음 소자의 base 로 넘긴다.** 그래야 에이전트의 patch 가 '무엇에 대한
        변경'인지 분명해지고, 에이전트를 못 부르는 상황(API 실패)에서도
        마지막으로 검증된 조건이 남는다. 스스로 창을 옮기지는 않는다 —
        근거를 보고 움직이는 것은 그 자체가 판단이기 때문이다.
        """
        return self._last_plan or base

    def _seed_for(self, site: Site, base: IVPlan) -> IVPlan:
        """이 소자의 첫(그리고 보통 유일한) 조건을 정한다.

        앞 소자들의 결과(prior_devices)가 ctx 에 들어가므로, 두 번째 소자부터는
        '방금 본 것'을 근거로 범위를 좁힐 수 있다. 실패하면 base 가 그대로 남는다.
        """
        base = self._baseline_for(base)
        if base is not None and self.prior_devices:
            print(f"  [기준안/{site.name}] {base.describe()}")
        plan = base
        if self.cfg.adapt_seed_per_site and hasattr(self.policy, "propose_seed"):
            # 응답까지 수십 초 걸린다. 아무것도 안 찍으면 멈춘 것처럼 보인다.
            print(f"  [시드/{site.name}] 에이전트에게 조건을 묻는 중… "
                  f"(응답 없으면 {getattr(self.policy, 'timeout_s', '?')}s 뒤 "
                  f"기준안으로 진행)", flush=True)
            t0 = time.time()
            try:
                prop = self.policy.propose_seed(self._ctx(site, base))
            except Exception as e:
                print(f"  [시드/{site.name}] 호출 실패 → 기준안 "
                      f"({type(e).__name__}: {e})")
                prop = None
            if prop is not None:
                if prop.status == "propose" and prop.plan is not None:
                    errs = [str(v) for v in validate(
                        prop.plan, self.bounds, terminals=self.ex.cfg.roles.keys())
                        if v.severity == "error"]
                    if errs:
                        print(f"  [시드/{site.name}] 제안이 경계 위반 → 기준안: "
                              + "; ".join(errs))
                    else:
                        plan = prop.plan
                        print(f"  [시드/{site.name}] {plan.describe()}  "
                              f"({time.time() - t0:.0f}s)")
                        print(f"      이유: {prop.reason}")
                else:
                    print(f"  [시드/{site.name}] {prop.status} — {prop.reason} "
                          f"→ 기준안")

        # 사람이 못 박은 방향은 에이전트 판단보다 우선한다.
        if self.cfg.force_direction and plan.var1.direction != self.cfg.force_direction:
            plan = with_axis(plan, direction=self.cfg.force_direction)
            print(f"      (스윕 방향은 {self.cfg.force_direction} 로 고정됨)")
        return plan

    # --- 한 소자 ----------------------------------------------------------
    def run_site(self, site: Site, seed: IVPlan, *,
                 fixed_plan: Optional[IVPlan] = None) -> SiteResult:
        """한 소자를 끝까지 돈다.

        fixed_plan 을 주면 에이전트를 부르지 않고 그 조건 한 번만 측정한다
        (캘리브레이션이 끝난 area 의 나머지 소자용).
        """
        res = SiteResult(site=site)
        # 확정 plan 이 있으면 그대로. 아니면 이 소자의 조건을 정한다(앞 소자
        # 결과가 근거로 들어간다 — max_iters=1 에서는 이게 유일한 조정 기회다).
        plan: Optional[IVPlan] = fixed_plan or self._seed_for(site, seed)
        ctx = self._ctx(site, plan)
        history: List[dict] = []
        retries = 0

        for it in range(self.cfg.max_iters):
            # --- 안전 검사 (executor 를 부르기 직전) ------------------------
            errs = [str(v) for v in validate(plan, self.bounds,
                                             terminals=self.ex.cfg.roles.keys())
                    if v.severity == "error"]
            if errs:
                if fixed_plan is not None or retries >= self.cfg.max_retry_on_violation:
                    res.status, res.error = "out_of_bounds", "; ".join(errs)
                    break
                retries += 1
                history.append({"iteration": it, "plan": plan.describe(),
                                "plan_obj": plan, "payload": {}, "violations": errs})
                prop = self._ask(ctx, history)
                if prop.status != "propose" or prop.plan is None:
                    res.status, res.error = prop.status, prop.reason
                    break
                plan = prop.plan
                continue

            # --- 측정 -------------------------------------------------------
            t0 = time.time()
            try:
                df = self.ex.run(site, plan) if it == 0 else self.ex.measure(plan)
            except Exception as e:
                res.status, res.error = "error", f"{type(e).__name__}: {e}"
                break
            elapsed = time.time() - t0

            m = summarize(df, plan, bounds=self.bounds)
            payload = self._payload(m, df, plan)
            res.metrics, res.final_plan, res.iterations = m, plan, it + 1
            # 실제로 측정에 쓴 조건. 다음 소자의 base 가 된다(판단 없음).
            self._last_plan = plan

            # --- 다음 행동 --------------------------------------------------
            if fixed_plan is not None:
                prop = Proposal("converged", "확정 plan 적용", 1.0, source="seed")
            else:
                history.append({"iteration": it, "plan": plan.describe(),
                                "plan_obj": plan, "payload": payload})
                prop = self._ask(ctx, history)
                history[-1]["proposal"] = prop.to_dict()

            self.store.save(site=site, plan=plan, df=df, metrics=m, iteration=it,
                            proposal=prop.to_dict(), elapsed_s=elapsed,
                            extra={"llm_payload": payload})

            res.status = prop.status
            if prop.status != "propose" or prop.plan is None:
                break
            plan = prop.plan
        else:
            # 측정 횟수 상한에 걸려 끝났다. max_iters=1(소자당 1회)이면 흔한
            # 결말이고 실패가 아니다 — 다만 에이전트가 무엇을 더 원했는지는
            # 남겨둔다. 그게 '한 번으로 충분했나'를 나중에 판단할 근거다.
            res.status = "max_iters"
            want = locals().get("prop")
            if want is not None and want.status == "propose":
                res.error = f"에이전트는 추가 측정을 원했다: {want.reason}"

        res.history = history
        return res

    def _payload(self, m: dict, df, plan: IVPlan) -> dict:
        """관측을 LLM 에 넘길 형태로.

        **원본 CSV 는 절대 안 나간다.** 지표(코드가 계산) + 20~30점 다운샘플
        곡선만 간다. 원본은 store 가 결과 폴더에 data.csv 로 그대로 저장하므로
        사람은 언제든 볼 수 있고, 잃는 것이 없다.

        원본을 보내는 경로가 있었는데 없앴다. 실측으로 페이로드가 105,479 자
        (≈30k 토큰)였고 이력이 턴마다 쌓여 요청 하나가 150k 토큰이 됐다.
        반면 다운샘플은 903 자(≈258 토큰)다. 400배를 더 내고 얻는 것이,
        LLM 이 긴 배열을 눈대중으로 읽는 것뿐이다 — 지표는 어차피 코드가 낸다.
        """
        return to_llm_payload(
            m, downsample_curve(df, plan, self.cfg.curve_points))

    def _ask(self, ctx: dict, history: List[dict]) -> Proposal:
        # 호출이 실패해도 실행 전체를 죽이지 않는다. 소자는 이미 접촉되어
        # 스트레스를 받았고 다른 소자들이 남아 있다. 이 소자만 사람에게 넘기고
        # 나머지는 계속 간다.
        try:
            prop = self.policy.propose(ctx, history)
        except Exception as e:
            return Proposal("needs_human",
                            f"에이전트 호출 실패: {type(e).__name__}: {e}",
                            0.0, source="error")
        if prop.status == "propose" and prop.confidence < self.cfg.min_confidence:
            return Proposal("needs_human",
                            f"확신도 {prop.confidence:.2f} < {self.cfg.min_confidence}"
                            f" (원래 이유: {prop.reason})",
                            prop.confidence, source=prop.source)
        return prop

    # --- area -------------------------------------------------------------
    def run_area(self, sites: List[Site], seed: IVPlan) -> List[SiteResult]:
        """area 하나. 앞의 `calibration_sites` 개만 탐색하고, 거기서 확정된
        plan 을 나머지에 그대로 적용한다.

        **탐색 소자 수는 상한이다.** 예전에는

            calibrating = i < calibration_sites or locked is None

        이라 확정 plan 을 못 얻으면 *모든* 소자가 계속 에이전트를 불렀다.
        16 소자면 4회로 끝날 실행이 32회가 됐다(실측). 상한을 지키는 편이
        비용만이 아니라 데이터에도 낫다 — 조건이 안 정해진 채로 남은 소자를
        계속 찍으면 서로 비교할 수 없는 곡선만 쌓인다.

        그래서 탐색 소자를 다 쓰고도 수렴하지 못하면 **멈춘다.** 남은 소자는
        건드리지 않는다(측정되지 않은 소자는 스트레스도 안 받는다). 사람이
        조건을 보고 다시 정하는 편이 낫다는 뜻이고, 그 판단은 사람 몫이다.

        `calibration_sites <= 0` 은 '탐색하지 않는다' 로 읽는다 — 시드를
        그대로 확정 plan 으로 쓰고 에이전트를 한 번도 부르지 않는다.
        """
        results: List[SiteResult] = []
        n_cal = max(int(self.cfg.calibration_sites), 0)
        locked: Optional[IVPlan] = seed if n_cal == 0 else None
        if n_cal == 0:
            print("탐색 소자 0개 — 시드 조건을 그대로 적용한다 (에이전트 호출 없음)")

        for i, site in enumerate(sites):
            calibrating = i < n_cal
            if not calibrating and locked is None:
                print(f"\n[수렴 실패] 탐색 소자 {n_cal}개를 다 썼는데 확정된 조건이 "
                      f"없다. 남은 {len(sites) - i}개는 측정하지 않고 멈춘다.\n"
                      f"  조건을 보고 다시 정할 것 — 범위를 넓히거나, 스택의 "
                      f"안전 경계/기하를 확인하거나, 조건을 직접 지정한다.")
                break

            r = self.run_site(site, seed, fixed_plan=None if calibrating else locked)
            results.append(r)
            print(f"[{site.area}/{site.name}] {r.status} "
                  f"({r.iterations} iter)"
                  + (f" — {r.error}" if r.error else ""))

            if calibrating:
                self.prior_devices.append(r.summary_row())
                if r.status == "converged" and r.final_plan is not None:
                    locked = r.final_plan
        if locked is not None and n_cal:
            print(f"[{sites[0].area if sites else '?'}] 확정 plan: {locked.describe()}")
        return results
