"""plotting.py — 결과 그림의 형식을 한 곳에 모은다.

store 가 회차마다 남기는 PNG 와, 여러 소자를 겹쳐 그리는 비교 그림이 같은
형식을 쓰도록 여기서 정의한다. 형식을 코드 여기저기에 흩어 두면 그림마다
축 이름과 눈금이 미묘하게 달라져서, 나중에 붙여놓고 볼 때 비교가 안 된다.

형식 기준 — Origin_data_Template.pdf (연구실 표준)
  윈도우      10 × 10 inch
  레이어 영역  Left 20%, Top 20%, Width 60%, Height 60%  (→ 정사각 플롯)
  축 눈금 글자 31,  축 이름 38 bold
  축선        검정, 두께 1.5, 사방 다 그림
  Y 축 눈금    Major = 안쪽(In), Minor = 없음  ← decade 에만 작대기
  X 축 눈금    Major = 바깥(Out), Minor = 바깥
  위·오른쪽    선만 그리고 눈금은 없음
  눈금 표기    Scientific — 눈금마다 `4.0×10^-8` 처럼 지수를 전부 적는다

Y 만 안쪽인 것이 어색해 보이지만 템플릿이 그렇다(PDF p.6 Left=In, p.7 Bottom=Out).

주의: PNG 는 판단 입력이 아니라 **사람이 볼 기록**이다(store 참고). 그래서
형식을 맞추는 데 드는 비용은 나중에 사람이 그림을 읽는 시간으로 회수된다.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

# 축 이름 — 이름, 기호(수식), 단위를 다 적는다.
LABELS = {
    "vd": r"Drain voltage, $V_\mathrm{DS}$ (V)",
    "vg": r"Gate voltage, $V_\mathrm{GS}$ (V)",
    "id": r"Drain current, $I_\mathrm{DS}$ (A)",
    "id_abs": r"Drain current, $|I_\mathrm{DS}|$ (A)",
    "ig_abs": r"Gate current, $|I_\mathrm{GS}|$ (A)",
}

# --- Origin 템플릿 치수 ------------------------------------------------------
FIGSIZE = (10.0, 10.0)          # 윈도우 10 × 10 inch
# 레이어 영역: Left 20%, Top 20%, W 60%, H 60% → matplotlib rect 의 bottom 은
# 위에서 잰 Top 을 아래 기준으로 바꾼 값이다 (1 - 0.20 - 0.60 = 0.20).
AXES_RECT = (0.20, 0.20, 0.60, 0.60)
DPI = 100                       # 10 inch × 100 = 1000 px
AXIS_LW = 1.5                   # 축선 두께
TICK_LEN = 8                    # 눈금 길이
FONT = {"title": 34, "label": 38, "tick": 31, "legend": 22}


def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def new_figure():
    """템플릿 치수의 빈 그림. 축 위치를 직접 잡으므로 tight_layout 은 쓰지 않는다."""
    plt = _mpl()
    fig = plt.figure(figsize=FIGSIZE, dpi=DPI)
    ax = fig.add_axes(AXES_RECT)
    return fig, ax


def save(fig, path) -> None:
    """저장. **plot 영역 6×6 inch 는 유지하되 글자가 잘리지 않게** 한다.

    템플릿의 20% 여백은 축 이름이 `ID (A)` 처럼 짧을 때를 상정한 값이다.
    `Drain current, |I_DS| (A)` 처럼 긴 이름을 38 pt bold 로 쓰면 2 inch 를
    넘어 잘린다. 비율(=플롯 크기와 글자 크기의 관계)이 중요한 것이므로,
    바깥 여백만 글자에 맞춰 늘린다.
    """
    fig.savefig(path, bbox_inches="tight", pad_inches=0.15)


def sci_formatter(values: Sequence[float]):
    """눈금마다 `4 × 10^-8` 꼴로 적는 포매터 (Origin 의 Scientific 표기).

    matplotlib 기본은 지수를 축 구석에 한 번만 빼는데, 그러면 눈금 숫자만
    보고는 자릿수를 알 수 없어 그림을 오려 붙였을 때 오독하기 쉽다.
    """
    from matplotlib.ticker import Formatter

    finite = [abs(v) for v in values if v and math.isfinite(v)]
    fallback = int(math.floor(math.log10(max(finite)))) if finite else 0

    class _Sci(Formatter):
        """눈금 **간격**에서 지수를 정한다.

        최댓값 기준으로 잡으면 0.2·0.4·… 처럼 소수가 되어 읽기 나쁘다.
        간격 기준이면 2·4·6·… 으로 떨어진다. 눈금은 set_locs 로 한 번에
        받으므로 호출 순서에 의존하지 않는다.
        """

        def __init__(self):
            self.exp = fallback

        def set_locs(self, locs):
            vals = sorted({abs(v) for v in locs if v and math.isfinite(v)})
            gaps = [b - a for a, b in zip(vals, vals[1:]) if b > a]
            base = min(gaps) if gaps else (vals[-1] if vals else 0)
            if base > 0:
                self.exp = int(math.floor(math.log10(base)))

        def __call__(self, x, pos=None):
            if x == 0:
                return "0"
            # \times 앞뒤 공백을 없앤다(`{\times}`). 31 pt 에서 공백 있는 표기는
            # 눈금 하나가 2 inch 를 넘어 20% 여백(2 inch)을 뚫는다.
            return rf"${x / 10.0 ** self.exp:g}{{\times}}10^{{{self.exp}}}$"

    return _Sci()


def blues(n: int):
    """파랑 계열 순차 색. 값이 클수록 진하다. 흰색에 가까운 끝은 피한다."""
    plt = _mpl()
    cmap = plt.get_cmap("Blues")
    if n <= 1:
        return [cmap(0.75)]
    return [cmap(0.30 + 0.65 * i / (n - 1)) for i in range(n)]


def style_axes(ax, *, title: str = "", xlabel: str = "", ylabel: str = "",
               sci_y: bool = False, yvalues: Optional[Sequence[float]] = None,
               log_y: bool = False):
    """축 하나에 템플릿 형식을 입힌다."""
    from matplotlib.ticker import LogLocator, NullLocator

    if title:
        ax.set_title(title, fontsize=FONT["title"], pad=16)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=FONT["label"], fontweight="bold",
                      labelpad=14)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=FONT["label"], fontweight="bold",
                      labelpad=14)

    for s in ax.spines.values():          # 사방 테두리, 검정 1.5
        s.set_linewidth(AXIS_LW)
        s.set_color("black")
    ax.grid(False)

    # 축마다 방향이 다르다 (템플릿 p.6~p.9)
    #   Y 왼쪽  : major 안쪽, minor 없음   ← decade 에만 작대기
    #   X 아래  : major/minor 모두 바깥
    #   위·오른쪽: 눈금 없음 (선만)
    ax.tick_params(axis="y", which="major", direction="in", length=TICK_LEN,
                   width=AXIS_LW, labelsize=FONT["tick"], left=True, right=False,
                   pad=8)
    ax.tick_params(axis="y", which="minor", left=False, right=False)
    ax.tick_params(axis="x", which="major", direction="out", length=TICK_LEN,
                   width=AXIS_LW, labelsize=FONT["tick"], bottom=True, top=False,
                   pad=8)
    ax.tick_params(axis="x", which="minor", direction="out",
                   length=TICK_LEN * 0.55, width=AXIS_LW * 0.8,
                   bottom=True, top=False)

    if log_y:
        # decade 에만 눈금. 기본은 2·3·…·9 자리에도 minor 를 찍어 지저분하다.
        ax.yaxis.set_major_locator(LogLocator(base=10.0))
        ax.yaxis.set_minor_locator(NullLocator())
    elif sci_y and yvalues is not None:
        ax.yaxis.set_major_formatter(sci_formatter(yvalues))
    return ax


def legend(ax, *, ncol: int = 1):
    h, _ = ax.get_legend_handles_labels()
    if not h:
        return
    ax.legend(loc="upper left", fontsize=FONT["legend"], frameon=False,
              ncol=ncol, handlelength=1.8, columnspacing=1.4,
              labelspacing=0.35, borderaxespad=0.8)
