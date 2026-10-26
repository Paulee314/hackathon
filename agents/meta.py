"""Thin client for Meta Model API: chat (text and vision), image generation, speech to text.

Standard library only. Reads META_API_KEY from .env (never printed). Retries rate limits and
server errors with backoff. Every call appends one line to data/usage.jsonl so spend is visible.
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


def _log(kind: str, model: str, seconds: float, usage: Optional[Dict[str, Any]] = None, extra: Optional[Dict] = None):
    path = ROOT / "data" / "usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, "model": model, "seconds": round(seconds, 2)}
    if usage:
        row["usage"] = usage
    if extra:
        row.update(extra)
    with open(path, "a") as f:
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
