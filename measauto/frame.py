"""frame.py — 장비가 뱉은 DataFrame 을 단자 이름 기준으로 정규화한다.

B1500(pymeasure) 이 주는 컬럼은 슬롯 기준이다:
    "SMU2 Current Measurement (A)", "SMU2 Voltage Output (V)",
    "VAR2 SMU1 Vg (V)"
슬롯 번호는 배선이 바뀌면 바뀌는 값이라 분석 코드가 알면 안 된다.
여기서 한 번만 갈아끼워 두면 metrics/store 는 단자 이름만 본다:
    V_TG, V_BG, V_D, V_S, I_TG, I_BG, I_D, I_S, step

pandas 말고는 아무것도 import 하지 않는다 — 장비 없이 과거 CSV 로
metrics 를 단위테스트할 수 있어야 하므로.
"""

from __future__ import annotations

import re
from typing import Dict, Mapping, Optional

import pandas as pd

_CUR = re.compile(r"SMU(\d+)\s+Current", re.I)
_VOL = re.compile(r"SMU(\d+)\s+Voltage", re.I)
_VAR2 = re.compile(r"VAR2\s+SMU(\d+)", re.I)
_TIME = re.compile(r"time", re.I)


def canonicalize(df: pd.DataFrame, roles: Mapping[str, int]) -> pd.DataFrame:
    """슬롯 기준 컬럼 → 단자 기준 컬럼.

    roles : {"TG": 1, "BG": 2, "D": 3, "S": 4} 처럼 단자 → SMU 채널 번호.
    반환   : 원본은 건드리지 않고 새 DataFrame. 매핑 안 되는 컬럼은 그대로 둔다.
    """
    ch2term: Dict[int, str] = {int(ch): t for t, ch in roles.items()}
    out: Dict[str, pd.Series] = {}
    leftovers: Dict[str, pd.Series] = {}

    for col in df.columns:
        s = df[col]
        m = _VAR2.search(str(col))
        if m:
            t = ch2term.get(int(m.group(1)))
            out["step"] = s
            if t:
                out[f"V_{t}"] = s          # VAR2 채널의 DC 값 = 그 단자의 전압
            continue
        m = _CUR.search(str(col))
        if m:
            t = ch2term.get(int(m.group(1)))
            (out if t else leftovers)[f"I_{t}" if t else str(col)] = s
            continue
        m = _VOL.search(str(col))
        if m:
            t = ch2term.get(int(m.group(1)))
            if t and f"V_{t}" in out:
                # VAR2 로 이미 채운 단자면 sweep 출력이 우선(실측값)
                out[f"V_{t}"] = s
            else:
                (out if t else leftovers)[f"V_{t}" if t else str(col)] = s
            continue
        if _TIME.search(str(col)):
            out["t"] = s
            continue
        leftovers[str(col)] = s

    res = pd.DataFrame({**out, **leftovers})
    return res.reset_index(drop=True)


def fill_constants(df: pd.DataFrame, constants: Mapping[str, float]) -> pd.DataFrame:
    """측정되지 않은 DC 고정 단자의 전압 컬럼을 채워 넣는다.

    B1500 은 sweep/측정 채널만 전압을 돌려주므로, 0 V 로 잡아둔 소스나
    고정 게이트는 컬럼이 아예 없다. 분석에서 'V_S 가 없다'로 갈리지 않게 채운다.
    """
    out = df.copy()
    for t, v in constants.items():
        col = f"V_{t}"
        if col not in out.columns:
            out[col] = float(v)
    return out


def load_legacy_csv(path, roles: Mapping[str, int],
                    constants: Optional[Mapping[str, float]] = None) -> pd.DataFrame:
    """기존 grid_measure 노트북이 저장한 CSV 를 그대로 읽어 정규화한다.

    metrics 를 장비 없이 검증할 때 쓰는 입구. 3단계(반자동)에서 쌓인
    results_idvd/*.csv 가 그대로 들어온다.
    """
    df = pd.read_csv(path)
    df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed")]]
    out = canonicalize(df, roles)
    if constants:
        out = fill_constants(out, constants)
    return out
