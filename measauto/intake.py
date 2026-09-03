"""intake.py — 가진 자료를 그대로 주면 에이전트가 stack 으로 구조화한다.

왜 필요한가
  설계자료는 벤더·팀마다 형식이 다르다. ε 를 주는 곳도, TOX 만 주는 곳도,
  "SiO2 200nm" 한 줄만 있는 곳도 있다. 정해진 칸을 사람이 채우게 하면
  자료가 바뀔 때마다 막힌다. 그래서 **무엇이든 받아서** 구조화하는 단계를 둔다.

무엇을 에이전트가 하고 무엇을 안 하나
  한다   : 자유형식 자료 → 구조화된 값. 단위 환산(Å↔nm, cm↔m), 항목 이름 맞추기,
           재료 지식으로 빈 칸 메우기, 무엇이 없는지 말하기.
  안 한다: 안전 검사. 그건 여기서 나온 값을 받아 safety.validate 가 한다.
           검사받는 쪽이 검사를 쓰지 않는다는 규칙은 그대로다.

값마다 근거를 같이 받는다 (provenance)
  document          : 자료에 그렇게 적혀 있다 (quote 로 어디인지 남긴다)
  material_knowledge: 자료엔 없고 재료 일반 지식에서 왔다 (SiO2 의 ε=3.9 같은)
  inference         : 자료의 다른 값에서 유도했다 (TOX 와 C 로부터 ε 등)
  assumption        : 근거 없이 관행으로 넣었다  ← 사람이 반드시 확인할 것

이 구분이 이 모듈의 핵심이다. 값 자체보다 '어디서 왔는지'가 검토를 가능하게
한다. assumption 이 붙은 값은 review() 가 따로 모아 보여준다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

STACK_DIR = Path(__file__).with_name("stacks")

BASES = ("document", "material_knowledge", "inference", "assumption")

SYSTEM_PROMPT = """\
당신은 반도체 소자의 설계자료를 읽고, 자동측정 시스템이 쓸 구조화된 소자 정보를
만든다. 자료의 형식은 정해져 있지 않다 — 표일 수도, 문장일 수도, 단편일 수도 있다.

## 하는 일
- 유전막 층 구성(재료·두께·유전율), 소자 기하(W/L), 그리고 자료가 말해주는
  기대 특성(Vth, SS, 특성화한 전압 범위 등)을 뽑는다.
- 단위를 맞춘다. 두께는 nm 로 통일한다. 표의 머리글과 칸 안의 단위가 다르면
  **칸 안에 명시된 단위를 믿고**, 그 사실을 note 에 적는다.
- 자료에 없는 값은 비워 두거나, 채운다면 왜 채웠는지 basis 로 밝힌다.

## 값마다 basis 를 반드시 붙인다
- document           : 자료에 적혀 있다. quote 에 해당 구절/표 항목을 남긴다.
- material_knowledge : 자료엔 없고 재료 일반 지식에서 왔다 (예: SiO2 의 ε≈3.9).
- inference          : 자료의 다른 값에서 유도했다. quote 에 근거가 된 값을 적는다.
- assumption         : 근거 없이 관행으로 넣었다.

**추측한 값을 document 로 표시하지 마라.** 이 구분이 사람이 검토할 수 있게 하는
유일한 장치다. 확실하지 않으면 낮은 confidence 를 주고 questions 에 적어라.

## 절연내압(e_max_MV_per_cm)에 대해
자료에 없으면 비워 두는 편이 낫다. 시스템에 재료 물성표가 있어서 비어 있으면
거기서 채운다. 자료가 내압을 명시한 경우에만 적어라.

## 특성화 범위
자료가 "Vg -20~20 V 에서 측정했다" 같은 정보를 준다면 반드시 옮겨라. 그 범위는
소자가 그 안에서 살아 동작한다는 문서화된 실적이라, 안전 상한을 좁히는 근거가 된다.

