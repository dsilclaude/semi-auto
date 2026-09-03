from flowlib import *

c = Canvas(1320, 1760)
c.caption(30, 34, "그림 B — 시드 결정   (_seed_for → _baseline_for → _extend_past_gm_peak)", 14)
c.note(30, 62, "조정은 '소자 사이'에서 일어난다. 같은 소자를 다시 재면 전하 트래핑이 쌓여 Vth 가 밀린다.",
       color="#8a6d3b", size=9)

X = 520
s  = c.term(X,  100, 250, 42, "시드 결정 시작")
q1 = c.dec (X,  172, 330, 76, "앞 소자 결과가 있나?")
q2 = c.dec (X,  278, 440, 96, "turn-on 을 창 안에서 본 소자가 있나?")

# --- 왼쪽: 아직 못 찾았다 → 창을 넓혀 다음 소자에서 본다 ------------------
w1 = c.proc(250, 414, 330, 56, "중심 유지한 채 창 × widen_factor\n점 수도 같은 비율 (스텝 유지)")
w2 = c.dec (250, 506, 220, 70, "경계 안?")
base = c.proc(150, 800, 250, 50, "stack 기준안 그대로")

# --- 오른쪽: 찾았다 → 실측으로 좁히고, 필요하면 gm 꼭대기까지 -------------
r1 = c.proc(830, 414, 340, 56, "실측 Vth · SS 중앙값을 반영해\nseed_transfer 재계산")
r2 = c.dec (830, 506, 340, 88, "앞 소자 전부 gm 꼭대기가\n창 끝이었나?")
r3 = c.proc(830, 630, 340, 56, "켜지는 쪽 끝만 창 폭의 50% 연장\nturn-on 쪽 끝은 그대로")
r4 = c.dec (830, 722, 240, 70, "경계 안?")

cand = c.proc(X, 920, 320, 46, "이 소자의 기준안 확정")

c.down(s, q1)
c.edge([q1.left(), (60, 210), (60, 780), (150, 780), base.top()], "없다 (첫 소자)", (350, 196), lha="right")
c.down(q1, q2, "있다", (526, 262))
c.edge([q2.left(), (250, 326), w1.top()], "아니오", (296, 350))
c.edge([q2.right(), (830, 326), r1.top()], "예", (790, 350), lha="right")
c.down(w1, w2)
c.edge([w2.left(), (60, 541)], "넓히면 위반", (135, 528), lha="right")
c.down(r1, r2)
c.edge([r2.bot(), r3.top()], "예 — 이동도가 하한이다", (836, 607))
c.edge([r2.right(), (1090, 550), (1090, 757), r4.right()], "아니오\n이미 꼭대기를 봤다", (1098, 480))
c.down(r3, r4)

# 합류 버스
c.edge([base.bot(), (150, 896)], head=False)
c.edge([w2.bot(), (250, 896)], "통과", (256, 620), head=False)
c.edge([r4.bot(), (830, 896)], "통과", (836, 830), head=False)
c.edge([r4.left(), (700, 757), (700, 896)], "위반 — 연장 없이", (700, 790), lha="right", head=False)
c.edge([(150, 896), (830, 896)], head=False)
c.edge([(X, 896), cand.top()])

# --- 에이전트에게 물어보기 --------------------------------------------
q3 = c.dec (X, 1000, 400, 92, "adapt_seed_per_site 이고\npolicy 에 propose_seed 가 있나?")
ask = c.proc(X, 1130, 380, 56, "에이전트에게 조건을 묻는다\n목적 · 경계 · 소자 스펙 · prior_devices")
q4 = c.dec (X, 1216, 330, 80, "propose 로 답했나?")
q5 = c.dec (X, 1330, 330, 80, "제안이 경계 안?")
use = c.proc(X, 1444, 320, 46, "plan = 에이전트 제안")
keep = c.proc(940, 1223, 240, 56, "기준안 유지\n(에이전트 판단 버림)")
q6 = c.dec (X, 1534, 320, 76, "force_direction 지정?")
fd = c.proc(940, 1547, 260, 50, "스윕 방향 강제\n에이전트 판단보다 우선")
out = c.term(X, 1650, 280, 44, "이 소자의 plan 확정")

c.down(cand, q3)
c.down(q3, ask, "예", (526, 1112))
c.edge([q3.right(), (940, 1046), keep.top()], "아니오", (760, 1032))
c.down(ask, q4)
c.edge([q4.right(), keep.left()], "아니오 · 호출 실패 · 타임아웃", (700, 1240))
c.down(q4, q5, "예", (526, 1312))
c.edge([q5.left(), (390, 1370), (390, 1305), (818, 1305), (818, 1279)], "위반", (356, 1370), lha="right")
c.down(q5, use, "통과", (526, 1425))
c.down(use, q6)
c.edge([keep.right(), (1200, 1251), (1200, 1512), (523, 1512)])
c.edge([q6.right(), fd.left()], "예", (700, 1560))
c.down(q6, out, "아니오", (526, 1628))
c.edge([(1070, 1597), (1200, 1597), (1200, 1672), (662, 1672)])
c.note(30, 1712, "· 어떤 경로로 새는 plan 이든 executor 직전에 validate 를 한 번 더 통과해야 한다 (그림 C)",
       color="#666666", size=9)
c.save("dia_b")
