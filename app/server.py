"""The phone app: one page and a small JSON API.

    .venv/bin/uvicorn app.server:app --port 8765
    open http://localhost:8765

APP_MONTH (default samples/october) picks the month folder; APP_TODAY (default 2026-10-31) is the
day the app pretends it is, so the synthetic month reads as "almost closed".
"""
from __future__ import annotations

import io
import os
import shutil
import tempfile
import time
import wave
from datetime import date
from pathlib import Path
from typing import Dict, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from agents.meta import MetaAPIError, transcribe
from agents.segment import cut_all, find_receipts, read_all
from agents.voice import KEYWORDS
from app import state as st
from app.assistant import explain, handle
from app.books import Books

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
MONTH = Path(os.environ.get("APP_MONTH", ROOT / "samples" / "october")).resolve()
TODAY = date.fromisoformat(os.environ.get("APP_TODAY", "2026-10-31"))
MAX_AUDIO_SECONDS = 60
# Receipts enter the books by photo (SAM 3.1 + Muse Spark). APP_RECEIPTS=preloaded uses the batch run.
RECEIPTS_FROM_PHOTO = os.environ.get("APP_RECEIPTS", "photo") != "preloaded"
PILES = st.PATH.parent / "pile"
SAMPLE_PILE = MONTH / "receipt_pile.jpg"
MAX_PHOTO_BYTES = 15 * 1024 * 1024

app = FastAPI(title="hackathon phone app", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_cache: Dict = {}


def books() -> Books:
    state = st.load()
    if _cache.get("v") != state.get("v") or "books" not in _cache:
        _cache.update(v=state.get("v"), books=Books(MONTH, TODAY, state, receipts_from_photo=RECEIPTS_FROM_PHOTO))
    return _cache["books"]


def _model_error(e: Exception):
    raise HTTPException(502, f"The Meta model didn't answer: {e}")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/home")
def home():
    return books().home()


@app.get("/api/detail/{which}")
def detail(which: str):
    d = books().detail(which)
    if d is None:
        raise HTTPException(404, "Nothing to show for that bubble.")
    return d


@app.get("/api/review")
def review():
    items = books().review_items()
    items.sort(key=lambda i: i["decision"] is not None)
    return {"items": items}


@app.post("/api/review/{item_id}")
def decide(item_id: str, body: Dict = Body(...)):
    items = {i["id"]: i for i in books().review_items()}
    if item_id not in items:
        raise HTTPException(404, "That item isn't in the review list.")
    choice = body.get("choice")
    if choice not in {c["choice"] for c in items[item_id]["choices"]}:
        raise HTTPException(400, "Pick one of the choices on the card.")
    amount: Optional[float] = None
    if choice == "amount":
        try:
            amount = round(float(body.get("amount")), 2)
        except (TypeError, ValueError):
            raise HTTPException(400, "Type the amount, like 9.99.")
        if amount <= 0:
            raise HTTPException(400, "The amount has to be more than zero.")

    def change(s):
        s["decisions"][item_id] = {"choice": choice}
        if amount is not None:
            s["amount_overrides"][item_id] = amount
    st.update(change)
    return {"home": books().home(), "items": review()["items"]}


@app.delete("/api/review/{item_id}")
def undo_decision(item_id: str):
    def change(s):
        s["decisions"].pop(item_id, None)
        s["amount_overrides"].pop(item_id, None)
    st.update(change)
    return {"home": books().home(), "items": review()["items"]}


@app.post("/api/review/{item_id}/explain")
def explain_item(item_id: str):
    cached = st.load()["explanations"].get(item_id)
    if cached:
        return {"text": cached}
    items = {i["id"]: i for i in books().review_items()}
    if item_id not in items:
        raise HTTPException(404, "That item isn't in the review list.")
    try:
        text = explain(items[item_id])
    except MetaAPIError as e:
        _model_error(e)
    st.update(lambda s: s["explanations"].__setitem__(item_id, text))
    return {"text": text}


def _respond(text: str, spoken: bool) -> Dict:
    try:
        result = handle(text, books(), spoken)
    except (MetaAPIError, ValueError) as e:
        _model_error(e)
    if result["type"] == "logged":
        st.update(lambda s: s["entries"].append(result["entry"]))
        result["entry_id"] = result.pop("entry")["id"]
    return {"result": result, "home": books().home()}


@app.post("/api/ask")
def ask(body: Dict = Body(...)):
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Type or say something first.")
    return {"heard": text, **_respond(text[:500], spoken=bool(body.get("spoken")))}


@app.post("/api/transcribe")
async def transcribe_audio(request: Request):
    """Speech to text only, so the page can show the words before the answer is ready."""
    audio = await request.body()
    try:
        with wave.open(io.BytesIO(audio)) as w:
            fmt = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            seconds = w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError):
        raise HTTPException(400, "That recording wasn't a WAV file.")
    if fmt[0] != 1 or fmt[1] != 2 or fmt[2] not in (16000, 24000):
        raise HTTPException(400, "The recording must be mono 16-bit audio at 16 or 24 kHz.")
    if seconds < 0.3:
        raise HTTPException(400, "Hold the cloud while you talk, then let go.")
    if seconds > MAX_AUDIO_SECONDS:
        raise HTTPException(400, "Keep it under a minute.")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio)
        path = Path(f.name)
    try:
        heard = transcribe(path, keywords=KEYWORDS)
    except MetaAPIError as e:
        _model_error(e)
    finally:
        path.unlink(missing_ok=True)
    return {"heard": (heard.get("transcript") or "").strip(), "audio_ms": heard.get("audioDurationMs")}


