# -*- coding: utf-8 -*-
"""measauto_ui.py — DSIL 자동측정 데스크톱 UI (PySide6)

Cascade S300 + Keysight B1500A 자동측정(measauto)의 조작 화면.
로고는 DSIL_Final.pptx 에서 추출해 파일에 embed 했다 — 외부 에셋 없이 단일 파일.

저장소 루트(measauto/ 폴더가 있는 곳)에 두고 실행한다:

    .\\.venv\\Scripts\\python.exe measauto_ui.py

또는 run_ui.ps1 을 우클릭 → "PowerShell에서 실행".

필요한 것:  pip install PySide6

────────────────────────────────────────────────────────────────────────
설계 규칙 — 이 파일을 고칠 때 반드시 지킬 것
────────────────────────────────────────────────────────────────────────
1. **UI 는 measauto 를 고치지 않는다.** import 만 한다.
   조건이 이미 '값'이라서 UI 는 그 값을 만들어 넘기는 얇은 층이면 된다.

2. **UI 는 판단하지 않는다.** 안전 경계 계산은 safety, 지표는 metrics,
   다음 조건은 agent 가 정한다. UI 가 "이 정도면 괜찮겠지" 하고 값을
   보정하기 시작하면 판단이 두 곳에 생기고 그때부터 추적이 안 된다.
   화면에 띄우는 스텝(=span/(점수-1))처럼 **산술**은 판단이 아니다.
   값을 평가하거나 대신 고쳐 주는 것이 판단이다.

3. **측정은 워커 스레드에서 돈다.** UI 스레드가 막히면 중단 버튼이
   안 눌린다. 중단 버튼이 안 눌리는 장비 UI 는 없느니만 못하다.

4. **중단은 소자 사이에서 걸린다.** 스윕 도중에 끊으면 장비가 어중간한
   상태로 남는다. 지금 소자를 끝내고 → separate → 원점 복귀 순으로 멈춘다.

5. run_area() 의 루프를 여기서 다시 쓴 이유는 (4) 때문이다. 로직은
   session.run_area 와 같아야 한다 — 그쪽이 바뀌면 여기도 맞출 것.
   유일한 추가가 '고정 조건' 경로다: 모든 소자에 fixed_plan 을 넘기면
   session 이 policy 를 한 번도 안 부른다. 이건 로직 변경이 아니라
   run_site(fixed_plan=...) 를 처음부터 쓰는 것뿐이다.

6. **색·간격은 C / QSS 한 곳에서만 정한다.** 위젯마다 setStyleSheet 를 뿌리면
   나중에 테마를 못 바꾼다.

7. **직접 입력한 조건은 그대로 나간다.** 사람이 값을 적었는데 에이전트가
   덮어쓰면 화면이 거짓말을 하는 것이다. 그래서 '직접 입력' 은 기본으로
   고정 조건(=API 호출 0회)과 묶여 있다.
"""

from __future__ import annotations

import io
import os
import sys
import time
import traceback
from contextlib import redirect_stdout
from pathlib import Path
from typing import List, Optional

def _fatal(title: str, message: str):                      # pragma: no cover
    """창을 띄우지도 못하는 상황을 사람에게 알린다.

    run_ui.bat 은 콘솔 없이(pythonw) 띄운다 — 아이콘을 눌렀는데 아무 일도
    안 일어나는 것처럼 보이면 안 되므로, print 대신 OS 대화상자로 띄운다.
    ctypes 는 표준 라이브러리라 이 시점에도 확실히 쓸 수 있다.
    """
    sys.stderr.write(f"{title}\n{message}\n")
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # ICONERROR
    except Exception:
        pass
    sys.exit(1)


try:
    from PySide6.QtCore import (Qt, QByteArray, QObject, QSettings, QThread,
                                Signal, Slot)
    from PySide6.QtGui import QColor, QPixmap, QTextCursor
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
        QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
        QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
        QScrollArea, QSplitter, QStatusBar, QTableWidget, QTableWidgetItem,
        QVBoxLayout, QWidget, QButtonGroup, QFrame, QHeaderView,
    )
    import matplotlib
except ImportError as _e:                                  # pragma: no cover
    # 스택트레이스는 여기서 도움이 안 된다. 무엇을 하면 되는지만 적는다.
    _fatal("자동측정 UI 를 열 수 없습니다",
           f"필요한 패키지를 불러오지 못했습니다:\n  {_e}\n\n"
           f"저장소 폴더에서 아래를 실행하세요:\n"
           f"  .\\.venv\\Scripts\\python.exe -m pip install PySide6 matplotlib")

matplotlib.use("QtAgg")


def _korean_font_family() -> list:
    """한글 축 라벨이 깨지지 않게. 실제로 설치된 것만 넘긴다 —
    없는 이름을 그대로 두면 그릴 때마다 findfont 경고가 콘솔을 덮는다."""
    from matplotlib import font_manager
    have = {f.name for f in font_manager.fontManager.ttflist}
    picked = [n for n in ("Malgun Gothic", "AppleGothic", "NanumGothic") if n in have]
    return picked + ["DejaVu Sans"]


matplotlib.rcParams["font.family"] = _korean_font_family()
matplotlib.rcParams["axes.unicode_minus"] = False
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- measauto ---------------------------------------------------------------
# 없으면 UI 는 뜨되 실행 버튼이 잠긴다. "왜 안 되는지"를 화면에서 보여주는 게
# 콘솔 스택트레이스보다 낫다.
IMPORT_ERROR = ""
try:
    from measauto.agent.policy import HeuristicPolicy, default_policy
    from measauto.config import DEFAULT
    from measauto.executor import (BusTimeout, Executor, list_visa_resources,
                                   load_sites)
    from measauto.safety import (MissingLimitError, bounds_from_stack,
                                 load_stack, validate)
    from measauto.seed import seed_output, seed_transfer
    from measauto.session import Session, SessionConfig
    from measauto.store import Store
    from measauto.plan import with_axis
    from dataclasses import replace as _dc_replace
except Exception as e:                                    # pragma: no cover
    IMPORT_ERROR = f"{type(e).__name__}: {e}"


# DSIL 로고 — DSIL_Final.pptx 에서 추출. 높이 88px = 표시 44px @2x.
# 흰 버전은 pptx 의 흰 로고(배경 알파가 244 라 파란 띠 위에 사각형이 비친다) 대신
# 파란 로고의 글자만 뽑아 흰색으로 뒤집어 만들었다.
LOGO_WHITE_B64 = (
    "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"
    "AAAAAElFTkSuQmCC")

LOGO_BLUE_B64 = (
    "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"
    "0BEL7/x/yL7u2aAGOsoAAAAASUVORK5CYII=")


OBJECTIVE = {
    "transfer": (
        "BG transfer 에서 소자 스펙 시트 다섯 항목을 얻는다: "
        "Vth, SS, 전계효과 이동도, on/off 비, 히스테리시스. "
        "히스테리시스는 정/역 방향 차이이므로 왕복(double) 스윕이 필요하다."),
    "output": (
        "출력특성(Id-Vd)에서 접촉저항(선형영역 기울기), 포화 진입 여부, "
        "채널길이변조 λ 와 출력저항을 얻는다. 포화 구간이 남아야 λ 가 나오므로 "
        "가장 높은 게이트 스텝에서도 Vd 상한이 포화 위에 있어야 한다."),
}


# ═══════════════════════════════════════════════════════════════════════════
#  워커 — 측정은 전부 여기서 돈다
# ═══════════════════════════════════════════════════════════════════════════
class _Tee(io.TextIOBase):
    """measauto 가 print 하는 것을 UI 로그로 흘려보낸다."""

    def __init__(self, emit):
        self._emit = emit
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._emit(line.rstrip())
        return len(s)

    def flush(self) -> None:
        if self._buf.strip():
            self._emit(self._buf.rstrip())
        self._buf = ""


class CheckWorker(QObject):
    """장비와 통신이 되는지만 본다. **측정도 이동도 하지 않는다.**

    examples/check_b1500 과 같은 순서다: 버스 조회 → B1500 열기 → IDN/모듈
    → 출력 OFF → 닫기. 프로버는 버스에 보이는지만 확인하고 **열지 않는다** —
    S300 은 connect 할 때 설정 명령이 나가므로, '확인'이 뭔가를 바꾸면 안 된다.

    워커 스레드에서 도는 이유: 버스가 물려 있으면 조회에만 20초가 걸린다.
    그동안 UI 가 굳으면 사람은 또 프로그램이 죽은 줄 안다.
    """

    log = Signal(str)
    done = Signal(bool, str)

    @Slot()
    def run(self) -> None:
        tee = _Tee(self.log.emit)
        ok, msg = False, ""
        try:
            with redirect_stdout(tee):
                ok, msg = self._run_inner()
        except BusTimeout as e:
            msg = "버스가 물려 있습니다 — 어댑터 USB 재연결이 필요합니다"
            self.log.emit(str(e))
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            self.log.emit(traceback.format_exc())
        finally:
            tee.flush()
        self.done.emit(ok, msg)

    def _run_inner(self):
        from measauto.drivers import B1500

        cfg = DEFAULT
        self.log.emit(f"VISA   {cfg.visa_library}")
        self.log.emit(f"버스 조회 중… (최대 {cfg.bus_scan_timeout_s:g}s)")
        found = list_visa_resources(cfg.visa_library,
                                    timeout_s=cfg.bus_scan_timeout_s)
        self.log.emit(f"  보이는 자원: {found or '(없음)'}")

        if cfg.s300_address in found:
            self.log.emit(f"  S300  {cfg.s300_address}  버스에 있음 (열지 않았습니다)")
        else:
            self.log.emit(f"  [경고] S300 {cfg.s300_address} 가 버스에 없습니다 — "
                          f"전원·GPIB 케이블·주소를 확인하세요")
        if cfg.b1500_address not in found:
            return False, (f"B1500 이 버스에 없습니다 ({cfg.b1500_address}). "
                           f"GPIB0 이 통째로 없으면 어댑터 USB 를 재연결하세요.")

        # 진단용이라 짧게 연다. 측정용 타임아웃(300초)을 쓰면 막혔을 때
        # 5분을 기다리게 된다.
        self.log.emit("B1500 여는 중… (타임아웃 5s)")
        self.log.emit("  여기서 막히면 본체에서 EasyEXPERT 가 돌고 있는 것입니다 "
                      "— 종료하고 다시 시도하세요.")
        b = B1500(cfg.b1500_address, cfg.visa_library, timeout_ms=5000).connect()
        try:
            self.log.emit(f"  IDN    {b.idn()}")
            self.log.emit(f"  모듈   {b.modules()}")
            b.outputs_off()
            self.log.emit("  전 채널 출력 OFF (CL) 성공")
        finally:
            b.close()
        self.log.emit(f"  roles  {dict(cfg.roles)}  ← 위 모듈 목록과 맞는지 확인하세요")
        self.log.emit("B1500 본체에 FlexGUI 창이 떴을 것입니다 — 접속됐다는 표시입니다.")
        return True, "장비 확인 통과 — 측정을 시작해도 됩니다"


