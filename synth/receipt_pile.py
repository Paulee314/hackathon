"""Build samples/october/receipt_pile.jpg: the month's five synthetic receipts in one photo.

    .venv/bin/python -m synth.receipt_pile

Muse Image draws an empty car seat, SAM 3.1 cuts each receipt off its countertop photo, and
Pillow lays them out with a slight tilt and a soft shadow, at their true relative sizes.
Synthetic data only: the receipts are the fictional ones in samples/october/receipts.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageFilter

from agents.meta import MetaAPIError, generate_image
from agents.segment import find_receipts

ROOT = Path(__file__).resolve().parents[1]
MONTH = ROOT / "samples" / "october"
OUT = MONTH / "receipt_pile.jpg"
CANVAS = (3000, 2250)
SCALE = 0.6
LAYOUT = [  # receipt file, centre x, centre y, tilt in degrees
    ("2026-10-09_bayou-shine.jpg", 640, 620, -6),
    ("2026-10-11_lone-star-burger.jpg", 1500, 590, 3),
    ("2026-10-12_detailpro.jpg", 2360, 630, -2),
    ("2026-10-18_bayou-fresh.jpg", 1060, 1640, 4),
    ("2026-10-24_bayou-shine.jpg", 1950, 1650, -4),
]
SEAT = ("Top-down photo looking straight down at the empty passenger seat of a car, charcoal gray "
        "woven fabric filling the whole frame, soft natural daylight, nothing on the seat, no text")


def background() -> Image.Image:
    try:
        raw = generate_image(SEAT, size="1024x768")
    except MetaAPIError:
        raw = generate_image(SEAT, size="768x1024")
    seat = Image.open(io.BytesIO(raw)).convert("RGB")
    if seat.height > seat.width:
        seat = seat.rotate(90, expand=True)
    return seat.resize(CANVAS, Image.LANCZOS)


def slip(path: Path) -> Image.Image:
    boxes = find_receipts(path)
    if not boxes:
        raise SystemExit(f"SAM found no receipt in {path.name}")
    b = max(boxes, key=lambda b: b["confidence"])
    with Image.open(path) as img:
        cut = img.convert("RGB").crop((b["x1"], b["y1"], b["x2"] + 1, b["y2"] + 1))
    return cut.resize((int(cut.width * SCALE), int(cut.height * SCALE)), Image.LANCZOS)


def place(canvas: Image.Image, piece: Image.Image, cx: int, cy: int, angle: float):
    rgba = piece.convert("RGBA")
    turned = rgba.rotate(angle, resample=Image.BICUBIC, expand=True)
    alpha = turned.split()[3]
    shadow = Image.new("RGBA", turned.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.point(lambda a: int(a * 0.35)).filter(ImageFilter.GaussianBlur(14)))
    x, y = cx - turned.width // 2, cy - turned.height // 2
    canvas.alpha_composite(shadow, (x + 14, y + 20))
    canvas.alpha_composite(turned, (x, y))


def main():
    canvas = background().convert("RGBA")
    for name, cx, cy, angle in LAYOUT:
        place(canvas, slip(MONTH / "receipts" / name), cx, cy, angle)
        print(f"  placed {name}")
    canvas.convert("RGB").save(OUT, "JPEG", quality=88)
    print(f"Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
