"""measauto — 자동측정 에이전트 시스템.

설계 원칙: 바뀌는 건 값, 안 바뀌는 건 코드.
모듈 경계는 '기능 종류'가 아니라 '변경 주기'로 가른다.

    층                          변경 주기    형태
    ------------------------    ---------    -----
    drivers/  (VISA/SCPI)       거의 없음    코드
    executor  (측정 primitive)  가끔         코드
    plan      (측정 조건)       매번         값
    stacks/   (소자 스택)       웨이퍼마다   값
    store/metrics (저장·분석)   독립         코드

불변 규칙 둘
  1. drivers 는 이 패키지의 어떤 것도 import 하지 않는다.
  2. agent 는 drivers 를 import 하지 않는다 → 장비 없이 과거 데이터로 리플레이 가능.

검증(validate)은 executor 밖에 있다. executor 는 "이미 안전하다"고 가정하고
실행만 하고, 검증은 session 이 executor 를 부르기 전에 건다.
"""

from .plan import IVPlan, SweepAxis, Timing, Adc, RangeSpec, transfer, output
from .safety import Bounds, bounds_from_stack, load_stack, validate, assert_valid
from . import metrics

__all__ = [
    "IVPlan", "SweepAxis", "Timing", "Adc", "RangeSpec", "transfer", "output",
    "Bounds", "bounds_from_stack", "load_stack", "validate", "assert_valid",
    "metrics",
]

__version__ = "0.1.0"
