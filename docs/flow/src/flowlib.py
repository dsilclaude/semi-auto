"""draw.io 풍 순서도를 matplotlib 로 그린다. 좌표는 픽셀, y 는 위에서 아래."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Polygon, Rectangle

for cand in ("Malgun Gothic", "Apple SD Gothic Neo", "NanumGothic", "DejaVu Sans"):
    try:
        matplotlib.font_manager.findfont(cand, fallback_to_default=False)
        plt.rcParams["font.family"] = cand
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False

GREEN = ("#d5e8d4", "#82b366")
GRAY = ("#f5f5f5", "#666666")
ORANGE = ("#ffe6cc", "#d79b00")
BLUE = ("#dae8fc", "#6c8ebf")
PURPLE = ("#e1d5e7", "#9673a6")
EDGE = "#4d4d4d"
FS = 8.5
FS_LBL = 8.2


class Node:
    def __init__(self, cx, y, w, h):
        self.cx, self.y, self.w, self.h = cx, y, w, h

    def top(self):    return (self.cx, self.y)
    def bot(self):    return (self.cx, self.y + self.h)
    def left(self):   return (self.cx - self.w / 2, self.y + self.h / 2)
    def right(self):  return (self.cx + self.w / 2, self.y + self.h / 2)
    def cy(self):     return self.y + self.h / 2


class Canvas:
    def __init__(self, w, h, grid=True):
        self.w, self.h = w, h
        self.fig = plt.figure(figsize=(w / 100, h / 100), dpi=100)
        ax = self.fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, w); ax.set_ylim(h, 0)
        ax.set_aspect("equal"); ax.axis("off")
        ax.add_patch(Rectangle((0, 0), w, h, fc="white", ec="none", zorder=0))
        if grid:
            for x in range(0, w + 1, 20):
                ax.plot([x, x], [0, h], color="#eeeeee", lw=0.6, zorder=0)
            for y in range(0, h + 1, 20):
                ax.plot([0, w], [y, y], color="#eeeeee", lw=0.6, zorder=0)
        self.ax = ax

    # --- 도형 ------------------------------------------------------------
    def _text(self, cx, cy, text, size=FS, color="#111111", weight="normal"):
        self.ax.text(cx, cy, text, ha="center", va="center", fontsize=size,
                     color=color, zorder=5, linespacing=1.45, weight=weight)

    def proc(self, cx, y, w, h, text, style=GRAY, rounding=3):
        self.ax.add_patch(FancyBboxPatch(
            (cx - w / 2 + rounding, y + rounding), w - 2 * rounding, h - 2 * rounding,
            boxstyle=f"round,pad=0,rounding_size={rounding}",
            fc=style[0], ec=style[1], lw=1.2, zorder=3))
        self._text(cx, y + h / 2, text)
        return Node(cx, y, w, h)

    def term(self, cx, y, w, h, text, style=GREEN):
        import math
        r = h / 2.0
        cyc = y + r
        pts = []
        for k in range(25):                      # 오른쪽 반원
            a = math.radians(-90 + 180 * k / 24)
            pts.append((cx + w / 2 - r + r * math.cos(a), cyc + r * math.sin(a)))
        for k in range(25):                      # 왼쪽 반원
            a = math.radians(90 + 180 * k / 24)
            pts.append((cx - w / 2 + r + r * math.cos(a), cyc + r * math.sin(a)))
        self.ax.add_patch(Polygon(pts, closed=True, fc=style[0], ec=style[1],
                                  lw=1.2, zorder=3))
        self._text(cx, cyc, text)
        return Node(cx, y, w, h)

    def dec(self, cx, y, w, h, text, style=ORANGE):
        pts = [(cx, y), (cx + w / 2, y + h / 2), (cx, y + h), (cx - w / 2, y + h / 2)]
        self.ax.add_patch(Polygon(pts, closed=True, fc=style[0], ec=style[1],
                                  lw=1.2, zorder=3))
        self._text(cx, y + h / 2, text)
        return Node(cx, y, w, h)

    def io(self, cx, y, w, h, text, style=GRAY):
        s = h * 0.42
        pts = [(cx - w / 2 + s, y), (cx + w / 2, y),
               (cx + w / 2 - s, y + h), (cx - w / 2, y + h)]
        self.ax.add_patch(Polygon(pts, closed=True, fc=style[0], ec=style[1],
                                  lw=1.2, zorder=3))
        self._text(cx, y + h / 2, text)
        return Node(cx, y, w, h)

    def sub(self, cx, y, w, h, text, style=BLUE):
        n = self.proc(cx, y, w, h, text, style=style, rounding=2)
        for dx in (-w / 2 + 9, w / 2 - 9):
            self.ax.plot([cx + dx, cx + dx], [y, y + h], color=style[1],
                         lw=1.2, zorder=4)
        return n

    # --- 선 --------------------------------------------------------------
    def edge(self, pts, label=None, lxy=None, dashed=False, lha="left",
             lva="center", color=EDGE, head=True):
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        self.ax.plot(xs, ys, color=color, lw=1.2, zorder=2,
                     dashes=(4, 3) if dashed else (None, None),
                     solid_joinstyle="miter")
        if head:
            self.ax.annotate("", xy=pts[-1], xytext=pts[-2], zorder=2,
                             arrowprops=dict(arrowstyle="-|>", color=color,
                                             lw=1.2, shrinkA=0, shrinkB=0,
                                             mutation_scale=11))
        if label:
            if lxy is None:
                mx = (pts[0][0] + pts[1][0]) / 2; my = (pts[0][1] + pts[1][1]) / 2
                lxy = (mx + 5, my)
            self.ax.text(lxy[0], lxy[1], label, fontsize=FS_LBL, color="#333333",
                         ha=lha, va=lva, zorder=6, linespacing=1.35,
                         bbox=dict(fc="white", ec="none", pad=1.0, alpha=0.9))

    def down(self, a, b, label=None, lxy=None):
        """세로로 이어진 두 노드 (a 아래 → b 위)."""
        self.edge([a.bot(), b.top()], label,
                  lxy or (a.cx + 6, (a.bot()[1] + b.top()[1]) / 2))

    def group(self, x, y, w, h, title):
        self.ax.add_patch(Rectangle((x, y), w, h, fc="none", ec="#8fa9c4",
                                    lw=1.0, ls=(0, (5, 4)), zorder=1))
        self.ax.text(x + w - 6, y + 14, title, fontsize=10, color="#4a6785",
                     ha="right", va="center", zorder=6, weight="bold")

    def caption(self, x, y, text, size=13):
        self.ax.text(x, y, text, fontsize=size, color="#222222", ha="left",
                     va="center", zorder=6, weight="bold")

    def note(self, x, y, text, ha="left", size=8.2, color="#666666"):
        self.ax.text(x, y, text, fontsize=size, color=color, ha=ha, va="center",
                     zorder=6, linespacing=1.4)

    def save(self, base):
        self.fig.savefig(base + ".png", dpi=150, facecolor="white")
        self.fig.savefig(base + ".svg", facecolor="white")
        plt.close(self.fig)
        print("saved:", base + ".png/.svg")
