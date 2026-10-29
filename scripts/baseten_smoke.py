"""One short call to Muse Spark Open on Baseten, to check the key and the route. Prints no secrets.

    .venv/bin/python scripts/baseten_smoke.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents import meta  # noqa: E402

start = time.time()
out = meta.chat_json([{"role": "user", "content":
    'A driver paid "BAYOU SHINE CAR WASH #12" $18.00 on the business card. Return only JSON: '
    '{"schedule_c_line": "<line number> <line name>", "reason": "<one short sentence>"}'}],
    model=meta.OPEN_MODEL, reasoning_effort="low")
print(json.dumps(out), f"({time.time() - start:.1f}s)")
row = json.loads(meta.USAGE_LOG.read_text().splitlines()[-1])
print("usage:", row.get("usage"), "cost $", row.get("cost"))
