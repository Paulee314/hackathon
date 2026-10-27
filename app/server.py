"""The phone app: one page and a small JSON API.

    .venv/bin/uvicorn app.server:app --port 8765
    open http://localhost:8765

APP_MONTH (default samples/october) picks the month folder; APP_TODAY (default 2026-10-31) is the
day the app pretends it is, so the synthetic month reads as "almost closed".

Each browser is its own visitor (a cookie holds a random id), with its own copy of the month: see
app/state.py. Requests that call a Meta model pass through the guards in app/limits.py first.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import threading
import time
import wave
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Dict, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from agents.meta import MetaAPIError, transcribe
from agents.segment import cut_all, find_receipts, read_all
from agents.voice import KEYWORDS
from app import state as st
from app.assistant import explain, handle
from app.books import Books
from app.limits import Budget, RateLimit

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
MONTH = Path(os.environ.get("APP_MONTH", ROOT / "samples" / "october")).resolve()
TODAY = date.fromisoformat(os.environ.get("APP_TODAY", "2026-10-31"))
MAX_AUDIO_SECONDS = 60
# Receipts enter the books by photo (SAM 3.1 + Muse Spark). APP_RECEIPTS=preloaded uses the batch run.
RECEIPTS_FROM_PHOTO = os.environ.get("APP_RECEIPTS", "photo") != "preloaded"
SAMPLE_PILE = MONTH / "receipt_pile.jpg"
MAX_PHOTO_BYTES = 15 * 1024 * 1024

COOKIE = "rideoff_visitor"
COOKIE_DAYS = 7
SWEEP_EVERY = 600            # seconds between clean-ups of idle visitors
BOOKS_KEPT = 64              # visitors whose computed books stay in memory
BUDGET = Budget()
PER_VISITOR = RateLimit(int(os.environ.get("APP_VISITOR_LIMIT", "30")), 600)    # model requests per 10 minutes
PER_ADDRESS = RateLimit(int(os.environ.get("APP_ADDRESS_LIMIT", "90")), 600)

app = FastAPI(title="Ride-off phone app", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_books: "OrderedDict[str, tuple]" = OrderedDict()
_books_lock = threading.Lock()
_last_sweep = {"at": 0.0}


@app.middleware("http")
async def visitor(request: Request, call_next):
    """Give every browser its own visitor id on its first request, and sweep idle visitors."""
    vid = request.cookies.get(COOKIE)
    fresh = not st.valid_id(vid)
    if fresh:
        vid = st.new_id()
    request.state.visitor = vid
    if time.time() - _last_sweep["at"] > SWEEP_EVERY:
        _last_sweep["at"] = time.time()
        await run_in_threadpool(st.sweep)
    response = await call_next(request)
    if fresh:
        https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        response.set_cookie(COOKIE, vid, max_age=COOKIE_DAYS * 86400, httponly=True, samesite="lax", secure=https)
    return response


def store(request: Request) -> st.Store:
    return st.for_visitor(request.state.visitor)


def books(request: Request) -> Books:
    """This visitor's month, rebuilt only when their state changed."""
    vid = request.state.visitor
    state = store(request).load()
    key = (state.get("v"), state.get("born"))
    with _books_lock:
        hit = _books.get(vid)
    if hit is None or hit[0] != key:
        hit = (key, Books(MONTH, TODAY, state, receipts_from_photo=RECEIPTS_FROM_PHOTO))
    with _books_lock:
        _books[vid] = hit
        _books.move_to_end(vid)
        while len(_books) > BOOKS_KEPT:
            _books.popitem(last=False)
    return hit[1]