## 없는 것은 없다고 말한다
빈 칸을 그럴듯하게 메우지 말고 missing 에 적어라. questions 에는 사람에게 물어야
할 것을 적어라. 측정 조건(스윕 범위·스텝)은 여기서 정하지 않는다 — 다른 단계가
이 값들로부터 계산한다."""


def _layer_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "material": {"type": "string", "description": "재료명 (SiO2, SiNx 등)"},
            "t_nm": {"type": "number", "description": "두께 [nm]"},
            "eps_r": {"type": ["number", "null"], "description": "비유전율. 모르면 null"},
            "e_max_MV_per_cm": {"type": ["number", "null"],
                                "description": "절연내압 [MV/cm]. 자료에 있을 때만"},
        },
        "required": ["material", "t_nm", "eps_r", "e_max_MV_per_cm"],
        "additionalProperties": False,
    }


DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "짧은 식별자 (파일명이 된다)"},
        "description": {"type": "string"},
        "gates": {
            "type": "array",
            "description": "게이트별 유전막. 단일 게이트면 항목 1개.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "BG / TG / G 등"},
                    "opposing": {"type": "array", "items": {"type": "string"},
                                 "description": "이 게이트와 마주보는 단자들 (보통 S, D)"},
                    "dielectric": {"type": "array", "items": _layer_schema()},
                },
                "required": ["name", "opposing", "dielectric"],
                "additionalProperties": False,
            },
        },
        "geometry": {
            "type": "object",
            "properties": {
                "W_um": {"type": ["number", "null"]},
                "L_um": {"type": ["number", "null"]},
            },
            "required": ["W_um", "L_um"],
            "additionalProperties": False,
        },
        "expected": {
            "type": "object",
            "properties": {
                "vth_V": {"type": ["number", "null"]},
                "ss_V_per_dec": {"type": ["number", "null"]},
                "i_off_A": {"type": ["number", "null"]},
                "i_on_A": {"type": ["number", "null"],
                           "description": "자료가 보인 최대 on 전류 [A]"},
                "characterized_vg_range_V": {
                    "type": ["array", "null"], "items": {"type": "number"},
                    "description": "자료가 실제로 측정한 게이트 전압 범위 [min, max]"},
                "characterized_vd_max_V": {"type": ["number", "null"]},
            },
            "required": ["vth_V", "ss_V_per_dec", "i_off_A", "i_on_A",
                         "characterized_vg_range_V", "characterized_vd_max_V"],
            "additionalProperties": False,
        },
        "provenance": {
            "type": "array",
            "description": "위에 넣은 값마다 한 줄씩. 빠뜨리지 말 것.",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string",
                              "description": "예: gates[0].dielectric[1].t_nm"},
                    "basis": {"type": "string", "enum": list(BASES)},
                    "quote": {"type": ["string", "null"],
                              "description": "자료의 해당 구절 또는 유도 근거"},
                    "confidence": {"type": "number", "description": "0~1"},
                },
                "required": ["field", "basis", "quote", "confidence"],
                "additionalProperties": False,
            },
        },
        "missing": {"type": "array", "items": {"type": "string"},
                    "description": "자료에 없어서 못 채운 것"},
        "questions": {"type": "array", "items": {"type": "string"},
                      "description": "사람에게 물어야 할 것"},
        "notes": {"type": "array", "items": {"type": "string"},
                  "description": "단위 불일치 등 읽으면서 눈에 띈 것"},
    },
    "required": ["name", "description", "gates", "geometry", "expected",
                 "provenance", "missing", "questions", "notes"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# 에이전트 호출
# ---------------------------------------------------------------------------
def draft_from_spec(spec_text: str, *, model: str = "claude-opus-5",
                    effort: str = "high", max_tokens: int = 16000,
                    client=None, hint: str = "") -> dict:
    """자유형식 자료 → 구조화된 draft. 안전 검사는 하지 않는다."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    user = (f"## 소자 자료\n{spec_text}\n\n"
            + (f"## 추가 정보 (사람이 준 것)\n{hint}\n\n" if hint else "")
            + "위 자료에서 구조화된 소자 정보를 만들어라. "
              "값마다 basis 를 붙이고, 없는 것은 missing 에 적어라.")

    resp = client.messages.create(
        model=model, max_tokens=max_tokens,
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort,
                       "format": {"type": "json_schema", "schema": DRAFT_SCHEMA}},
    )
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    return json.loads(text)


