"""python -m measauto.tests.run_all  — pytest 없이 전부 돌린다."""

from __future__ import annotations

import traceback

from . import (test_cost_and_output, test_intake, test_loop, test_metrics,
               test_report, test_safety, test_seed, test_seed_agent,
               test_unknown_width)

MODULES = [test_safety, test_metrics, test_seed, test_seed_agent,
           test_unknown_width, test_intake, test_report, test_loop,
           test_cost_and_output]


def main() -> int:
    passed = failed = 0
    for mod in MODULES:
        print(f"\n--- {mod.__name__} ---")
        for fn in mod.TESTS:
            try:
                fn()
                print(f"  ok   {fn.__name__}")
                passed += 1
            except Exception:
                print(f"  FAIL {fn.__name__}")
                traceback.print_exc()
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
