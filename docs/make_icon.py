# -*- coding: utf-8 -*-
r"""make_icon.py — DSIL 로고의 파란 D 를 바로가기 아이콘(.ico)으로 굽는다.

    .\.venv\Scripts\python.exe docs\make_icon.py

원본은 `measauto_ui.py` 에 embed 된 파란 로고다(LOGO_BLUE_B64). 로고 파일을
따로 두지 않는 것이 그 파일의 방침이라, 아이콘도 거기서 뽑아 쓴다 — 로고가
바뀌면 이 스크립트를 다시 돌리면 된다.

두 벌을 굽는다:
  dsil_d.ico       투명 배경 위의 파란 D. 로고 그대로다.
  dsil_d_tile.ico  흰 라운드 타일 위의 파란 D. 어두운 배경화면에서 읽힌다
                   (#004191 은 어두운 색이라 검은 바탕에서 잘 안 보인다).
"""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent

# .ico 에 넣을 크기들. 작은 것을 큰 것에서 줄이면 뭉개지므로 전부 넣는다 —
# 탐색기는 보기 모드마다 다른 크기를 골라 쓴다.
SIZES = [16, 24, 32, 48, 64, 128, 256]
MARGIN = 0.10          # 캔버스 대비 여백. 0 이면 타일 모서리에 글자가 닿는다


def blue_logo() -> Image.Image:
    src = (ROOT / "measauto_ui.py").read_text(encoding="utf-8")
    m = re.search(r"LOGO_BLUE_B64 = \((.*?)\)\n", src, re.S)
    if m is None:
        raise SystemExit("measauto_ui.py 에서 LOGO_BLUE_B64 를 못 찾았다")
    b64 = "".join(re.findall(r'"([^"]*)"', m.group(1)))
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")


def first_glyph(img: Image.Image) -> Image.Image:
    """왼쪽 첫 글자(=D)만 잘라낸다. 알파가 비는 열로 글자를 가른다."""
    import numpy as np
    alpha = np.array(img)[..., 3]
    ink = alpha > 40
    cols = ink.sum(axis=0)
    x0 = int(np.argmax(cols > 0))
    x1 = x0
    while x1 + 1 < len(cols) and cols[x1 + 1] > 0:
        x1 += 1
    rows = np.where(ink.any(axis=1))[0]
    return img.crop((x0, int(rows.min()), x1 + 1, int(rows.max()) + 1))


def square(glyph: Image.Image, n: int, tile: bool) -> Image.Image:
    """정사각 캔버스 가운데에 글자를 앉힌다. 비율은 유지한다."""
    canvas = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    if tile:
        d = ImageDraw.Draw(canvas)
        d.rounded_rectangle([0, 0, n - 1, n - 1], radius=max(2, round(n * 0.18)),
                            fill=(255, 255, 255, 255))

    box = n * (1 - 2 * MARGIN)
    w, h = glyph.size
    scale = min(box / w, box / h)
    g = glyph.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                     Image.LANCZOS)
    canvas.alpha_composite(g, ((n - g.size[0]) // 2, (n - g.size[1]) // 2))
    return canvas


def bake(glyph: Image.Image, path: Path, *, tile: bool) -> None:
    frames = [square(glyph, n, tile) for n in SIZES]
    # Pillow 는 sizes= 로 알아서 줄이지만, 그러면 16px 이 256px 에서 축소돼
    # 뭉갠다. 크기마다 직접 그린 프레임을 append_images 로 넣는다.
    frames[-1].save(path, format="ICO", sizes=[(n, n) for n in SIZES],
                    append_images=frames[:-1])
    print(f"  {path.name:18s} {path.stat().st_size:>7,} bytes  {SIZES}")


def main() -> None:
    d = first_glyph(blue_logo())
    print(f"D 크기 {d.size}")

    import numpy as np
    a = np.array(d)
    cy, cx = a.shape[0] // 2, a.shape[1] // 2
    print(f"가운데(카운터) 픽셀 알파 = {a[cy, cx, 3]} "
          f"({'투명 — 로고 그대로' if a[cy, cx, 3] < 40 else '불투명'})")

    bake(d, ROOT / "dsil_d.ico", tile=False)
    bake(d, ROOT / "dsil_d_tile.ico", tile=True)

    # 눈으로 볼 미리보기. 밝은/어두운 배경 둘 다에서 확인한다.
    prev = Image.new("RGBA", (4 * 96, 2 * 96), (0, 0, 0, 0))
    for row, bgc in enumerate([(240, 240, 240, 255), (24, 26, 30, 255)]):
        for col, (n, tile) in enumerate([(48, False), (256, False),
                                         (48, True), (256, True)]):
            cell = Image.new("RGBA", (96, 96), bgc)
            ic = square(d, 64, tile) if n == 48 else square(d, 80, tile)
            cell.alpha_composite(ic, ((96 - ic.size[0]) // 2,
                                      (96 - ic.size[1]) // 2))
            prev.alpha_composite(cell, (col * 96, row * 96))
    out = ROOT / "docs" / "icon_preview.png"
    prev.convert("RGB").save(out)
    print(f"  미리보기: {out}")


if __name__ == "__main__":
    main()
