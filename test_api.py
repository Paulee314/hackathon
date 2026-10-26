"""Day-one smoke test for the Meta Model API.

1. Muse Spark text call
2. Muse Image generates a synthetic receipt (fake merchant, safe for the public repo)
3. Muse Spark reads that receipt back as structured JSON

Reads META_API_KEY from .env. Never prints the key. Standard library only.
"""
import base64
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

BASE_URL = "https://api.meta.ai/v1"
HERE = pathlib.Path(__file__).resolve().parent


def load_key():
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line.startswith("META_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("META_API_KEY", "")


def post(path, payload, key, timeout=180):
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), time.time() - start
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:800]
        return e.code, {"error_body": body}, time.time() - start


def main():
    key = load_key()
    if not key:
        print("FAIL: META_API_KEY is empty in .env")
        sys.exit(1)
    print(f"Key loaded ({len(key)} characters, not shown)\n")

    # 1. Text
    print("1) Muse Spark text call ...")
    status, body, secs = post("/chat/completions", {
        "model": "muse-spark-1.3",
        "messages": [{"role": "user", "content": "Reply with exactly: Muse Spark is working."}],
    }, key)
    if status == 200:
        print(f"   OK {secs:.1f}s -> {body['choices'][0]['message']['content'].strip()}")
    else:
        print(f"   FAIL HTTP {status}: {body}")
        sys.exit(1)

    # 2. Synthetic receipt with Muse Image
    print("\n2) Muse Image generating a synthetic receipt ...")
    status, body, secs = post("/images/generations", {
        "model": "muse-image-1.0",
        "prompt": (
            "Top-down photo of a printed paper receipt lying on a car seat. Clearly legible text: "
            "'BAYOU SHINE CAR WASH', 'Houston TX', 'Date 10/24/2026', "
            "'Exterior wash 18.00', 'Interior detail 24.00', 'Subtotal 42.00', 'Tax 3.47', "
            "'TOTAL 45.47', 'VISA ****1234'. Realistic, sharp, well lit."
        ),
        "n": 1,
        "size": "768x1024",
        "response_format": "b64_json",
        "output_format": "png",
    }, key)
    if status != 200:
        print(f"   FAIL HTTP {status}: {body}")
        sys.exit(1)
    img_bytes = base64.b64decode(body["data"][0]["b64_json"])
    out = HERE / "samples" / "test_receipt.png"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(img_bytes)
    print(f"   OK {secs:.1f}s -> saved samples/test_receipt.png ({len(img_bytes) // 1024} KB)")

    # 3. Muse Spark reads the receipt
    print("\n3) Muse Spark reading the receipt ...")
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    status, body, secs = post("/chat/completions", {
        "model": "muse-spark-1.3",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": (
                    "Extract this receipt as JSON with keys: merchant, date (YYYY-MM-DD), "
                    "line_items (list of {description, amount}), subtotal, tax, total, "
                    "payment_method. Return only the JSON."
                )},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
    }, key)
    if status == 200:
        print(f"   OK {secs:.1f}s ->")
        print(body["choices"][0]["message"]["content"].strip())
        usage = body.get("usage")
        if usage:
            print(f"\n   usage: {usage}")
    else:
        print(f"   FAIL HTTP {status}: {body}")
        sys.exit(1)

    print("\nAll three calls succeeded.")


if __name__ == "__main__":
    main()