class MeasureWorker(QObject):
    log = Signal(str)
    progress = Signal(str)                 # 상태줄 한 줄
    run_dir = Signal(str)                  # 이번 실행의 결과 폴더
    site_done = Signal(object, str)        # (SiteResult, data.csv 경로)
    finished = Signal(str)                 # 종료 사유
    failed = Signal(str)

    def __init__(self, params: dict):
        super().__init__()
        self.p = params
        self._abort = False

    @Slot()
    def abort(self) -> None:
        """UI 스레드에서 호출. 다음 소자 경계에서 멈춘다."""
        self._abort = True
        self.log.emit("■ 중단 요청됨 — 지금 소자를 끝내고 멈춥니다.")

    @Slot()
    def run(self) -> None:
        tee = _Tee(self.log.emit)
        try:
            with redirect_stdout(tee):
                self._run_inner()
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")
        finally:
            tee.flush()

    # -- 실제 루프 ---------------------------------------------------------
    def _run_inner(self) -> None:
        p = self.p
        bounds = bounds_from_stack(p["stack"])
        seed = p["seed"]
        sites = p["sites"]
        fixed = bool(p["fixed_condition"])
        t_start = time.time()

        self.log.emit(f"목적: {p['objective']}")
        self.log.emit(bounds.describe())
        self.log.emit(f"조건: {seed.describe()}")
        if fixed:
            self.log.emit("판단: **고정 조건** — 모든 소자에 위 조건을 그대로 적용합니다. "
                          "에이전트(API)를 한 번도 부르지 않습니다.")
        else:
            self.log.emit(
                f"판단: {type(p['policy']).__name__} — 앞 {p['calib']} 개 소자로 "
                f"조건을 탐색한 뒤 확정된 plan 을 나머지에 적용합니다.")

        self.progress.emit("장비 연결 중…")
        ex = Executor(DEFAULT)
        try:
            ex.connect()
        except Exception:
            # 한쪽만 열린 채 남지 않게 한다. 팁은 아직 사람이 둔 자리 그대로다.
            try:
                ex.close()
            except Exception:
                pass
            raise
        try:
            self.log.emit(f"장비 상태: {ex.check()}")

            if p["set_reference"]:
                x, y, z = ex.set_reference()
                self.log.emit(
                    f"원점 등록: 직전 좌표계의 ({x:g}, {y:g}) 를 (0,0) 으로, "
                    f"contact 높이 z={z:g} 로 등록")

            store = Store(p["out"])
            self.run_dir.emit(str(store.run_dir))
            sess = Session(
                executor=ex, bounds=bounds, store=store,
                policy=p["policy"], stack=load_stack(p["stack"]),
                config=SessionConfig(
                    objective=p["objective"],
                    max_iters=p["max_iters"],
                    calibration_sites=p["calib"],
                    adapt_seed_per_site=p["adapt_seed"],
                    force_direction=p["force_direction"],
                ),
            )

            # session.run_area 와 같은 로직 + 소자 사이 중단 검사.
            # 고정 조건이면 첫 소자부터 locked 가 채워져 있어 calibrating 이
            # 한 번도 참이 되지 않는다 = policy 호출 0회.
            n_cal = 0 if fixed else p["calib"]
            locked = seed if n_cal == 0 else None
            reason = "완료"
            n = len(sites)
            for i, site in enumerate(sites):
                if self._abort:
                    reason = f"중단 ({i}/{n} 소자까지 완료)"
                    break

                # 탐색 소자를 다 쓰고도 확정된 조건이 없으면 멈춘다.
                # (session.run_area 와 같은 규칙 — 그쪽 주석에 이유가 있다)
                calibrating = i < n_cal
                if not calibrating and locked is None:
                    reason = f"수렴 실패 ({n_cal}개 탐색, 확정된 조건 없음)"
                    self.log.emit(
                        f"\n[수렴 실패] 탐색 소자 {n_cal}개를 다 썼는데 확정된 "
                        f"조건이 없습니다. 남은 {n - i}개는 측정하지 않고 멈춥니다.\n"
                        f"  · 측정된 {i}개의 곡선과 지표는 결과 폴더에 그대로 있습니다.\n"
                        f"  · 조건을 보고 다시 정하세요 — 전압 범위를 넓히거나, "
                        f"[직접 입력] 으로 조건을 지정하거나, 스택의 안전 경계를 "
                        f"확인합니다.")
                    break

                self.progress.emit(f"측정 중 — {site.area}/{site.name} "
                                   f"({i + 1}/{n})  ·  경과 "
                                   f"{self._hms(time.time() - t_start)}")
                r = sess.run_site(site, seed,
                                  fixed_plan=None if calibrating else locked)
                self.log.emit(f"[{site.area}/{site.name}] {r.status} "
                              f"({r.iterations} iter)"
                              + (f" — {r.error}" if r.error else ""))
                self.site_done.emit(r, self._latest_csv(store, site))

                if calibrating:
                    sess.prior_devices.append(r.summary_row())
                    if r.status == "converged" and r.final_plan is not None:
                        locked = r.final_plan
                        self.log.emit(f"확정 plan: {locked.describe()}")

            self.log.emit(f"소요 시간 {self._hms(time.time() - t_start)}  ·  "
                          f"결과 {store.run_dir}")
            # 이 장비는 버스 신호로 로컬 복귀가 안 된다(drivers/b1500.set_local
            # 의 실측 기록). 그대로 두고 측정을 이어가도 되지만, 본체 앞에서
            # EasyEXPERT 를 쓰려면 사람이 눌러야 한다는 것을 여기서 알려준다.
            self.log.emit(
                "B1500 은 원격(REM) 상태로 남습니다 — 본체에서 EasyEXPERT 를 "
                "쓰시려면 FlexGUI 의 Tools > Go to Local & Close 를 누르세요. "
                "측정을 계속하실 거면 그대로 두셔도 됩니다.")
            self.progress.emit("출력 OFF · 원점 복귀 중…")
            self.finished.emit(reason)
        finally:
            # 무슨 일이 있어도 팁은 안전한 자리로. home() 이 separate → 원점 →
            # 재접촉을 하므로 다음 실행 전제가 그대로 성립한다.
            try:
                ex.home()
            except Exception as e:
                self.log.emit(f"[경고] 원점 복귀 실패: {e}")
                try:
                    ex.s300.separate()
                    self.log.emit("팁만 분리해 두었습니다.")
                except Exception:
                    self.log.emit("[위험] 팁 분리도 실패 — 장비를 직접 확인하세요.")
            finally:
                ex.close()

    @staticmethod
    def _hms(sec: float) -> str:
        m, s = divmod(int(sec), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @staticmethod
    def _latest_csv(store, site) -> str:
        """이 소자에서 마지막으로 저장된 data.csv. 실시간 곡선용."""
        try:
            d = store.run_dir / site.area / site.name
            cands = sorted(d.glob("iter*/data.csv"))
            return str(cands[-1]) if cands else ""
        except Exception:
            return ""


# ═══════════════════════════════════════════════════════════════════════════
#  디자인 — DSIL 다크 테마
# ═══════════════════════════════════════════════════════════════════════════
C = {
    "bg":       "#0F1318",   # 바탕
    "panel":    "#171C22",   # 카드
    "panel2":   "#1E242B",   # 입력·표 헤더
    "line":     "#2A323B",
    "line_soft":"#222932",
    "ink":      "#E4E9EE",
    "dim":      "#94A1AE",
    "faint":    "#657483",
    "brand":    "#004191",   # DSIL 로고 색
    "brand_dk": "#00306E",
    "accent":   "#3E8FE0",   # 다크 배경에서 쓰려고 브랜드색을 띄운 값
    "accent_dk":"#2B77C4",
    "ok":       "#3FB68B",
    "warn":     "#D8A93E",
    "danger":   "#E2685C",
}

FONT_UI = '"Segoe UI", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif'
FONT_MONO = '"Cascadia Mono", "Consolas", "D2Coding", monospace'

QSS = f"""
QWidget {{
    background: {C['bg']};
    color: {C['ink']};
    font-family: {FONT_UI};
    font-size: 12px;
}}
QLabel#appTitle   {{ color: #FFFFFF; font-size: 15px; font-weight: 600; }}
QLabel#appSub     {{ color: #A9C4E4; font-size: 11px; }}
QFrame#header     {{ background: {C['brand']}; border: none; }}
QFrame#header QLabel {{ background: transparent; }}
QFrame#headerRule {{ background: rgba(255,255,255,0.28); max-width: 1px; }}

QFrame#card {{
    background: {C['panel']};
    border: 1px solid {C['line']};
    border-radius: 8px;
}}
QLabel#cardTitle  {{ color: {C['ink']}; font-size: 12px; font-weight: 600; }}
QLabel#cardHint   {{ color: {C['faint']}; font-size: 11px; }}
QLabel#badge {{
    background: {C['accent']}; color: #06121F;
    font-size: 11px; font-weight: 700;
    min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px;
    border-radius: 9px;
}}
QLabel#formLabel  {{ color: {C['dim']}; font-size: 11px; }}

QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {C['panel2']};
    border: 1px solid {C['line']};
    border-radius: 5px;
    padding: 5px 8px;
    selection-background-color: {C['accent_dk']};
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {C['accent']}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QComboBox:disabled {{ color: {C['faint']}; background: #151A20; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {C['panel2']}; border: 1px solid {C['line']};
    selection-background-color: {C['accent_dk']}; outline: none;
}}
QPlainTextEdit#mono, QPlainTextEdit#log {{
    font-family: {FONT_MONO}; font-size: 11px;
    background: #12171D; color: {C['dim']};
}}

QCheckBox {{ spacing: 7px; color: {C['ink']}; }}
QCheckBox::indicator {{ width: 13px; height: 13px; }}
QCheckBox::indicator:unchecked {{
    border: 1px solid {C['faint']}; border-radius: 4px; background: {C['panel2']};
}}
QCheckBox::indicator:checked {{
    border: 1px solid {C['accent']}; border-radius: 4px; background: {C['accent']};
}}

QPushButton {{
    background: {C['panel2']}; border: 1px solid {C['line']};
    border-radius: 6px; padding: 7px 14px; color: {C['ink']};
}}
QPushButton:hover  {{ border-color: {C['accent']}; }}
QPushButton:disabled {{ color: {C['faint']}; border-color: {C['line_soft']}; }}
QPushButton#segLeft, QPushButton#segRight {{
    background: {C['panel2']}; border: 1px solid {C['line']};
    color: {C['dim']}; padding: 7px 10px; font-size: 11px;
}}
QPushButton#segLeft  {{ border-top-right-radius: 0; border-bottom-right-radius: 0; }}
QPushButton#segRight {{ border-top-left-radius: 0; border-bottom-left-radius: 0;
                        border-left: none; }}
QPushButton#segLeft:checked, QPushButton#segRight:checked {{
    background: {C['accent']}; border-color: {C['accent']};
    color: #06121F; font-weight: 700;
}}
QPushButton#primary {{
    background: {C['accent']}; border: 1px solid {C['accent']};
    color: #06121F; font-weight: 700; padding: 9px 26px;
}}
QPushButton#primary:hover    {{ background: #57A0EA; }}
QPushButton#primary:disabled {{ background: #23303D; border-color: #23303D; color: {C['faint']}; }}
QPushButton#stop {{
    border: 1px solid {C['danger']}; color: {C['danger']};
    font-weight: 700; padding: 9px 22px;
}}
QPushButton#stop:hover    {{ background: rgba(226,104,92,0.14); }}
QPushButton#stop:disabled {{ border-color: {C['line_soft']}; color: {C['faint']}; }}

QTableWidget {{
    background: {C['panel']}; border: 1px solid {C['line']};
    border-radius: 6px; gridline-color: transparent;
    alternate-background-color: #1B2128;
}}
QTableWidget::item {{ padding: 5px 8px; border: none; }}
QTableWidget::item:selected {{ background: {C['accent_dk']}; color: #FFFFFF; }}
QHeaderView::section {{
    background: {C['panel2']}; color: {C['dim']};
    padding: 6px 8px; border: none;
    border-bottom: 1px solid {C['line']};
    font-size: 11px; font-weight: 600;
}}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #2E3742; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #3C4854; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; }}
QScrollBar::handle:horizontal {{ background: #2E3742; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: transparent; width: 12px; }}
QStatusBar {{ background: {C['panel']}; border-top: 1px solid {C['line']}; color: {C['dim']}; }}
QStatusBar::item {{ border: none; }}
QToolTip {{
    background: {C['panel2']}; color: {C['ink']};
    border: 1px solid {C['line']}; padding: 5px;
}}
"""

# matplotlib 도 같은 팔레트로. 흰 그래프가 다크 UI 위에 뜨면 고장난 것처럼 보인다.
matplotlib.rcParams.update({
    "figure.facecolor": C["panel"],
    "axes.facecolor":   "#12171D",
    "axes.edgecolor":   C["line"],
    "axes.labelcolor":  C["dim"],
    "text.color":       C["ink"],
    "xtick.color":      C["faint"],
    "ytick.color":      C["faint"],
    "xtick.direction":  "out",
    "ytick.direction":  "in",
    "grid.color":       C["line_soft"],
    "font.size":        8.5,
    "axes.titlesize":   9.5,
    "legend.frameon":   False,
})


def _pixmap_from_b64(b64: str) -> QPixmap:
    pm = QPixmap()
    pm.loadFromData(QByteArray.fromBase64(b64.encode()), "PNG")
    pm.setDevicePixelRatio(2.0)          # 2x 로 만들어둔 이미지
    return pm


def _card(title: str, step: str = "", hint: str = "") -> tuple:
    """카드 프레임과 그 안에 채워 넣을 레이아웃을 돌려준다."""
    f = QFrame(); f.setObjectName("card")
    outer = QVBoxLayout(f)
    outer.setContentsMargins(14, 12, 14, 14)
    outer.setSpacing(9)

    head = QHBoxLayout(); head.setSpacing(8)
    if step:
        b = QLabel(step); b.setObjectName("badge"); b.setAlignment(Qt.AlignCenter)
        head.addWidget(b)
    t = QLabel(title); t.setObjectName("cardTitle")
    head.addWidget(t)
    head.addStretch(1)
    if hint:
        h = QLabel(hint); h.setObjectName("cardHint")
        head.addWidget(h)
    outer.addLayout(head)

    body = QVBoxLayout(); body.setSpacing(8)
    outer.addLayout(body)
    return f, body


def _truthy(v) -> bool:
    """QSettings 는 bool 을 "true"/"false" 문자열로 돌려줄 때가 있다."""
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


def _label(text: str) -> QLabel:
    l = QLabel(text); l.setObjectName("formLabel")
    return l


def _form_row(form: QFormLayout, text: str, widget: QWidget) -> tuple:
    """QFormLayout 한 줄을 추가하고 (라벨, 위젯) 을 돌려준다.

    측정 종류에 따라 줄을 통째로 숨겨야 하는데, QFormLayout 은 줄 단위
    숨기기를 안 준다 — 라벨을 따로 안 잡아두면 위젯만 사라지고 라벨이 남는다.
    """
    lab = _label(text)
    form.addRow(lab, widget)
    return lab, widget


def _show_row(row: tuple, on: bool) -> None:
    for w in row:
        w.setVisible(on)


class StatusPill(QLabel):
    """헤더 오른쪽 상태 표시. 색으로 상태를 먼저 읽히게 한다."""

    TONE = {"idle": ("#A9C4E4", "rgba(255,255,255,0.13)"),
            "run":  ("#0F1318", C["accent"]),
            "ok":   ("#06201A", C["ok"]),
            "err":  ("#2A0F0C", C["danger"])}

    def __init__(self):
        super().__init__("● 대기")
        self.setAlignment(Qt.AlignCenter)
        self.set_state("idle", "대기")

    def set_state(self, tone: str, text: str):
        fg, bg = self.TONE.get(tone, self.TONE["idle"])
        self.setText(f"● {text}")
        self.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:10px;"
            f"padding:3px 12px; font-size:11px; font-weight:600;")