def _save_photo(raw: bytes, out: Path):
    from PIL import Image, ImageOps
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    except Exception:
        raise HTTPException(400, "That photo couldn't be opened. Try a JPEG or PNG.")
    img.thumbnail((3200, 3200))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=90)
    return img.width, img.height


@app.get("/api/receipts")
def receipts_view():
    return {"pile": books().pile_view(), "sample": SAMPLE_PILE.exists()}


@app.post("/api/receipts/photo")
async def receipts_photo(request: Request):
    """Step 1: save the photo and let SAM 3.1 find every receipt in it. A JSON body
    {"sample": true} uses the month's sample photo instead of an upload."""
    if (request.headers.get("content-type") or "").startswith("application/json"):
        if not SAMPLE_PILE.exists():
            raise HTTPException(404, "There's no sample photo for this month.")
        raw = SAMPLE_PILE.read_bytes()
    else:
        raw = await request.body()
    if not raw or len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(400, "Send one photo under 15 MB.")
    pile_id = str(int(time.time() * 1000))
    folder = PILES / pile_id
    width, height = _save_photo(raw, folder / "photo.jpg")
    started = time.time()
    try:
        boxes = await run_in_threadpool(find_receipts, folder / "photo.jpg")
    except MetaAPIError as e:
        shutil.rmtree(folder, ignore_errors=True)
        _model_error(e)
    if not boxes:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, "SAM 3.1 didn't find a receipt in that photo. Lay them flat and try again.")
    crops = await run_in_threadpool(cut_all, folder / "photo.jpg", boxes, folder)
    found = round(time.time() - started, 1)

    def change(s):
        s["pile"] = {"id": pile_id, "photo": f"pile/{pile_id}/photo.jpg", "width": width, "height": height,
                     "boxes": boxes, "crops": [c.name for c in crops], "receipts": None, "seconds": {"find": found}}
    st.update(change)
    for old in PILES.iterdir():  # keep only the newest photo's files
        if old.is_dir() and old.name != pile_id:
            shutil.rmtree(old, ignore_errors=True)
    return {"pile": books().pile_view()}


@app.post("/api/receipts/read")
async def receipts_read():
    """Step 2: Muse Spark reads every crop in parallel; code checks each against the card charges."""
    pile = st.load().get("pile")
    if not pile or not pile.get("crops"):
        raise HTTPException(400, "Snap a photo of the receipts first.")
    folder = PILES / pile["id"]
    started = time.time()
    receipts = await run_in_threadpool(read_all, [folder / n for n in pile["crops"]], pile["boxes"],
                                       books().txns, f"pile/{pile['id']}/")
    if not any(r.get("extracted") for r in receipts):
        raise HTTPException(502, "Muse Spark couldn't read the receipts just now. Try again.")
    read_seconds = round(time.time() - started, 1)

    def change(s):
        if (s.get("pile") or {}).get("id") != pile["id"]:
            return
        s["pile"]["receipts"] = [{k: r[k] for k in ("file", "box", "extracted", "error") if k in r} for r in receipts]
        s["pile"]["crops"] = [Path(r["file"]).name for r in receipts]
        s["pile"]["seconds"]["read"] = read_seconds
    st.update(change)
    b = books()
    return {"pile": b.pile_view(), "home": b.home()}


@app.post("/api/entries/{entry_id}/undo")
def undo_entry(entry_id: str):
    def change(s):
        for e in s["entries"]:
            if e["id"] == entry_id:
                e["undone"] = True
    st.update(change)
    return {"home": books().home()}


@app.post("/api/reset")
def reset():
    st.reset()
    shutil.rmtree(PILES, ignore_errors=True)
    return {"home": books().home()}


@app.get("/evidence/{rel:path}")
def evidence(rel: str):
    base = PILES.resolve() if rel.startswith("pile/") else MONTH
    path = (base / (rel[5:] if rel.startswith("pile/") else rel)).resolve()
    if base not in path.parents or not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".wav"}:
        raise HTTPException(404, "No such file.")
    return FileResponse(path)
