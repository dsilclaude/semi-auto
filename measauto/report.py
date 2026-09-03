"""report.py — 저장된 실행 기록을 '무엇을 보고 왜 그렇게 했는지'로 엮는다.

새로 계산하는 게 없다. store 가 이미 회차마다 남긴 것들을 읽어 순서대로 잇는다:

    plan.json    이 데이터를 만든 조건 + note(그 조건을 고른 이유)
    metrics.json 지표 + proposal(에이전트 판단·근거·patch) + LLM 에 넘긴 payload

여기서 하는 일은 셋이다.
  · 회차마다 [조건 → 관측 → 판단 → 바꾼 것] 을 한 줄기로 세운다
  · 다음 회차 지표와 비교해 **그 조정이 실제로 뭘 바꿨는지** 붙인다
  · 소자를 가로질러 같은 조건에서 값이 얼마나 흔들리는지 모은다

세 번째가 중요하다. 같은 소자를 반복 측정하면 트래핑이 쌓여 값이 흔들리는데,
그게 소자 특성인지 측정 이력인지는 한 회차만 봐서는 안 갈린다.

--llm 을 주면 이 표를 에이전트에게 넘겨 서술형 분석을 받는다. 표 자체는
장비도 API 도 없이 만들어지므로, 기록만 있으면 언제든 다시 뽑을 수 있다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

# 리포트에 싣는 지표와 읽기 좋은 이름. 여기 없는 건 metrics.json 에 그대로 있다.
FIELDS = [
    ("vth_cc", "Vth(정전류)", "V"),
    ("vth_lin", "Vth(선형외삽)", "V"),
    ("ss", "SS", "V/dec"),
    ("ss_points", "SS 점수", ""),
    ("decades", "on/off", "dec"),
    ("id_on", "Ion", "A"),
    ("id_off", "Ioff", "A"),
    ("floor_slope", "floor 기울기", "dec/V"),
    ("hysteresis_V", "히스테리시스", "V"),
    ("ig_max", "Ig max", "A"),
    ("ig_over_id", "Ig/Id", ""),
    ("compliance_hit", "컴플라이언스 접촉", ""),
]

# 값이 커지면 좋은 것 / 작아지면 좋은 것. 화살표 방향을 정하는 데만 쓴다.
BETTER_LOWER = {"ss", "floor_slope", "ig_max", "ig_over_id", "id_off"}
BETTER_HIGHER = {"decades", "id_on"}


# ---------------------------------------------------------------------------
# 읽기
# ---------------------------------------------------------------------------
def load_run(root: str | Path, run_id: Optional[str] = None) -> List[dict]:
    """한 실행의 회차 기록을 시간순으로. run_id 를 안 주면 가장 최근 실행."""
    root = Path(root)
    runs = sorted(p for p in root.iterdir() if p.is_dir() and p.name[:2].isdigit())
    if not runs:
        raise FileNotFoundError(f"{root} 에 실행 폴더가 없다")
    run_dir = (root / run_id) if run_id else runs[-1]
    if not run_dir.exists():
        raise FileNotFoundError(f"실행을 못 찾음: {run_dir}")

    recs: List[dict] = []
    for pj in sorted(run_dir.glob("*/*/iter*/plan.json")):
        d = pj.parent
        plan = json.loads(pj.read_text(encoding="utf-8"))
        mj = d / "metrics.json"
        blob = json.loads(mj.read_text(encoding="utf-8")) if mj.exists() else {}
        recs.append({
            "run": run_dir.name,
            "dir": d,
            "area": plan["site"]["area"],
            "site": str(plan["site"]["name"]),
            "iteration": plan["iteration"],
            "elapsed_s": plan.get("elapsed_s"),
            "plan": plan["plan"],
            "note": plan["plan"].get("note", ""),
            "metrics": blob.get("metrics", {}),
            "proposal": blob.get("proposal", {}),
            "payload": blob.get("llm_payload", {}),
        })
    recs.sort(key=lambda r: (r["area"], r["site"], r["iteration"]))
    return recs


# ---------------------------------------------------------------------------
# 서식
# ---------------------------------------------------------------------------
def _num(v, unit: str = "") -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, (int, float)):
        s = f"{v:.4g}"
        return f"{s} {unit}".strip()
    return str(v)


def _delta(cur, prev, key: str) -> str:
    """직전 회차 대비 변화. 좋아졌는지 나빠졌는지 화살표로."""
    if not isinstance(cur, (int, float)) or not isinstance(prev, (int, float)):
        return ""
    if isinstance(cur, bool) or isinstance(prev, bool) or prev == 0:
        return ""
    d = cur - prev
    if abs(d) < abs(prev) * 1e-3:
        return "  (변화 없음)"
    mark = ""
    if key in BETTER_LOWER:
        mark = " 개선" if d < 0 else " 악화"
    elif key in BETTER_HIGHER:
        mark = " 개선" if d > 0 else " 악화"
    return f"  ({d:+.3g}{mark})"


def describe_sweep(plan: dict) -> str:
    v1 = plan["var1"]
    s = (f"{v1['terminal']} {v1['start']:g}→{v1['stop']:g} V, "
         f"{v1['points']}점, {'왕복' if v1['direction'] == 'double' else '편도'}")
    step = abs(v1["stop"] - v1["start"]) / max(v1["points"] - 1, 1)
    s += f" (스텝 {step:.3g} V)"
    if plan.get("var2"):
        v2 = plan["var2"]
        s += f" × {v2['terminal']} {v2['start']:g}→{v2['stop']:g} V {v2['points']}스텝"
    consts = ", ".join(f"{k}={v:g}" for k, v in (plan.get("constants") or {}).items())
    # 마크다운 표 칸에 들어가므로 '|' 를 쓰지 않는다 (표가 깨진다)
    return s + (f", 고정 {consts}" if consts else "")


def _patch_diff(patch: Optional[dict]) -> List[str]:
    """에이전트가 실제로 건드린 필드만."""
    if not patch:
        return []
    return [f"`{k}` → {v}" for k, v in patch.items() if v is not None]


# ---------------------------------------------------------------------------
# 리포트
# ---------------------------------------------------------------------------
def render(recs: List[dict]) -> str:
    if not recs:
        return "기록이 없다."
    out: List[str] = [f"# 측정 리포트 — {recs[0]['run']}", ""]

    # --- 전체 요약 --------------------------------------------------------
    out += ["## 한눈에", "",
            "| 소자 | 회차 | 조건 | Vth | SS | on/off | 판단 |",
            "|---|---|---|---|---|---|---|"]
    for r in recs:
        m, p = r["metrics"], r["proposal"]
        out.append(
            f"| {r['area']}/{r['site']} | {r['iteration']} | "
            f"{describe_sweep(r['plan'])} | {_num(m.get('vth_cc'))} | "
            f"{_num(m.get('ss'))} | {_num(m.get('decades'))} | "
            f"{p.get('status', '—')} |")
    out.append("")

    # --- 소자별 --------------------------------------------------------
    by_site: Dict[str, List[dict]] = {}
    for r in recs:
        by_site.setdefault(f"{r['area']}/{r['site']}", []).append(r)

    for site, rs in by_site.items():
        out += [f"## 소자 {site}", ""]
        prev_m: dict = {}
        for r in rs:
            m, p = r["metrics"], r["proposal"]
            out += [f"### 회차 {r['iteration']} — {describe_sweep(r['plan'])}", ""]

            if r["note"]:
                out += ["**이 조건을 고른 이유**", "", f"> {r['note']}", ""]

            out += ["**관측**", "",
                    "| 지표 | 값 | 직전 대비 |", "|---|---|---|"]
            for key, label, unit in FIELDS:
                if key not in m or m.get(key) is None:
                    continue
                out.append(f"| {label} | {_num(m[key], unit)} | "
                           f"{_delta(m.get(key), prev_m.get(key), key).strip() or '—'} |")
            if m.get("ss_window_V"):
                out.append(f"| SS 측정 구간 | {m['ss_window_V']} V | — |")
            out.append("")

            if p:
                out += [f"**판단: {p.get('status', '?')}**"
                        + (f" (확신도 {p['confidence']:g}, {p.get('source', '?')})"
                           if p.get("confidence") is not None else ""), ""]
                if p.get("reason"):
                    out += [f"> {p['reason']}", ""]
                diff = _patch_diff(p.get("patch"))
                if diff:
                    out += ["**바꾼 것**: " + ", ".join(diff), ""]
            prev_m = m

        # 회차가 여럿이면 처음↔끝을 직접 비교해 준다
        if len(rs) > 1:
            a, b = rs[0]["metrics"], rs[-1]["metrics"]
            out += ["**첫 회차 → 마지막 회차**", ""]
            for key, label, unit in FIELDS:
                if a.get(key) is None or b.get(key) is None:
                    continue
                if isinstance(a[key], bool):
                    continue
                out.append(f"- {label}: {_num(a[key], unit)} → {_num(b[key], unit)}"
                           f"{_delta(b.get(key), a.get(key), key)}")
            out.append("")

    # --- 소자 간 비교 -----------------------------------------------------
    if len(by_site) > 1:
        out += ["## 소자 간 비교", "",
                "같은 조건에서 소자마다 값이 얼마나 다른가. 차이가 크면 소자 편차거나",
                "측정 이력(트래핑) 차이다 — 한 회차만 봐서는 안 갈린다.", "",
                "| 소자 | 마지막 조건 | Vth | SS | on/off | Ig max |",
                "|---|---|---|---|---|---|"]
        for site, rs in by_site.items():
            m = rs[-1]["metrics"]
            out.append(f"| {site} | {describe_sweep(rs[-1]['plan'])} | "
                       f"{_num(m.get('vth_cc'))} | {_num(m.get('ss'))} | "
                       f"{_num(m.get('decades'))} | {_num(m.get('ig_max'))} |")
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# LLM 서술 분석 (선택)
# ---------------------------------------------------------------------------
ANALYSIS_PROMPT = """\
당신은 반도체 소자 측정 결과를 읽고 **무슨 일이 있었는지** 설명한다.
아래는 자동측정 시스템이 남긴 기록이다 — 회차마다 어떤 조건으로 쟀고, 어떤 지표가
나왔고, 그때 어떤 판단으로 조건을 어떻게 바꿨는지가 순서대로 들어 있다.

