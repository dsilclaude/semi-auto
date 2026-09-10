"""metrics.py — 측정 결과에서 지표를 뽑는 순수 함수.

여기 있는 함수는 장비도 파일도 에이전트도 모른다. 입력은 정규화된 DataFrame
(frame.canonicalize 통과) 과 IVPlan 뿐이고, 출력은 dict 다.
→ 기존 CSV 로 단위테스트가 되고, 리플레이 평가의 절반이 여기서 나온다.

중요한 설계 결정 하나: 여기서는 **판정하지 않는다**.
"vth 가 3.2 V 다", "floor 기울기가 0.02 다" 까지가 이 모듈의 일이고,
"죽은 소자다 / 범위를 넓혀야 한다" 는 판단은 agent 가 한다.

  왜냐면 범위 탐색 단계에서는 예외가 예외가 아니기 때문이다. 첫 스윕에서
  평평한 선이 나왔을 때 죽은 소자인지, Vth 가 범위 밖인지, 팁이 안 닿았는지는
  그 데이터만으로 안 갈라진다. 규칙으로 가르려 하면 곡선이 이미 보이는 걸
  전제하게 되고, 정작 초반엔 계산조차 안 된다.

LLM 에 넘기는 형태 (to_llm_payload)
  · 지표는 여기서 정확히 계산한다 — 이게 수치의 근거다.
  · 곡선은 20~30점으로 다운샘플. 지표가 못 잡는 것(kink, 이중 turn-on,
    floor 미세 기울기)을 보완한다.
  · Ig 필수. 게이트 누설 판단은 Id 만으로 안 되고, t_ox 를 모를 때 경계를
    올릴 근거가 Ig 뿐이다.
  · 전류는 log10 로 준다. 데이터가 8 decade 에 걸쳐 있어서 선형 소수점으로
    쓰면 off 영역이 전부 0.00000000000012 꼴이라 구분이 안 된다.
    로그면 -12.9, -6.5 로 값 사이 거리가 균등해진다. Vg 는 선형 그대로.
  · 함정: 노이즈로 Id 가 음수로 찍히면 log10 이 깨진다. 산화물 TFT off 영역에서
    실제로 자주 나오니 |I| + floor 로 받고 부호는 별도 필드로 준다.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .plan import IVPlan

I_FLOOR = 1e-15          # log10 가 깨지지 않게 더하는 바닥값 [A]
CC_REF_A_PER_SQUARE = 1e-9  # constant-current Vth 기준 전류 (W/L 로 정규화)
# W 를 몰라도 쓸 수 있는 turn-on 기준: 측정된 off floor 에서 몇 decade 위인가.
# Id 도 Ioff 도 W 에 비례하므로 **그 비는 W 와 무관하다.** 정전류 Vth 가
# W/L 을 알아야 성립하는 것과 대조된다.
ONSET_DECADES = 3.0
# 바닥이 기울었다고 말하려면 이만큼은 기울어야 한다 [decade/V]. 노이즈로
# 방향을 잘못 잡으면 다음 소자의 창을 반대쪽으로 넓히게 된다.
TREND_MIN_DEC_PER_V = 0.02
# 이보다 가파르면 **문턱하 구간**이다 (SS ≤ 3.3 V/dec). 문턱 위에서는 Id ∝ (Vg-Vth)
# 라 로그 기울기가 1/(ln10·(Vg-Vth)) 로 훨씬 완만하다. 이 둘을 안 가르면 방향을
# 정반대로 잡는다 — 둘 다 '전류가 Vg 와 함께 오른다'인데, 문턱하면 turn-on 이
# 위에 있고 문턱 위면 아래에 있다.
SUBTHRESHOLD_MIN_DEC_PER_V = 0.3
# 문턱하 꼬리는 **창 끝에서만** 보인다. 창 전체 평균 기울기로 재면 평평한 부분에
# 희석되어 못 잡는다(실측 모사: 꼬리가 걸린 창에서 전체 0.009 vs 끝 1.2 dec/V).
EDGE_FRACTION = 0.2
# 이미 켜져 있는 곡선은 노이즈가 아니라 진짜 신호다. 그래서 훨씬 작은 기울기도
# 방향 근거로 쓸 수 있다 — 바닥에서 요구하는 문턱보다 한 자릿수 낮게 둔다.
CONDUCTING_TREND_MIN = 0.002
# 측정계 바닥의 이 배 위에 있으면 '흐르고 있다'고 본다.
CONDUCTING_MARGIN = 100.0
# 레인지 이름 → 그 레인지에서 볼 수 있는 최소 전류 [A].
_RANGE_A = {"10 pa": 1e-11, "100 pa": 1e-10, "1 na": 1e-9, "10 na": 1e-8,
            "100 na": 1e-7, "1 ua": 1e-6, "10 ua": 1e-5, "100 ua": 1e-4,
            "1 ma": 1e-3, "10 ma": 1e-2, "100 ma": 1e-1}


def range_floor_A(name: Optional[str]) -> Optional[float]:
    """'10 pA limited' → 1e-11. 모르는 이름이면 None."""
    if not name:
        return None
    key = " ".join(str(name).split()[:2]).lower()
    return _RANGE_A.get(key)


# ---------------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------------
def log10_abs(i) -> np.ndarray:
    return np.log10(np.abs(np.asarray(i, dtype=float)) + I_FLOOR)


def _f(x) -> Optional[float]:
    """json 안전한 float. nan/inf 는 None."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(x) or math.isinf(x)) else x