# ---------------------------------------------------------------------------
# draft → stack / limits
# ---------------------------------------------------------------------------
def to_stack(draft: dict) -> dict:
    """draft → safety 가 읽는 stack dict. 값은 그대로, 형태만 바꾼다."""
    gates: Dict[str, dict] = {}
    for g in draft.get("gates") or []:
        gates[g["name"]] = {
            "opposing": list(g.get("opposing") or []),
            "dielectric": [
                {k: v for k, v in L.items() if v is not None}
                for L in (g.get("dielectric") or [])
            ],
        }

    exp = {k: v for k, v in (draft.get("expected") or {}).items() if v is not None}
    if "characterized_vd_max_V" in exp:
        exp["characterized_vd_V"] = [0.1, exp["characterized_vd_max_V"]]
    if "i_on_A" in exp:
        exp["i_on_A_at_vd0p1_vg20"] = exp["i_on_A"]

    stack = {
        "name": draft.get("name") or "unnamed",
        "description": draft.get("description", ""),
        "terminals": sorted({*gates, "D", "S"}),
        "gates": gates,
        "geometry": {k: v for k, v in (draft.get("geometry") or {}).items()
                     if v is not None},
        "expected": exp,
        "_intake": {
            "provenance": draft.get("provenance") or [],
            "missing": draft.get("missing") or [],
            "questions": draft.get("questions") or [],
            "notes": draft.get("notes") or [],
        },
    }
    if len(gates) == 2:
        stack["gate_gate"] = {"pair": sorted(gates), "v_max": None}
    return stack


def to_limits(draft: dict) -> dict:
    """자료가 특성화 범위를 줬으면 그걸 상한을 좁히는 근거로 쓰게 해둔다."""
    exp = draft.get("expected") or {}
    gates = [g["name"] for g in (draft.get("gates") or [])]
    lim: dict = {
        "_comment": ["intake 가 만든 초안. 값은 draft 의 근거에서 왔다.",
                     "빈 칸은 materials.json 과 기본값이 메운다."],
        "limits": {},
        "provenance": {"source": "intake (에이전트 구조화)",
                       "approved_by": None, "date": None},
    }
    if exp.get("characterized_vg_range_V") and gates:
        lim["policy"] = "characterized_envelope"
        lim["characterized_gate"] = gates[0]
    if exp.get("characterized_vd_max_V") is not None:
        lim["limits"]["pair_v_max"] = {"D-S": float(exp["characterized_vd_max_V"])}
    if exp.get("i_on_A"):
        lim["limits"]["i_compliance_A"] = {"D": float(exp["i_on_A"]) * 10.0}
    return lim


# ---------------------------------------------------------------------------
# 검토 / 저장
# ---------------------------------------------------------------------------
def review(draft: dict) -> str:
    """사람이 훑어볼 요약. assumption 과 낮은 confidence 를 앞에 모은다."""
    prov = draft.get("provenance") or []
    by: Dict[str, List[dict]] = {b: [] for b in BASES}
    for p in prov:
        by.setdefault(p.get("basis", "assumption"), []).append(p)

    out = [f"[intake] {draft.get('name')} — {draft.get('description','')}"]

    weak = by.get("assumption", []) + [p for p in prov
                                       if p.get("basis") != "assumption"
                                       and (p.get("confidence") or 1) < 0.7]
    if weak:
        out.append("\n확인 필요 (근거 없음 또는 확신 낮음):")
        for p in weak:
            out.append(f"  ! {p['field']}  [{p['basis']}] "
                       f"conf={p.get('confidence')}  {p.get('quote') or ''}")

    out.append("\n근거별 개수: " + ", ".join(
        f"{b}={len(by.get(b, []))}" for b in BASES))

    for key, label in (("missing", "자료에 없어 못 채운 것"),
                       ("questions", "사람에게 물을 것"),
                       ("notes", "메모")):
        items = draft.get(key) or []
        if items:
            out.append(f"\n{label}:")
            out += [f"  - {x}" for x in items]
    return "\n".join(out)


def write(draft: dict, *, out_dir: str | Path = STACK_DIR,
          name: Optional[str] = None) -> tuple:
    """stack / limits json 두 개를 쓴다. 반환 (stack_path, limits_path)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    n = name or draft.get("name") or "unnamed"
    sp, lp = d / f"{n}.json", d / f"{n}.limits.json"
    sp.write_text(json.dumps(to_stack(draft), ensure_ascii=False, indent=2),
                  encoding="utf-8")
    lp.write_text(json.dumps(to_limits(draft), ensure_ascii=False, indent=2),
                  encoding="utf-8")
    return sp, lp
