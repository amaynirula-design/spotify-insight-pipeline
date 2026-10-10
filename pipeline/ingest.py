"""Stage 1 - Prepare (code only): read the whole CSV, preserve IDs and source values, hash each
row, profile quality, find exact duplicate texts and load the pending-work queue into SQLite."""

import csv
import statistics
from collections import Counter
from pathlib import Path

from .util import FIELDS, now_iso, row_sha, sha_file, text_key, write_json, ROOT, read_json

CHUNK = 20000  # rows per insert transaction: bounds memory for large files


def read_rows(path):
    """Same parser settings as check_submission.csv_rows: multiline quoted text is preserved."""
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, strict=True)
        missing = [k for k in FIELDS if k not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"{path}: missing required columns {missing}")
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError("Malformed CSV row")
            yield row


def ingest(db, path):
    path = Path(path)
    file_sha = sha_file(path)
    input_id = file_sha[:12]
    existing = db.input_row(input_id)
    if existing:
        return input_id, read_report(existing)

    counts = Counter()
    months, ratings, text_counts, lengths = Counter(), Counter(), Counter(), []
    empty_ids, duplicate_ids, seen = [], [], set()
    first = last = None
    buffer = []

    def flush():
        with db.tx() as c:
            c.executemany("INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?,?)", buffer)
        buffer.clear()

    for row_num, row in enumerate(read_rows(path)):
        counts["records"] += 1
        rid, text, ts = row["review_id"], row["review_text"], row["review_timestamp"]
        months[ts[:7]] += 1
        ratings[row["review_rating"]] += 1
        first = ts if first is None else min(first, ts)
        last = ts if last is None else max(last, ts)
        counts["missing_app_version"] += not row["app_version"].strip()
        if rid in seen:
            # Only the first row of a repeated ID can be accounted for under that ID.
            counts["duplicate_review_ids"] += 1
            duplicate_ids.append(rid)
            continue
        seen.add(rid)
        empty = not text.strip()
        if empty:
            counts["empty_review_text"] += 1
            empty_ids.append(rid)
        else:
            text_counts[text] += 1
        buffer.append((input_id, row_num, rid, text, row["review_rating"], row["review_likes"], row["app_version"],
                       ts, row_sha(row), None if empty else text_key(text)))
        if len(buffer) >= CHUNK:
            flush()
    flush()

    lengths = sorted(len(t) for t in text_counts)
    nonempty = sum(text_counts.values())
    repeated = {t: n for t, n in text_counts.items() if n > 1}
    report = {
        "input_path": str(path), "file_sha256": file_sha, "file_bytes": path.stat().st_size, "input_id": input_id,
        "ingested_at": now_iso(), "parser": "csv.DictReader(strict=True, newline='', utf-8-sig)",
        "counts": {"records": counts["records"], "duplicate_review_ids": counts["duplicate_review_ids"],
                   "empty_review_text": counts["empty_review_text"], "missing_app_version": counts["missing_app_version"]},
        "reviews_by_month": dict(sorted(months.items())), "reviews_by_rating": dict(sorted(ratings.items())),
        "first_review": first, "last_review": last,
        "classification_scope": {
            "nonempty_to_classify": nonempty, "distinct_nonempty_texts": len(text_counts),
            "rows_served_by_exact_text_reuse": nonempty - len(text_counts),
            "duplicate_text_groups": len(repeated),
            "largest_duplicate_groups": [{"text": t[:60], "rows": n} for t, n in Counter(repeated).most_common(10)],
        },
        "text_length_chars_distinct": ({
            "mean": round(statistics.fmean(lengths), 2), "median": lengths[len(lengths) // 2],
            "p90": lengths[int(.9 * len(lengths))], "p99": lengths[int(.99 * len(lengths))], "max": lengths[-1],
            "total": sum(lengths), "multiline_texts": sum("\n" in t for t in text_counts)} if lengths else {}),
        "quarantined_at_ingestion": [{"review_id": rid, "reason": "empty_review_text"} for rid in empty_ids],
        "dropped_duplicate_id_rows": duplicate_ids,
        "notes": ["Missing app_version does not prevent classification.",
                  "Exact-text reuse keys on the unmodified review_text string; no trimming or case folding."],
    }
    manifest_path = ROOT / "data" / "manifest.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        expected = manifest.get("files", {}).get(path.name)
        if expected:
            report["manifest_check"] = {"sha256_matches": expected["sha256"] == file_sha,
                                        "bytes_match": expected["bytes"] == report["file_bytes"]}
        if path.name == "spotify_reviews_18months.csv":
            report.setdefault("manifest_check", {})
            report["manifest_check"]["profile_matches"] = manifest.get("profile") == report["counts"]
            report["manifest_check"]["months_match"] = manifest.get("reviews_by_month") == report["reviews_by_month"]
            report["manifest_check"]["ratings_match"] = manifest.get("reviews_by_rating") == report["reviews_by_rating"]
    import json
    with db.tx() as c:
        c.execute("INSERT INTO inputs VALUES(?,?,?,?,?,?)",
                  (input_id, str(path), file_sha, counts["records"], report["ingested_at"], json.dumps(report)))
    return input_id, report


def read_report(input_row):
    import json
    return json.loads(input_row["report"])


def save_reports(run_dir, report, cfg):
    write_json(run_dir / "ingestion_report.json", report)
    write_json(run_dir / "data_manifest.json", {
        "source_file": Path(report["input_path"]).name, "file_sha256": report["file_sha256"],
        "file_bytes": report["file_bytes"], "records": report["counts"]["records"],
        "dataset": "BwandoWando, 3.4 Million Spotify Google Store Reviews, v2 (CC0), course 18-month extract",
        "dataset_url": "https://www.kaggle.com/datasets/bwandowando/3-4-million-spotify-google-store-reviews",
        "label_config": cfg.label_config, "provider": cfg.provider,
        "stage_configs": {name: cfg.stage_config(name) for name in ("enrich", "verify", "group", "memo")},
    })
