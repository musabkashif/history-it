"""Build the Odyssey primary mark from measurements of the logo usage guide."""
from __future__ import annotations

import math
from pathlib import Path

# Guide PNG measurements (primary mark), image pixels:
# ring center (131, 218), outer radius 86, stroke 17
# word ODYSSEY: x 53–431, y 197–238 (baseline 238)
# tagline: x 226–426, y 256–265
# side gaps ≈ ±38° from 3 o'clock and 9 o'clock

OUT = Path(__file__).resolve().parents[1] / "public"

CX, CY = 240.0, 180.0
IMG_CX, IMG_CY = 131.0, 218.0
R = 77.5  # stroke centerline; outer radius 86, stroke 17
STROKE = 17
GAP = 38


def ix(x: float) -> float:
    return CX + (x - IMG_CX)


def iy(y: float) -> float:
    return CY + (y - IMG_CY)


def pt(deg: float) -> tuple[float, float]:
    rad = math.radians(deg)
    return CX + R * math.cos(rad), CY - R * math.sin(rad)


x1, y1 = pt(180 - GAP)
x2, y2 = pt(GAP)
top = f"M {x1:.2f} {y1:.2f} A {R} {R} 0 0 0 {x2:.2f} {y2:.2f}"
x3, y3 = pt(180 + GAP)
x4, y4 = pt(360 - GAP)
bottom = f"M {x3:.2f} {y3:.2f} A {R} {R} 0 0 1 {x4:.2f} {y4:.2f}"

word_x = ix(53)
word_y = iy(238)
word_w = 431 - 53
tag_x = ix(426)
tag_y = iy(265)
tag_w = 426 - 226

# Pad viewBox around outer ring (R_outer = 86) and the word/tagline.
left = min(CX - 86, word_x) - 16
top_y = CY - 86 - 16
right = max(CX + 86, word_x + word_w, tag_x) + 16
bot_y = max(CY + 86, tag_y) + 18
vb = f"{left:.0f} {top_y:.0f} {right - left:.0f} {bot_y - top_y:.0f}"


def svg(fill: str, gid: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" role="img" aria-label="Odyssey Preservation Software">
  <defs>
    <linearGradient id="{gid}" x1="0%" y1="50%" x2="100%" y2="50%">
      <stop offset="0%" stop-color="#08A2A7"/>
      <stop offset="45%" stop-color="#039569"/>
      <stop offset="100%" stop-color="#A6CE3A"/>
    </linearGradient>
  </defs>
  <!--
    Primary mark traced from the Odyssey logo usage guide:
    thick broken ring (gaps at 3 and 9 o'clock), ODYSSEY centered
    vertically through the ring and continuing out the right gap,
    PRESERVATION SOFTWARE right-aligned under the wordmark.
  -->
  <g fill="none" stroke="url(#{gid})" stroke-width="{STROKE}" stroke-linecap="butt">
    <path d="{top}"/>
    <path d="{bottom}"/>
  </g>
  <text x="{word_x:.1f}" y="{word_y:.1f}" fill="{fill}"
        font-family="Montserrat, Avenir Next, Segoe UI, sans-serif"
        font-size="58" font-weight="700"
        textLength="{word_w}" lengthAdjust="spacing">ODYSSEY</text>
  <text x="{tag_x:.1f}" y="{tag_y:.1f}" text-anchor="end" fill="{fill}"
        font-family="Montserrat, Avenir Next, Segoe UI, sans-serif"
        font-size="13" font-weight="500" letter-spacing="0.08em"
        textLength="{tag_w}" lengthAdjust="spacing">PRESERVATION SOFTWARE</text>
</svg>
"""


(OUT / "odyssey-mark-on-dark.svg").write_text(svg("#FFFFFF", "odysseyRingDark"), encoding="utf-8")
(OUT / "odyssey-mark.svg").write_text(svg("#054657", "odysseyRingLight"), encoding="utf-8")
print("viewBox", vb)
print("top", top)
print("bottom", bottom)
print("word", word_x, word_y, word_w)
print("tag", tag_x, tag_y, tag_w)
