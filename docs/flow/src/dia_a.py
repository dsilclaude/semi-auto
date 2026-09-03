from flowlib import *

c = Canvas(1760, 1350)
c.caption(30, 34, "measauto — 실행 전체   (examples/run_area.py  +  Session.run_area)", 14)

# ============================ 패널 1 : 준비 ============================
c.group(30, 62, 700, 900, "준비 · 안전 검사")
X = 300; RX = 610

n1  = c.term(X,  92, 170, 40, "시작")
n2  = c.io  (X, 162, 330, 54, "stack · objective · 좌표 CSV\ncalib · max_iters")
n3  = c.proc(X, 244, 330, 50, "안전 경계 계산\nbounds_from_stack")
n4  = c.dec (X, 324, 290, 76, "한계값이 다 있나?")
x4  = c.term(RX, 337, 190, 50, "중단\n한계부터 채운다", GRAY)
n5  = c.proc(X, 430, 330, 50, "기준안 계산\nseed_transfer  (stack 기반)")
n6  = c.dec (X, 510, 290, 76, "기준안이 경계 안인가?")
x6  = c.term(RX, 523, 190, 50, "중단\n조건을 고치고 다시", GRAY)
n7  = c.dec (X, 616, 230, 68, "--dry ?")
x7  = c.term(RX, 625, 190, 50, "검증만 하고 종료\n장비를 열지 않는다")
n8  = c.proc(X, 714, 340, 54, "장비 연결 · 상태 확인\n--set-reference 면 원점 등록")
n9  = c.dec (X, 798, 230, 68, "연결 성공?")
x9  = c.term(RX, 807, 190, 50, "중단\n연결부터 확인", GRAY)
n10 = c.term(X, 896, 240, 42, "→ 소자 순회로")

c.down(n1, n2); c.down(n2, n3); c.down(n3, n4)
c.edge([n4.right(), x4.left()], "없다", (452, 350))
c.down(n4, n5, "있다", (306, 415))
c.down(n5, n6)
c.edge([n6.right(), x6.left()], "위반", (452, 536))
c.down(n6, n7, "통과", (306, 601))
c.edge([n7.right(), x7.left()], "예", (452, 638))
c.down(n7, n8, "아니오", (306, 699))
c.down(n8, n9)
c.edge([n9.right(), x9.left()], "실패", (452, 820))
c.down(n9, n10, "성공", (306, 881))

# ======================= 패널 2 : 소자 순회 =======================
c.group(770, 62, 960, 1250, "소자 순회  (area 하나)")
Y = 1080; RY = 1500

a = c.term(Y,   92, 240, 42, "소자 순회 시작")
b = c.proc(Y,  164, 240, 44, "소자 i 선택")
d = c.dec (Y,  238, 390, 92, "탐색 소자인가?\ni < calibration_sites  또는  locked 없음")
d2 = c.proc(RY, 253, 260, 62, "확정 plan(locked) 그대로\n에이전트 호출 없음")
e = c.sub (Y,  360, 300, 48, "시드 결정  →  그림 B")
f = c.sub (Y,  438, 300, 48, "반복 루프  →  그림 C")
g = c.dec (Y,  516, 280, 76, "탐색 소자였나?")
h = c.proc(Y,  622, 350, 56, "prior_devices 에 결과 기록\nVth · SS · turn-on 창 · gm 끝")
i = c.dec (Y,  708, 300, 76, "status == converged ?")
i2 = c.proc(RY, 723, 260, 46, "locked = final_plan")
j = c.dec (Y,  814, 280, 76, "남은 소자 있나?")
k = c.proc(Y,  920, 240, 44, "요약 출력")
l = c.proc(Y,  994, 350, 56, "home → close\n팁을 원점 contact 로  (finally)")
m = c.dec (Y, 1080, 230, 68, "--report ?")
m2 = c.proc(RY, 1090, 260, 50, "report.md 생성\n장비를 닫은 뒤에")
z = c.term(Y, 1178, 180, 42, "끝")

# 패널 1 → 패널 2
c.edge([n10.right(), (748, 916), (748, 113), a.left()], dashed=True)

c.down(a, b)
c.down(b, d)
c.edge([d.right(), d2.left()], "아니오", (1288, 250))
c.down(d, e, "예", (1086, 345))
c.down(e, f)
c.edge([(RY, 315), (RY, 462), f.right()], "그대로 1회 측정", (1494, 400), lha="right")
c.down(f, g)
c.edge([g.left(), (900, 554), (900, 800), (1078, 800)], "아니오", (930, 566))
c.down(g, h, "예", (1086, 605))
c.down(h, i)
c.edge([i.right(), i2.left()], "예", (1240, 734))
c.down(i, j, "아니오", (1086, 799))
c.edge([(RY, 769), (RY, 852), (1226, 852)])
c.edge([j.left(), (850, 852), (850, 186), b.left()], "예", (928, 840))
c.note(842, 520, "다음 소자", ha="right")
c.down(j, k, "아니오", (1086, 905))
c.down(k, l)
c.down(l, m)
c.edge([m.right(), m2.left()], "예", (1210, 1101))
c.down(m, z, "아니오", (1086, 1163))
c.edge([(RY, 1140), (RY, 1199), (1176, 1199)])

# 소자 간 학습 경로
c.edge([h.right(), (1680, 650), (1680, 384), e.right()], dashed=True)
c.note(1672, 500, "앞 소자에서 본 것이\n다음 소자의 기준안 근거\n(같은 소자를 다시 재지 않는다)",
       ha="right", color="#8a6d3b")

c.save("dia_a")