# ═══════════════════════════════════════════════════════════════════════════
#  메인 창
# ═══════════════════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    _abort_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DSIL 자동측정 — measauto")
        self.resize(1420, 940)
        self.thread: Optional[QThread] = None
        self.worker: Optional[MeasureWorker] = None
        self.chk_thread: Optional[QThread] = None      # 장비 확인은 별도 스레드
        self.chk_worker: Optional[CheckWorker] = None
        self._sites = []
        self._seed = None
        self._bounds = None
        self._filling = False        # 표를 채우는 동안 itemChanged 를 무시
        self._run_dir = ""           # 마지막 실행의 결과 폴더
        self._closing = False        # 정리가 끝나면 자동으로 닫아야 하는가

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._build_left())
        split.addWidget(self._build_right())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([432, 980])
        split.setChildrenCollapsible(False)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(16, 14, 16, 12)
        bl.setSpacing(12)
        bl.addWidget(split, 1)
        bl.addWidget(self._build_actionbar())

        root = QWidget()
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self._build_header())
        rl.addWidget(body, 1)
        self.setCentralWidget(root)

        self.setStatusBar(QStatusBar())
        if IMPORT_ERROR:
            self.statusBar().showMessage(f"measauto 를 불러오지 못했습니다 — {IMPORT_ERROR}")
            self.pill.set_state("err", "모듈 없음")
            self.btn_run.setEnabled(False)
            self.btn_dry.setEnabled(False)
            self.btn_check.setEnabled(False)
        else:
            self.statusBar().showMessage("준비됨 — 먼저 [검증만] 으로 조건을 확인하세요.")
            self._load_settings()
            self._on_kind_changed()      # 종류에 맞게 칸을 보이고 숨긴다
            self._reload_stack()
            self._reload_sites()

    # -- 헤더 --------------------------------------------------------------
    def _build_header(self) -> QWidget:
        f = QFrame(); f.setObjectName("header"); f.setFixedHeight(58)
        h = QHBoxLayout(f)
        h.setContentsMargins(18, 0, 18, 0)
        h.setSpacing(14)

        logo = QLabel()
        logo.setPixmap(_pixmap_from_b64(LOGO_WHITE_B64))
        h.addWidget(logo)

        rule = QFrame(); rule.setObjectName("headerRule")
        rule.setFixedWidth(1); rule.setFixedHeight(26)
        h.addWidget(rule)

        box = QVBoxLayout(); box.setSpacing(0)
        t = QLabel("자동측정"); t.setObjectName("appTitle")
        s = QLabel("Cascade S300 · Keysight B1500A"); s.setObjectName("appSub")
        box.addWidget(t); box.addWidget(s)
        h.addLayout(box)

        h.addStretch(1)
        self.pill = StatusPill()
        h.addWidget(self.pill)
        return f

    # -- 왼쪽: 설정 --------------------------------------------------------
    def _build_left(self) -> QWidget:
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        # 1 · 소자와 안전 경계
        c1, b1 = _card("소자와 안전 경계", "1", "stacks/*.json 에서 계산")
        row = QHBoxLayout(); row.setSpacing(8)
        row.addWidget(_label("스택"))
        self.cb_stack = QComboBox()
        self.cb_stack.addItems(self._stack_names())
        self.cb_stack.currentTextChanged.connect(lambda _: self._reload_stack())
        row.addWidget(self.cb_stack, 1)
        b1.addLayout(row)
        self.txt_bounds = QPlainTextEdit(); self.txt_bounds.setObjectName("mono")
        self.txt_bounds.setReadOnly(True); self.txt_bounds.setFixedHeight(128)
        b1.addWidget(self.txt_bounds)
        v.addWidget(c1)

        # 2 · 측정 지점
        c2, b2 = _card("측정 지점", "2", "체크한 소자만 측정")
        self.lb_nsites = QLabel("—"); self.lb_nsites.setObjectName("cardHint")
        row = QHBoxLayout(); row.setSpacing(8)
        self.ed_coords = QLineEdit(str(ROOT / "utils" / "grid_4x4.csv"))
        row.addWidget(self.ed_coords, 1)
        btn = QPushButton("찾기"); btn.setFixedWidth(58)
        btn.clicked.connect(self._pick_coords)
        row.addWidget(btn)
        b2.addLayout(row)
        row = QHBoxLayout(); row.setSpacing(8)
        row.addWidget(_label("area"))
        self.ed_area = QLineEdit("A"); self.ed_area.setFixedWidth(64)
        row.addWidget(self.ed_area)
        bt_all = QPushButton("전체"); bt_all.setFixedWidth(48)
        bt_none = QPushButton("해제"); bt_none.setFixedWidth(48)
        bt_all.clicked.connect(lambda: self._check_all_sites(True))
        bt_none.clicked.connect(lambda: self._check_all_sites(False))
        row.addWidget(bt_all); row.addWidget(bt_none)
        row.addStretch(1)
        row.addWidget(self.lb_nsites)
        b2.addLayout(row)
        self.tb_sites = QTableWidget(0, 4)
        self.tb_sites.setHorizontalHeaderLabels(["", "이름", "X [µm]", "Y [µm]"])
        self.tb_sites.setFixedHeight(140)
        self.tb_sites.verticalHeader().setVisible(False)
        self.tb_sites.setAlternatingRowColors(True)
        self.tb_sites.setEditTriggers(QTableWidget.NoEditTriggers)
        hh = self.tb_sites.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        self.tb_sites.setColumnWidth(0, 28)
        self.tb_sites.itemChanged.connect(self._on_site_check)
        b2.addWidget(self.tb_sites)
        self.ed_coords.textChanged.connect(self._reload_sites)
        self.ed_area.textChanged.connect(self._reload_sites)
        v.addWidget(c2)

        # 3 · 측정 조건
        c3, b3 = _card("측정 조건", "3")

        # 무엇을 재는가. 이게 아래 모든 칸의 뜻을 바꾼다.
        self.rb_tr = QPushButton("transfer  (Id–Vg)")
        self.rb_out = QPushButton("output  (Id–Vd)")
        self.rb_tr.setObjectName("segLeft")
        self.rb_out.setObjectName("segRight")
        for bt in (self.rb_tr, self.rb_out):
            bt.setCheckable(True)
        self.rb_tr.setChecked(True)
        self._seg_kind = QButtonGroup(self); self._seg_kind.setExclusive(True)
        self._seg_kind.addButton(self.rb_tr); self._seg_kind.addButton(self.rb_out)
        seg = QHBoxLayout(); seg.setSpacing(0)
        seg.addWidget(self.rb_tr, 1); seg.addWidget(self.rb_out, 1)
        b3.addLayout(seg)

        b3.addWidget(_label("측정 목적 — 시드 범위와 수렴 판단이 여기서 갈린다"))
        self.ed_obj = QPlainTextEdit(OBJECTIVE["transfer"])
        self.ed_obj.setFixedHeight(66)
        b3.addWidget(self.ed_obj)

        # 두 모드에 공통인 값. 게이트·V_D·방향은 '직접 입력' 여부와 무관하게
        # 조건을 바꾸므로 토글 밖에 둔다. 안에 두면 자동 모드에서 화면의 값과
        # 실제로 나가는 값이 달라진다.
        fm = QFormLayout(); fm.setContentsMargins(0, 2, 0, 2)
        fm.setSpacing(7); fm.setLabelAlignment(Qt.AlignLeft)
        self.cb_gate = QComboBox()
        # 배선된 단자만 고를 수 있게 한다. config.roles 에 없는 단자를 plan 이
        # 건드리면 executor 가 막는데(지금 셋업은 SMU 3개라 TG 가 없다), 고를
        # 수 있게 두면 검증에서야 알게 된다.
        wired = [t for t in ("BG", "TG") if t in DEFAULT.roles] or ["BG"]
        self.cb_gate.addItems(wired)
        if len(wired) == 1:
            self.cb_gate.setToolTip(
                f"이 셋업에 배선된 게이트는 {wired[0]} 뿐이다 "
                f"(config.roles = {dict(DEFAULT.roles)}).")
        fm.addRow(_label("게이트"), self.cb_gate)
        self.sp_vd = QDoubleSpinBox()
        self.sp_vd.setRange(-200, 200); self.sp_vd.setDecimals(3)
        self.sp_vd.setSingleStep(0.1); self.sp_vd.setValue(0.1)
        self.sp_vd.setSuffix(" V"); self.sp_vd.setFixedWidth(96)
        self.sp_vd.setToolTip("드레인 전압. 선형영역 이동도 공식이 성립하려면\n"
                              "Vd ≪ Vg − Vth 여야 한다 (관례값 0.1 V).")
        self.row_vd = _form_row(fm, "드레인 전압 V_D", self.sp_vd)
        self.cb_dir = QComboBox()
        self.cb_dir.addItems(["에이전트가 정함", "single (편도)", "double (왕복)"])
        self.cb_dir.setToolTip("히스테리시스를 얻으려면 왕복(double)이어야 한다.\n"
                               "왕복은 실측 점 수가 2배가 된다.")
        fm.addRow(_label("스윕 방향"), self.cb_dir)
        b3.addLayout(fm)

        # 세그먼트 토글. QPushButton 이지만 isChecked()/toggled 는 라디오와 같다.
        self.rb_auto = QPushButton("스택에서 자동 계산")
        self.rb_manual = QPushButton("직접 입력")
        self.rb_auto.setObjectName("segLeft")
        self.rb_manual.setObjectName("segRight")
        for bt in (self.rb_auto, self.rb_manual):
            bt.setCheckable(True)
        self.rb_auto.setChecked(True)
        self._seg = QButtonGroup(self); self._seg.setExclusive(True)
        self._seg.addButton(self.rb_auto); self._seg.addButton(self.rb_manual)
        seg = QHBoxLayout(); seg.setSpacing(0)
        seg.addWidget(self.rb_auto, 1); seg.addWidget(self.rb_manual, 1)
        b3.addLayout(seg)

        self.box_manual = QWidget()
        fm = QFormLayout(self.box_manual)
        fm.setContentsMargins(22, 4, 0, 4)
        fm.setSpacing(7)
        fm.setLabelAlignment(Qt.AlignLeft)
        hb = QHBoxLayout(); hb.setSpacing(6)
        self.sp_start = QDoubleSpinBox(); self.sp_start.setRange(-200, 200); self.sp_start.setValue(-2.84)
        self.sp_stop = QDoubleSpinBox(); self.sp_stop.setRange(-200, 200); self.sp_stop.setValue(2.84)
        for sp in (self.sp_start, self.sp_stop):
            sp.setSuffix(" V"); sp.setFixedWidth(86); sp.setDecimals(2)
        hb.addWidget(self.sp_start); hb.addWidget(_label("→")); hb.addWidget(self.sp_stop); hb.addStretch(1)
        wrap = QWidget(); wrap.setLayout(hb)
        self.row_span = _form_row(fm, "게이트 전압 범위", wrap)
        hb = QHBoxLayout(); hb.setSpacing(8)
        self.sp_points = QSpinBox(); self.sp_points.setRange(2, 4000); self.sp_points.setValue(115)
        self.sp_points.setFixedWidth(86)
        self.lb_step = QLabel("—"); self.lb_step.setObjectName("cardHint")
        self.lb_step.setToolTip("전압 범위 ÷ (점 수 − 1). 스텝이 SS 보다 성기면\n"
                                "SS 가 부풀려진 채로 그럴듯하게 나온다.")
        hb.addWidget(self.sp_points); hb.addWidget(self.lb_step); hb.addStretch(1)
        wrap = QWidget(); wrap.setLayout(hb)
        self.row_points = _form_row(fm, "점 수", wrap)

        # output 전용 — 게이트를 스텝으로 세우고 그 스텝마다 Vd 를 훑는다.
        hb = QHBoxLayout(); hb.setSpacing(6)
        self.sp_g0 = QDoubleSpinBox(); self.sp_g0.setRange(-200, 200); self.sp_g0.setValue(2.0)
        self.sp_g1 = QDoubleSpinBox(); self.sp_g1.setRange(-200, 200); self.sp_g1.setValue(18.0)
        for sp in (self.sp_g0, self.sp_g1):
            sp.setSuffix(" V"); sp.setFixedWidth(86); sp.setDecimals(2)
        hb.addWidget(self.sp_g0); hb.addWidget(_label("→")); hb.addWidget(self.sp_g1)
        hb.addStretch(1)
        wrap = QWidget(); wrap.setLayout(hb)
        self.row_gspan = _form_row(fm, "게이트 스텝 범위", wrap)
        self.sp_gsteps = QSpinBox(); self.sp_gsteps.setRange(1, 50); self.sp_gsteps.setValue(5)
        self.sp_gsteps.setFixedWidth(86)
        self.sp_gsteps.setToolTip(
            "곡선 개수. 스텝마다 게이트를 그 전압에 붙들고 Vd 를 훑으므로\n"
            "스텝이 늘수록 소자에 쌓이는 스트레스도 늘어난다.\n"
            "turn-on 아래 스텝은 바닥에 붙어 정보가 없다.")
        self.row_gsteps = _form_row(fm, "게이트 스텝 수", self.sp_gsteps)

        bt_fill = QPushButton("자동 계산값 가져오기")
        bt_fill.setToolTip("지금 스택·게이트로 계산한 기준안을 위 칸에 채운다.\n"
                           "덮어쓰기는 이 버튼을 누를 때만 일어난다.")
        bt_fill.clicked.connect(self._fill_manual_from_auto)
        fm.addRow("", bt_fill)
        b3.addWidget(self.box_manual)
        self.box_manual.setEnabled(False)
        for wdg in (self.sp_g0, self.sp_g1, self.sp_gsteps):
            wdg.valueChanged.connect(lambda _: self._rebuild_seed())
        self.rb_out.toggled.connect(lambda _: self._on_kind_changed())
        self.rb_manual.toggled.connect(self.box_manual.setEnabled)
        self.rb_manual.toggled.connect(lambda _: self._on_mode_changed())
        for wdg in (self.sp_start, self.sp_stop, self.sp_points, self.sp_vd):
            wdg.valueChanged.connect(lambda _: self._rebuild_seed())
        self.cb_gate.currentIndexChanged.connect(lambda _: self._rebuild_seed())
        self.cb_dir.currentIndexChanged.connect(lambda _: self._rebuild_seed())

        self.txt_seed = QPlainTextEdit(); self.txt_seed.setObjectName("mono")
        self.txt_seed.setReadOnly(True); self.txt_seed.setFixedHeight(88)
        b3.addWidget(self.txt_seed)
        v.addWidget(c3)

        # 4 · 판단
        c4, b4 = _card("판단", "4")
        self.ck_fixed = QCheckBox("위 조건을 모든 소자에 그대로 적용 (AI 호출 0회)")
        self.ck_fixed.setToolTip(
            "켜면 에이전트를 한 번도 부르지 않는다 — API 과금이 없고, 화면에\n"
            "적힌 조건이 그대로 측정에 들어간다.\n"
            "끄면 앞의 몇 소자로 조건을 탐색한 뒤 확정된 조건을 나머지에 적용한다.")
        b4.addWidget(self.ck_fixed)
        self.box_agent = QWidget()
        fm = QFormLayout(self.box_agent)
        fm.setSpacing(7); fm.setContentsMargins(22, 2, 0, 2)
        fm.setLabelAlignment(Qt.AlignLeft)
        self.cb_policy = QComboBox()
        self.cb_policy.addItems(["AI 판단 (Claude)", "규칙 기반 (API 키 불필요)"])
        fm.addRow(_label("판단 주체"), self.cb_policy)
        self.sp_calib = QSpinBox(); self.sp_calib.setRange(1, 99); self.sp_calib.setValue(2)
        self.sp_calib.setFixedWidth(72)
        self.sp_calib.setToolTip(
            "이 수만큼만 에이전트가 조건을 찾는다 — 상한이다.\n"
            "다 쓰고도 조건이 확정되지 않으면 남은 소자는 측정하지 않고 멈춘다.\n"
            "AI 호출은 소자당 최대 2회(조건 제안 + 관측 후 판단)다.")
        fm.addRow(_label("탐색할 소자 수"), self.sp_calib)
        self.sp_iters = QSpinBox(); self.sp_iters.setRange(1, 20); self.sp_iters.setValue(1)
        self.sp_iters.setFixedWidth(72)
        self.sp_iters.setToolTip("소자당 측정 횟수 상한. 기본 1 — 같은 소자를 반복하면\n"
                                 "전하 트래핑이 쌓여 특성이 아니라 스트레스 이력을 재게 된다.")
        fm.addRow(_label("소자당 최대 반복"), self.sp_iters)
        hint = QLabel("AI 에는 지표와 20~30점 다운샘플 곡선만 갑니다 "
                      "(약 900자).\n측정 CSV 원본은 결과 폴더에만 저장됩니다.")
        hint.setObjectName("cardHint")
        hint.setWordWrap(True)
        fm.addRow("", hint)
        b4.addWidget(self.box_agent)
        self.ck_fixed.toggled.connect(lambda on: self.box_agent.setEnabled(not on))
        self.ck_fixed.toggled.connect(lambda _: self._rebuild_seed())
        self.ck_setref = QCheckBox("실행 전에 지금 위치를 원점으로 등록")
        self.ck_setref.setToolTip("Nucleus 에서 첫 소자에 팁을 접촉시킨 상태여야 한다.\n"
                                  "그 자리가 좌표 (0, 0) 이 된다.")
        b4.addWidget(self.ck_setref)
        v.addWidget(c4)

        v.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return scroll

    # -- 오른쪽: 모니터 ----------------------------------------------------
    def _build_right(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        cP, bP = _card("측정 곡선", "", "소자 하나가 끝날 때마다 갱신")
        self.fig = Figure(figsize=(5, 3.2), tight_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setStyleSheet("background:transparent;")
        self._clear_plot()
        bP.addWidget(self.canvas)
        v.addWidget(cP, 5)

        cR, bR = _card("결과", "", "측정 종류에 따라 열이 바뀐다")
        self.tb_res = QTableWidget(0, 8)
        self.tb_res.verticalHeader().setVisible(False)
        self.tb_res.setAlternatingRowColors(True)
        self.tb_res.setEditTriggers(QTableWidget.NoEditTriggers)
        hh = self.tb_res.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)   # 상태는 안 잘리게
        self.tb_res.setColumnWidth(0, 70)
        bR.addWidget(self.tb_res)
        v.addWidget(cR, 3)

        cL, bL = _card("로그", "", "measauto 출력 그대로")
        self.txt_log = QPlainTextEdit(); self.txt_log.setObjectName("log")
        self.txt_log.setReadOnly(True)
        bL.addWidget(self.txt_log)
        v.addWidget(cL, 4)
        return w

    def _build_actionbar(self) -> QWidget:
        f = QFrame(); f.setObjectName("card")
        h = QHBoxLayout(f)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(10)
        self.btn_check = QPushButton("장비 확인")
        self.btn_check.setToolTip(
            "GPIB 버스와 B1500 통신만 확인한다. 측정도 이동도 하지 않는다.\n"
            "프로버는 버스에 보이는지만 보고 열지 않는다.")
        self.btn_check.clicked.connect(self._on_check)
        h.addWidget(self.btn_check)
        self.btn_dry = QPushButton("검증만  ·  장비 안 엶")
        self.btn_dry.clicked.connect(self._on_dry)
        h.addWidget(self.btn_dry)
        self.btn_open = QPushButton("결과 폴더")
        self.btn_open.clicked.connect(self._open_results)
        h.addWidget(self.btn_open)
        note = QLabel("실행 전 · B1500 EasyEXPERT 종료 · Nucleus 에서 척 로드 → "
                      "Alignment → Set Contact → 첫 소자 접촉")
        note.setObjectName("cardHint")
        note.setMinimumWidth(0)
        note.setTextInteractionFlags(Qt.NoTextInteraction)
        h.addWidget(note, 1)
        self.btn_stop = QPushButton("■  중단"); self.btn_stop.setObjectName("stop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        h.addWidget(self.btn_stop)
        self.btn_run = QPushButton("측정 시작"); self.btn_run.setObjectName("primary")
        self.btn_run.clicked.connect(self._on_run)
        h.addWidget(self.btn_run)
        return f

    # -- 설정 읽기 ---------------------------------------------------------
    @staticmethod
    def _stack_names() -> List[str]:
        d = ROOT / "measauto" / "stacks"
        if not d.exists():
            return ["sd_dualgate"]
        names = sorted({p.stem.replace(".limits", "") for p in d.glob("*.json")})
        return names or ["sd_dualgate"]

    def _pick_coords(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "좌표 파일", str(ROOT / "utils"), "좌표 (*.csv *.xlsx)")
        if p:
            self.ed_coords.setText(p)

    def _reload_stack(self):
        if IMPORT_ERROR:
            return
        try:
            self._bounds = bounds_from_stack(self.cb_stack.currentText())
            self.txt_bounds.setPlainText(self._bounds.describe())
        except MissingLimitError as e:
            self._bounds = None
            self.txt_bounds.setPlainText(f"[안전 경계 미설정]\n{e}")
        except Exception as e:
            self._bounds = None
            self.txt_bounds.setPlainText(f"{type(e).__name__}: {e}")
        self._rebuild_seed()
        self._reload_sites()

    def _reload_sites(self):
        if IMPORT_ERROR:
            return
        try:
            self._sites = load_sites(self.ed_coords.text(), area=self.ed_area.text() or "A")
        except Exception as e:
            self._sites = []
            self.lb_nsites.setText(f"읽기 실패: {type(e).__name__}")
            self.tb_sites.setRowCount(0)
            return
        self._filling = True
        self.tb_sites.setRowCount(len(self._sites))
        for i, s in enumerate(self._sites):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked)
            self.tb_sites.setItem(i, 0, chk)
            for j, val in enumerate([s.name, f"{s.x:g}", f"{s.y:g}"], start=1):
                self.tb_sites.setItem(i, j, QTableWidgetItem(str(val)))
        self._filling = False
        self._on_site_check()

    def _selected_sites(self) -> list:
        """체크된 소자만. 순서는 파일 순서 그대로 — 이동 경로가 곧 그 순서다."""
        out = []
        for i, s in enumerate(self._sites):
            it = self.tb_sites.item(i, 0)
            if it is None or it.checkState() == Qt.Checked:
                out.append(s)
        return out

    def _check_all_sites(self, on: bool):
        self._filling = True
        for i in range(self.tb_sites.rowCount()):
            it = self.tb_sites.item(i, 0)
            if it is not None:
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self._filling = False
        self._on_site_check()

    def _on_site_check(self, *_):
        if getattr(self, "_filling", False):
            return
        n = len(self._selected_sites())
        self.lb_nsites.setText(f"{n} / {len(self._sites)} 개 선택")

    def _on_mode_changed(self):
        """직접 입력으로 넘어가면 고정 조건도 같이 켠다.

        사람이 값을 적어 넣었는데 에이전트가 그 값을 덮어쓰면 화면과 실제가
        갈라진다. 반대 방향(고정 해제)은 막지 않는다 — '직접 적은 조건을
        출발점으로 삼아 탐색' 도 하고 싶을 수 있고, 그건 사람이 정할 일이다.
        """
        if self.rb_manual.isChecked() and not self.ck_fixed.isChecked():
            self.ck_fixed.setChecked(True)      # → _rebuild_seed 가 따라 돈다
        else:
            self._rebuild_seed()

    def _fill_manual_from_auto(self):
        """스택에서 계산한 기준안을 직접 입력 칸에 채운다.

        UI 가 값을 '보정'하는 게 아니라 seed.py 가 계산한 그 값을 그대로
        옮겨 적는 것이다. 사람이 버튼을 누를 때(또는 측정 종류를 바꿀 때)만.
        """
        try:
            auto = self._auto_seed()
        except Exception as e:
            self._seed = None
            self.txt_seed.setPlainText(f"{type(e).__name__}: {e}")
            self._update_step_label()
            return
        vals = [(self.sp_start, auto.var1.start),
                (self.sp_stop, auto.var1.stop),
                (self.sp_points, auto.var1.n_steps)]
        if auto.var2 is not None:
            vals += [(self.sp_g0, auto.var2.start), (self.sp_g1, auto.var2.stop),
                     (self.sp_gsteps, auto.var2.n_steps)]
        for wdg, val in vals:
            wdg.blockSignals(True)
            wdg.setValue(val)
            wdg.blockSignals(False)
        self._rebuild_seed()

    def _direction(self) -> Optional[str]:
        d = self.cb_dir.currentText()
        return "single" if d.startswith("single") else \
               "double" if d.startswith("double") else None

    def _kind(self) -> str:
        return "output" if self.rb_out.isChecked() else "transfer"

    def _auto_seed(self):
        """스택에서 계산한 기준안. 측정 종류에 따라 다른 함수가 만든다."""
        stack, gate = self.cb_stack.currentText(), self.cb_gate.currentText()
        if self._kind() == "output":
            return seed_output(stack, gate=gate)
        return seed_transfer(stack, gate=gate, vd=self.sp_vd.value())

    def _rebuild_seed(self):
        if IMPORT_ERROR:
            return
        try:
            seed = self._auto_seed()
            if self.rb_manual.isChecked():
                kw = dict(start=self.sp_start.value(), stop=self.sp_stop.value(),
                          points=self.sp_points.value())
                if self._direction():
                    kw["direction"] = self._direction()
                # IVPlan 은 frozen dataclass 라 속성 대입이 안 된다.
                seed = with_axis(seed, **kw)
                if self._kind() == "output" and seed.var2 is not None:
                    # 게이트 스텝(var2). constants 의 게이트 값도 같이 옮긴다 —
                    # var2 시작값과 다르면 validate 가 경고한다(실제로는 스텝
                    # 값이 덮어쓰므로 무시되지만, 화면과 기록이 어긋난다).
                    g0, g1 = self.sp_g0.value(), self.sp_g1.value()
                    v2 = _dc_replace(seed.var2, start=g0, stop=g1,
                                     points=self.sp_gsteps.value())
                    consts = dict(seed.constants)
                    consts[v2.terminal] = g0
                    seed = _dc_replace(seed, var2=v2, constants=consts)
                seed = _dc_replace(seed, note="UI 에서 직접 입력한 조건")
            elif self._direction():
                # 자동 계산이어도 사람이 못 박은 방향은 즉시 화면에 반영한다.
                # (session 도 force_direction 으로 같은 것을 강제한다)
                seed = with_axis(seed, direction=self._direction())
            self._seed = seed
            self.txt_seed.setPlainText(seed.describe() + "\n\n근거: " + (seed.note or "—"))
        except Exception as e:
            self._seed = None
            self.txt_seed.setPlainText(f"{type(e).__name__}: {e}")
        self._update_step_label()

    def _on_kind_changed(self):
        """측정 종류가 바뀌면 화면의 뜻이 통째로 바뀐다.

        output 은 **에이전트 경로가 없다.** Vd 범위는 포화 진입 여부가 정하는
        것이라 사람이 정하고(seed_output 의 주석), HeuristicPolicy 도 output 을
        받으면 곧장 converged 로 끝낸다. 프롬프트(agent/prompt.py)도 transfer
        기준으로 쓰여 있다. 그래서 고정 조건으로 묶는다 — 없는 판단을 있는 척
        하는 것보다 낫고, 덤으로 API 호출이 0 이 된다.
        """
        out = self._kind() == "output"
        _show_row(self.row_vd, not out)
        _show_row(self.row_gspan, out)
        _show_row(self.row_gsteps, out)
        self.row_span[0].setText("드레인 전압 V_D 범위" if out else "게이트 전압 범위")
        self.lb_step.setVisible(not out)

        if out:
            self.ck_fixed.setChecked(True)
        self.ck_fixed.setEnabled(not out)
        self.ck_fixed.setToolTip(
            "output(Id–Vd)은 에이전트가 정할 것이 없다 — Vd 범위는 포화 진입\n"
            "여부가 정하고, 그건 사람이 판단한다. 그래서 고정 조건뿐이다."
            if out else
            "켜면 에이전트를 한 번도 부르지 않는다 — API 과금이 없고, 화면에\n"
            "적힌 조건이 그대로 측정에 들어간다.\n"
            "끄면 앞의 몇 소자로 조건을 탐색한 뒤 확정된 조건을 나머지에 적용한다.")

        # 목적 문장은 사람이 고친 게 아니면 종류에 맞춰 갈아 끼운다.
        cur = self.ed_obj.toPlainText().strip()
        if cur in ("", OBJECTIVE["transfer"], OBJECTIVE["output"]):
            self.ed_obj.setPlainText(OBJECTIVE[self._kind()])

        self._fill_manual_from_auto()      # 칸의 뜻이 바뀌었으니 값도 갈아준다
        self._set_result_columns()

    def _update_step_label(self):
        """스텝 = 범위 ÷ (점 수 − 1). 산술일 뿐 판단이 아니다."""
        n = self.sp_points.value()
        span = abs(self.sp_stop.value() - self.sp_start.value())
        if n < 2 or span == 0:
            self.lb_step.setText("—")
            return
        step = span / (n - 1)
        extra = ""
        if self._seed is not None and self._seed.var1.direction == "double":
            extra = f"  ·  왕복이라 실측 {2 * n} 점"
        self.lb_step.setText(f"스텝 {step:.4g} V{extra}")

    def _validate(self) -> bool:
        """실행 전 검사. 위반이 있으면 화면에 띄우고 False."""
        if self._seed is None or self._bounds is None:
            QMessageBox.warning(self, "확인", "스택과 조건을 먼저 확인하세요.")
            return False
        if not self._sites:
            QMessageBox.warning(self, "확인", "측정 지점 파일을 읽지 못했습니다.")
            return False
        if not self._selected_sites():
            QMessageBox.warning(self, "확인", "측정할 소자를 하나 이상 체크하세요.")
            return False
        viol = validate(self._seed, self._bounds, terminals=DEFAULT.roles.keys())
        for v in viol:
            self._log(f"  {v}")
        errs = [v for v in viol if v.severity == "error"]
        if errs:
            QMessageBox.critical(
                self, "안전 경계 위반",
                "조건이 안전 경계를 넘습니다. 실행할 수 없습니다.\n\n"
                + "\n".join(str(v) for v in errs))
            return False
        return True

    # -- 실행 --------------------------------------------------------------
    def _make_policy(self):
        """판단 주체. default_policy() 는 자격증명이 없으면 조용히 규칙 기반으로
        떨어지므로, 무엇이 실제로 선택됐는지는 부른 쪽에서 로그에 남긴다."""
        if self.cb_policy.currentIndex() == 0:
            return default_policy()
        return HeuristicPolicy()

    def _plan_line(self) -> str:
        """이번 실행이 AI 를 최대 몇 번 부르는지. 실행 전에 보이게 한다 —
        비용은 나중에 청구서로 알게 되면 늦다."""
        if self.ck_fixed.isChecked():
            return "고정 조건 · AI 호출 0회"
        n = self.sp_calib.value()
        return (f"앞 {n}개만 탐색 (상한) · AI 호출 최대 {2 * n}회 · "
                f"{n}개 안에 조건이 안 잡히면 중단")

    @Slot(str)
    def _on_run_dir(self, path: str):
        self._run_dir = path
        self.btn_open.setEnabled(True)

    def _open_results(self):
        target = self._run_dir or str(ROOT / "results_agent")
        try:
            os.startfile(target)                       # noqa: S606 (Windows 전용)
        except Exception as e:
            QMessageBox.warning(self, "폴더 열기", f"{target}\n\n{type(e).__name__}: {e}")

    def _on_check(self):
        """장비 확인. 측정 경로와 스레드를 따로 쓴다 — 서로 끼어들면 안 된다."""
        if self.thread is not None or self.chk_thread is not None:
            return
        self.txt_log.clear()
        self._log("=== 장비 확인 — 측정도 이동도 하지 않습니다 ===")
        self.pill.set_state("idle", "확인 중")
        self.statusBar().showMessage("장비 확인 중…")
        self.btn_check.setEnabled(False)
        self.btn_run.setEnabled(False)
        self.btn_dry.setEnabled(False)

        self.chk_thread = QThread(self)
        self.chk_worker = CheckWorker()
        self.chk_worker.moveToThread(self.chk_thread)
        self.chk_thread.started.connect(self.chk_worker.run)
        self.chk_worker.log.connect(self._log)
        self.chk_worker.done.connect(self._on_check_done)
        self.chk_thread.start()

    @Slot(bool, str)
    def _on_check_done(self, ok: bool, msg: str):
        self._log(f"\n=== {'통과' if ok else '실패'} — {msg} ===")
        self.pill.set_state("ok" if ok else "err", "확인 통과" if ok else "확인 실패")
        self.statusBar().showMessage(msg)
        if self.chk_thread:
            self.chk_thread.quit()
            self.chk_thread.wait(3000)
        self.chk_thread = None
        self.chk_worker = None
        self.btn_check.setEnabled(True)
        self.btn_run.setEnabled(True)
        self.btn_dry.setEnabled(True)

    def _on_dry(self):
        self.txt_log.clear()
        self.pill.set_state("idle", "검증 중")
        self._log("=== 검증만 — 장비를 열지 않습니다 ===")
        self._log(f"목적: {self.ed_obj.toPlainText().strip()}")
        if self._bounds:
            self._log(self._bounds.describe())
        if self._seed:
            self._log(f"조건: {self._seed.describe()}")
            self._log(f"  근거: {self._seed.note}")
        sel = self._selected_sites()
        self._log(f"소자 {len(sel)} / {len(self._sites)} 개 선택 — {self._plan_line()}")
        if not self.ck_fixed.isChecked():
            self._log(f"  판단 주체: {type(self._make_policy()).__name__}")
        if self._validate():
            self._log("\n[검증 통과] 조건이 안전 경계 안에 있습니다.")
            self.statusBar().showMessage("검증 통과 — 이제 [측정 시작] 을 눌러도 됩니다.")
            self.pill.set_state("ok", "검증 통과")
        else:
            self.pill.set_state("err", "경계 위반")

    def _on_run(self):
        if self.chk_thread is not None:
            QMessageBox.information(self, "장비 확인 중",
                                    "장비 확인이 끝난 뒤에 시작하세요.")
            return
        if not self._validate():
            return
        sel = self._selected_sites()
        summary = (
            f"소자 {len(sel)} 개  ·  {self._seed.describe()}\n"
            f"판단: {self._plan_line()}\n"
            + ("" if self.ck_fixed.isChecked()
               else f"        ({type(self._make_policy()).__name__})\n")
            + ("원점 등록: 지금 접촉된 자리를 (0,0) 으로\n"
               if self.ck_setref.isChecked() else ""))
        if QMessageBox.question(
                self, "측정 시작",
                summary + "\n"
                "아래를 끝내 두셨습니까?\n\n"
                "B1500A 본체\n"
                "  0. EasyEXPERT 종료 (Start EasyEXPERT 화면으로)\n"
                "     띄워둔 채로는 외부 GPIB 제어가 안 들어갑니다.\n\n"
                "Nucleus UI\n"
                "  1. 척 로드 / 진공 ON\n"
                "  2. Alignment (2-point align)\n"
                "  3. Tipping / Set Contact\n"
                "  4. 첫 소자에 팁 contact\n\n"
                "안 되어 있으면 좌표가 통째로 어긋납니다.") != QMessageBox.Yes:
            return

        self.txt_log.clear()
        self._set_result_columns()
        self._clear_plot()
        self.canvas.draw_idle()
        fixed = self.ck_fixed.isChecked()
        params = dict(
            stack=self.cb_stack.currentText(),
            seed=self._seed,
            sites=self._selected_sites(),
            objective=self.ed_obj.toPlainText().strip(),
            out=str(ROOT / "results_agent"),
            calib=self.sp_calib.value(),
            max_iters=1 if fixed else self.sp_iters.value(),
            adapt_seed=not fixed,
            fixed_condition=fixed,
            force_direction=self._direction(),
            set_reference=self.ck_setref.isChecked(),
            # 고정 조건이면 policy 는 한 번도 안 불린다. 그래도 Session 이
            # 요구하므로 API 를 건드리지 않는 규칙 기반을 넣어 둔다.
            policy=HeuristicPolicy() if fixed else self._make_policy(),
        )

        self.thread = QThread(self)
        self.worker = MeasureWorker(params)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self._log)
        self.worker.progress.connect(self.statusBar().showMessage)
        self.worker.run_dir.connect(self._on_run_dir)
        self.worker.site_done.connect(self._on_site_done)
        self.worker.finished.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self._abort_requested.connect(self.worker.abort, Qt.DirectConnection)
        self.thread.start()

        self.btn_run.setEnabled(False)
        self.btn_dry.setEnabled(False)
        self.btn_check.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.pill.set_state("run", "측정 중")

    def _on_stop(self):
        self.btn_stop.setEnabled(False)
        self.pill.set_state("idle", "중단 중")
        self.statusBar().showMessage("중단 요청 — 지금 소자를 끝내고 멈춥니다…")
        self._abort_requested.emit()

    # 측정 종류마다 볼 값이 다르다. transfer 는 스펙 시트 다섯 항목,
    # output 은 접촉저항·포화·λ (SPICE 피팅에 들어가는 값들).
    _COLS = {
        "transfer": ["소자", "상태", "Vth [V]", "SS [V/dec]", "decades",
                     "µ [cm²/Vs]", "ΔVth [V]", "반복"],
        "output": ["소자", "상태", "Id@Vd,max [A]", "포화비", "R_on [Ω]",
                   "λ [1/V]", "r_out [Ω]", "반복"],
    }

    def _set_result_columns(self, kind: Optional[str] = None):
        self.tb_res.setRowCount(0)
        self.tb_res.setHorizontalHeaderLabels(self._COLS[kind or self._kind()])

    @staticmethod
    def _result_row(r, kind: str) -> list:
        m = r.metrics or {}
        if kind == "output":
            return [r.site.name, r.status,
                    MainWindow._fmt(m.get("id_at_vdmax")),
                    MainWindow._fmt(m.get("saturation_ratio")),
                    MainWindow._fmt(m.get("r_on_ohm")),
                    MainWindow._fmt(m.get("lambda_per_V")),
                    MainWindow._fmt(m.get("r_out_ohm")), str(r.iterations)]
        mu = MainWindow._fmt(m.get("mobility_cm2_Vs"))
        if mu != "—" and m.get("mobility_is_lower_bound"):
            # gm 꼭대기를 창 안에서 못 넘겼으면 이동도는 하한이다.
            # 값만 적어 두면 나중에 그냥 이동도로 읽힌다.
            mu = "≥ " + mu
        return [r.site.name, r.status,
                MainWindow._fmt(m.get("vth_cc")), MainWindow._fmt(m.get("ss")),
                MainWindow._fmt(m.get("decades")), mu,
                MainWindow._fmt(m.get("hysteresis_V")), str(r.iterations)]

    @Slot(object, str)
    def _on_site_done(self, r, csv_path: str):
        m = r.metrics or {}
        row = self.tb_res.rowCount()
        self.tb_res.insertRow(row)
        kind = m.get("kind") or self._kind()
        vals = self._result_row(r, kind)
        for j, v in enumerate(vals):
            it = QTableWidgetItem(v)
            if j >= 2:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if j == 1:      # 상태는 색으로 먼저 읽히게
                it.setForeground(QColor(self._status_color(r.status)))
                f = it.font(); f.setBold(True); it.setFont(f)
            self.tb_res.setItem(row, j, it)
        self.tb_res.scrollToBottom()
        if csv_path:
            term = r.final_plan.var1.terminal if r.final_plan is not None else None
            self._plot(csv_path, f"{r.site.area}/{r.site.name}", m, term, kind)

    @staticmethod
    def _status_color(status: str) -> str:
        return {"converged": C["ok"],
                "propose": C["accent"],
                "max_iters": C["warn"],
                "needs_human": C["warn"],
                "dead": C["danger"],
                "leaky": C["danger"],
                "out_of_bounds": C["danger"],
                "polarity_anomaly": C["warn"]}.get(status, C["dim"])

    @Slot(str)
    def _on_finished(self, reason: str):
        self._log(f"\n=== {reason} ===")
        safe = "팁은 원점에 있고 SMU 출력은 꺼져 있습니다."
        if reason.startswith("수렴 실패"):
            self.pill.set_state("err", "수렴 실패")
            self.statusBar().showMessage(f"{reason} — {safe}")
            QMessageBox.warning(
                self, "수렴 실패",
                f"{reason}\n\n"
                f"탐색할 소자 수 안에서 측정 조건이 확정되지 않아 "
                f"남은 소자는 측정하지 않았습니다.\n"
                f"측정된 소자의 곡선과 지표는 결과 폴더에 그대로 있습니다.\n\n"
                f"다음 중 하나를 하세요:\n"
                f"  · 전압 범위를 넓혀 다시 (turn-on 이 창 밖일 수 있습니다)\n"
                f"  · [직접 입력] 으로 조건을 지정하고 고정 조건으로 실행\n"
                f"  · 탐색할 소자 수를 늘려 다시 (AI 호출도 그만큼 늘어납니다)")
        else:
            self.pill.set_state("ok" if reason == "완료" else "idle",
                                reason.split(" ")[0])
            self.statusBar().showMessage(f"{reason} — {safe}")
        self._teardown()

    @Slot(str)
    def _on_failed(self, msg: str):
        self._log(f"\n[실패]\n{msg}")
        self.statusBar().showMessage("실패 — 로그를 확인하세요.")
        self.pill.set_state("err", "실패")
        QMessageBox.critical(self, "실패", msg.split("\n\n")[0])
        self._teardown()

    def _teardown(self):
        if self.thread:
            self.thread.quit()
            self.thread.wait(3000)
        self.thread = None
        self.worker = None
        self.btn_run.setEnabled(True)
        self.btn_dry.setEnabled(True)
        self.btn_check.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if self._closing:
            # 닫으려다 정리를 기다리던 참이었다. 이제 안전하다.
            self.close()

    # -- 표시 --------------------------------------------------------------
    @staticmethod
    def _fmt(v) -> str:
        if v is None:
            return "—"
        try:
            return f"{float(v):.3g}"
        except (TypeError, ValueError):
            return str(v)

    def _style_axes(self, log: bool = True):
        # output 은 선형이다. 포화 무릎과 선형영역 기울기(=접촉저항)가
        # 로그 축에서는 안 보인다 — 그게 output 을 재는 이유인데.
        self.ax.set_yscale("log" if log else "linear")
        self.ax.tick_params(top=False, right=False, length=3)
        self.ax.xaxis.set_tick_params(direction="out")
        self.ax.yaxis.set_tick_params(direction="in")
        for side in ("top", "right"):
            self.ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            self.ax.spines[side].set_color(C["line"])

    def _clear_plot(self):
        out = self._kind() == "output"
        self.ax.clear()
        self.ax.set_xlabel("Drain voltage (V)" if out else "Gate voltage (V)")
        self.ax.set_ylabel("I$_D$ (A)" if out else "|I$_D$| (A)")
        self._style_axes(log=not out)
        self.ax.text(0.5, 0.5, "측정을 시작하면 여기에 곡선이 나타납니다",
                     ha="center", va="center", transform=self.ax.transAxes,
                     color=C["faint"], fontsize=9)

    def _plot(self, csv_path: str, title: str, metrics: dict,
              sweep_terminal: Optional[str] = None, kind: str = "transfer"):
        try:
            import pandas as pd
            df = pd.read_csv(csv_path)
            # x 축은 **실제로 sweep 한 단자**여야 한다. 고정 단자도 컬럼이
            # 채워져 나오므로(frame.fill_constants), 이름만 보고 고르면
            # TG sweep 인데 V_BG(=0 V 고정)를 x 로 잡아 곡선이 세로선이 된다.
            cands = ([f"V_{sweep_terminal}"] if sweep_terminal else []) \
                + (["V_D"] if kind == "output" else ["V_BG", "V_TG", "V_D"])
            xcol = next((c for c in cands if c in df.columns), None)
            if xcol is None or "I_D" not in df.columns:
                return
            self.ax.clear()

            if kind == "output":
                self._plot_output(df, xcol)
            else:
                self._plot_transfer(df, xcol, metrics)

            self.ax.set_xlabel(f"{xcol} (V)")
            self.ax.set_ylabel("I$_D$ (A)" if kind == "output" else "|I$_D$| (A)")
            self.ax.set_title(title, color=C["ink"], loc="left", pad=8)
            self._style_axes(log=(kind != "output"))
            self.canvas.draw_idle()
        except Exception as e:
            self._log(f"[그림 실패] {type(e).__name__}: {e}")

    def _plot_transfer(self, df, xcol: str, metrics: dict):
        gcol = f"I_{xcol[2:]}"              # 스윕한 게이트의 전류
        self.ax.plot(df[xcol], df["I_D"].abs() + 1e-15,
                     lw=1.3, color=C["accent"], label="$I_D$")
        if gcol in df.columns and gcol != "I_D":
            self.ax.plot(df[xcol], df[gcol].abs() + 1e-15, lw=0.8,
                         ls="--", color=C["faint"], label="$I_G$")
        vth = metrics.get("vth_cc")
        if vth is not None:
            self.ax.axvline(float(vth), color=C["warn"], ls=":", lw=1.1)
            self.ax.annotate(f"$V_{{th}}$ = {float(vth):.2f} V",
                             xy=(float(vth), 0.04), xycoords=("data", "axes fraction"),
                             color=C["warn"], fontsize=8.5,
                             ha="left", va="bottom", xytext=(4, 0),
                             textcoords="offset points")
        self.ax.legend(fontsize=8, labelcolor=C["dim"], loc="upper left")

    def _plot_output(self, df, xcol: str):
        """게이트 스텝마다 한 곡선. 스텝을 겹쳐 그리지 않으면 곡선이
        지그재그로 이어져 포화 무릎이 사라진다."""
        import numpy as np
        if "step" in df.columns and df["step"].nunique() > 1:
            steps = sorted(df["step"].unique())
        else:
            steps = [None]
        # 스텝이 높을수록(=게이트가 셀수록) 진하게. 순서가 눈에 보여야 한다.
        shades = np.linspace(0.35, 1.0, len(steps))
        base = tuple(int(C["accent"].lstrip("#")[i:i + 2], 16) / 255
                     for i in (0, 2, 4))
        for s, k in zip(steps, shades):
            sub = df if s is None else df[df["step"] == s]
            self.ax.plot(sub[xcol], sub["I_D"], lw=1.2,
                         color=tuple(c * k + (1 - k) * 0.35 for c in base),
                         label=None if s is None else f"$V_G$={float(s):g} V")
        if steps[0] is not None:
            self.ax.legend(fontsize=7.5, labelcolor=C["dim"], loc="upper left",
                           ncol=2 if len(steps) > 4 else 1)

    @Slot(str)
    def _log(self, line: str):
        self.txt_log.appendPlainText(line)
        self.txt_log.moveCursor(QTextCursor.End)

    # -- 설정 기억 ---------------------------------------------------------
    # 매번 같은 값을 다시 채워 넣는 것이 이 화면에서 제일 성가신 일이다.
    # 장비를 여는 값(원점 등록)은 일부러 저장하지 않는다 — 그건 그날의
    # 상태에 달린 것이고, 기억해 두면 의도치 않게 켜진 채로 실행된다.
    _SETTINGS = [
        ("kind", lambda w: w._kind(),
         lambda w, v: (w.rb_out if v == "output" else w.rb_tr).setChecked(True)),
        ("stack", lambda w: w.cb_stack.currentText(),
         lambda w, v: w.cb_stack.setCurrentText(v)),
        ("coords", lambda w: w.ed_coords.text(), lambda w, v: w.ed_coords.setText(v)),
        ("area", lambda w: w.ed_area.text(), lambda w, v: w.ed_area.setText(v)),
        ("objective", lambda w: w.ed_obj.toPlainText(),
         lambda w, v: w.ed_obj.setPlainText(v)),
        ("gate", lambda w: w.cb_gate.currentText(),
         lambda w, v: w.cb_gate.setCurrentText(v)),
        ("vd", lambda w: w.sp_vd.value(), lambda w, v: w.sp_vd.setValue(float(v))),
        ("dir", lambda w: w.cb_dir.currentIndex(),
         lambda w, v: w.cb_dir.setCurrentIndex(int(v))),
        ("manual", lambda w: w.rb_manual.isChecked(),
         lambda w, v: (w.rb_manual if _truthy(v) else w.rb_auto).setChecked(True)),
        ("start", lambda w: w.sp_start.value(), lambda w, v: w.sp_start.setValue(float(v))),
        ("stop", lambda w: w.sp_stop.value(), lambda w, v: w.sp_stop.setValue(float(v))),
        ("points", lambda w: w.sp_points.value(), lambda w, v: w.sp_points.setValue(int(v))),
        ("g0", lambda w: w.sp_g0.value(), lambda w, v: w.sp_g0.setValue(float(v))),
        ("g1", lambda w: w.sp_g1.value(), lambda w, v: w.sp_g1.setValue(float(v))),
        ("gsteps", lambda w: w.sp_gsteps.value(),
         lambda w, v: w.sp_gsteps.setValue(int(v))),
        ("fixed", lambda w: w.ck_fixed.isChecked(),
         lambda w, v: w.ck_fixed.setChecked(_truthy(v))),
        ("policy", lambda w: w.cb_policy.currentIndex(),
         lambda w, v: w.cb_policy.setCurrentIndex(int(v))),
        ("calib", lambda w: w.sp_calib.value(), lambda w, v: w.sp_calib.setValue(int(v))),
        ("iters", lambda w: w.sp_iters.value(), lambda w, v: w.sp_iters.setValue(int(v))),
    ]

    def _load_settings(self):
        s = QSettings("DSIL", "measauto_ui")
        for key, _get, set_ in self._SETTINGS:
            v = s.value(f"ui/{key}", None)
            if v is None:
                continue
            try:
                set_(self, v)
            except Exception:
                pass                    # 값 형식이 바뀐 옛 설정은 그냥 무시한다
        geo = s.value("ui/geometry", None)
        if geo is not None:
            self.restoreGeometry(geo)

    def _save_settings(self):
        s = QSettings("DSIL", "measauto_ui")
        for key, get, _set in self._SETTINGS:
            try:
                s.setValue(f"ui/{key}", get(self))
            except Exception:
                pass
        s.setValue("ui/geometry", self.saveGeometry())

    def closeEvent(self, ev):
        """측정 중이면 **정리가 끝날 때까지 창을 닫지 않는다.**

        여기서 창을 닫으면 마지막 창이라 프로세스가 죽고, 워커의 finally
        (ex.home → 출력 OFF → GTL → 세션 close)가 실행되지 않는다. 그러면
        SMU 출력이 켜진 채 팁이 닿아 있고, VISA 세션도 남아 다음 실행이 막힌다.

        예전에는 60초 기다렸다가 그냥 닫았는데, 스윕 하나가 그보다 길면
        정확히 그 사고가 났다. 이제는 기다리지 않고 창을 열어둔 채 중단을
        걸고, 정리가 끝나면 _teardown 이 다시 닫는다.
        """
        if self.chk_thread is not None and self.chk_thread.isRunning():
            # 확인은 길어야 25초다. 그 사이에 프로세스가 죽으면 VISA 세션이
            # 남으므로 잠깐 기다리게 한다.
            QMessageBox.information(
                self, "장비 확인 중",
                "장비 확인이 돌고 있습니다. 끝난 뒤에 닫아 주세요.\n"
                "(길어야 25초입니다)")
            ev.ignore()
            return

        if self.thread and self.thread.isRunning():
            if self._closing:
                # 두 번째 시도 — 정리가 안 끝나고 있다. 강제로 나갈지 묻는다.
                if QMessageBox.warning(
                        self, "정리 중",
                        "장비 정리가 아직 안 끝났습니다.\n\n"
                        "지금 강제로 닫으면 SMU 출력이 켜진 채 남고(팁이 소자에 "
                        "닿아 있습니다) VISA 세션도 남아 다음 실행이 막힐 수 "
                        "있습니다.\n\n그래도 닫으시겠습니까?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No) != QMessageBox.Yes:
                    ev.ignore()
                    return
                self._save_settings()
                ev.accept()
                return

            if QMessageBox.question(
                    self, "측정 중",
                    "측정이 돌고 있습니다. 정말 닫으시겠습니까?\n\n"
                    "지금 소자를 끝내고 팁을 원점으로 되돌린 뒤 "
                    "자동으로 닫힙니다.") != QMessageBox.Yes:
                ev.ignore()
                return
            self._closing = True
            self._abort_requested.emit()
            self.pill.set_state("idle", "정리 중")
            self.statusBar().showMessage(
                "중단 요청 — 지금 소자를 끝내고 장비를 정리한 뒤 자동으로 닫힙니다…")
            ev.ignore()             # 정리가 끝나면 _teardown 이 다시 닫는다
            return

        self._save_settings()
        ev.accept()


def main():
    # 창이 뜨기 전에 터지면 콘솔 없이 띄운 경우 아무것도 안 보인다.
    # (창이 뜬 뒤의 예외는 Qt 가 받고, 화면의 로그/상태줄로 나간다)
    try:
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        app.setStyleSheet(QSS)
        win = MainWindow()
        win.show()
    except Exception:                                      # pragma: no cover
        _fatal("자동측정 UI 를 열지 못했습니다", traceback.format_exc())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
