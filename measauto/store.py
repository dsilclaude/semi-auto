"""store.py — 결과 CSV + 조건 JSON (+ PNG) 을 같이 저장한다.

조건이 '값'이라서 생기는 부수 효과: 결과 옆에 조건을 그대로 떨궈 둘 수 있다.
에이전트가 조건을 만들기 시작하면 이게 유일한 실험 기록이므로, 나중에
"왜 이렇게 판단했지"를 되짚을 수 있도록 지표·제안·근거까지 같이 남긴다.

PNG 는 판단 입력이 아니라 **로그용**이다. 이미지는 픽셀→축 매핑이라 2.1인지
2.4인지 못 가르고, 로그축에서 -12 와 -13 은 몇 픽셀 차이라 Ioff 판정에 특히
취약하다. 그래도 사람이 나중에 볼 때는 곡선 한 장이 압도적으로 빠르다.

    results/
      index.csv                       ← 모든 실행의 모든 측정 한 줄씩 (누적)
      260819_141238/                  ← 실행마다 (run id)
        A/1/iter00_tr_BG_-2.07to19V_118pt_dbl_Vd0.1/
            data.csv      정규화된 측정 데이터
            plan.json     이 데이터를 만든 조건 (전부)
            metrics.json  지표 + 에이전트 제안/근거
            curve.png     로그용 그림

경로에 실행 시각과 측정 조건이 같이 들어간다. 이유가 둘이다.
  · 같은 소자를 다시 재면 이전 결과를 덮어쓰던 문제. 소자는 스트레스 이력이
    쌓이므로 '두 번째 측정'은 첫 번째와 다른 데이터다. 덮으면 그 이력이 사라진다.
  · 폴더 이름만 보고 어떤 조건이었는지 알 수 있어야 한다. 조건을 확인하려고
    매번 plan.json 을 열지 않아도 된다.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from .metrics import jsonable
from .plan import IVPlan

INDEX_COLUMNS = [
    "timestamp", "run", "area", "site", "iteration", "kind", "label",
    "sweep", "n_points", "elapsed_s", "status", "vth_cc", "ss", "decades",
    "id_on", "ig_max", "hysteresis_V", "compliance_hit", "path", "note",
]


_UNSAFE = re.compile(r"[^0-9A-Za-z가-힣._+-]+")


def plan_slug(plan: IVPlan, max_len: int = 90) -> str:
    """측정 조건을 폴더 이름으로. 사람이 읽어서 조건을 알 수 있게 짧게 압축한다."""
    v1 = plan.var1
    parts = [
        {"transfer": "tr", "output": "out"}.get(plan.kind, plan.kind[:3]),
        v1.terminal,
        f"{v1.start:g}to{v1.stop:g}V",
        f"{v1.n_steps}pt",
        "dbl" if v1.direction == "double" else "sgl",
    ]
    if v1.spacing != "linear":
        parts.append(v1.spacing)
    if plan.var2 is not None:
        parts.append(f"x{plan.var2.terminal}{plan.var2.start:g}to"
                     f"{plan.var2.stop:g}V{plan.var2.n_steps}st")
    for t, v in sorted(plan.constants.items()):
        if t == plan.var1.terminal or (plan.var2 and t == plan.var2.terminal):
            continue
        if v:                      # 0 V 고정은 기본이라 이름에 안 넣는다
            parts.append(f"V{t}{v:g}")
    return _UNSAFE.sub("_", "_".join(parts))[:max_len]


class Store:
    def __init__(self, root: str | Path, *, make_png: bool = True,
                 run_id: Optional[str] = None):
        """run_id 를 안 주면 생성 시각으로 만든다 — 실행마다 폴더가 갈린다."""
        self.root = Path(root)
        self.run_id = run_id or datetime.now().strftime("%y%m%d_%H%M%S")
        self.run_dir = self.root / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.make_png = make_png
        self.index_path = self.root / "index.csv"   # 인덱스는 실행을 가로질러 누적

    # --- 저장 -------------------------------------------------------------
    def save(self, *, site, plan: IVPlan, df: pd.DataFrame,
             metrics: Optional[dict] = None, iteration: int = 0,
             proposal: Optional[dict] = None, elapsed_s: Optional[float] = None,
             extra: Optional[Dict[str, Any]] = None) -> Path:
        d = (self.run_dir / str(site.area) / str(site.name)
             / f"iter{iteration:02d}_{plan_slug(plan)}")
        d.mkdir(parents=True, exist_ok=True)

        df.to_csv(d / "data.csv", index=False)

        with open(d / "plan.json", "w", encoding="utf-8") as f:
            json.dump(jsonable({
                "site": {"name": site.name, "x": site.x, "y": site.y,
                         "area": site.area, "note": site.note},
                "iteration": iteration,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "elapsed_s": elapsed_s,
                "plan": plan.to_dict(),
            }), f, ensure_ascii=False, indent=2)

        payload = {"metrics": metrics or {}, "proposal": proposal or {},
                   **(extra or {})}
        with open(d / "metrics.json", "w", encoding="utf-8") as f:
            json.dump(jsonable(payload), f, ensure_ascii=False, indent=2)

        if self.make_png:
            self._plot(d / "curve.png", df, plan, metrics)

        self._append_index(site, plan, metrics, iteration, elapsed_s, d, proposal)
        return d

    # --- index -----------------------------------------------------------
    def _append_index(self, site, plan, metrics, iteration, elapsed_s, path, proposal):
        m = metrics or {}
        row = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "run": self.run_id,
            "area": site.area, "site": site.name, "iteration": iteration,
            "kind": plan.kind, "label": plan.label,
            # 조건을 인덱스에서 바로 비교할 수 있게 한 칸에 요약해 둔다
            "sweep": (f"{plan.var1.terminal} {plan.var1.start:g}→{plan.var1.stop:g}V "
                      f"{plan.var1.n_steps}pt {plan.var1.direction}"),
            "n_points": plan.n_points,
            "elapsed_s": round(elapsed_s, 1) if elapsed_s else "",
            "status": (proposal or {}).get("status", ""),
            "vth_cc": m.get("vth_cc", ""), "ss": m.get("ss", ""),
            "decades": m.get("decades", ""), "id_on": m.get("id_on", ""),
            "ig_max": m.get("ig_max", ""),
            "hysteresis_V": m.get("hysteresis_V", ""),
            "compliance_hit": m.get("compliance_hit", ""),
            "path": str(path.relative_to(self.root)),
            "note": plan.note[:200],
        }
        self._ensure_index_header()
        with open(self.index_path, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=INDEX_COLUMNS, extrasaction="ignore")
            if self.index_path.stat().st_size == 0:
                w.writeheader()
            w.writerow(row)

    def _ensure_index_header(self) -> None:
        """열 구성이 바뀌었으면 옛 index 를 옆으로 치우고 새로 시작한다.

        그냥 이어 쓰면 헤더는 옛 열인데 행은 새 열이라 파일이 깨진다(실제로 깨졌다).
        지우지 않고 이름만 바꿔 두므로 과거 기록은 남는다.
        """
        if not self.index_path.exists():
            self.index_path.touch()
            return
        with open(self.index_path, "r", newline="", encoding="utf-8-sig") as f:
            header = next(csv.reader(f), [])
        if header and header != INDEX_COLUMNS:
            stamp = datetime.now().strftime("%y%m%d_%H%M%S")
            old = self.index_path.with_name(f"index_before_{stamp}.csv")
            self.index_path.rename(old)
            print(f"[store] index.csv 열 구성이 바뀌어 이전 기록을 "
                  f"{old.name} 로 옮겼다")
            self.index_path.touch()

    def read_index(self) -> pd.DataFrame:
        if not self.index_path.exists():
            return pd.DataFrame(columns=INDEX_COLUMNS)
        return pd.read_csv(self.index_path)

    # --- 그림 -------------------------------------------------------------
    def _plot(self, path: Path, df: pd.DataFrame, plan: IVPlan, metrics):
        """회차 그림. 형식은 plotting.py 가 정한다(비교 그림과 같은 형식).

        output 은 선형축(포화·기울기를 눈으로 보는 그림), transfer 는 로그축
        (7~8 decade 를 한 화면에 담아야 하므로)으로 그린다.
        """
        try:
            import numpy as np

            from .plotting import (LABELS, blues, legend, new_figure, save,
                                   style_axes)
        except Exception as e:                      # 그림은 있으면 좋고 없어도 됨
            print(f"[store] PNG 생략 (matplotlib: {e})")
            return

        vcol = f"V_{plan.var1.terminal}"
        if vcol not in df.columns or "I_D" not in df.columns:
            return

        is_output = plan.kind == "output"
        fig, ax = new_figure()

        groups = list(df.groupby("step", sort=False)) if "step" in df.columns \
            else [(None, df)]
        colors = blues(len(groups))
        for (v2, g), c in zip(groups, colors):
            y = g["I_D"] if is_output else np.abs(g["I_D"]) + 1e-15
            # 스텝 값은 linspace 라 6.4075 같은 꼬리가 붙는다. 범례에서는 자른다.
            lbl = None if v2 is None else (
                rf"$V_\mathrm{{{plan.var2.terminal}}}$ = {float(v2):.3g} V")
            ax.plot(g[vcol], y, lw=2.0, color=c, label=lbl)

        # transfer 에서는 게이트 누설도 같이 본다 — off 바닥이 누설인지 가르려면
        # 두 곡선이 한 그림에 있어야 한다.
        icol = f"I_{plan.var1.terminal}"
        if not is_output and icol in df.columns:
            ax.plot(df[vcol], np.abs(df[icol]) + 1e-15, lw=1.4, ls="--",
                    color="0.55", label=LABELS["ig_abs"].split(",")[0])

        if is_output:
            style_axes(ax, title="Output curve", xlabel=LABELS["vd"],
                       ylabel=LABELS["id"], sci_y=True,
                       yvalues=df["I_D"].to_numpy())
            ax.set_xlim(left=0)
            ax.set_ylim(bottom=0)
        else:
            ax.set_yscale("log")
            style_axes(ax, title="Transfer curve", xlabel=LABELS["vg"],
                       ylabel=LABELS["id_abs"], log_y=True)
            if (metrics or {}).get("vth_cc") is not None:
                ax.axvline(metrics["vth_cc"], color="crimson", lw=1.2, ls=":",
                           label=rf"$V_\mathrm{{th}}$ = {metrics['vth_cc']:g} V")

        legend(ax, ncol=2 if len(groups) > 5 else 1)
        save(fig, path)
        import matplotlib.pyplot as plt
        plt.close(fig)
