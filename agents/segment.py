"""Find every receipt in one photo with SAM 3.1, then read each one with Muse Spark.

    .venv/bin/python -m agents.segment samples/october/receipt_pile.jpg

SAM 3.1 returns a box for each receipt it finds. Each box is cropped (Pillow) and read with the
same prompt and the same checks as a single receipt photo (agents/receipts.py): the lines must add
up, there must be a card charge for exactly that amount within three days, and personal-looking
spending is flagged. The models find and read; code decides what matches.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional

from agents.meta import chat_json, image_part, segment
from agents.receipts import PROMPT, assess

ROOT = Path(__file__).resolve().parents[1]
# SAM 3.1 takes a short noun phrase. On the demo photos "receipt" missed one slip that
# "white paper" found (and vice versa is possible), so both are asked and the boxes merged.
SAM_PROMPTS = ("receipt", "white paper")
MIN_CONFIDENCE = 0.3
PAD = 0.012                  # share of the photo's size added around each box
_BOX = re.compile(r"<\|box;([^|]*)\|>")


def parse_boxes(text: str, min_confidence: float = MIN_CONFIDENCE) -> List[Dict]:
    """The boxes in SAM's output, in reading order (rows top to bottom, then left to right).

    SAM writes one record per match: <|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=..|><|mask;...|>,
    where x2 and y2 are inclusive, w and h are the image size, and c is the confidence.
    Low-confidence boxes, near-duplicates and a box that merely wraps several others are dropped.
    """
    boxes = []
    for body in _BOX.findall(text or ""):
        fields = dict(part.split("=", 1) for part in body.split(";") if "=" in part)
        try:
            box = {k: int(float(fields[k])) for k in ("x1", "y1", "x2", "y2", "w", "h")}
            box["confidence"] = round(float(fields.get("c", 1.0)), 3)
        except (KeyError, ValueError):
            continue
        if box["confidence"] >= min_confidence and box["x2"] > box["x1"] and box["y2"] > box["y1"]:
            boxes.append(box)
    boxes = _without_wrappers(_without_duplicates(boxes))
    return _reading_order(boxes)


def _area(b: Dict) -> int:
    return (b["x2"] - b["x1"] + 1) * (b["y2"] - b["y1"] + 1)


def _overlap(a: Dict, b: Dict) -> float:
    w = min(a["x2"], b["x2"]) - max(a["x1"], b["x1"]) + 1
    h = min(a["y2"], b["y2"]) - max(a["y1"], b["y1"]) + 1
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / float(_area(a) + _area(b) - inter)


def _without_duplicates(boxes: List[Dict]) -> List[Dict]:
    kept: List[Dict] = []
    for b in sorted(boxes, key=lambda b: -b["confidence"]):
        if all(_overlap(b, k) < 0.5 for k in kept):
            kept.append(b)
    return kept


def _center(b: Dict):
    return (b["x1"] + b["x2"]) / 2.0, (b["y1"] + b["y2"]) / 2.0


def _without_wrappers(boxes: List[Dict]) -> List[Dict]:
    def inside(c, b):
        return b["x1"] <= c[0] <= b["x2"] and b["y1"] <= c[1] <= b["y2"]
    return [b for b in boxes
            if sum(1 for o in boxes if o is not b and inside(_center(o), b)) < 2]


def _reading_order(boxes: List[Dict]) -> List[Dict]:
    rows: List[List[Dict]] = []
    for b in sorted(boxes, key=lambda b: _center(b)[1]):
        if rows and _center(b)[1] <= max(o["y2"] for o in rows[-1]) and \
                _center(b)[1] >= min(o["y1"] for o in rows[-1]):
            rows[-1].append(b)
        else:
            rows.append([b])
    return [b for row in rows for b in sorted(row, key=lambda b: b["x1"])]


def crop(photo: Path, box: Dict, out: Path) -> Path:
    """Cut one box out of the photo (with a little margin) and save it as a JPEG."""
    from PIL import Image  # Pillow lives in the app's .venv; parsing above needs only the stdlib

    with Image.open(photo) as img:
        img = img.convert("RGB")
        pad_x, pad_y = int(img.width * PAD), int(img.height * PAD)
        area = (max(0, box["x1"] - pad_x), max(0, box["y1"] - pad_y),
                min(img.width, box["x2"] + 1 + pad_x), min(img.height, box["y2"] + 1 + pad_y))
        out.parent.mkdir(parents=True, exist_ok=True)
        img.crop(area).save(out, "JPEG", quality=92)
    return out


def _slug(extracted: Dict, fallback: str) -> str:
    words = re.findall(r"[a-z]+", (extracted.get("merchant") or "").lower())[:2]
    day = extracted.get("date") or ""
    name = "_".join(p for p in (day, "-".join(words)) if p)
    return name or fallback


def read_crop(path: Path) -> Dict:
    return chat_json([{"role": "user", "content": [{"type": "text", "text": PROMPT}, image_part(path)]}])


def find_receipts(photo: Path, find: Callable[[Path, str], str] = segment) -> List[Dict]:
    """Ask SAM once per prompt, both at the same time, and merge: duplicates collapse to the
    most confident box."""
    with ThreadPoolExecutor(max_workers=len(SAM_PROMPTS)) as pool:
        outputs = list(pool.map(lambda p: find(photo, p), SAM_PROMPTS))
    return parse_boxes("".join(outputs))


def cut_all(photo: Path, boxes: List[Dict], out_dir: Path,
            cut: Callable[[Path, Dict, Path], Path] = crop) -> List[Path]:
    return [cut(photo, b, out_dir / f"receipt-{i + 1}.jpg") for i, b in enumerate(boxes)]


def read_all(crops: List[Path], boxes: List[Dict], txns, prefix: str = "",
             read: Callable[[Path], Dict] = read_crop) -> List[Dict]:
    """Read every crop in parallel, check each reading in code, and rename each crop after
    what's on it (date and merchant), so a decision about it survives a re-snap."""
    def one(i: int) -> Dict:
        try:
            return {"extracted": read(crops[i])}
        except Exception as e:  # one unreadable slip shouldn't sink the others
            return {"error": str(e)[:200]}

    with ThreadPoolExecutor(max_workers=max(1, min(5, len(crops)))) as pool:
        readings = list(pool.map(one, range(len(crops))))

    receipts, used = [], set()
    for i, (box, reading) in enumerate(zip(boxes, readings)):
        name = f"receipt-{i + 1}"
        if "extracted" in reading:
            name = _slug(reading["extracted"], name)
            while name in used:
                name += "-2"
            target = crops[i].with_name(name + ".jpg")
            if crops[i].exists() and crops[i] != target:
                crops[i].replace(target)
        used.add(name)
        row = {"file": f"{prefix}{name}.jpg", "box": box}
        if "extracted" in reading:
            row.update(extracted=reading["extracted"], **assess(reading["extracted"], txns))
        else:
            row.update(error=reading["error"])
        receipts.append(row)
    return receipts


