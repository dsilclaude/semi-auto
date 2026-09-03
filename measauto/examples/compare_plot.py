"""examples/compare_plot.py — 여러 소자·여러 폭을 한 그림에 겹쳐 그린다.

    python -m measauto.examples.compare_plot --kind output --out results_agent
    python -m measauto.examples.compare_plot --kind transfer --png 비교.png

저장된 data.csv 들을 읽어 폭별로 색을 나눠 겹친다. 형식은 plotting.py 가
정하므로 회차 그림과 같다 — 나란히 놓고 봐도 축이 어긋나지 않는다.

폭은 그 측정의 plan.json 옆에 있는 stack 이름에서 못 읽으므로, 결과 경로의
run 폴더와 index.csv 를 통해 label 로 받는다(--label 로 직접 줄 수도 있다).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

from ..plotting import LABELS, blues, legend, new_figure, save, style_axes


def _collect(root: Path, kind: str) -> list:
    """(라벨, 폭, DataFrame, plan) 목록. 폭은 stack 이름에서 뽑는다."""
    out = []
    for pj in sorted(root.glob("*/*/*/iter*/plan.json")):
        d = json.loads(pj.read_text(encoding="utf-8-sig"))
        plan = d["plan"]
        if plan.get("kind") != kind:
            continue
        csv = pj.parent / "data.csv"
        if not csv.exists():
            continue
        note = plan.get("note", "")
        m = re.search(r"stack=SD_w(\d+)", note)
        w = int(m.group(1)) if m else None
        out.append({"w": w, "site": d["site"]["name"], "run": pj.parts[-5],
                    "df": pd.read_csv(csv), "plan": plan})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results_agent", help="결과 폴더")
    ap.add_argument("--kind", choices=["output", "transfer"], default="output")
    ap.add_argument("--png", help="저장 경로 (기본: 화면 대신 파일로)")
    args = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    recs = _collect(Path(args.out), args.kind)
    if not recs:
        raise SystemExit(f"{args.out} 에서 {args.kind} 측정을 못 찾았다")

    # 폭이 클수록 진하게. 폭을 모르면 맨 뒤로.
    widths = sorted({r["w"] for r in recs if r["w"] is not None})
    colors = dict(zip(widths, blues(len(widths))))

    fig, ax = new_figure()
    seen = set()
    ally = []
    for r in recs:
        c = colors.get(r["w"], "0.5")
        vcol = f"V_{r['plan']['var1']['terminal']}"
        if vcol not in r["df"].columns or "I_D" not in r["df"].columns:
            continue
        # 같은 폭·같은 소자는 범례에 한 번만
        key = (r["w"], r["site"])
        lbl = None
        if key not in seen:
            seen.add(key)
            lbl = (rf"W = {r['w']} µm  (#{r['site']})" if r["w"]
                   else f"(#{r['site']})")
        groups = (list(r["df"].groupby("step", sort=False))
                  if "step" in r["df"].columns else [(None, r["df"])])
        for i, (_, g) in enumerate(groups):
            y = g["I_D"] if args.kind == "output" else g["I_D"].abs() + 1e-15
            ax.plot(g[vcol], y, lw=1.8, color=c,
                    label=lbl if i == 0 else None)
            ally.extend(y.tolist())

    if args.kind == "output":
        style_axes(ax, title="Output curve", xlabel=LABELS["vd"],
                   ylabel=LABELS["id"], sci_y=True, yvalues=ally)
        ax.set_xlim(left=0)
        ax.set_ylim(bottom=0)
    else:
        ax.set_yscale("log")
        style_axes(ax, title="Transfer curve", xlabel=LABELS["vg"],
                   ylabel=LABELS["id_abs"], log_y=True)

    legend(ax, ncol=2 if len(seen) > 5 else 1)
    path = Path(args.png or f"compare_{args.kind}.png")
    save(fig, path)
    plt.close(fig)
    print(f"저장: {path.resolve()}  ({len(recs)}개 측정, 폭 {widths})")


if __name__ == "__main__":
    main()