다음을 서술로 써라. 표를 다시 그리지 말고, 숫자는 근거로만 인용해라.

1. **관측** — 데이터에서 눈에 띄는 것. 어느 지표가 기대와 달랐나.
2. **원인 추정** — 그 관측을 설명할 수 있는 물리적 원인. 확실한 것과 가능성만
   있는 것을 구분해라. 예: 히스테리시스와 회차 간 Vth 이동이 같이 나타나면
   전하 트래핑, floor 기울기가 크면 누설 또는 접촉 문제.
3. **조정과 그 결과** — 조건을 바꾼 것이 실제로 무엇을 바꿨나. 바꿨는데 안 변한
   것도 중요한 정보다.
4. **남은 의문** — 이 데이터로는 못 가르는 것. 무엇을 더 재야 갈리는지 구체적으로.

주의
- 지표는 정확히 계산된 값이다. 다시 눈대중으로 읽지 마라.
- 회차가 하나뿐이면 추세를 말할 수 없다. 그 한계를 분명히 적어라.
- 소자 간 차이를 곧바로 '소자 편차'로 단정하지 마라. 측정 순서와 이력이 다르면
  같은 소자여도 다르게 나온다."""


def analyze(recs: List[dict], *, model: str = "claude-opus-5",
            effort: str = "high", max_tokens: int = 8000, client=None) -> str:
    """리포트를 에이전트에게 넘겨 서술형 분석을 받는다."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model, max_tokens=max_tokens,
        system=[{"type": "text", "text": ANALYSIS_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": render(recs)}],
        output_config={"effort": effort},
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