def _round(x, sig: int = 4):
    x = _f(x)
    if x is None or x == 0:
        return x
    return float(f"%.{sig}g" % x)


def jsonable(obj):
    """dict/list 안의 numpy·nan 을 전부 순수 파이썬으로."""
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return _f(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def _linfit(x, y) -> Tuple[float, float]:
    """1차 최소제곱. 점이 모자라면 (nan, nan)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 2:
        return float("nan"), float("nan")
    a, b = np.polyfit(x[ok], y[ok], 1)
    return float(a), float(b)


# ---------------------------------------------------------------------------
# 곡선 쪼개기
# ---------------------------------------------------------------------------
def split_branches(df: pd.DataFrame, plan: IVPlan) -> List[dict]:
    """var2 스텝별 / 왕복 방향별로 곡선을 쪼갠다.

    반환: [{"step": v2값 or None, "forward": DataFrame, "backward": DataFrame|None}, ...]
    """
    if plan.var2 is not None and "step" in df.columns:
        groups = [(v, g) for v, g in df.groupby("step", sort=False)]
    else:
        groups = [(None, df)]

    out = []
    for v2, g in groups:
        g = g.reset_index(drop=True)
        if plan.var1.direction == "double" and len(g) >= 4:
            half = len(g) // 2
            out.append({"step": _f(v2), "forward": g.iloc[:half].reset_index(drop=True),
                        "backward": g.iloc[half:].reset_index(drop=True)})
        else:
            out.append({"step": _f(v2), "forward": g, "backward": None})
    return out


# ---------------------------------------------------------------------------
# transfer 지표
# ---------------------------------------------------------------------------
def transfer_metrics(vg, id_, ig=None, *, wl: Optional[float] = None,
                     cox: Optional[float] = None, vd: Optional[float] = None,
                     compliance: Optional[float] = None,
                     i_range_floor: Optional[float] = None) -> dict:
    """한 개의 편도 transfer 곡선에서 지표를 뽑는다.

    vg  : 게이트 전압 [V]
    id_ : 드레인 전류 [A]  (부호 포함)
    ig  : 게이트 전류 [A]  (없으면 누설 지표는 None)
    wl  : W/L. 있으면 id_norm 과 constant-current Vth 기준에 쓴다.
    cox : 게이트 커패시턴스 [F/cm²]. vd 와 함께 있으면 전계효과 이동도.
    i_range_floor : 드레인 측정 레인지의 바닥 [A]. 있으면 '평평한 곡선'이
        off 바닥인지 켜진 채 포화된 것인지 가른다 — 그 둘은 창을 넓혀야 할
        방향이 정반대다.
    """
    vg = np.asarray(vg, float)
    idd = np.asarray(id_, float)
    ok = np.isfinite(vg) & np.isfinite(idd)
    vg, idd = vg[ok], idd[ok]
    m: dict = {"n_points": int(vg.size)}
    if vg.size < 5:
        m["error"] = "포인트가 5개 미만이라 지표 계산 불가"
        return m

    order = np.argsort(vg)
    vg, idd = vg[order], idd[order]
    if ig is not None:
        igg = np.asarray(ig, float)[ok][order]
    else:
        igg = None

    aid = np.abs(idd)
    logi = log10_abs(idd)

    m["vg_start"] = _round(vg[0])
    m["vg_stop"] = _round(vg[-1])
    m["vg_step"] = _round(np.median(np.diff(vg)))

    # --- on/off, decades ---------------------------------------------------
    k = max(3, vg.size // 20)
    i_off = float(np.median(np.sort(aid)[:k]))
    i_on = float(np.median(np.sort(aid)[-k:]))
    m["id_on"] = _round(i_on)
    m["id_off"] = _round(i_off)
    m["decades"] = _round(math.log10(max(i_on, I_FLOOR) / max(i_off, I_FLOOR)), 3)
    if wl:
        m["id_norm"] = _round(i_on / wl)

    # --- 극성: on 영역이 Vg 큰 쪽인가 작은 쪽인가 --------------------------
    half = vg.size // 2
    m["polarity"] = "n" if np.median(logi[half:]) >= np.median(logi[:half]) else "p"
    m["negative_id_fraction"] = _round(float(np.mean(idd < 0)), 3)

    # --- gm, Vth(선형 외삽) ------------------------------------------------
    gm = np.gradient(idd, vg)
    sign = 1.0 if m["polarity"] == "n" else -1.0
    gm_s = gm * sign
    j = int(np.argmax(gm_s))
    m["gm_peak"] = _round(gm_s[j])
    m["gm_peak_vg"] = _round(vg[j])

    # gm 이 창 끝에서 최대면 그건 최고점이 아니라 '아직 오르는 중'일 수 있다.
    # argmax 는 둘을 구분하지 못하고 마지막 점을 돌려준다. 꺾이는 걸 봐야
    # 최고점이므로, 켜지는 쪽 끝에 붙어 있으면 이동도는 값이 아니라 하한이다.
    lo, hi = float(np.min(vg)), float(np.max(vg))
    span = hi - lo
    on_edge = hi if m["polarity"] == "n" else lo      # 더 세게 켜지는 쪽
    step = float(np.median(np.abs(np.diff(vg)))) if vg.size > 1 else 0.0
    margin = abs(on_edge - float(vg[j]))
    m["gm_peak_margin_V"] = _round(margin, 3)
    m["gm_peak_at_edge"] = bool(margin <= max(3.0 * step, 0.03 * span))

    if gm_s[j] > 0:
        m["vth_lin"] = _round(vg[j] - idd[j] / gm[j], 4)
    else:
        m["vth_lin"] = None

    # --- Vth(정전류 기준) --------------------------------------------------
    # 기준 전류가 W/L 에 비례한다. **W 를 모르면 이 기준이 통째로 어긋난다** —
    # W/L=6 인 소자를 1 로 가정하면 기준이 6배 낮아 Vth 가 낮게 나온다.
    # 값은 내되 가정했다는 사실을 같이 남긴다. 범위 탐색은 이 값을 믿지 않는다.
    i_ref = CC_REF_A_PER_SQUARE * (wl if wl else 1.0)
    m["vth_cc_ref_A"] = _round(i_ref)
    m["vth_cc"] = _round(_cross_vg(vg, aid, i_ref, rising=(m["polarity"] == "n")), 4)
    m["vth_cc_wl_known"] = bool(wl)

    # --- turn-on 위치 (W 무관) ---------------------------------------------
    # floor 에서 ONSET_DECADES 만큼 올라온 지점. 분자·분모가 같이 W 에
    # 비례하므로 W 를 몰라도 성립한다. 범위 탐색은 이쪽을 쓴다.
    # 창이 off 까지 못 내려가면 i_off 는 floor 가 아니라 '이 창의 최소값'이다.
    # 그대로 ×1000 하면 기준이 id_on 을 넘어 교차점이 사라지고 turn-on 이
    # 영영 None 이 된다(공핍형에서 실제로 그랬다). 그럴 땐 관측된 구간의
    # 기하평균으로 내려잡는다 — 여전히 W 무관이고, 교차는 반드시 생긴다.
    onset_ref = i_off * 10.0 ** ONSET_DECADES
    if onset_ref >= i_on:
        onset_ref = math.sqrt(max(i_off, I_FLOOR) * max(i_on, I_FLOOR))
    onset_ref = max(onset_ref, I_FLOOR)
    m["vth_onset_ref_A"] = _round(onset_ref)
    m["vth_onset_V"] = _round(
        _cross_vg(vg, aid, onset_ref, rising=(m["polarity"] == "n")), 4)

    # 창을 정할 때 기대는 단일 값. W 를 알면 관례대로 정전류 Vth 를,
    # 모르면 W 무관 기준을 쓴다. 어느 쪽인지 basis 에 남는다.
    if wl and m["vth_cc"] is not None:
        m["turn_on_V"], m["turn_on_basis"] = m["vth_cc"], "vth_cc (W/L 실측)"
    else:
        m["turn_on_V"] = m["vth_onset_V"]
        m["turn_on_basis"] = (
            f"vth_onset (floor +{ONSET_DECADES:g} decade, W 무관)"
            + ("" if wl else " — W/L 을 몰라 정전류 Vth 는 신뢰할 수 없다"))

    # --- SS (subthreshold swing) ------------------------------------------
    # 관례 단위는 mV/dec 다(스펙 시트가 그렇게 쓴다). 계산은 V/dec 로 하고
    # 아래에서 mV 도 같이 남긴다 — 단위 혼동이 제일 흔한 오독 원인이다.
    # 성긴 survey 에서는 문턱하 구간에 점이 몇 개 안 들어온다. 창을 줄여서라도
    # 값을 내되, 몇 점으로 잰 건지 같이 남긴다 — 그래야 '거친 값'인 걸 안다.
    for win in (5, 4, 3):
        m["ss"], m["ss_window_V"] = _subthreshold_swing(vg, aid, i_off, i_on, win=win)
        if m["ss"] is not None:
            m["ss_points"] = win
            break
    else:
        m["ss_points"] = None
    m["ss_mV_per_dec"] = _round(m["ss"] * 1000.0, 4) if m.get("ss") else None
    # SS 는 '어디서 쟀나'가 값을 바꾼다. 실제 곡선은 floor 근처에서 완만하고
    # 위로 갈수록 가팔라져서, fit 창이 얹힌 위치에 따라 20% 씩 갈린다.
    # 그래서 폭·점수·중심을 같이 남긴다 — 소자끼리 SS 를 비교하려면 먼저
    # 이 셋이 비슷한지부터 봐야 한다.
    w = m.get("ss_window_V")
    if w and len(w) == 2:
        m["ss_window_width_V"] = _round(abs(w[1] - w[0]), 3)
        m["ss_window_center_V"] = _round((w[0] + w[1]) / 2.0, 3)
    else:
        m["ss_window_width_V"] = m["ss_window_center_V"] = None

    # --- off 영역 바닥 기울기 ----------------------------------------------
    # 지표가 못 잡는 '살짝 기운 바닥'을 숫자로 남긴다. 진짜 off 면 ≈0.
    thr = max(i_off * 10, I_FLOOR)
    off_mask = aid <= thr
    if off_mask.sum() >= 3:
        slope, _ = _linfit(vg[off_mask], logi[off_mask])
        m["floor_slope"] = _round(slope, 3)        # [decade/V]
        m["floor_span_V"] = _round(vg[off_mask].max() - vg[off_mask].min(), 3)
    else:
        m["floor_slope"] = None
        m["floor_span_V"] = None

    # --- 게이트 누설 -------------------------------------------------------
    if igg is not None and np.isfinite(igg).any():
        aig = np.abs(igg)
        jm = int(np.nanargmax(aig))
        m["ig_max"] = _round(aig[jm])
        m["ig_max_vg"] = _round(vg[jm])
        m["ig_over_id"] = _round(float(aig[jm] / max(i_on, I_FLOOR)), 3)
        # off 상태에서 게이트 누설이 문제인지는 컴플라이언스가 아니라 **Ioff 와**
        # 견줘야 안다. 컴플라이언스는 '태우지 않는 선'이지 '측정을 오염시키지
        # 않는 선'이 아니다. 이 값이 1 에 가까우면 on/off 의 분모가 사실상
        # 게이트 누설이라 decades 를 소자 성능으로 읽으면 안 된다.
        m["ig_over_ioff"] = _round(float(aig[jm] / max(i_off, I_FLOOR)), 3)
        # 지수적 상승은 절연막이 새기 시작했다는 제일 이른 신호
        pos = aig > I_FLOOR * 10
        if pos.sum() >= 5:
            s, _ = _linfit(vg[pos], np.log10(aig[pos]))
            m["ig_slope"] = _round(s, 3)           # [decade/V]
    else:
        m["ig_max"] = m["ig_max_vg"] = m["ig_over_id"] = m["ig_slope"] = None
        m["ig_over_ioff"] = None

    # --- 컴플라이언스 ------------------------------------------------------
    if compliance:
        m["compliance_A"] = _round(compliance)
        m["compliance_hit"] = bool(np.any(aid >= 0.95 * compliance))
        m["compliance_hit_fraction"] = _round(float(np.mean(aid >= 0.95 * compliance)), 3)
    else:
        m["compliance_hit"] = None

    # --- 이동도 ------------------------------------------------------------
    if cox and vd and wl and m["gm_peak"]:
        # 선형영역 전계효과 이동도: µ = gm·L / (W·Cox·Vd)
        m["mobility_cm2_Vs"] = _round(m["gm_peak"] / (wl * cox * abs(vd)), 3)
    else:
        m["mobility_cm2_Vs"] = None
    # gm 이 아직 오르는 중이었다면 이 값은 '적어도 이만큼'이라는 뜻이다.
    m["mobility_is_lower_bound"] = bool(
        m["mobility_cm2_Vs"] is not None and m["gm_peak_at_edge"])

    # --- 두 Vth 정의의 간격 -------------------------------------------------
    # 정전류법은 로그축에서 '켜지기 시작하는 곳', 선형외삽은 '전류가 세게 흐르기
    # 시작하는 곳'을 잡는다. 이상적인 소자면 비슷하지만, 이동도가 Vg 에 따라
    # 계속 오르거나(트랩 제한/퍼콜레이션) 직렬 접촉저항이 크면 크게 벌어진다.
    # transfer 하나로는 접촉이냐 채널이냐가 안 갈린다 — output 의 선형 기울기가
    # 필요하다. 그래서 '값'이 아니라 '신호'로 남긴다.
    if m.get("vth_lin") is not None and m.get("vth_cc") is not None:
        m["vth_gap_V"] = _round(m["vth_lin"] - m["vth_cc"], 3)
    else:
        m["vth_gap_V"] = None

    # --- 사실 관찰 (판정 아님) ---------------------------------------------
    m["is_flat"] = bool(m["decades"] is not None and m["decades"] < 1.0)
    t = m["turn_on_V"]
    m["turn_on_inside_window"] = bool(t is not None and vg[0] < t < vg[-1])
    # '창 안에 있다'와 '전이를 다 봤다'는 다르다. off floor 를 못 봤으면
    # turn-on 도 SS 도 창 끝에 눌린 값이라, 그걸 근거로 다음 창을 잡으면 안 된다.
    # (공핍형에서 2.3 decade 만 보고 SS 를 1.46 으로 잡은 적이 있다 — 참값은 0.30)
    m["transition_captured"] = bool(
        m["turn_on_inside_window"] and (m["decades"] or 0.0) >= ONSET_DECADES)

    # 창 안에서 로그 전류가 전체적으로 어느 쪽으로 기우나 [decade/V].
    # turn-on 을 못 찾았을 때 **어느 쪽으로 넓혀야 하나**의 단서다.
    trend, _ = _linfit(vg, logi)
    m["id_trend_dec_per_V"] = _round(trend, 4)

    # 창 양 끝의 기울기 중 가파른 쪽. 문턱하 꼬리는 창 끝에만 걸리므로 전체
    # 평균으로는 못 잡는다 — 평평한 구간에 희석된다.
    e = max(5, int(vg.size * EDGE_FRACTION))
    lo_slope, _ = _linfit(vg[:e], logi[:e])
    hi_slope, _ = _linfit(vg[-e:], logi[-e:])
    edge = hi_slope if abs(hi_slope) >= abs(lo_slope) else lo_slope
    m["id_edge_trend_dec_per_V"] = _round(edge, 4)

    # 창이 전부 평평할 때 그게 'off 바닥'인지 '켜진 채 포화'인지 가른다.
    # 측정계 바닥과 견줘야 알 수 있다 — 절대 전류만으로는 W 를 모르면 못 가른다.
    if i_range_floor and i_off > i_range_floor * CONDUCTING_MARGIN:
        m["id_state"] = "conducting"
    elif i_range_floor:
        m["id_state"] = "off_floor"
    else:
        m["id_state"] = None

    # 전류가 큰 쪽이 '켜지는' 방향. n형이면 위, p형이면 아래.
    kk = max(3, vg.size // 20)
    rising = float(np.median(aid[-kk:])) > float(np.median(aid[:kk]))
    m["turn_on_side"] = _turn_on_side(
        m["transition_captured"], rising, edge, m["id_state"])
    return m


def _turn_on_side(captured: bool, rising: bool, edge: float,
                  state: Optional[str]) -> Optional[str]:
    """전이를 다 못 봤으면 어느 쪽을 더 봐야 하나. 모르면 None(→ 대칭 확장).

    **기울기의 부호로 정하면 안 된다.** '전류가 Vg 와 함께 오른다'가 두 뜻이다:
    문턱하 꼬리(turn-on 이 **위**)일 수도, 이미 켜진 채 Id ∝ (Vg−Vth)로 오르는
    것(turn-on 이 **아래**)일 수도 있다. 게다가 turn-on 이 창 끝 *근처*에 있으면
    끝 기울기가 그 전이를 물어서 부호가 정반대로 나온다.

    그래서 부호가 아니라 **무엇이 모자란가**로 정한다.

      · 창 전체가 켜져 있다  → 모자란 것은 off 쪽. 켜지는 방향의 **반대**로.
      · 창 전체가 바닥이다   → 모자란 것은 on 쪽. 꼬리가 보일 때만 그쪽으로.
                              (꼬리도 안 보이면 turn-on 이 한참 멀다 = 근거 없음)
    """
    if captured:
        return None
    if state == "conducting":
        return "below" if rising else "above"
    if np.isfinite(edge) and abs(edge) >= SUBTHRESHOLD_MIN_DEC_PER_V:
        return "above" if rising else "below"
    return None


def _cross_vg(vg: np.ndarray, aid: np.ndarray, i_ref: float, rising: bool) -> float:
    """|Id| 가 i_ref 를 처음 넘는 Vg. log 축에서 선형 보간."""
    if aid.max() < i_ref or aid.min() > i_ref:
        return float("nan")
    li, lr = np.log10(aid + I_FLOOR), math.log10(i_ref)
    idxs = np.where(np.diff(np.sign(li - lr)) != 0)[0]
    if idxs.size == 0:
        return float("nan")
    k = idxs[0] if rising else idxs[-1]
    y0, y1 = li[k], li[k + 1]
    if y1 == y0:
        return float(vg[k])
    return float(vg[k] + (lr - y0) / (y1 - y0) * (vg[k + 1] - vg[k]))


def _subthreshold_swing(vg: np.ndarray, aid: np.ndarray, i_off: float, i_on: float,
                        win: int = 5) -> Tuple[Optional[float], Optional[list]]:
    """SS[V/decade] 최소값. 눈대중이 아니라 슬라이딩 창 회귀로 구한다."""
    lo, hi = max(i_off * 10, I_FLOOR * 100), i_on * 0.1
    if hi <= lo:
        return None, None
    mask = (aid > lo) & (aid < hi)
    idx = np.where(mask)[0]
    if idx.size < win:
        return None, None
    li = np.log10(aid + I_FLOOR)
    best, best_rng = None, None
    for s in range(idx.size - win + 1):
        sel = idx[s:s + win]
        if not np.all(np.diff(sel) == 1):
            continue
        slope, _ = _linfit(vg[sel], li[sel])   # [decade/V]
        if not np.isfinite(slope) or abs(slope) < 1e-6:
            continue
        ss = abs(1.0 / slope)
        if best is None or ss < best:
            best, best_rng = ss, [float(vg[sel[0]]), float(vg[sel[-1]])]
    return (_round(best, 3), [_round(x, 3) for x in best_rng] if best_rng else None)


# ---------------------------------------------------------------------------
# output 지표
# ---------------------------------------------------------------------------
def output_metrics(vd, id_, *, compliance: Optional[float] = None) -> dict:
    """한 개의 편도 output 곡선(고정 Vg)에서 지표."""
    vd = np.asarray(vd, float)
    idd = np.asarray(id_, float)
    ok = np.isfinite(vd) & np.isfinite(idd)
    vd, idd = vd[ok], idd[ok]
    m: dict = {"n_points": int(vd.size)}
    if vd.size < 5:
        m["error"] = "포인트가 5개 미만이라 지표 계산 불가"
        return m

    order = np.argsort(vd)
    vd, idd = vd[order], idd[order]
    aid = np.abs(idd)

    m["vd_start"], m["vd_stop"] = _round(vd[0]), _round(vd[-1])
    m["id_at_vdmax"] = _round(idd[-1])
    mid = int(np.searchsorted(vd, (vd[0] + vd[-1]) / 2))
    mid = min(max(mid, 1), vd.size - 1)
    m["id_at_vdhalf"] = _round(idd[mid])
    m["saturation_ratio"] = _round(
        aid[-1] / max(aid[mid], I_FLOOR), 3)   # 1 에 가까울수록 잘 포화

    # 선형영역 저항: 저Vd 구간 기울기의 역수
    lin = vd <= vd[0] + 0.15 * (vd[-1] - vd[0])
    if lin.sum() >= 3:
        g, _ = _linfit(vd[lin], idd[lin])
        m["r_on_ohm"] = _round(1.0 / g, 4) if abs(g) > 0 else None
    else:
        m["r_on_ohm"] = None

    # crowding(접촉 저항 지배): 저Vd 에서 위로 휘면 origin 기울기 < 중간 기울기
    g_all, _ = _linfit(vd, idd)
    if m["r_on_ohm"] and abs(g_all) > 0:
        m["crowding_index"] = _round((1.0 / m["r_on_ohm"]) / g_all, 3)
    else:
        m["crowding_index"] = None

    # kink: 포화 구간에서 dId/dVd 가 다시 튀어오르는가
    d1 = np.gradient(idd, vd)
    sat = vd >= vd[0] + 0.5 * (vd[-1] - vd[0])
    if sat.sum() >= 4:
        base = float(np.median(np.abs(d1[sat])))
        m["kink_index"] = _round(float(np.max(np.abs(d1[sat])) / max(base, 1e-30)), 3)
    else:
        m["kink_index"] = None

    # --- 채널 길이 변조 λ, 출력 저항 -----------------------------------------
    # 포화 영역에서  Id = Idsat·(1 + λ·Vd)  이므로, 그 구간을 직선으로 맞추면
    # 기울기/절편 = λ 다. 회로에서는 이게 출력 저항(r_out = 1/(λ·Idsat))으로
    # 쓰이고, SPICE 파라미터 피팅에 그대로 들어간다.
    # 주의: 포화에 못 들어간 곡선(Vd 가 낮거나 Vg 가 높은 조건)에서는 의미가 없다.
    #       그래서 포화 진입 여부(saturation_ratio)를 같이 보고 판단해야 한다.
    if sat.sum() >= 4:
        slope, intercept = _linfit(vd[sat], idd[sat])
        if np.isfinite(slope) and np.isfinite(intercept) and abs(intercept) > 0:
            lam = slope / intercept              # [1/V]
            m["lambda_per_V"] = _round(lam, 4)
            m["idsat_extrap_A"] = _round(intercept)
            # 출력 저항 = dVd/dId 를 포화 구간에서
            m["r_out_ohm"] = _round(1.0 / slope, 4) if abs(slope) > 0 else None
        else:
            m["lambda_per_V"] = m["idsat_extrap_A"] = m["r_out_ohm"] = None
        m["sat_fit_range_V"] = [_round(float(vd[sat][0]), 3),
                                _round(float(vd[sat][-1]), 3)]
    else:
        m["lambda_per_V"] = m["idsat_extrap_A"] = m["r_out_ohm"] = None
        m["sat_fit_range_V"] = None

    if compliance:
        m["compliance_hit"] = bool(np.any(aid >= 0.95 * compliance))
    return m


# ---------------------------------------------------------------------------
# 최상위: summarize
# ---------------------------------------------------------------------------
def summarize(df: pd.DataFrame, plan: IVPlan, *, bounds=None,
              wl: Optional[float] = None, cox: Optional[float] = None) -> dict:
    """정규화된 DataFrame + plan → 지표 dict.

    bounds 를 주면 W/L 과 Cox 를 거기서 자동으로 꺼내 쓴다(직접 넘긴 값이 우선).
    반환에는 branch 별 지표와, 대표 branch(=on 전류가 가장 큰 것)의 지표가
    최상위에 평평하게 올라온다.
    """
    if bounds is not None:
        wl = wl if wl is not None else bounds.wl_ratio
        if cox is None:
            gate = plan.var1.terminal if plan.kind == "transfer" else (
                plan.var2.terminal if plan.var2 else None)
            cox = bounds.cox_of(gate) if gate else None

    branches = split_branches(df, plan)
    kind = plan.kind
    out_branches: List[dict] = []

    for b in branches:
        entry: dict = {"step": b["step"]}
        for tag in ("forward", "backward"):
            sub = b[tag]
            if sub is None or len(sub) == 0:
                continue
            entry[tag] = _branch_metrics(sub, plan, kind, wl=wl, cox=cox)
        # 히스테리시스: 정/역 Vth 차이
        f, r = entry.get("forward"), entry.get("backward")
        if kind == "transfer" and f and r and f.get("vth_cc") is not None \
                and r.get("vth_cc") is not None:
            entry["hysteresis_V"] = _round(f["vth_cc"] - r["vth_cc"], 3)
        out_branches.append(entry)

    # 대표 branch: on 전류 최대. 판단이 아니라 '가장 정보가 많은 곡선' 선택.
    def _on(e):
        f = e.get("forward") or {}
        return abs(f.get("id_on") or 0.0)

    primary_i = int(np.argmax([_on(e) for e in out_branches])) if out_branches else 0
    primary = (out_branches[primary_i].get("forward") or {}) if out_branches else {}

    # 드레인 측정 레인지. off floor 가 소자 것인지 측정계 바닥인지 가르려면
    # 이게 있어야 한다 — 'limited' 는 그 레인지 아래로 안 내려가므로, Ioff 가
    # 레인지 근처에 붙어 있으면 소자가 아니라 설정이 만든 바닥이다.
    drain_range = None
    r = (plan.ranges or {}).get("D")
    if r is not None:
        drain_range = f"{r.range} {r.mode}"

    result = dict(primary)
    result.update({
        "kind": kind,
        "plan_label": plan.label,
        "meas_range_D": drain_range,
        "n_branches": len(out_branches),
        "primary_step": out_branches[primary_i]["step"] if out_branches else None,
        "hysteresis_V": out_branches[primary_i].get("hysteresis_V") if out_branches else None,
        "branches": out_branches,
        "wl": _round(wl) if wl else None,
        "cox_F_per_cm2": _round(cox) if cox else None,
    })
    return jsonable(result)


def _branch_metrics(sub: pd.DataFrame, plan: IVPlan, kind: str, *, wl, cox) -> dict:
    r = (plan.ranges or {}).get("D")
    floor = range_floor_A(r.range if r is not None else None)
    sweep_t = plan.var1.terminal
    vcol = f"V_{sweep_t}"
    if vcol not in sub.columns:
        return {"error": f"{vcol} 컬럼이 없다 (roles 매핑 확인)"}

    if kind == "transfer":
        vd = plan.constants.get("D")
        if vd is None and "V_D" in sub.columns:
            vd = float(np.median(sub["V_D"]))
        return transfer_metrics(
            sub[vcol].to_numpy(),
            sub["I_D"].to_numpy() if "I_D" in sub.columns else np.full(len(sub), np.nan),
            sub.get(f"I_{sweep_t}"),
            wl=wl, cox=cox, vd=vd, compliance=plan.compliance_of("D"),
            i_range_floor=floor,
        )
    return output_metrics(
        sub[vcol].to_numpy(),
        sub["I_D"].to_numpy() if "I_D" in sub.columns else np.full(len(sub), np.nan),
        compliance=plan.compliance_of("D"),
    )


# ---------------------------------------------------------------------------
# LLM 페이로드
# ---------------------------------------------------------------------------
def downsample_curve(df: pd.DataFrame, plan: IVPlan, n: int = 25) -> dict:
    """대표 곡선을 n 점으로 줄인다. 전류는 log10, Vg 는 선형.

    201점 × 3열을 통째로 넘기면 6~8k 토큰이고 이력까지 쌓이면 5턴에 30k 를
    넘는다. 비용보다 문제는 긴 배열 중간 정보를 잘 못 본다는 것이다.
    """
    b = split_branches(df, plan)
    if not b:
        return {}
    sub = b[0]["forward"]
    sweep_t = plan.var1.terminal
    vcol = f"V_{sweep_t}"
    if vcol not in sub.columns or len(sub) == 0:
        return {}

    idx = np.unique(np.linspace(0, len(sub) - 1, min(n, len(sub))).astype(int))
    s = sub.iloc[idx]
    idd = s["I_D"].to_numpy() if "I_D" in s.columns else np.full(len(s), np.nan)

    curve = {
        "x_name": f"V{sweep_t}",
        "x": [_round(v, 4) for v in s[vcol].to_numpy()],
        "log_id": [_round(v, 4) for v in log10_abs(idd)],
        "id_sign": [int(np.sign(v)) if np.isfinite(v) else 0 for v in idd],
    }
    icol = f"I_{sweep_t}"
    if icol in s.columns:
        curve["log_ig"] = [_round(v, 4) for v in log10_abs(s[icol].to_numpy())]
    if plan.var2 is not None and "step" in sub.columns:
        curve["step"] = _round(float(sub["step"].iloc[0]))
    return jsonable(curve)


def to_llm_payload(m: dict, curve: Optional[dict] = None, *,
                   keep: Sequence[str] = ()) -> dict:
    """지표 dict → LLM 에 넣을 최소 페이로드.

    branches 통째로는 안 넘긴다(토큰). 대표 branch 지표 + 다운샘플 곡선.
    순서: 지표만으로 시작 → 애매한 케이스가 보이면 곡선 추가 → 이상 케이스에서만
    점수를 올린다. 지표 없이 곡선만은 안 되고, 곡선 없이 지표만은 괜찮다.

    **원본 CSV 를 싣는 경로는 없다.** 있었는데 없앴다 — 실측 105,479 자
    (≈30k 토큰) vs 다운샘플 903 자(≈258 토큰)였고, 그 400배로 얻는 것이 없었다.
    원본은 store 가 결과 폴더에 data.csv 로 저장한다. 다시 넣지 말 것.
    """
    default_keep = (
        # transfer — 소자 스펙 시트에 들어가는 다섯 (Vth, SS, µ, Ion/Ioff, 히스테리시스)
        "kind", "polarity", "vth_lin", "vth_cc", "vth_cc_wl_known",
        "vth_onset_V", "turn_on_V", "turn_on_basis", "turn_on_side",
        "id_trend_dec_per_V", "id_edge_trend_dec_per_V", "id_state",
        "ss", "ss_mV_per_dec",
        "ss_points", "ss_window_V", "ss_window_width_V", "ss_window_center_V",
        "decades", "id_on", "id_off", "id_norm", "meas_range_D",
        "mobility_cm2_Vs", "mobility_is_lower_bound",
        "gm_peak_vg", "gm_peak_at_edge", "gm_peak_margin_V",
        "hysteresis_V", "floor_slope",
        "vth_gap_V", "ig_max", "ig_over_id", "ig_over_ioff", "ig_slope",
        "compliance_hit",
        "vg_start", "vg_stop", "vg_step",
        "is_flat", "turn_on_inside_window", "negative_id_fraction",
        # output — 접촉저항(선형 기울기)과 포화·λ (SPICE 피팅 입력)
        "saturation_ratio", "r_on_ohm", "crowding_index", "kink_index",
        "lambda_per_V", "r_out_ohm", "idsat_extrap_A", "sat_fit_range_V",
        "n_branches", "primary_step",
    )
    keys = tuple(keep) or default_keep
    payload = {"metrics": {k: m[k] for k in keys if k in m and m[k] is not None}}
    if curve:
        payload["curve"] = curve
    return jsonable(payload)
