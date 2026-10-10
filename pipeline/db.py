"""SQLite state store: source rows, exact-text result cache, call log and stage handoffs.

One connection guarded by a lock; every batch commits in a single transaction, so workers can
neither overwrite nor double-count results and an interrupted run keeps all finished batches.
"""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS inputs(
  input_id TEXT PRIMARY KEY, path TEXT, file_sha256 TEXT, n_rows INT, ingested_at TEXT, report TEXT);
CREATE TABLE IF NOT EXISTS reviews(
  input_id TEXT, row_num INT, review_id TEXT, review_text TEXT, review_rating TEXT, review_likes TEXT,
  app_version TEXT, review_timestamp TEXT, source_sha256 TEXT, text_key TEXT,
  PRIMARY KEY(input_id, review_id)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS reviews_text ON reviews(input_id, text_key, row_num);
CREATE TABLE IF NOT EXISTS results(
  text_key TEXT, label_config TEXT, status TEXT, reason TEXT, source_review_id TEXT, call_id INT, attempts INT,
  code TEXT, topic TEXT, intent TEXT, sentiment REAL, severity INT, needs_review INT, evidence_quote TEXT,
  quote_origin TEXT, entities TEXT, notes TEXT, created_at REAL,
  PRIMARY KEY(text_key, label_config)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS calls(
  call_id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT, invocation TEXT, input_id TEXT, role TEXT, phase TEXT,
  attempt_kind TEXT, provider TEXT, model TEXT, effort TEXT, tier TEXT, stage_config TEXT, review_ids TEXT, n_items INT,
  outcome TEXT, error TEXT, input_tokens INT, cached_input_tokens INT, output_tokens INT, reasoning_tokens INT,
  usage_source TEXT, started_at REAL, duration_s REAL, worker INT, reserved_usd REAL, cost_usd REAL, artifacts TEXT);
CREATE TABLE IF NOT EXISTS call_io(call_id INTEGER PRIMARY KEY, input_text TEXT, output_text TEXT);
CREATE TABLE IF NOT EXISTS verify(
  input_id TEXT, review_id TEXT, verify_config TEXT, topic TEXT, intent TEXT, severity INT, call_id INT,
  PRIMARY KEY(input_id, review_id, verify_config)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS issues(
  input_id TEXT, label_config TEXT, issue_id TEXT, name TEXT, description TEXT, sample_ids TEXT, off_topic_ids TEXT,
  group_config TEXT, call_id INT, PRIMARY KEY(input_id, label_config, issue_id)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


class DB:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    def q(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def get_meta(self, key, default=None):
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_meta(self, key, value):
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, json.dumps(value)))

    def input_row(self, input_id):
        return self.one("SELECT * FROM inputs WHERE input_id=?", (input_id,))

    # ---- call log -------------------------------------------------------------------------
    def open_call(self, **fields):
        """Record a call as in_flight before the request leaves, so an interrupted request with an
        unknown outcome (and possibly a charge) stays visible for reconciliation."""
        fields.setdefault("started_at", time.time())
        fields["outcome"] = "in_flight"
        fields["review_ids"] = json.dumps(fields.get("review_ids", []))
        cols = ", ".join(fields)
        with self.tx() as c:
            cur = c.execute(f"INSERT INTO calls({cols}) VALUES({', '.join('?' * len(fields))})", tuple(fields.values()))
            return cur.lastrowid

    def close_call(self, conn, call_id, **fields):
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE calls SET {sets} WHERE call_id=?", (*fields.values(), call_id))

    def reconcile_in_flight(self):
        """Calls still in_flight at start-up were interrupted: outcome unknown, cost unresolved."""
        with self.tx() as c:
            cur = c.execute("UPDATE calls SET outcome='unknown', error='interrupted before a response was saved; "
                            "charge unknown (reserved amount kept as a labelled estimate)' WHERE outcome='in_flight'")
            return cur.rowcount

    # ---- queue / status -------------------------------------------------------------------
    def pending_texts(self, input_id, label_config):
        """Distinct nonempty texts of this input with no saved result under label_config.
        The first source row carrying a text is the one sent to the model (the cache original)."""
        return self.q(
            """SELECT r.text_key AS text_key, r.review_id AS review_id, r.review_text AS review_text, MIN(r.row_num) AS row_num
               FROM reviews r LEFT JOIN results s ON s.text_key = r.text_key AND s.label_config = ?
               WHERE r.input_id = ? AND r.text_key IS NOT NULL AND s.text_key IS NULL
               GROUP BY r.text_key ORDER BY row_num""", (label_config, input_id))

    def status_counts(self, input_id, label_config):
        row = self.one(
            """SELECT COUNT(*) AS n_rows,
                      SUM(r.text_key IS NULL) AS empty_text,
                      COUNT(DISTINCT r.text_key) AS distinct_texts,
                      SUM(s.status = 'completed') AS completed,
                      SUM(s.status = 'quarantined') AS quarantined_nonempty,
                      SUM(r.text_key IS NOT NULL AND s.text_key IS NULL) AS pending,
                      SUM(s.status = 'completed' AND s.source_review_id <> r.review_id) AS cache_reuse_rows
               FROM reviews r LEFT JOIN results s ON s.text_key = r.text_key AND s.label_config = ?
               WHERE r.input_id = ?""", (label_config, input_id))
        return {k: (row[k] or 0) for k in row.keys()}

    def completed_ids(self, input_id, label_config):
        rows = self.q(
            """SELECT r.review_id FROM reviews r JOIN results s ON s.text_key = r.text_key AND s.label_config = ?
               WHERE r.input_id = ? AND s.status = 'completed' ORDER BY r.row_num""", (label_config, input_id))
        return [r["review_id"] for r in rows]
