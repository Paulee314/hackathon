"""The receipt pile: parsing SAM 3.1's boxes, merging its two prompts, and reading every crop.

Offline and standard library only. SAM's output is written by hand in the format the API returns,
and tests/fixtures/receipts_extracted.json (a recorded Muse Spark reading of the five receipts)
stands in for the reads, so no model is called and no image library is needed.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.segment import SAM_PROMPTS, find_receipts, parse_boxes, read_all, read_pile  # noqa: E402
from ingest.load import load_folder  # noqa: E402

MONTH = ROOT / "samples" / "october"
FIXTURE = ROOT / "tests" / "fixtures" / "receipts_extracted.json"
W, H = 3000, 2250

# Where the five slips sit in samples/october/receipt_pile.jpg (two rows: three, then two).
PILE = [(420, 260, 860, 980), (1290, 300, 1710, 880), (2140, 270, 2580, 990),
        (840, 1240, 1280, 2040), (1730, 1300, 2170, 2000)]


def sam(*boxes) -> str:
    """SAM 3.1's text output for boxes given as (x1, y1, x2, y2, confidence)."""
    return "<0f>0" + "".join(
        f"<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={W};h={H};c={c}|><|mask;enc=one_bit;data=AAAA|>"
        for x1, y1, x2, y2, c in boxes)


def corners(boxes):
    return [(b["x1"], b["y1"], b["x2"], b["y2"]) for b in boxes]


class ParseBoxes(unittest.TestCase):
    def test_reads_coordinates_size_and_confidence(self):
        [box] = parse_boxes(sam((10, 20, 110, 220, 0.91)))
        self.assertEqual(box, {"x1": 10, "y1": 20, "x2": 110, "y2": 220, "w": W, "h": H, "confidence": 0.91})

    def test_drops_low_confidence_and_malformed_boxes(self):
        text = sam((10, 20, 110, 220, 0.91), (500, 20, 600, 220, 0.12)) + "<|box;x1=5;y1=oops|>"
        self.assertEqual(corners(parse_boxes(text)), [(10, 20, 110, 220)])
        self.assertEqual(parse_boxes(""), [])
        self.assertEqual(parse_boxes(None), [])

    def test_keeps_the_more_confident_of_two_near_duplicates(self):
        [box] = parse_boxes(sam((100, 100, 400, 700, 0.62), (104, 96, 402, 705, 0.88)))
        self.assertEqual(box["confidence"], 0.88)

    def test_drops_a_box_that_wraps_several_receipts(self):
        text = sam((0, 0, W - 1, H - 1, 0.5), (100, 100, 400, 700, 0.9), (900, 100, 1200, 700, 0.9))
        self.assertEqual(corners(parse_boxes(text)), [(100, 100, 400, 700), (900, 100, 1200, 700)])

    def test_reading_order_is_rows_then_left_to_right(self):
        shuffled = [PILE[4], PILE[1], PILE[3], PILE[0], PILE[2]]
        boxes = parse_boxes(sam(*[(*p, 0.9) for p in shuffled]))
        self.assertEqual(corners(boxes), PILE)


class FindReceipts(unittest.TestCase):
    def test_both_prompts_are_asked_and_merged(self):
        asked = []

        def find(photo, prompt):
            asked.append(prompt)
            if prompt == "receipt":   # misses the last slip, as it did on the real photo
                return sam(*[(*p, 0.9) for p in PILE[:4]])
            return sam(*[(*p, 0.8) for p in PILE[2:]])

        boxes = find_receipts(Path("pile.jpg"), find=find)
        self.assertEqual(sorted(asked), sorted(SAM_PROMPTS))
        self.assertEqual(corners(boxes), PILE)
        self.assertEqual([b["confidence"] for b in boxes], [0.9, 0.9, 0.9, 0.9, 0.8])


class ReadPile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.txns, _, _ = load_folder(MONTH)
        cls.recorded = [r["extracted"] for r in json.loads(FIXTURE.read_text())]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.boxes = parse_boxes(sam(*[(*p, 0.9) for p in PILE]))

    def tearDown(self):
        self.tmp.cleanup()

    def crops(self, n=5):
        paths = []
        for i in range(n):
            p = self.out / f"receipt-{i + 1}.jpg"
            p.write_bytes(b"jpeg")
            paths.append(p)
        return paths

    def read(self, path):
        return self.recorded[int(re.search(r"receipt-(\d+)", path.name).group(1)) - 1]

    def test_every_receipt_is_read_matched_and_renamed(self):
        rows = read_all(self.crops(), self.boxes, self.txns, prefix="pile/t/", read=self.read)
        self.assertEqual([r["file"] for r in rows], [
            "pile/t/2026-10-09_bayou-shine.jpg", "pile/t/2026-10-11_lone-star.jpg", "pile/t/2026-10-12_detailpro-supply.jpg",
            "pile/t/2026-10-18_bayou-fresh.jpg", "pile/t/2026-10-24_bayou-shine.jpg"])
        self.assertTrue(all(r["match"] for r in rows))
        self.assertEqual(sorted(r["file"].split("/")[-1] for r in rows), sorted(p.name for p in self.out.iterdir()))
        personal = [r["extracted"]["total"] for r in rows if r["extracted"]["kind"] in ("personal", "mixed")]
        self.assertEqual(sorted(personal), [14.32, 126.43])
        self.assertEqual([r["box"] for r in rows], self.boxes)

    def test_one_unreadable_slip_does_not_sink_the_rest(self):
        def flaky(path):
            if path.name == "receipt-3.jpg":
                raise RuntimeError("model timed out")
            return self.read(path)

        rows = read_all(self.crops(), self.boxes, self.txns, read=flaky)
        self.assertEqual(rows[2]["file"], "receipt-3.jpg")
        self.assertIn("timed out", rows[2]["error"])
        self.assertNotIn("extracted", rows[2])
        self.assertEqual(sum(1 for r in rows if r.get("match")), 4)

    def test_two_slips_from_the_same_place_and_day_get_different_names(self):
        same = self.recorded[0]
        rows = read_all(self.crops(2), self.boxes[:2], self.txns, read=lambda p: same)
        self.assertEqual([r["file"] for r in rows], ["2026-10-09_bayou-shine.jpg", "2026-10-09_bayou-shine-2.jpg"])

    def test_read_pile_end_to_end(self):
        def cut(photo, box, out):
            out.write_bytes(b"jpeg")
            return out

        result = read_pile(Path("pile.jpg"), self.txns, self.out,
                           find=lambda photo, prompt: sam(*[(*p, 0.9) for p in PILE]), cut=cut, read=self.read)
        self.assertEqual((result["width"], result["height"]), (W, H))
        self.assertEqual(result["sam_prompts"], list(SAM_PROMPTS))
        self.assertEqual(len(result["receipts"]), 5)
        self.assertTrue(all(r["match"] for r in result["receipts"]))
        self.assertEqual(set(result["seconds"]), {"find", "read"})

    def test_a_photo_with_no_receipts(self):
        result = read_pile(Path("pile.jpg"), self.txns, self.out, find=lambda photo, prompt: "<0f>0",
                           cut=None, read=None)
        self.assertEqual((result["boxes"], result["receipts"], result["width"]), ([], [], None))


if __name__ == "__main__":
    unittest.main()
