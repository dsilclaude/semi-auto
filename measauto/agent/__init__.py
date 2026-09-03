"""agent — 관측을 보고 다음 plan 을 정하는 층.

불변 규칙: 여기서는 drivers 를 import 하지 않는다.
장비 없이 과거 데이터로 리플레이할 수 있어야 하고, 그게 이 층의 유일한
검증 수단이기 때문이다.

에이전트가 만드는 건 값(patch)이고, 코드는 고정이다. 에이전트는 safety 를
수정할 수 없고 validator 의 존재도 모른다 — 검사받는 쪽이 검사를 고쳐 쓰는
구조가 되면 안 되니까.
"""

from .schema import PlanPatch, PROPOSAL_SCHEMA, STATUSES, apply_patch
from .policy import Proposal, Policy, LLMPolicy, HeuristicPolicy

__all__ = [
    "PlanPatch", "PROPOSAL_SCHEMA", "STATUSES", "apply_patch",
    "Proposal", "Policy", "LLMPolicy", "HeuristicPolicy",
]