def _address(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def guard(request: Request):
    """Run before any request that calls a Meta model."""
    if BUDGET.left() <= 0:
        raise HTTPException(503, "This demo has used today's budget for Meta model calls. Everything on screen "
                                 "still works; new AI requests start again tomorrow.")
    vid, addr = request.state.visitor, _address(request)
    if not PER_VISITOR.allow(vid):
        minutes = max(1, round(PER_VISITOR.retry_after(vid) / 60))
        raise HTTPException(429, f"That's this demo's limit of AI requests for now, so it stays up for "
                                 f"everyone. Try again in about {minutes} minute{'s' if minutes != 1 else ''}.")
    if not PER_ADDRESS.allow(addr):
        raise HTTPException(429, "Lots of requests from your network just now. Try again in a few minutes.")


def _model_error(e: Exception):
    raise HTTPException(502, f"The Meta model didn't answer: {e}")


@app.get("/")
def index():
    """The page, with the stylesheet and script stamped by content so a browser never runs an old copy."""
    html = (STATIC / "index.html").read_text()
    for name in ("app.css", "app.js"):
        stamp = hashlib.sha1((STATIC / name).read_bytes()).hexdigest()[:10]
        html = html.replace(f'/static/{name}"', f'/static/{name}?v={stamp}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/home")
def home(request: Request):
    return books(request).home()


@app.get("/api/detail/{which}")
def detail(which: str, request: Request):
    d = books(request).detail(which)
    if d is None:
        raise HTTPException(404, "Nothing to show for that bubble.")
    return d


def _review(request: Request) -> Dict:
    items = books(request).review_items()
    items.sort(key=lambda i: i["decision"] is not None)
    return {"items": items}


@app.get("/api/review")
def review(request: Request):
    return _review(request)


@app.post("/api/review/{item_id}")
def decide(item_id: str, request: Request, body: Dict = Body(...)):
    items = {i["id"]: i for i in books(request).review_items()}
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
        if not 0 < amount < 100000:
            raise HTTPException(400, "The amount has to be more than zero.")

    def change(s):
        s["decisions"][item_id] = {"choice": choice}
        if amount is not None:
            s["amount_overrides"][item_id] = amount
    store(request).update(change)
    return {"home": books(request).home(), "items": _review(request)["items"]}


@app.delete("/api/review/{item_id}")
def undo_decision(item_id: str, request: Request):
    def change(s):
        s["decisions"].pop(item_id, None)
        s["amount_overrides"].pop(item_id, None)
    store(request).update(change)
    return {"home": books(request).home(), "items": _review(request)["items"]}


@app.post("/api/review/{item_id}/explain")
def explain_item(item_id: str, request: Request):
    cached = store(request).load()["explanations"].get(item_id)
    if cached:
        return {"text": cached}
    items = {i["id"]: i for i in books(request).review_items()}
    if item_id not in items:
        raise HTTPException(404, "That item isn't in the review list.")
    guard(request)
    try:
        text = explain(items[item_id])
    except MetaAPIError as e:
        _model_error(e)
    store(request).update(lambda s: s["explanations"].__setitem__(item_id, text))
    return {"text": text}


def _respond(request: Request, text: str, spoken: bool) -> Dict:
    try:
        result = handle(text, books(request), spoken)
    except (MetaAPIError, ValueError) as e:
        _model_error(e)
    if result["type"] == "logged":
        store(request).update(lambda s: s["entries"].append(result["entry"]))
        result["entry_id"] = result.pop("entry")["id"]
    return {"result": result, "home": books(request).home()}


@app.post("/api/ask")
def ask(request: Request, body: Dict = Body(...)):
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Type or say something first.")
    guard(request)
    return {"heard": text, **_respond(request, text[:500], spoken=bool(body.get("spoken")))}


@app.post("/api/transcribe")
async def transcribe_audio(request: Request):
    """Speech to text only, so the page can show the words before the answer is ready."""
    audio = await request.body()
    if len(audio) > 4 * 1024 * 1024:
        raise HTTPException(400, "Keep it under a minute.")
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
    guard(request)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio)
        path = Path(f.name)
    try:
        heard = await run_in_threadpool(transcribe, path, KEYWORDS)
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
def receipts_view(request: Request):
    return {"pile": books(request).pile_view(), "sample": SAMPLE_PILE.exists()}


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
    guard(request)
    piles = store(request).piles
    pile_id = str(int(time.time() * 1000))
    folder = piles / pile_id
    width, height = await run_in_threadpool(_save_photo, raw, folder / "photo.jpg")
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
    store(request).update(change)
    for old in piles.iterdir():  # keep only the newest photo's files
        if old.is_dir() and old.name != pile_id:
            shutil.rmtree(old, ignore_errors=True)
    return {"pile": books(request).pile_view()}


@app.post("/api/receipts/read")
async def receipts_read(request: Request):
    """Step 2: Muse Spark reads every crop in parallel; code checks each against the card charges."""
    pile = store(request).load().get("pile")
    if not pile or not pile.get("crops"):
        raise HTTPException(400, "Snap a photo of the receipts first.")
    if pile.get("receipts") is not None:  # already read: don't pay for it twice
        b = books(request)
        return {"pile": b.pile_view(), "home": b.home()}
    guard(request)
    folder = store(request).piles / pile["id"]
    started = time.time()
    receipts = await run_in_threadpool(read_all, [folder / n for n in pile["crops"]], pile["boxes"],
                                       books(request).txns, f"pile/{pile['id']}/")
    if not any(r.get("extracted") for r in receipts):
        raise HTTPException(502, "Muse Spark couldn't read the receipts just now. Try again.")
    read_seconds = round(time.time() - started, 1)

    def change(s):
        if (s.get("pile") or {}).get("id") != pile["id"]:
            return
        s["pile"]["receipts"] = [{k: r[k] for k in ("file", "box", "extracted", "error") if k in r} for r in receipts]
        s["pile"]["crops"] = [Path(r["file"]).name for r in receipts]
        s["pile"]["seconds"]["read"] = read_seconds
    store(request).update(change)
    b = books(request)
    return {"pile": b.pile_view(), "home": b.home()}


@app.post("/api/entries/{entry_id}/undo")
def undo_entry(entry_id: str, request: Request):
    def change(s):
        for e in s["entries"]:
            if e["id"] == entry_id:
                e["undone"] = True
    store(request).update(change)
    return {"home": books(request).home()}


@app.post("/api/reset")
def reset(request: Request):
    """Clears this visitor's answers, entries and photos; nobody else's."""
    store(request).reset()
    return {"home": books(request).home()}


@app.get("/evidence/{rel:path}")
def evidence(rel: str, request: Request):
    """A file the page shows as evidence: the month's receipts and voice notes, or this visitor's photos."""
    if rel.startswith("pile/"):
        base, rel = store(request).piles.resolve(), rel[5:]
    else:
        base = MONTH
    path = (base / rel).resolve()
    if base not in path.parents or not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".wav"}:
        raise HTTPException(404, "No such file.")
    return FileResponse(path)