def read_pile(photo: Path, txns, out_dir: Path, prefix: str = "",
              find: Callable[[Path, str], str] = segment,
              cut: Callable[[Path, Dict, Path], Path] = crop,
              read: Callable[[Path], Dict] = read_crop) -> Dict:
    """One photo in, every receipt out: SAM finds the boxes, each crop is read in parallel,
    and each reading is checked against the card charges in code (assess)."""
    started = time.time()
    boxes = find_receipts(photo, find)
    found_at = time.time()
    crops = cut_all(photo, boxes, out_dir, cut)
    receipts = read_all(crops, boxes, txns, prefix, read)
    size = (boxes[0]["w"], boxes[0]["h"]) if boxes else (None, None)
    return {"width": size[0], "height": size[1], "sam_prompts": list(SAM_PROMPTS), "boxes": boxes,
            "receipts": receipts, "seconds": {"find": round(found_at - started, 1),
                                               "read": round(time.time() - found_at, 1)}}


def main():
    ap = argparse.ArgumentParser(description="Find, crop and read every receipt in one photo.")
    ap.add_argument("photo")
    ap.add_argument("--month", default="samples/october")
    ap.add_argument("--out", default=str(ROOT / "data" / "app" / "pile" / "cli"))
    args = ap.parse_args()
    from ingest.load import load_folder
    txns, _, _ = load_folder(Path(args.month))
    result = read_pile(Path(args.photo), txns, Path(args.out))
    print(f"SAM 3.1 found {len(result['boxes'])} receipts in {result['seconds']['find']}s; "
          f"Muse Spark read them in {result['seconds']['read']}s")
    for r in result["receipts"]:
        e, m = r.get("extracted") or {}, r.get("match")
        print(f"  {r['file']:<40} total {e.get('total')!s:>8}  "
              f"{'matched ' + m['date'] if m else 'NO MATCH':<20} {e.get('kind', r.get('error', ''))}")
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / "result.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    sys.exit(main())
