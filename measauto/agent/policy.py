"""agent/policy.py — 관측 → 다음 행동.

propose(ctx, history) -> Proposal 이 인터페이스의 전부다. 구현이 LLM 이든
규칙이든 session 은 구분하지 않는다.

왜 LLM 인가 ("정상은 규칙, 예외는 LLM" 이 틀린 이유)
  범위 탐색 단계에서는 예외가 예외가 아니다. 첫 스윕의 평평한 선이 죽은
  소자인지, Vth 가 범위 밖인지, 팁이 안 닿았는지는 그 데이터만으로 안 갈라진다.
  범위를 모르면 이 애매함이 기본값이고, 규칙들은 곡선이 이미 보이는 걸
  전제해서 정작 초반엔 계산조차 안 된다.
  그래서 지표는 버리지 않되 '판정 규칙'이 아니라 'LLM 에 보여줄 관측량'으로
  돌린다. 받는 것은 구조화된 status 다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from ..plan import IVPlan
from .prompt import (SEED_SYSTEM_PROMPT, SYSTEM_PROMPT, build_seed_message,
                     build_user_message)
from .schema import PROPOSAL_SCHEMA, PlanPatch, apply_patch

DEFAULT_MODEL = "claude-opus-5"


@dataclass
class Proposal:
    status: str
    reason: str = ""
    confidence: float = 0.0
    plan: Optional[IVPlan] = None       # status="propose" 일 때만 채워진다
    patch: Optional[PlanPatch] = None
    raw: Dict[str, Any] = field(default_factory=dict)
    source: str = ""                    # "llm" | "heuristic" | "seed"

    def to_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason,
                "confidence": self.confidence, "source": self.source,
                "patch": self.raw.get("patch")}


class Policy(Protocol):
    def propose(self, ctx: dict, history: List[dict]) -> Proposal: ...


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
class LLMPolicy:
    """Claude 로 다음 조건을 정한다.

    ctx 는 session 이 만들어 준다:
      objective, bounds_text, seed_plan(IVPlan), seed_text, device_text,
      prior_devices

    재현성 메모: Claude Opus 5 는 temperature/top_p 를 받지 않는다(400).
    "temperature 0 으로 고정" 은 이 모델에서 불가능하므로, 리플레이 평가는
    effort 를 낮게 고정하고 '몇 턴에 수렴 / 경계 몇 번 접촉' 을 여러 번 돌려
    분포로 본다. 같은 소자에서 결론이 갈리면 프롬프트가 부실하다는 신호다.
    """

    def __init__(self, *, model: str = DEFAULT_MODEL, effort: str = "medium",
                 max_tokens: int = 8000, client=None, use_fallbacks: bool = True,
                 timeout_s: float = 180.0, max_retries: int = 1):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.use_fallbacks = use_fallbacks
        # 기본 타임아웃(10분)에 재시도까지 붙으면 아무 출력 없이 아주 오래 멈춰
        # 있는 것처럼 보인다. 그 사이 팁은 소자에 닿아 있고 Ctrl+C 도 소켓
        # 읽기에 막혀 잘 안 먹는다. 측정 루프에서는 '오래 기다리는 것'보다
        # '빨리 실패하고 기준안으로 가는 것'이 낫다 — 폴백이 항상 있으므로.
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import anthropic
            # 자격증명은 환경(ANTHROPIC_API_KEY 또는 `ant auth login` 프로필)에서.
            self._client = anthropic.Anthropic(timeout=self.timeout_s,
                                               max_retries=self.max_retries)
        return self._client

    def propose(self, ctx: dict, history: List[dict]) -> Proposal:
        user = build_user_message(
            objective=ctx["objective"],
            bounds_text=ctx["bounds_text"],
            seed_text=ctx["seed_text"],
            history=history,
            device_text=ctx.get("device_text", ""),
            prior_devices=ctx.get("prior_devices"),
        )
        kwargs = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": PROPOSAL_SCHEMA},
            },
        )
        resp = self._call(kwargs)

        if getattr(resp, "stop_reason", None) == "refusal":
            return Proposal("needs_human", "모델이 응답을 거절했다", 0.0,
                            source="llm")

        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return Proposal("needs_human", f"응답 파싱 실패: {text[:200]}", 0.0,
                            source="llm")
        return self._to_proposal(data, ctx["seed_plan"])

    def propose_seed(self, ctx: dict) -> Proposal:
        """첫 조건. 관측이 없으므로 목적·스펙·경계만 보고 정한다.

        baseline(seed.py 가 계산한 것)에 patch 를 덮어쓰는 형태라, 실패하면
        baseline 이 그대로 남는다 — 결정론적 폴백이 항상 있다.
        """
        user = build_seed_message(
            objective=ctx["objective"],
            bounds_text=ctx["bounds_text"],
            baseline_text=ctx["seed_text"],
            expected_text=ctx.get("expected_text", ""),
            device_text=ctx.get("device_text", ""),
            terminals_text=ctx.get("terminals_text", ""),
            fixed_text=ctx.get("fixed_text", ""),
            prior_devices=ctx.get("prior_devices"),
        )
        kwargs = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": SEED_SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": PROPOSAL_SCHEMA},
            },
        )
        resp = self._call(kwargs)
        if getattr(resp, "stop_reason", None) == "refusal":
            return Proposal("needs_human", "모델이 응답을 거절했다", 0.0, source="llm")
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return Proposal("needs_human", f"응답 파싱 실패: {text[:200]}", 0.0,
                            source="llm")
        return self._to_proposal(data, ctx["seed_plan"])

    def _call(self, kwargs: dict):
        """안전 분류기 거절 시 다른 모델이 이어받게 해 둔다(서버측 fallback).

        SDK/계정이 이 베타를 아직 모르면 조용히 일반 경로로 내려간다."""
        if self.use_fallbacks:
            try:
                return self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default", **kwargs)
            except Exception:
                pass
        return self.client.messages.create(**kwargs)

    @staticmethod
    def _to_proposal(data: dict, seed: IVPlan) -> Proposal:
        status = data.get("status", "needs_human")
        reason = data.get("reason", "")
        conf = float(data.get("confidence") or 0.0)
        patch = PlanPatch.from_dict(data.get("patch"))
        plan = None
        if status == "propose":
            if patch is None:
                return Proposal("needs_human", "propose 인데 patch 가 비었다",
                                conf, raw=data, source="llm")
            plan = apply_patch(seed, patch, note=reason)
        return Proposal(status, reason, conf, plan=plan, patch=patch,
                        raw=data, source="llm")


# ---------------------------------------------------------------------------
# 규칙 기반 (장비 없는 드라이런 / API 없는 환경용)
# ---------------------------------------------------------------------------
class HeuristicPolicy:
    """LLM 없이 돌려보기 위한 최소 구현.

    이건 '판정 규칙이 낫다'는 주장이 아니다. 배선 점검이나 스모크 테스트처럼
    LLM 을 부를 이유가 없는 상황에서 파이프라인을 끝까지 돌려보기 위한 것이고,
    실제로 애매한 케이스를 잘 못 가른다 — 그게 LLM 을 쓰는 이유였다.
    """

    def __init__(self, *, max_expand: int = 3, expand: float = 1.5,
                 target_decades: float = 4.0):
        self.max_expand = max_expand
        self.expand = expand
        self.target_decades = target_decades

    def propose_seed(self, ctx: dict) -> Proposal:
        """규칙 기반은 baseline 을 그대로 쓴다 — 목적을 읽을 수 없기 때문이다.
        어떤 범위가 필요한지는 목적에 달렸고, 그건 문장이라 규칙으로 못 가른다."""
        return Proposal("propose", "baseline 그대로 (규칙 기반은 목적을 못 읽는다)",
                        0.5, plan=ctx["seed_plan"], source="heuristic")

    def propose(self, ctx: dict, history: List[dict]) -> Proposal:
        if not history:
            return Proposal("propose", "시드 조건", 0.5,
                            plan=ctx["seed_plan"], source="heuristic")

        last = history[-1]
        m = last["payload"].get("metrics", {})
        seed: IVPlan = ctx["seed_plan"]
        prev: IVPlan = last.get("plan_obj", seed)

        ig = m.get("ig_max") or 0.0
        ig_abort = ctx.get("ig_abort_A", 1e-9)
        if ig >= ig_abort:
            return Proposal("leaky", f"Ig {ig:g} A ≥ abort {ig_abort:g} A", 0.8,
                            source="heuristic")

        if m.get("kind") == "output":
            # output 은 범위 탐색 대상이 아니다(Vd 범위는 사람이 정한다).
            # 규칙으로 더 할 말이 없으니 여기서 멈춘다.
            return Proposal("converged",
                            f"출력특성 확보 (saturation_ratio="
                            f"{m.get('saturation_ratio')})", 0.6,
                            source="heuristic")

        decades = m.get("decades")
        if decades is not None and decades >= self.target_decades \
                and m.get("turn_on_inside_window"):
            return Proposal("converged",
                            f"{decades:.1f} decade, turn-on 이 창 안에 있다", 0.7,
                            source="heuristic")

        n_expand = sum(1 for h in history if h.get("expanded"))
        if n_expand >= self.max_expand:
            return Proposal("needs_human",
                            f"{n_expand}회 확장했는데도 곡선이 안 잡힌다", 0.4,
                            source="heuristic")

        # 곱셈 확장: 중심 유지하고 폭만 ×expand
        lo, hi = prev.var1.span
        mid, half = (lo + hi) / 2, (hi - lo) / 2 * self.expand
        patch = PlanPatch(var1_start=mid - half, var1_stop=mid + half)
        plan = apply_patch(seed, patch, note=f"곡선 미확보 → 범위 ×{self.expand}")
        return Proposal("propose", f"decades={decades} → 범위를 ×{self.expand} 확장",
                        0.5, plan=plan, patch=patch, source="heuristic")


def default_policy() -> Policy:
    """API 자격증명이 보이면 LLM, 아니면 규칙 기반."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return LLMPolicy()
    try:
        import anthropic  # noqa: F401
        return LLMPolicy()      # `ant auth login` 프로필도 SDK 가 알아서 찾는다
    except ImportError:
        return HeuristicPolicy()
