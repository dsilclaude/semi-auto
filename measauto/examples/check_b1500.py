"""examples/check_b1500.py — 장비를 열기 전에 버스부터 본다. 프로버는 안 건드린다.

    python -m measauto.examples.check_b1500
    python -m measauto.examples.check_b1500 --timeout 15   # 느린 장비면

측정을 시작하기 전에 확인할 것들을, **매달리지 않고** 확인한다.

  1단계  VISA 자원 목록      장비를 열지 않는다. 정상이면 1초.
  2단계  B1500 열기           1단계가 통과했을 때만.
         IDN · 모듈 구성 · CL(전 채널 출력 OFF)

⚠️ 진단은 빨리 실패해야 정보가 된다. 그래서 측정용 타임아웃(300초)을 쓰지
   않고 기본 5초로 연다. 실제 측정은 점 수에 맞춰 따로 계산한다
   (drivers/b1500.expected_seconds).

⚠️ 버스가 물려 있으면 VISA 호출이 드라이버 안에서 막힌다 — Ctrl+C 도 안 먹고
   프로세스를 죽여도 안 풀린다(실측 2026-09-10). 1단계에 시간 제한을 둔 이유가
   그것이다. 걸리면 어댑터 USB 를 재연결하라는 안내가 나온다.

프로버(S300)는 열지 않으므로 팁은 그대로다. 실패해도 잃을 것이 없다 —
그래서 실장비 첫 실행 때 이걸 먼저 돌린다.
"""

from __future__ import annotations

import argparse

from ..config import DEFAULT
from ..executor import BusTimeout, list_visa_resources


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=5.0,
                    help="장비를 여는 데 쓸 타임아웃 [초]. 기본 5 (진단용)")
    ap.add_argument("--bus-timeout", type=float, default=DEFAULT.bus_scan_timeout_s,
                    help="버스 조회 제한 시간 [초]. 넘으면 버스가 물린 것으로 본다")
    args = ap.parse_args()

    print(f"주소      {DEFAULT.b1500_address}")
    print(f"VISA      {DEFAULT.visa_library}")

    # --- 1단계: 버스 (장비를 열지 않는다) ----------------------------------
    print(f"\n[1/2] 버스 조회… (최대 {args.bus_timeout:g}s)")
    try:
        found = list_visa_resources(DEFAULT.visa_library, timeout_s=args.bus_timeout)
    except BusTimeout as e:
        raise SystemExit(f"\n{e}\n")
    except ConnectionError as e:
        raise SystemExit(f"\n{e}\n")

    print(f"      보이는 자원: {found or '(없음)'}")
    if DEFAULT.b1500_address not in found:
        raise SystemExit(
            f"\n[B1500 이 버스에 없다] {DEFAULT.b1500_address}\n"
            "\n"
            "  GPIB0 이 통째로 안 보이면 → 어댑터가 드라이버에서 빠진 것이다.\n"
            "    NI GPIB-USB-HS 의 USB 를 뽑았다 5초 뒤 다시 꽂을 것.\n"
            "  주소만 안 보이면 → B1500 의 전원 · GPIB 케이블 · 주소를 확인할 것.\n"
            "\n"
            "  장비를 연 적이 없으므로 팁은 안전하다.\n")

    # --- 2단계: 열기 -------------------------------------------------------
    from ..drivers import B1500

    print(f"\n[2/2] 여는 중… (타임아웃 {args.timeout:g}s)")
    print("      여기서 막히면 다른 프로그램(EasyEXPERT 등)이 세션을 잡고 있다 —\n"
          "      GPIB 장비는 한 번에 한 세션만 받는다.")
    b = B1500(DEFAULT.b1500_address, DEFAULT.visa_library,
              timeout_ms=int(args.timeout * 1000)).connect()
    try:
        print("\nIDN      ", b.idn())
        print("모듈     ", b.modules())
        print("상태     ", b.check())

        # connect 가 이미 CL 을 보냈다. 한 번 더 보내 예외 없이 도는지 본다 —
        # 이 명령이 안 먹으면 측정 뒤에 소자에 전압이 남는다.
        b.outputs_off()
        print("\nCL(전 채널 출력 OFF) 전송 성공")
    finally:
        b.close()          # 여기서도 CL 을 보낸다
    print("닫힘. 프로버는 건드리지 않았다.")

    print(f"\nconfig.roles = {dict(DEFAULT.roles)}")
    print("  위 '모듈' 목록과 맞는지 확인할 것. smuN 은 슬롯 번호가 아니다 —")
    print("  슬롯 1 이 WGFMU 라 SMU 번호가 한 칸 밀려 있다(config.py 주석).")


if __name__ == "__main__":
    main()
