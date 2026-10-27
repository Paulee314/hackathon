"""Thin client for Meta Model API: chat (text and vision), image generation, speech to text,
and segmentation (SAM 3.1).

Standard library only. Reads META_API_KEY from .env (never printed). Retries rate limits and
server errors with backoff. Every call appends one line to data/usage.jsonl (USAGE_LOG moves it)
with what it cost, so spend is visible and the hosted app can cap it.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_URL = "https://api.meta.ai/v1"
ROOT = Path(__file__).resolve().parents[1]
CHAT_MODEL = "muse-spark-1.3"
IMAGE_MODEL = "muse-image-1.0"
VOICE_MODEL = "muse-voice-transcribe-1.0"
SEGMENT_MODEL = "sam-3.1"
USAGE_LOG = Path(os.environ.get("USAGE_LOG", ROOT / "data" / "usage.jsonl"))

# Meta Model API list prices (Oct 2026), in dollars.
PER_MILLION = {"muse-spark-1.3": (1.25, 0.15, 4.25)}  # dollars per million: input, cached input, output
PER_IMAGE = {IMAGE_MODEL: 0.01, SEGMENT_MODEL: 0.0025}
PER_AUDIO_HOUR = {VOICE_MODEL: 0.18}


class MetaAPIError(RuntimeError):
    pass


def _key() -> str:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("META_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    value = os.environ.get("META_API_KEY", "")
    if not value:
        raise MetaAPIError("META_API_KEY is not set in .env")
    return value


def cost_of(row: Dict[str, Any]) -> float:
    """Dollars for one usage-log row, from the token counts, images or audio it records."""
    model = row.get("model", "")
    if model in PER_IMAGE:
        return PER_IMAGE[model] * (row.get("images") or 1)
    if model in PER_AUDIO_HOUR:
        return PER_AUDIO_HOUR[model] * (row.get("audio_ms") or 0) / 3_600_000
    usage = row.get("usage") or {}
    price_in, price_cached, price_out = PER_MILLION.get(model, PER_MILLION[CHAT_MODEL])
    n_in = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    cached = (usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}).get("cached_tokens") or 0
    n_out = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    return ((n_in - cached) * price_in + cached * price_cached + n_out * price_out) / 1_000_000


def _log(kind: str, model: str, seconds: float, usage: Optional[Dict[str, Any]] = None, extra: Optional[Dict] = None):
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, "model": model, "seconds": round(seconds, 2)}
    if usage:
        row["usage"] = usage
    if extra:
        row.update(extra)
    row["cost"] = round(cost_of(row), 6)
    with open(USAGE_LOG, "a") as f:
        f.write(json.dumps(row) + "\n")


def _send(req: urllib.request.Request, timeout: int = 180, attempts: int = 4) -> Dict[str, Any]:
    delay = 2.0
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:600]
            if e.code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise MetaAPIError(f"HTTP {e.code}: {body}") from None
        except urllib.error.URLError as e:
            if attempt < attempts - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise MetaAPIError(f"network error: {e.reason}") from None
    raise MetaAPIError("unreachable")


def _post_json(path: str, payload: Dict[str, Any], timeout: int = 180) -> Dict[str, Any]:
    req = urllib.request.Request(
        BASE_URL + path, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"})
    return _send(req, timeout)


def chat(messages: List[Dict[str, Any]], model: str = CHAT_MODEL, temperature: float = 0.0) -> str:
    start = time.time()
    body = _post_json("/chat/completions", {"model": model, "messages": messages, "temperature": temperature})
    _log("chat", model, time.time() - start, body.get("usage"))
    return body["choices"][0]["message"]["content"]


def image_part(path: Path) -> Dict[str, Any]:
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def parse_json(text: str) -> Any:
    """Models sometimes wrap JSON in prose or code fences; take the outermost object or array."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError(f"no JSON in model output: {text[:200]!r}")
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start:end + 1])


def chat_json(messages: List[Dict[str, Any]], model: str = CHAT_MODEL) -> Any:
    return parse_json(chat(messages, model=model))


def generate_image(prompt: str, size: str = "768x1024", fmt: str = "jpeg") -> bytes:
    start = time.time()
    body = _post_json("/images/generations", {"model": IMAGE_MODEL, "prompt": prompt, "n": 1, "size": size,
                                               "response_format": "b64_json", "output_format": fmt})
    _log("image", IMAGE_MODEL, time.time() - start, extra={"images": 1})
    return base64.b64decode(body["data"][0]["b64_json"])


def _output_text(body: Dict[str, Any]) -> str:
    """The text of a /responses reply, whether or not the server adds the output_text shortcut."""
    if isinstance(body.get("output_text"), str):
        return body["output_text"]
    texts = []
    for item in body.get("output") or []:
        for part in item.get("content") or []:
            if part.get("type") in ("output_text", "text"):
                texts.append(part.get("text", ""))
    return "".join(texts)


def segment(image: Path, prompt: str) -> str:
    """SAM 3.1 on one image with a short noun-phrase prompt. Returns SAM's raw output text: one
    <|box;...|> (and mask) record per match, with a confidence c on each box. Empty means no match."""
    mime = mimetypes.guess_type(str(image))[0] or "image/jpeg"
    b64 = base64.b64encode(Path(image).read_bytes()).decode("ascii")
    payload = {
        "model": SEGMENT_MODEL,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": f"data:{mime};base64,{b64}"},
        ]}],
        "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"},
    }
    start = time.time()
    body = _post_json("/responses", payload)
    _log("segment", SEGMENT_MODEL, time.time() - start, body.get("usage"), extra={"images": 1})
    return _output_text(body)


def transcribe(wav: Path, keywords: Optional[List[str]] = None) -> Dict[str, Any]:
    """Batch speech to text. wav must be mono 16-bit PCM at 16 or 24 kHz."""
    settings: Dict[str, Any] = {"mode": "PUSH_TO_TALK", "model": VOICE_MODEL, "audioEncoding": "WAV"}
    if keywords:
        settings["keywords"] = keywords
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"request\"\r\nContent-Type: application/json\r\n\r\n".encode()
        + json.dumps(settings).encode() + b"\r\n",
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"{Path(wav).name}\"\r\nContent-Type: audio/wav\r\n\r\n".encode()
        + Path(wav).read_bytes() + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(
        BASE_URL + "/asr/transcribe", data=b"".join(parts), method="POST",
        headers={"Authorization": f"Bearer {_key()}", "Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Accept": "application/json"})
    start = time.time()
    body = _send(req)
    _log("voice", VOICE_MODEL, time.time() - start, extra={"audio_ms": body.get("audioDurationMs")})
    return body
