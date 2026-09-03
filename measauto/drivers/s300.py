"""drivers/s300.py — Cascade S300 프로버(Nucleus). GPIB 문법을 아는 유일한 곳.

코드가 대체하지 않는 준비 단계 (Nucleus UI 에서 사람이 먼저 끝내야 함)
  1. 척 로드 / 진공 ON / 소자 세팅
  2. Alignment (2-point align) — 안 하면 좌표가 통째로 어긋난다
  3. Tipping / Set Contact — 팁 contact 높이를 잡아둠
  4. 첫 소자에 팁을 직접 contact 시킨 상태로 둠 → set_reference() 로 등록

축 방향(실측 확인): z 증가 = 척 위(팁 쪽) / z 감소 = 척 아래(팁에서 멀어짐).
"""

from __future__ import annotations

import time
from typing import Tuple


class S300Error(RuntimeError):
    pass


class CascadeS300:
    def __init__(self, address: str, visa_library: str = "",
                 chuck_id: int = 2, timeout_ms: int = 30000):
        # 매뉴얼 p.37: 2 = Elite/Summit/S300/Alessi 의 척 제어 device ID (축 번호 아님)
        self.address = address
        self.visa_library = visa_library
        self.chuck_id = chuck_id
        self.timeout_ms = timeout_ms
        self.rm = None
        self.inst = None

    # --- 연결 -------------------------------------------------------------
    def connect(self):
        import pyvisa
        self.rm = pyvisa.ResourceManager(self.visa_library) if self.visa_library \
            else pyvisa.ResourceManager()
        self.inst = self.rm.open_resource(self.address)
        self.inst.timeout = self.timeout_ms   # 이동 완료(COMPLETE)까지 대기
        return self

    def close(self):
        try:
            if self.inst is not None:
                self.inst.close()
        except Exception as e:
            print(f"[s300] close 중 예외(무시 가능): {e}")
        finally:
            self.inst = None

    # --- 저수준 통신 -------------------------------------------------------
    def _write(self, command: str) -> None:
        """메타 명령($:...) 등 '응답 없는' 명령 전용."""
        self.inst.write(command)

    def ask(self, command: str) -> str:
        """질의(? 명령) 또는 resp-on 상태의 명령. 응답 문자열 반환.
        $:set:resp on 상태에서 → 명령은 'COMPLETE', 질의는 값, 실패는 '@에러'."""
        try:
            return self.inst.query(command).strip()
        except Exception as e:
            return f"Error: {e}"

    def cmd(self, command: str) -> str:
        """action/설정 명령. resp on 덕에 완료되면 'COMPLETE' 반환(=완료 대기).
        응답이 '@' 로 시작하면 프로버 에러 → 예외."""
        resp = self.ask(command)
        if resp.startswith("@"):
            raise S300Error(f"S300 명령 실패: {command!r} -> {resp!r}")
        return resp

    def setup(self) -> str:
        """매뉴얼 정식 원격 셋업 (Nucleus4 가이드 p.9). 순서 중요.

        $:set:resp on 을 먼저 켜야 이후 명령이 'COMPLETE' 응답을 줘서 타임아웃
        없이 완료를 확인할 수 있다. 이게 빠지면 모든 action 명령이 타임아웃.
        ※ 이 프로버 펌웨어는 :set: 명령에 device ID 를 요구한다."""
        self._write("$:set:mode summit")
        self._write("$:set:resp on")
        self.cmd(":SYST:OPER:MODE REMOTE")
        self.cmd(f":set:unit {self.chuck_id} metric")   # 단위 = micron
        return "OK (mode=summit, resp=on, REMOTE, metric)"

    def set_local(self) -> str:
        """원격모드 해제. 케이블 뽑기/세션 종료 전에. 척을 움직이지 않는다."""
        return self.ask(":SYST:OPER:MODE LOCAL")

    def check(self) -> dict:
        """통신/준비 상태 확인. 값만 돌려주고 판정은 위층에서."""
        return {
            "idn": self.ask("*IDN?"),
            "interpreter": self.ask("$:set:mode?"),   # SUMMIT/EG = 인터프리터 동작중
            "self_test": self.ask("*tst?"),           # 0 = 정상
            "oper_mode": self.ask(":SYST:OPER:MODE?"),
            "align": self.ask(":align:wafer:busy?"),  # SUCCESS = 수동 alignment 완료
        }

    # --- 위치 -------------------------------------------------------------
    def read_position(self) -> Tuple[float, float, float]:
        """현재 척 좌표 (x, y, z) micron (:mov:abs?, p.62)."""
        resp = self.ask(f":mov:abs? {self.chuck_id}")
        try:
            x, y, z = (float(v) for v in resp.replace(",", " ").split()[:3])
        except ValueError as e:
            raise S300Error(f"위치 파싱 실패: {resp!r}") from e
        return x, y, z

    def set_reference(self) -> Tuple[float, float, float]:
        """사람이 첫 소자에 contact 시킨 현재 위치를 원점(0,0)+contact 높이로 등록.
        현재 XY → 원점 (:set:pres, p.154) / 현재 Z → contact 높이 (:set:cont, p.139)."""
        x, y, z = self.read_position()
        self.cmd(f":set:pres {self.chuck_id} 0 0")
        self.cmd(f":set:cont {self.chuck_id} {int(z)}")
        return x, y, z

    # --- 분리 / 접촉 / 이동 (COMPLETE = 동작 완료. busy 폴링 불필요) --------
    def separate(self) -> str:
        """팁 분리 (:mov:sep, p.89)."""
        return self.cmd(f":mov:sep {self.chuck_id}")

    def contact(self) -> str:
        """팁 접촉 — set 된 contact 높이로 (:mov:cont, p.63).
        그 위로는 안 올라가므로 소자를 찍지 않는다."""
        return self.cmd(f":mov:cont {self.chuck_id}")

    def move_xy(self, x: float, y: float) -> Tuple[float, float, float]:
        """원점 기준 (x,y) micron 이동. z=none → Z 안전높이 자동분리(p.56)라
        이동 중 소자가 안 긁힌다."""
        self.cmd(f":mov:abs {self.chuck_id} {int(x)} {int(y)} none")
        return self.read_position()

    def move_z(self, z: float) -> Tuple[float, float, float]:
        """XY 유지한 채 Z 만 절대이동."""
        x, y, _ = self.read_position()
        self.cmd(f":mov:abs {self.chuck_id} {int(x)} {int(y)} {int(z)}")
        return self.read_position()

    # --- 팁 보호 -----------------------------------------------------------
    def slow_axes(self) -> None:
        """z 만 느리게. xy 는 장비 기본값 유지."""
        self.cmd(f":set:vel {self.chuck_id} z slow")

    def contact_slow(self, z_contact: float, step: int = 25, pause: float = 0.1,
                     final: int = 50) -> str:
        """분리 상태에서 z_contact 까지 step µm 씩 천천히 올린 뒤 마지막만 contact().

        실측 결과 :set:cont:spee 는 :mov:cont 전 구간 속도를 지배하지 않는다
        (25 µm/s 로 설정해도 1000 µm 를 0.28 s ≈ 3500 µm/s 로 이동). 그래서
        분리높이→접촉높이 구간을 :mov:abs 로 잘라 올린다. 마지막 final 구간은
        contact() 에 맡기므로 등록된 접촉 높이 위로는 절대 올라가지 않는다."""
        x, y, z = (int(v) for v in self.read_position())
        z_stop = int(z_contact) - final
        while z < z_stop:
            z = min(z + step, z_stop)
            self.cmd(f":mov:abs {self.chuck_id} {x} {y} {z}")
            time.sleep(pause)
        return self.contact()
