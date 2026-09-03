"""examples/report.py — 저장된 측정에서 '왜 그렇게 판단했는지' 리포트를 뽑는다.

    python -m measauto.examples.report                       가장 최근 실행
    python -m measauto.examples.report --run 260819_145756   특정 실행
    python -m measauto.examples.report --md report.md        파일로 저장
    python -m measauto.examples.report --llm                 서술형 분석까지 (API)

표 부분은 저장된 기록만으로 만들어진다 — 장비도 API 도 필요 없고, 기록이 있는 한
언제든 다시 뽑을 수 있다. --llm 을 줄 때만 에이전트가 서술 분석을 덧붙인다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..report import analyze, load_run, render


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results_agent", help="결과 폴더")
    ap.add_argument("--run", help="실행 id (기본: 가장 최근)")
    ap.add_argument("--md", help="마크다운으로 저장할 경로")
    ap.add_argument("--llm", action="store_true", help="서술형 분석 추가 (API 호출)")
    ap.add_argument("--effort", default="high")
    args = ap.parse_args()

    recs = load_run(args.out, args.run)
    text = render(recs)

    if args.llm:
        try:
            text += "\n\n---\n\n## 분석\n\n" + analyze(recs, effort=args.effort)
        except ImportError:
            print("[report] anthropic 패키지가 없어 분석을 건너뛴다")
        except Exception as e:
            print(f"[report] 분석 실패 ({type(e).__name__}: {e}) — 표만 출력한다")

    if args.md:
        Path(args.md).write_text(text, encoding="utf-8")
        print(f"저장: {Path(args.md).resolve()}")
    else:
        print(text)


if __name__ == "__main__":
    main()
