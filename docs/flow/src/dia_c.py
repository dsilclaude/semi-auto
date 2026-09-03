from flowlib import *

c = Canvas(1740, 1530)
c.caption(30, 34, "그림 C — 반복 루프   (Session.run_site,  소자 하나)", 14)
c.note(30, 62, "저장은 측정 경로에서만 일어난다. 경계 위반 되돌리기는 history 에만 남고 저장되지 않는다.",
       color="#8a6d3b", size=9)

X = 560
s    = c.term(X,  100, 220, 42, "소자 시작")
n1   = c.proc(X,  172, 340, 56, "plan = 확정 plan 또는 시드\nit = 0,  retries = 0")
itq  = c.dec (X,  258, 280, 72, "it < max_iters ?")
mx   = c.proc(250, 273, 300, 56, "status = max_iters\n에이전트가 더 원했으면 이유 기록")
val  = c.dec (X,  360, 430, 96, "validate : error 있나?\nexecutor 부르기 직전 · 유일한 관문")

# --- 경계 위반 되돌리기 (오른쪽) ---------------------------------------
rq   = c.dec (1080, 372, 340, 92, "확정 plan 이거나\nretries ≥ max_retry_on_violation ?")
oob  = c.proc(1430, 396, 230, 46, "status = out_of_bounds")
rb   = c.proc(1080, 500, 340, 56, "retries++ · 위반 내용을 history 에\n(측정하지 않는다)")
ra   = c.dec (1080, 590, 300, 80, "에이전트가 propose ?")
st   = c.proc(1430, 605, 230, 50, "status = 그 값\n(needs_human 등)")
rp   = c.proc(1080, 700, 280, 46, "plan 교체 · it++")
end2 = c.term(1430, 730, 210, 42, "소자 끝")

# --- 측정 (가운데) ------------------------------------------------------
first = c.dec (X, 500, 260, 72, "첫 회차인가?")
run   = c.proc(230, 610, 300, 56, "ex.run\n프로버 이동 · contact · 스윕")
mea   = c.proc(X, 610, 300, 56, "ex.measure\n같은 자리에서 재측정")
errq  = c.dec (X, 700, 260, 72, "측정 중 예외?")
err   = c.proc(210, 790, 240, 46, "status = error")
summ  = c.proc(X, 810, 430, 56, "summarize\nVth · SS · gm/이동도 · on-off · Ig · 히스테리시스")
pay   = c.proc(X, 900, 430, 56, "LLM 페이로드 구성\nfull CSV 또는 25점 다운샘플 + metrics")
fix2  = c.dec (X, 990, 300, 76, "확정 plan 인가?")
conv  = c.proc(215, 1090, 260, 56, "Proposal = converged\n(LLM 호출 없음)")
ask   = c.proc(X, 1100, 390, 56, "policy.propose\n호출 실패 · 낮은 확신도 → needs_human")
save  = c.proc(X, 1190, 440, 56, "store.save\nCSV · metrics · plan · proposal · 소요시간")
dec   = c.dec (X, 1280, 240, 72, "status")
np_   = c.proc(1000, 1293, 280, 46, "plan = 제안 · it++")
end   = c.term(X, 1420, 220, 44, "소자 끝")

# --- 배선 ---------------------------------------------------------------
c.down(s, n1); c.down(n1, itq)
c.edge([itq.left(), mx.right()], "아니오", (410, 282), lha="right")
c.down(itq, val, "예", (566, 345))

c.edge([val.right(), rq.left()], "있다", (800, 400))
c.down(val, first, "없다", (566, 478))
c.edge([rq.right(), oob.left()], "예", (1262, 405))
c.down(rq, rb, "아니오", (1086, 482))
c.down(rb, ra)
c.edge([ra.right(), st.left()], "아니오", (1240, 617))
c.down(ra, rp, "예", (1086, 685))
c.edge([oob.bot(), (1430, 470), (1290, 470), (1290, 751), end2.left()])
c.down(st, end2)
c.edge([rp.left(), (830, 723), (830, 294), itq.right()])
c.note(838, 500, "다음 회차", color="#333333")

c.edge([first.left(), (230, 536), run.top()], "예", (280, 560))
c.down(first, mea, "아니오", (566, 590))
c.edge([run.bot(), (230, 683), (X - 2, 683)])
c.down(mea, errq)
c.edge([errq.left(), (210, 736), err.top()], "예", (290, 758))
c.down(errq, summ, "아니오", (566, 791))
c.down(summ, pay); c.down(pay, fix2)
c.edge([fix2.left(), (215, 1028), conv.top()], "예", (290, 1050))
c.down(fix2, ask, "아니오", (566, 1083))
c.down(ask, save)
c.edge([conv.bot(), (215, 1218), save.left()])
c.down(save, dec)
c.edge([dec.right(), np_.left()], "propose", (700, 1300))
c.down(dec, end)
c.note(700, 1395, "converged\ndead · leaky · polarity_anomaly\nout_of_bounds · needs_human · max_iters")

# 왼쪽 종료 레인
c.edge([mx.left(), (55, 301), (55, 1442), end.left()], head=True)
c.edge([err.left(), (55, 813)], head=True)

# propose 되돌림 (오른쪽 레인)
c.edge([np_.right(), (1640, 1316), (1640, 294), (832, 294)], head=False)

c.note(30, 1490,
       "· 위반 되돌리기도 iteration 을 한 칸 소비한다 (for 문의 continue)      "
       "· 확정 plan 이면 에이전트를 부르지 않고 converged 로 끝낸다      "
       "· 첫 회차만 프로버가 움직인다", color="#666666", size=9)
c.save("dia_c")
