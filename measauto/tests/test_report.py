"""report — 저장된 기록에서 리포트가 만들어지는가. API 안 쓴다."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ..plan import transfer
from ..report import describe_sweep, load_run, render


def _write(root: Path, run: str, site: str, it: int, plan, metrics, proposal):
    d = root / run / "A" / site / f"iter{it:02d}_x"
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.json").write_text(json.dumps({
        "site": {"name": site, "x": 0, "y": 0, "area": "A", "note": ""},
        "iteration": it, "elapsed_s": 1.0, "plan": plan.to_dict()},
        ensure_ascii=False), encoding="utf-8")
    (d / "metrics.json").write_text(json.dumps(
        {"metrics": metrics, "proposal": proposal}, ensure_ascii=False),
        encoding="utf-8")


def test_report_traces_condition_observation_and_decision():
    p0 = transfer("BG", -3, 19, points=148, vd=0.1, direction="single",
                  note="off floor 확보를 위해 -3 V 부터")
    p1 = transfer("BG", -5, 19, points=161, vd=0.1, direction="single",
                  note="floor 가 부족해 -5 V 로")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root, "260819_120000", "1", 0, p0,
               {"vth_cc": 7.0, "ss": 0.79, "decades": 6.05},
               {"status": "propose", "reason": "floor 미확보", "confidence": 0.8,
                "source": "llm", "patch": {"var1_start": -5.0, "kind": None}})
        _write(root, "260819_120000", "1", 1, p1,
               {"vth_cc": 10.0, "ss": 0.48, "decades": 6.86},
               {"status": "converged", "reason": "충분", "confidence": 0.9,
                "source": "llm", "patch": None})

        recs = load_run(root)
        assert len(recs) == 2
        t = render(recs)

        assert "off floor 확보를 위해" in t, "조건을 고른 이유가 빠졌다"
        assert "floor 미확보" in t, "판단 근거가 빠졌다"
        assert "var1_start" in t, "무엇을 바꿨는지가 빠졌다"
        assert "kind" not in t.split("바꾼 것")[1][:80], "안 건드린 필드가 실렸다"
        assert "개선" in t, "회차 간 변화 방향이 안 붙었다"
        assert "첫 회차 → 마지막 회차" in t


def test_sweep_description_has_no_pipe():
    """마크다운 표 칸에 들어가므로 '|' 가 있으면 표가 깨진다."""
    p = transfer("BG", -3, 19, points=148, vd=0.1)
    assert "|" not in describe_sweep(p.to_dict())


def test_single_iteration_report_still_renders():
    """소자당 1회면 추세가 없다 — 그래도 리포트는 나와야 한다."""
    p = transfer("BG", -5, 19, points=161, vd=0.1)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root, "260819_130000", "1", 0, p,
               {"vth_cc": 2.0, "ss": 0.3, "decades": 6.5},
               {"status": "converged", "reason": "한 번으로 충분",
                "confidence": 0.9, "source": "llm", "patch": None})
        t = render(load_run(root))
        assert "회차 0" in t
        assert "첫 회차 → 마지막 회차" not in t, "1회인데 추세를 그렸다"


TESTS = [v for k, v in dict(globals()).items() if k.startswith("test_")]
