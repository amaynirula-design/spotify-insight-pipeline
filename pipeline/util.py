"""Small shared helpers. Standard library only."""

import hashlib
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ("review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp")


def row_sha(row):
    """SHA-256 of the six original field strings; identical to check_submission.row_sha."""
    payload = json.dumps([row[k] for k in FIELDS], ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def text_key(text):
    """Exact-text cache key. No trimming or normalisation: only identical strings share a result."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso(ts=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts if ts is not None else time.time()))


def write_text_atomic(path, text):
    """Write then rename, so an interrupted run never leaves a half-written artifact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_json(path, value):
    write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_dotenv(path=None):
    """Minimal .env loader: KEY=VALUE lines into os.environ without overriding existing values."""
    path = Path(path or ROOT / ".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value and key.strip() not in os.environ:
            os.environ[key.strip()] = value


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
