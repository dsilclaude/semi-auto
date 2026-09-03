"""Phase 1 — Data-based Pass/Fail (값 기준이 아닌, 무차원 특징 + 비지도 이상탐지)

트랜지스터 transfer 곡선(Id-Vg, grid_measure 의 measure_iv 결과 CSV)을 받아
소자별 Pass/Fail 을 판정한다.  머신러닝 라벨 불필요.

3단 구조 (인수인계 PDF 'Future Work: Data-based Pass/Fail' 의 Phase 1):

    ① 특징 추출  features()    : 곡선 → 무차원 숫자 (on/off비·SS·히스테리시스·단조성…)
                                 절대 전류크기를 안 봐서 CNT·MoS2·유기물 등 재료가 바뀌어도 통함.
    ② 룰 게이트  rule_gate()   : open/short/변조없음 등 '모든 트랜지스터 공통' 명백불량 컷.
    ③ 이상탐지   anomaly()     : 같은 배치 안에서 '튀는' 소자 컷. 한계선을 데이터가 스스로 정함
                                 (robust z-score(MAD) + Mahalanobis, 옵션으로 Isolation Forest).

참고:
- Part Average Testing (AEC-Q001) : 'data 가 한계선을 정한다' 의 산업 표준 → ③ 의 근거.
- Ortiz-Conde et al., Microelectronics Reliability 42 (2002) : Vth 등 파라미터 추출 → ①.
- Liu, Ting, Zhou, "Isolation Forest", ICDM 2008 : ③ 의 옵션 알고리즘.

의존성: numpy, pandas (필수) / scikit-learn (Isolation Forest, 선택) / matplotlib (plot, 선택).
하드웨어 없이 테스트하려면  `python pass_fail_phase1.py`  → 합성 데이터로 데모 실행.
"""

from __future__ import annotations

import glob
import os
import re
import warnings

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 설정 — 셋업에 맞게만 바꾸면 됨 (절대값 '판정 기준' 아님. 측정기 물리 한계값들)
# ---------------------------------------------------------------------------
NOISE_FLOOR_A = 1e-13   # |Id| 가 이 아래면 노이즈(=전류 없음). 측정 레인지에 맞게 조정.
SS_THERMAL_MV = 60.0    # 상온 subthreshold swing 물리하한 [mV/dec] (재료 무관 상수)

# ② 룰 게이트 임계 (전부 무차원 / 보편 기준 — 재료별 튜닝 불필요)
GATE_MIN_ONOFF_DEC   = 1.0   # on/off 비가 이보다 작으면(=1 decade 미만 스위칭) 변조없음
GATE_MIN_MONOTONIC   = 0.60  # forward 곡선 단조성 비율이 이보다 낮으면 곡선 깨짐
GATE_OPEN_FACTOR     = 10.0  # max|Id| < NOISE_FLOOR*이 값  → open(접촉불량)

# ③ 이상탐지 임계
ANOMALY_Z_THRESH     = 3.5   # robust z-score(MAD) 절대값이 이보다 크면 이상
ANOMALY_MAHA_Z       = 3.5   # Mahalanobis 거리의 robust z 가 이보다 크면 이상

# ③ 에 쓰는 '무차원' 특징들 (절대 전류크기 계열은 일부러 제외 → 재료 무관 유지)
ANOMALY_FEATURES = ["on_off_dec", "ss_ratio", "monotonicity", "hyst_norm", "smoothness"]


