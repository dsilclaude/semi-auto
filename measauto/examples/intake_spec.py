"""examples/intake_spec.py — 가진 자료를 그대로 넣으면 stack 을 만들어 준다.

    python -m measauto.examples.intake_spec --file 자료.txt
    python -m measauto.examples.intake_spec --file 자료.txt --hint "W=6um 소자만"
    python -m measauto.examples.intake_spec --file 자료.txt --write

정해진 칸을 채울 필요가 없다. 표든 문장이든 단편이든 그대로 넣으면 에이전트가
구조화하고, 값마다 어디서 왔는지(자료/재료지식/추론/가정)를 같이 낸다.

--write 없이 돌리면 **파일을 쓰지 않고 검토 결과만** 보여준다. 먼저 이걸로
확인하고, 근거가 납득되면 --write 로 저장하는 순서를 권한다.

PDF 는 텍스트로 뽑아서 넣을 것 (이 패키지는 PDF 를 읽지 않는다).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..intake import draft_from_spec, review, to_stack, write
from ..safety import MissingLimitError, bounds_from_stack
from ..seed import seed_transfer


def main() -> None:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="자료 텍스트 파일")
    src.add_argument("--text", help="자료를 직접 문자열로")
    ap.add_argument("--hint", default="", help="사람이 덧붙일 정보")
    ap.add_argument("--name", help="저장할 stack 이름 (기본: 에이전트가 정한 것)")
    ap.add_argument("--write", action="store_true", help="stacks/ 에 저장")
    ap.add_argument("--effort", default="high")
    args = ap.parse_args()

    spec = Path(args.file).read_text(encoding="utf-8") if args.file else args.text

    try:
        draft = draft_from_spec(spec, hint=args.hint, effort=args.effort)
    except ImportError:
        raise SystemExit("anthropic 패키지가 필요하다: pip install anthropic")
    except Exception as e:
        raise SystemExit(f"에이전트 호출 실패: {type(e).__name__}: {e}")

    print(review(draft))

    # 구조화된 결과로 실제 경계와 시드가 나오는지 바로 확인해 본다.
    stack = to_stack(draft)
    print("\n" + "-" * 60)
    try:
        bounds = bounds_from_stack(stack)
        print(bounds.describe())
        print("\n이 스택으로 계산된 시드:")
        print("  ", seed_transfer(stack).describe())
    except MissingLimitError as e:
        print(f"[경계 계산 불가]\n{e}")
    except Exception as e:
        print(f"[경계 계산 실패] {type(e).__name__}: {e}")

    if args.write:
        sp, lp = write(draft, name=args.name)
        print(f"\n저장: {sp}\n      {lp}")
    else:
        print("\n(--write 를 주면 stacks/ 에 저장한다. 지금은 저장하지 않았다)")
        print("\n--- stack 미리보기 ---")
        json.dump(stack, sys.stdout, ensure_ascii=False, indent=2)
        print()


if __name__ == "__main__":
    main()
