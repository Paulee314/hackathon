"""Host the Ride-off phone app on Modal, so judges (and the iPhone recording) get an https link.

One container, kept warm, serves the FastAPI app. Visitor state and the usage log live on a Modal
Volume, so a restart doesn't wipe them or reset the daily spend cap. The Meta key comes from a
Modal Secret; .env, data/ and .venv are never uploaded.

    .venv/bin/pip install modal
    .venv/bin/modal setup                         # once: log in to Modal in the browser
    # once: store the Meta key as a Modal Secret without printing it (see README, "Hosting")
    .venv/bin/modal deploy deploy/modal_app.py    # prints the https URL

After judging, set MIN_CONTAINERS to 0 (or `modal app stop ride-off`) so it stops using credits.
"""
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/root/rideoff"
MIN_CONTAINERS = 1   # keep one warm so the first judge doesn't wait for a cold start

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements(str(ROOT / "requirements.txt"))
    .add_local_dir(str(ROOT), remote_path=REMOTE,
                   ignore=[".env", ".env.*", ".git", ".venv", "data", "**/__pycache__", "**/*.pyc", "**/.DS_Store"])
)
volume = modal.Volume.from_name("ride-off-data", create_if_missing=True)
meta = modal.Secret.from_name("ride-off-meta")   # holds the Meta key as META_API_KEY
app = modal.App("ride-off", image=image)


@app.function(
    secrets=[meta],
    volumes={"/data": volume},
    min_containers=MIN_CONTAINERS,
    max_containers=1,   # one container, so visitor state and the spend cap live in one place
    scaledown_window=1200,
    timeout=300,
)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def web():
    import os
    import sys

    os.environ.setdefault("APP_DATA", "/data/app")
    os.environ.setdefault("USAGE_LOG", "/data/usage.jsonl")
    os.environ.setdefault("APP_DAILY_BUDGET", "5")
    sys.path.insert(0, REMOTE)
    os.chdir(REMOTE)
    from app.server import app as fastapi_app
    return fastapi_app