# ===========================================================================
# 0. 로더 — measure_iv 결과 CSV 에서 (Vg, Id) 뽑기
# ===========================================================================
def load_transfer(df: pd.DataFrame, vg_col: str | None = None,
                  id_col: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    """measure_iv DataFrame 에서 sweep 전압(Vg)과 드레인 전류(Id) 컬럼을 뽑는다.

    컬럼 예: 'SMU1 Voltage Output (V)', 'SMU2 Current Measurement (A)' ...
    - vg_col 미지정: '...Voltage Output...' 컬럼 자동 선택
    - id_col 미지정: Current 컬럼 중 '가장 많이 변하는'(=게이트에 반응하는 드레인) 채널 자동 선택
                     (게이트 누설 채널은 거의 안 변하므로 자연히 제외됨)
    """
    cols = list(df.columns)

    if vg_col is None:
        v_cands = [c for c in cols if "Voltage" in c]
        if not v_cands:
            raise ValueError(f"전압(Voltage) 컬럼을 못 찾음: {cols}")
        # 'Voltage Output'(소스가 sweep 한 전압) 우선
        vg_col = next((c for c in v_cands if "Output" in c), v_cands[0])

    if id_col is None:
        i_cands = [c for c in cols if "Current" in c]
        if not i_cands:
            raise ValueError(f"전류(Current) 컬럼을 못 찾음: {cols}")
        # 최대 전류크기(max|I|)가 가장 큰 채널 = 드레인. 게이트 누설은 항상 작으므로
        # short(드레인이 compliance 에 평평하게 붙음)에서도 드레인을 올바로 고른다.
        def mag(c):
            v = pd.to_numeric(df[c], errors="coerce").abs().to_numpy()
            v = v[np.isfinite(v)]
            return v.max() if v.size else -np.inf


            
        id_col = max(i_cands, key=mag)

    vg = pd.to_numeric(df[vg_col], errors="coerce").to_numpy(dtype=float)
    idd = pd.to_numeric(df[id_col], errors="coerce").to_numpy(dtype=float)
    return vg, idd


def _split_sweep(vg: np.ndarray):
    """LINEAR_DOUBLE(왕복) 곡선을 forward / backward 로 나눈다.
    전압 최대 지점을 꼭짓점으로 봄. 왕복이 아니면 backward = None."""
    k = int(np.argmax(vg))
    if k == 0 or k >= len(vg) - 1:        # 단조(왕복 아님)
        return slice(0, len(vg)), None
    return slice(0, k + 1), slice(k, len(vg))


# ===========================================================================
# ① 특징 추출 — 곡선 → 무차원 숫자
# ===========================================================================
def features(vg: np.ndarray, idd: np.ndarray,
             noise_floor: float = NOISE_FLOOR_A) -> dict:
    """transfer 곡선에서 재료 무관(무차원/자기정규화) 특징을 뽑는다."""
    vg = np.asarray(vg, float)
    idd = np.asarray(idd, float)
    has_nan = bool(np.any(~np.isfinite(vg)) or np.any(~np.isfinite(idd)))

    absI = np.abs(idd)
    absI = np.where(np.isfinite(absI), absI, np.nan)
    max_absI = float(np.nanmax(absI)) if np.any(np.isfinite(absI)) else 0.0
    min_absI = float(np.nanmin(absI)) if np.any(np.isfinite(absI)) else 0.0

    # log 전류 (노이즈 바닥으로 클램프 → 스케일 압축 + 0 나눗셈 방지)
    clamped = np.clip(absI, noise_floor, None)
    logI = np.log10(clamped)

    fwd, bwd = _split_sweep(vg)
    vg_f, logI_f = vg[fwd], logI[fwd]

    # --- on/off 비 (decade) : 비율이라 무차원 ---
    on_off_dec = float(np.nanmax(logI) - np.nanmin(logI))

    # --- 극성 (n: Vg↑→I↑ / p: Vg↑→I↓) : forward 추세로 판정 ---
    trend = logI_f[-1] - logI_f[0]
    polarity = "n" if trend >= 0 else "p"

    # --- 단조성 : forward 에서 추세와 같은 방향으로 가는 구간 비율 (0~1, 순수 모양) ---
    dlog = np.diff(logI_f)
    if dlog.size and np.any(dlog != 0):
        sign = np.sign(trend) if trend != 0 else 1.0
        monotonicity = float(np.mean(np.sign(dlog) == sign))
    else:
        monotonicity = 0.0

    # --- Subthreshold Swing [mV/dec] : turn-on 전이의 최급경사 (재료 무관, 60mV 하한 대비) ---
    ss_mV = np.nan
    rng = np.nanmax(logI_f) - np.nanmin(logI_f)
    if rng > 0.5 and dlog.size:                       # 최소 0.5 decade 는 켜져야 의미
        dV = np.abs(np.diff(vg_f))
        lo = np.nanmin(logI_f)
        with np.errstate(divide="ignore", invalid="ignore"):
            for i in range(len(dlog)):
                rising = dlog[i] * np.sign(trend) > 1e-6      # turn-on 방향으로 상승
                in_sub = logI_f[i] < lo + 0.6 * rng           # subthreshold(아랫부분)
                if rising and in_sub:
                    cand = dV[i] / abs(dlog[i]) * 1000.0       # V/dec → mV/dec
                    ss_mV = cand if np.isnan(ss_mV) else min(ss_mV, cand)
    ss_ratio = float(ss_mV / SS_THERMAL_MV) if np.isfinite(ss_mV) else np.nan

    # --- 히스테리시스 : forward vs backward log 전류 면적차 / on_off (무차원) ---
    hyst_dec, hyst_norm = 0.0, 0.0
    if bwd is not None:
        vb, lb = vg[bwd][::-1], logI[bwd][::-1]       # 증가 순으로 뒤집어 보간
        order = np.argsort(vg_f)
        lb_on_f = np.interp(vg_f[order], vb, lb)
        hyst_dec = float(np.mean(np.abs(logI_f[order] - lb_on_f)))
        hyst_norm = float(hyst_dec / on_off_dec) if on_off_dec > 1e-9 else 0.0

    # --- 매끄러움(jaggedness) : log 전류 2차차분 크기 / 진폭 (무차원, 클수록 거칢) ---
    if logI_f.size >= 3 and rng > 0:
        smoothness = float(np.mean(np.abs(np.diff(logI_f, 2))) / rng)
    else:
        smoothness = 0.0

    return {
        "n_points": int(len(vg)),
        "max_absI": max_absI,
        "min_absI": min_absI,
        "on_off_dec": on_off_dec,
        "polarity": polarity,
        "monotonicity": monotonicity,
        "ss_mV": float(ss_mV) if np.isfinite(ss_mV) else np.nan,
        "ss_ratio": ss_ratio,
        "hyst_dec": hyst_dec,
        "hyst_norm": hyst_norm,
        "smoothness": smoothness,
        "has_nan": has_nan,
    }


# ===========================================================================
# ② 룰 게이트 — 명백 불량 즉시 컷 (모든 트랜지스터 공통, 재료 무관)
# ===========================================================================
def rule_gate(feat: dict, noise_floor: float = NOISE_FLOOR_A) -> tuple[bool, str]:
    """무차원 특징으로 catastrophic 불량을 판정. 반환 (passed, reason)."""
    if feat["has_nan"]:
        return False, "NAN/측정오류"
    if feat["max_absI"] < noise_floor * GATE_OPEN_FACTOR:
        return False, "OPEN(전류없음/접촉불량)"
    if feat["on_off_dec"] < GATE_MIN_ONOFF_DEC:
        # 변조 없음. 전류가 계속 큰 채로 안 변하면 SHORT, 작은 채로면 동작안함.
        kind = "SHORT" if feat["min_absI"] > noise_floor * 1e3 else "변조없음"
        return False, f"{kind}(on/off<{GATE_MIN_ONOFF_DEC:.0f}dec)"
    if feat["monotonicity"] < GATE_MIN_MONOTONIC:
        return False, f"단조성붕괴({feat['monotonicity']:.2f})"
    return True, "gate-pass"


# ===========================================================================
# ③ 비지도 이상탐지 — 같은 배치 안에서 튀는 소자 (한계선을 데이터가 정함)
# ===========================================================================
def _robust_z(x: np.ndarray) -> np.ndarray:
    """median/MAD 기반 robust z-score (Iglewicz-Hoaglin modified z-score).
    값이 거의 일정해 MAD=0 이면 MeanAD 로 대체(소수의 이상치가 masking 되는 것 방지)."""
    x = np.asarray(x, float)
    med = np.nanmedian(x)
    mad = np.nanmedian(np.abs(x - med))
    if mad > 1e-12:
        return 0.6745 * (x - med) / mad          # 0.6745 = 정규분포 환산상수
    mean_ad = np.nanmean(np.abs(x - med))         # MAD=0 일 때 MeanAD 대체
    return np.zeros_like(x) if mean_ad <= 1e-12 else (x - med) / (1.253 * mean_ad)


def anomaly(feat_df: pd.DataFrame, feature_cols=ANOMALY_FEATURES,
            z_thresh: float = ANOMALY_Z_THRESH, maha_z: float = ANOMALY_MAHA_Z,
            use_iforest: bool = True) -> pd.DataFrame:
    """gate 를 통과한 소자들의 특징행렬에서 이상치를 찾는다.
    반환: feat_df + [robust_z_max, maha_z, iforest, is_outlier, anomaly_reason]."""
    out = feat_df.copy()
    n = len(out)
    cols = [c for c in feature_cols if c in out.columns]
    X = np.array(out[cols].apply(pd.to_numeric, errors="coerce").to_numpy(float))

    # 결측은 컬럼 중앙값으로 대치 (이상탐지 계산용)
    for j in range(X.shape[1]):
        m = np.nanmedian(X[:, j])
        X[np.isnan(X[:, j]), j] = m if np.isfinite(m) else 0.0

    if n < 3:
        # 표본이 너무 적으면 이상탐지 불가 — 통과 처리하고 경고
        out["robust_z_max"] = 0.0
        out["maha_z"] = 0.0
        out["iforest"] = 0
        out["is_outlier"] = False
        out["anomaly_reason"] = "표본<3, 이상탐지 생략"
        return out

    # (a) feature 별 robust z-score → 최대 절대값
    Z = np.column_stack([_robust_z(X[:, j]) for j in range(X.shape[1])])
    z_max = np.nanmax(np.abs(Z), axis=1)
    z_worst = np.array(cols)[np.nanargmax(np.abs(Z), axis=1)]

    # (b) Mahalanobis 거리 (특징 간 상관 고려) → 거리들에 다시 robust z
    Xc = X - np.nanmedian(X, axis=0)
    cov = np.cov(Xc, rowvar=False)
    cov = np.atleast_2d(cov) + np.eye(X.shape[1]) * 1e-9   # 특이행렬 방지
    try:
        inv = np.linalg.pinv(cov)
        maha = np.sqrt(np.einsum("ij,jk,ik->i", Xc, inv, Xc))
    except np.linalg.LinAlgError:
        maha = np.zeros(n)
    maha_zscore = _robust_z(maha)

    # (c) Isolation Forest (sklearn 있을 때만)
    iforest = np.zeros(n, dtype=int)
    if use_iforest:
        try:
            from sklearn.ensemble import IsolationForest
            if n >= 8:
                clf = IsolationForest(random_state=0, contamination="auto")
                iforest = clf.fit_predict(X)        # -1 = 이상, 1 = 정상
        except ImportError:
            pass

    is_out = (z_max > z_thresh) | (np.abs(maha_zscore) > maha_z) | (iforest == -1)

    reasons = []
    for i in range(n):
        r = []
        if z_max[i] > z_thresh:
            r.append(f"{z_worst[i]} z={z_max[i]:.1f}")
        if abs(maha_zscore[i]) > maha_z:
            r.append(f"maha z={maha_zscore[i]:.1f}")
        if iforest[i] == -1:
            r.append("iforest")
        reasons.append(", ".join(r) if r else "")

    out["robust_z_max"] = z_max
    out["maha_z"] = maha_zscore
    out["iforest"] = iforest
    out["is_outlier"] = is_out
    out["anomaly_reason"] = reasons
    return out


# ===========================================================================
# 파이프라인 — 곡선 묶음 → 최종 Pass/Fail 표
# ===========================================================================
def classify_batch(curves: dict[str, pd.DataFrame],
                   noise_floor: float = NOISE_FLOOR_A,
                   use_iforest: bool = True) -> pd.DataFrame:
    """{subsite_id: measure_iv DataFrame} 묶음 → 소자별 판정 표.

    절차: 각 곡선 ①특징 → ②게이트.  게이트 통과분만 모아 ③이상탐지.
    최종 PASS = 게이트통과 AND 이상아님.
    """
    rows = []
    for sub_id, df in curves.items():
        try:
            vg, idd = load_transfer(df)
            feat = features(vg, idd, noise_floor=noise_floor)
            passed, reason = rule_gate(feat, noise_floor=noise_floor)
        except Exception as e:                    # 깨진 파일도 Fail 로 흡수
            feat = {c: np.nan for c in ANOMALY_FEATURES}
            passed, reason = False, f"파싱오류:{e}"
        rows.append({"subsite": sub_id, "gate_pass": passed,
                     "gate_reason": reason, **feat})

    res = pd.DataFrame(rows).set_index("subsite")

    # ③ 게이트 통과분만 이상탐지
    gate_ok = res[res["gate_pass"]].copy()
    if len(gate_ok):
        scored = anomaly(gate_ok, use_iforest=use_iforest)
        for col in ["robust_z_max", "maha_z", "iforest", "is_outlier", "anomaly_reason"]:
            res.loc[scored.index, col] = scored[col]

    # 최종 판정
    res["is_outlier"] = res.get("is_outlier", False)
    res["is_outlier"] = res["is_outlier"].fillna(False).astype(bool)
    res["verdict"] = np.where(
        ~res["gate_pass"], "FAIL",
        np.where(res["is_outlier"], "FAIL", "PASS"))
    res["reason"] = np.where(
        ~res["gate_pass"], res["gate_reason"],
        np.where(res["is_outlier"], "이상치:" + res.get("anomaly_reason", "").astype(str), "정상"))

    front = ["verdict", "reason", "gate_pass", "on_off_dec", "ss_mV",
             "monotonicity", "hyst_norm", "smoothness"]
    front = [c for c in front if c in res.columns] + \
            [c for c in res.columns if c not in front]
    return res[front]


def classify_dir(results_dir: str, pattern: str = "subsite_*.csv",
                 exclude: str = "_sampling", **kw) -> pd.DataFrame:
    """results 폴더의 measure_iv CSV 들을 읽어 일괄 판정.
    (sampling 파일은 transfer 곡선이 아니므로 기본 제외)"""
    curves = {}
    for path in sorted(glob.glob(os.path.join(results_dir, pattern))):
        if exclude and exclude in os.path.basename(path):
            continue
        sub = re.sub(r"^subsite_|\.csv$", "", os.path.basename(path))
        curves[sub] = pd.read_csv(path, index_col=0)
    if not curves:
        raise FileNotFoundError(f"{results_dir} 에서 '{pattern}' 파일을 못 찾음")
    return classify_batch(curves, **kw)


# ===========================================================================
# (선택) plot — 판정 결과를 곡선으로 확인
# ===========================================================================
def plot_curve(df: pd.DataFrame, title: str = "", logy: bool = True):
    """단일 transfer 곡선 그리기 (matplotlib 필요)."""
    import matplotlib.pyplot as plt
    vg, idd = load_transfer(df)
    fig, ax = plt.subplots(figsize=(5, 4))
    y = np.abs(idd) if logy else idd
    ax.plot(vg, y, "o-", ms=3)
    if logy:
        ax.set_yscale("log")
    ax.set_xlabel("Vg (V)")
    ax.set_ylabel("|Id| (A)" if logy else "Id (A)")
    ax.set_title(title)
    fig.tight_layout()
    return fig


# ===========================================================================
# 합성 데이터 — 하드웨어 없이 테스트용 (정상/불량 여러 종류)
# ===========================================================================
def make_synthetic_transfer(kind: str = "good", nop: int = 31, seed: int = 0,
                            ion: float = 1e-4) -> pd.DataFrame:
    """가짜 transfer 곡선 생성 (measure_iv 와 같은 컬럼 구조, 왕복 sweep).
    kind: good / weak(약한변조) / open / short / leaky(누설큼) / noisy / hysteretic."""
    rng = np.random.default_rng(seed)
    vg_up = np.linspace(0, 3, nop)
    vg = np.concatenate([vg_up, vg_up[::-1]])         # LINEAR_DOUBLE 왕복

    def ideal(v, vth=1.0, ss_dec=0.1, ioff=1e-12, on=ion):
        sub = ioff * 10 ** (np.clip(v - vth, None, 0) / ss_dec)   # subthreshold
        above = on * np.clip(v - vth, 0, None) ** 1.3 / (3 - vth) ** 1.3
        return sub + above

    if kind == "good":
        idd = ideal(vg)
    elif kind == "weak":                              # on/off 비 작음 → 게이트 컷
        idd = ideal(vg, ioff=1e-7, on=1e-6)
    elif kind == "open":                              # 전류 없음(노이즈 바닥) → 게이트 컷
        idd = 1e-14 * rng.standard_normal(len(vg))    # 드레인 dead (sub-pA)
    elif kind == "short":                             # compliance 고정 → 게이트 컷
        idd = np.full_like(vg, 1e-3)
    elif kind == "leaky":                             # 누설 큼(off 높음) → 이상치 후보
        idd = ideal(vg, ioff=1e-9)
    elif kind == "noisy":                             # 거친 곡선 → 이상치 후보
        idd = ideal(vg) * (1 + 0.8 * rng.standard_normal(len(vg)))
        idd = np.abs(idd)
    elif kind == "hysteretic":                        # 히스테리시스 큼 → 이상치 후보
        shift = np.concatenate([np.zeros(nop), np.full(nop, 1.2)])
        idd = ideal(vg + shift)
    else:
        raise ValueError(kind)

    # 게이트 누설 = 드레인 크기의 작은 비율 (open 이면 게이트도 작아짐 → 드레인이 올바로 선택됨)
    gate_leak = (np.nanmax(np.abs(idd)) * 1e-4 + 1e-15) * rng.standard_normal(len(vg))
    return pd.DataFrame({
        "SMU1 Current Measurement (A)": gate_leak,    # 게이트 누설(작음)
        "SMU2 Current Measurement (A)": idd,          # 드레인(스위칭)
        "SMU1 Voltage Output (V)": vg,
    })


def _demo():
    """합성 배치로 전체 파이프라인 시연."""
    batch = {}
    # 정상 다수 + 불량 소수 (이상탐지는 '대부분 정상' 가정)
    for i in range(8):
        batch[f"good{i}"] = make_synthetic_transfer("good", seed=i, ion=1e-4 * (1 + 0.05 * i))
    batch["open1"] = make_synthetic_transfer("open", seed=10)
    batch["short1"] = make_synthetic_transfer("short", seed=11)
    batch["weak1"] = make_synthetic_transfer("weak", seed=12)
    batch["leaky1"] = make_synthetic_transfer("leaky", seed=13)
    batch["noisy1"] = make_synthetic_transfer("noisy", seed=14)
    batch["hyst1"] = make_synthetic_transfer("hysteretic", seed=15)

    res = classify_batch(batch)
    pd.set_option("display.width", 160, "display.max_columns", 30)
    show = res[["verdict", "reason", "on_off_dec", "ss_mV",
                "monotonicity", "hyst_norm", "smoothness"]].round(3)
    print("\n=== Phase 1 Pass/Fail 데모 (합성 데이터) ===\n")
    print(show.to_string())
    print(f"\nPASS {int((res['verdict'] == 'PASS').sum())} / "
          f"FAIL {int((res['verdict'] == 'FAIL').sum())}  (총 {len(res)})")
    return res


if __name__ == "__main__":
    # 실제 results 폴더가 있으면 그걸로, 없으면 합성 데모.
    here = os.path.dirname(os.path.abspath(__file__))
    rdir = os.path.join(here, "results")
    if os.path.isdir(rdir) and glob.glob(os.path.join(rdir, "subsite_*.csv")):
        print(f"results 폴더 판정: {rdir}")
        res = classify_dir(rdir)
        print(res[["verdict", "reason"]].to_string())
    else:
        warnings.simplefilter("ignore")
        _demo()
