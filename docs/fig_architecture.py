"""docs/fig_architecture.py — measauto 구성도를 그린다.

    python docs\fig_architecture.py

박스와 화살표는 실제 import 그래프와 session.run_site 의 실행 순서에서 가져왔다.
구조가 바뀌면 이 파일을 고쳐서 다시 돌린다 — 그림과 코드가 어긋나지 않게.

읽는 법
  세로 위치 = 실행 순서 (① 입력 → ⑥ 기록)
  색        = 누가 고치는가 (사람 / AI / 코드 / 장비)
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

# --- 색: 누가 고치는가 -------------------------------------------------------
HUMAN = dict(fc="#EAF2FA", ec="#2E6DA4")     # 사람이 쓰는 값
AI    = dict(fc="#FDF2E3", ec="#C8791E")     # AI 가 만드는 값
CODE  = dict(fc="#F5F5F5", ec="#4A4A4A")     # 코드 (안 바뀜)
HW    = dict(fc="#E3E3E3", ec="#1F1F1F")     # 장비에 고정
GATE  = dict(fc="#FBEDEC", ec="#B3261E")     # 안전 관문

FLOW = "#333333"
THIN = "#9A9A9A"

fig = plt.figure(figsize=(15.5, 11.5), dpi=150)
ax = fig.add_axes((0, 0, 1, 1))
ax.set_xlim(0, 100)
ax.set_ylim(-9, 109)
ax.axis("off")


def box(x0, x1, y0, y1, title, sub="", style=CODE, lw=1.6, ts=11.5, ss=8.8,
        z=3, mono=True):
    ax.add_patch(FancyBboxPatch(
        (x0, y0), x1 - x0, y1 - y0, boxstyle="round,pad=0,rounding_size=0.9",
        linewidth=lw, zorder=z, **style))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    dy = 1.5 if sub else 0
    ax.text(cx, cy + dy, title, ha="center", va="center", zorder=z + 1,
            fontsize=ts, fontweight="bold", color="#111111",
            family="Consolas" if mono and title.isascii() else "Malgun Gothic")
    if sub:
        ax.text(cx, cy - 2.1, sub, ha="center", va="center", zorder=z + 1,
                fontsize=ss, color="#333333", linespacing=1.45)


def arrow(p0, p1, color=FLOW, lw=2.0, rad=0.0, z=5, ls="-"):
    ax.add_patch(FancyArrowPatch(
        p0, p1, arrowstyle="-|>", mutation_scale=17, linewidth=lw,
        color=color, zorder=z, linestyle=ls, shrinkA=0, shrinkB=0,
        connectionstyle=f"arc3,rad={rad}"))


def step(y, num, name):
    ax.text(5.0, y, num, ha="center", va="center", fontsize=21,
            color="#2E6DA4", fontweight="bold")
    ax.text(5.0, y - 4.4, name, ha="center", va="center", fontsize=10.5,
            color="#444444")


# --- 제목 --------------------------------------------------------------------
ax.text(50, 106.5, "measauto — 소자 측정 자동화 시스템 구성",
        ha="center", va="center", fontsize=22, fontweight="bold")
ax.text(50, 102.3,
        "세로 위치 = 실행 순서    ·    색 = 누가 고치는가",
        ha="center", va="center", fontsize=11.5, color="#555555")

for i, (lbl, sty) in enumerate([("사람이 쓰는 값", HUMAN), ("AI 가 만드는 값", AI),
                                ("코드 (안 바뀜)", CODE), ("장비에 고정", HW)]):
    x = 26.5 + i * 13.0
    ax.add_patch(FancyBboxPatch((x, 98.4), 2.0, 2.0,
                                boxstyle="round,pad=0,rounding_size=0.4",
                                linewidth=1.4, **sty))
    ax.text(x + 2.9, 99.4, lbl, ha="left", va="center", fontsize=10,
            color="#333333")

# --- session.py 가 감싸는 영역 (루프) ----------------------------------------
ax.add_patch(FancyBboxPatch(
    (10.6, 12.0), 86.0, 73.5, boxstyle="round,pad=0,rounding_size=1.4",
    fc="none", ec="#BBBBBB", linewidth=1.5, linestyle=(0, (7, 5)), zorder=1))
ax.text(95.5, 86.4, "session.py — 이 루프를 돌린다", ha="right", va="bottom",
        fontsize=10.5, color="#777777", style="italic")

# --- ① 입력 -----------------------------------------------------------------
step(93.0, "①", "입력")
box(12, 41, 88.5, 97.0, "stacks/sd_w24.json",
    "층 구조 · 두께 · 전압 한계 · 실측값", HUMAN)
box(45, 68, 88.5, 97.0, "materials.json",
    "유전율 · 항복 전계", HUMAN)
box(72, 95, 88.5, 97.0, "utils/w24_4pt.csv",
    "소자 좌표 4점", HUMAN)

# --- ② 기준안 ---------------------------------------------------------------
step(80.0, "②", "기준안")
box(12, 51, 75.5, 84.0, "seed.py",
    "실측이 있으면 그 주변으로 좁히고,\n없으면 좁은 창에서 시작한다", CODE)
box(55, 95, 75.5, 84.0, "safety.load_stack  →  Bounds",
    "직렬 유전체에서 전압 상한을 계산한다\nV_max = min(E_i,max · ε_i · Σ t_j/ε_j)", CODE)

# --- ③ 조건 결정 (AI) -------------------------------------------------------
step(65.0, "③", "조건 결정")
ax.add_patch(FancyBboxPatch(
    (12, 57.5), 58.0, 14.0, boxstyle="round,pad=0,rounding_size=1.1",
    fc="#FEF9F1", ec="#C8791E", linewidth=2.0, zorder=2))
ax.text(41.0, 69.9, "agent/    값(JSON)만 만든다 — 코드는 만들지 않는다",
        ha="center", va="center", fontsize=10.8, fontweight="bold",
        color="#8A5210")
box(14.0, 31.0, 59.0, 67.6, "prompt.py",
    "목적 · 소자 정보\n앞 소자 결과", AI, ts=10.8, ss=8.4)
box(32.5, 49.5, 59.0, 67.6, "policy.py",
    "Claude API 호출\n실패하면 사람에게", AI, ts=10.8, ss=8.4)
box(51.0, 68.0, 59.0, 67.6, "schema.py",
    "JSON 스키마 강제\n필드 형식 검사", AI, ts=10.8, ss=8.4)
box(76, 95, 57.5, 71.5, "Claude API", "대화형 판단", AI, ts=12.5)
arrow((68.3, 64.5), (75.7, 64.5), "#C8791E", 1.7)
arrow((75.7, 61.5), (68.3, 61.5), "#C8791E", 1.7)

# --- ④ 검증 (관문) ----------------------------------------------------------
step(50.0, "④", "검증")
box(12, 70, 45.0, 55.0, "safety.validate(plan, bounds)",
    "전압 · 전류 · 점 수 · 컴플라이언스를 전부 검사\n위반이면 여기서 멈춘다 — 장비에 닿지 않는다",
    GATE, lw=3.0, ts=13)
ax.text(76.5, 52.0, "에이전트는 검증기의 존재를 모른다.",
        ha="left", va="center", fontsize=10, color="#B3261E",
        fontweight="bold")
ax.text(76.5, 48.2, "그래서 프롬프트를 바꿔도\n이 관문은 흔들리지 않는다.",
        ha="left", va="center", fontsize=9.5, color="#666666", linespacing=1.5)

# --- ⑤ 측정 -----------------------------------------------------------------
step(35.5, "⑤", "측정")
box(12, 36, 29.5, 41.5, "executor.py",
    "좌표 이동 → 스윕 실행\n버스 상태 먼저 확인", CODE)
box(40, 64, 29.5, 41.5, "drivers/", "s300.py   ·   b1500.py", HW)
box(68, 95, 29.5, 41.5, "S300 프로버   ·   B1500A",
    "GPIB0::28   ·   GPIB0::17", HW, ts=11)
arrow((36.3, 35.5), (39.7, 35.5), FLOW)
arrow((64.3, 35.5), (67.7, 35.5), FLOW)

# --- ⑥ 해석 · 기록 ----------------------------------------------------------
step(20.0, "⑥", "해석 · 기록")
box(12, 36, 14.0, 26.0, "metrics.py",
    "Vth · SS · 이동도\non/off · 히스테리시스", CODE)
box(40, 64, 14.0, 26.0, "store.py",
    "data.csv · plan.json\nmetrics.json · PNG", CODE)
box(68, 95, 14.0, 26.0, "report.py  ·  plotting.py",
    "판단 근거 리포트 · Origin 규격 그림", CODE, ts=11)
arrow((36.3, 20.0), (39.7, 20.0), FLOW)
arrow((64.3, 20.0), (67.7, 20.0), FLOW)

# --- plan.py: 공통 어휘 ------------------------------------------------------
box(12, 95, 3.5, 10.0, "plan.py",
    "모든 모듈이 공유하는 어휘 (IVPlan) — 아무것도 import 하지 않는다",
    CODE, ts=11.5, ss=9.2)

# --- 세로 흐름 --------------------------------------------------------------
arrow((26.5, 88.2), (26.5, 84.3))                       # stacks → seed
arrow((38.0, 88.2), (62.0, 84.3), THIN, 1.4, rad=-0.10)  # stacks → bounds
arrow((56.5, 88.2), (78.0, 84.3), THIN, 1.4, rad=0.10)   # materials → bounds
arrow((54.7, 79.8), (51.3, 79.8), THIN, 1.4)             # bounds → seed
arrow((26.5, 75.2), (26.5, 71.8))                       # seed → agent
ax.text(27.8, 73.5, "기준안", ha="left", va="center", fontsize=9.5,
        color="#555555")
# bounds → validate (agent 오른쪽 통로로 내려간다 — 에이전트를 거치지 않는다)
ax.plot([73.0, 73.0], [75.4, 50.0], color=THIN, lw=1.4, zorder=1)
arrow((73.0, 50.0), (70.3, 50.0), THIN, 1.4)
arrow((26.5, 57.2), (26.5, 55.3))                       # agent → safety
ax.text(27.8, 56.3, "JSON patch", ha="left", va="center", fontsize=9.5,
        color="#555555")
arrow((26.5, 44.7), (26.5, 41.8))                       # safety → executor
ax.text(27.8, 43.3, "통과한 조건만", ha="left", va="center", fontsize=9.5,
        color="#555555")
arrow((26.5, 29.2), (26.5, 26.3))                       # 측정 데이터 → metrics
ax.text(27.8, 27.8, "측정 데이터", ha="left", va="center", fontsize=9.5,
        color="#555555")

# 좌표 → executor (오른쪽 통로)
ax.plot([83.5, 98.4], [88.2, 88.2], color=THIN, lw=1.4, zorder=1)
ax.plot([98.4, 98.4], [88.2, 35.5], color=THIN, lw=1.4, zorder=1)
arrow((98.4, 35.5), (95.3, 35.5), THIN, 1.4)

# 되먹임: metrics → agent (왼쪽 통로)
ax.plot([12.0, 7.4], [17.5, 17.5], color="#C8791E", lw=2.0, zorder=5)
ax.plot([7.4, 7.4], [17.5, 63.3], color="#C8791E", lw=2.0, zorder=5)
arrow((7.4, 63.3), (11.7, 63.3), "#C8791E", 2.0)
ax.text(8.7, 40.0, "측정 지표 + 전체 CSV", ha="center", va="center",
        fontsize=10, color="#C8791E", rotation=90, fontweight="bold")

# --- 불변 조건 ---------------------------------------------------------------
ax.text(12, -1.6, "불변 조건", ha="left", va="top", fontsize=10.5,
        fontweight="bold", color="#B3261E")
ax.text(23, -1.6,
        "agent/ 는 drivers 를 import 하지 않는다 — 에이전트가 만든 값은 반드시 ④ 를 지난 뒤에야 장비에 닿는다.\n"
        "drivers/ 는 measauto 의 어떤 모듈도 import 하지 않는다 — 장비를 바꿔도 위쪽은 그대로다.",
        ha="left", va="top", fontsize=9.8, color="#444444", linespacing=1.6)

out = Path(__file__).with_name("fig_architecture.png")
fig.savefig(out, dpi=150, facecolor="white")
print(f"저장: {out}")
