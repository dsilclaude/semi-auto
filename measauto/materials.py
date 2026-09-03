"""materials.py — 유전막 재료 물성표를 읽고 층에 붙여준다.

왜 stack 이 아니라 여기 있나
  절연내압은 웨이퍼가 아니라 **재료**에 딸린 값이다. SiO2 는 어느 웨이퍼든
  SiO2 다. 웨이퍼마다 확인을 받으려 하면 매번 막히지만, 재료표는 한 번
  검토받아 두면 이후 모든 stack 에 적용된다.

  덕분에 stack json 에는 어느 설계자료에나 반드시 있는 것 — 재료 이름과
  두께 — 만 적으면 된다. 그게 이 시스템이 특정 자료에 안 묶이는 이유다.

우선순위 (좁은 것이 이긴다)
  1. 층에 직접 적힌 값      (그 웨이퍼만의 실측/자료값)
  2. 재료표                 (문헌 통상값)
  3. 없으면 예외            — 조용히 추측하지 않는다
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

MATERIALS_PATH = Path(__file__).with_name("materials.json")

_cache: Optional[dict] = None


class UnknownMaterialError(KeyError):
    """재료표에 없는 재료. 통상값을 지어내느니 멈춘다."""


def load_materials(path: str | Path | None = None) -> dict:
    global _cache
    # utf-8-sig — 편집기가 BOM 을 붙여도 읽히게 (json 은 BOM 에서 바로 깨진다)
    if path is not None:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    if _cache is None:
        with open(MATERIALS_PATH, "r", encoding="utf-8-sig") as f:
            _cache = json.load(f)
    return _cache


def resolve_layer(layer: dict, *, materials: dict | None = None,
                  field_basis: str | None = None) -> dict:
    """층 하나에 eps_r 과 e_max_MV_per_cm 을 채워 돌려준다(원본은 안 건드림).

    field_basis : "design"(기본) | "breakdown". 재료표의 policy 가 기본값.
    """
    m = materials or load_materials()
    table: Dict[str, dict] = m.get("materials", {})
    basis = field_basis or (m.get("policy") or {}).get("field_basis", "design")
    key = "e_design_MV_per_cm" if basis == "design" else "e_breakdown_MV_per_cm"

    out = dict(layer)
    name = str(layer.get("material", "")).strip()
    props = table.get(name)

    if out.get("eps_r") is None:
        if props is None or props.get("eps_r") is None:
            raise UnknownMaterialError(
                f"'{name}' 의 eps_r 을 찾을 수 없다. 층에 직접 적거나 "
                f"materials.json 에 추가할 것.")
        out["eps_r"] = props["eps_r"]

    if out.get("e_max_MV_per_cm") is None:
        if props is None:
            raise UnknownMaterialError(
                f"재료 '{name}' 가 materials.json 에 없다.\n"
                f"  추가할 곳: {MATERIALS_PATH}  →  materials.{name}\n"
                f"  필요한 값: eps_r, e_breakdown_MV_per_cm, e_design_MV_per_cm\n"
                f"  (문헌값이라도 출처를 note 에 남길 것. 지어내지 말 것)")
        if props.get(key) is None:
            raise UnknownMaterialError(
                f"재료 '{name}' 에 {key} 가 없다 (materials.json).")
        out["e_max_MV_per_cm"] = props[key]
        out["_e_max_source"] = f"materials.json / {name} / {key}"
    else:
        out["_e_max_source"] = "층에 직접 지정됨"

    return out


def resolve_layers(layers, **kw) -> list:
    return [resolve_layer(L, **kw) for L in layers]
