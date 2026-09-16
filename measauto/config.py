"""config.py — 이 장비 셋업만의 사실. 배선이 바뀔 때만 고친다.

'단자 이름 ↔ SMU 채널' 매핑이 사는 곳. plan/metrics/agent 는 단자 이름만 알고,
드라이버는 채널 번호만 안다. 그 사이를 여기서 한 번 이어준다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(frozen=True)
class HardwareConfig:
    # --- VISA 구현체 ------------------------------------------------------
    # 이 PC 에는 NI-VISA 와 Keysight VISA 가 둘 다 깔려 있다. 인자 없이 부르는
    # pyvisa.ResourceManager() 는 공용 visa32.dll(IVI 라우터)를 타고 Keysight
    # VISA 로 붙는데, Keysight 82357B 어댑터가 빠져 있어서(CM_PROB_PHANTOM)
    # GPIB 장비를 못 찾고 VI_ERROR_RSRC_NFOUND 가 난다. 실제로 꽂혀 있는 건
    # NI GPIB-USB-HS 이므로 NI-VISA 를 명시한다.
    visa_library: str = r"C:\Windows\System32\nivisa64.dll"

    s300_address: str = "GPIB0::28::INSTR"
    b1500_address: str = "GPIB0::17::INSTR"
    chuck_id: int = 2

    # --- 단자 ↔ SMU 채널 --------------------------------------------------
    # [주의] smuN 은 '장착 슬롯 번호'가 아니다. 실측 확인(b1500.smuN.channel):
    #   smu1 -> 본체 슬롯 2 (B1517A HRSMU)
    #   smu2 -> 본체 슬롯 3 (B1517A HRSMU)
    #   smu3 -> 본체 슬롯 4 (B1517A HRSMU)
    #   smu4 -> 없음 (슬롯 1 이 B1530A WGFMU 라 SMU 번호가 한 칸 밀림)
    # → 지금 셋업은 SMU 3개. dual gate 를 다 물리려면 4개가 필요하므로,
    #   TG 나 BG 중 하나를 빼고 측정하거나 모듈을 추가해야 한다.
    #   roles 에 없는 단자를 plan 이 건드리면 executor 가 막는다.
    roles: Dict[str, int] = field(default_factory=lambda: {
        "BG": 1,   # Gate 프로브
        "D": 2,    # Drain 프로브
        "S": 3,    # Source 프로브
    })

    # --- 팁 보호 ----------------------------------------------------------
    slow_contact: bool = True
    z_step_um: int = 25       # 한 스텝에 올릴 높이 (작을수록 느림)
    z_pause_s: float = 0.1    # 스텝 사이 대기
    z_final_um: int = 50      # 마지막 이 구간은 contact() 에 맡김
    settle_s: float = 0.2     # 접촉 후 안정화 대기

    b1500_timeout_ms: int = 300000
    s300_timeout_ms: int = 30000

    # 버스에 뭐가 있는지 훑는 데 이보다 오래 걸리면 버스가 물린 것으로 본다.
    # 정상이면 1초 안에 끝난다. 이 값이 필요한 이유는 executor.list_visa_resources
    # 의 주석에 있다 — 한 번 매달리면 프로세스를 죽여도 안 풀린다.
    bus_scan_timeout_s: float = 20.0

    def role_of(self, channel: int) -> Optional[str]:
        for t, ch in self.roles.items():
            if ch == channel:
                return t
        return None

    def require(self, terminals) -> None:
        missing = [t for t in terminals if t not in self.roles]
        if missing:
            raise KeyError(
                f"이 셋업에 배선되지 않은 단자: {missing}. "
                f"roles={self.roles}. config 를 고치거나 plan 을 바꿀 것.")


# 현재 셋업 기본값. 스크립트에서 replace() 로 갈아끼우면 된다.
DEFAULT = HardwareConfig()


# ---------------------------------------------------------------------------
# dual gate 셋업 예시 (SMU 4개일 때)
# ---------------------------------------------------------------------------
DUAL_GATE = HardwareConfig(roles={"BG": 1, "D": 2, "S": 3, "TG": 4})
