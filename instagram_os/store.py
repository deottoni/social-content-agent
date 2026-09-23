"""SQLite persistence for all operational state (per brand, in the brand folder)."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import (OPPORTUNITY_TRANSITIONS, PUBLICATION_TRANSITIONS, OpportunityStatus,
                     PublicationStatus, check_transition)

SCHEMA = """
CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    content_type TEXT NOT NULL,
    caption TEXT NOT NULL,
    asset_paths TEXT NOT NULL,
    asset_urls TEXT,
    topic TEXT, pillar TEXT, hook TEXT, cta TEXT, format TEXT, origin TEXT,
    status TEXT NOT NULL,
    status_reason TEXT,
    container_id TEXT,
    media_id TEXT UNIQUE,
    permalink TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    approved_at TEXT,
    published_at TEXT,
    api_response TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publication_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id INTEGER NOT NULL REFERENCES publications(id),
    from_status TEXT, to_status TEXT NOT NULL, reason TEXT, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comments (
    comment_id TEXT PRIMARY KEY,
    media_id TEXT NOT NULL,
    parent_id TEXT,
    username TEXT, user_id TEXT, text TEXT, timestamp TEXT,
    category TEXT, confidence REAL, rationale TEXT,
    decision TEXT, decision_reason TEXT,
    suggested_reply TEXT,
    reply_id TEXT UNIQUE, reply_text TEXT, replied_at TEXT,
    reasoner TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    source_account TEXT, source_media_id TEXT, source_url TEXT, source_timestamp TEXT,
    content_summary TEXT, relevance_reason TEXT,
    score REAL NOT NULL, priority TEXT NOT NULL,
    opportunity_type TEXT, suggested_action TEXT, suggested_comment TEXT,
    signals TEXT, status TEXT NOT NULL, notes TEXT,
    expires_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,           -- media id, or 'account'
    captured_at TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL
);
CREATE INDEX IF NOT EXISTS metrics_subject ON metrics(subject, metric, captured_at);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL, category TEXT NOT NULL, summary TEXT NOT NULL,
    reason TEXT, tool TEXT, api_action TEXT, result TEXT, error TEXT,
    confidence REAL, approval_required INTEGER, ref TEXT, dry_run INTEGER
);
CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, summary TEXT
);
CREATE TABLE IF NOT EXISTS locks (name TEXT PRIMARY KEY, acquired_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hashtag_searches (
    hashtag TEXT PRIMARY KEY, hashtag_id TEXT, first_searched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or utcnow()).isoformat(timespec="seconds")


def parse_iso(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    if len(s) >= 5 and s[-5] in "+-" and s[-3] != ":":  # Meta returns +0000
        s = s[:-2] + ":" + s[-2:]
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    @contextmanager
    def tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    # ---- locks (prevent overlapping job runs / accidental loops) ----
    def acquire_lock(self, name, stale_after=timedelta(hours=2)):
        with self.tx() as c:
            row = c.execute("SELECT acquired_at FROM locks WHERE name=?", (name,)).fetchone()
            if row and utcnow() - parse_iso(row["acquired_at"]) < stale_after:
                return False
            c.execute("INSERT OR REPLACE INTO locks(name, acquired_at) VALUES (?, ?)", (name, iso()))
            return True

    def release_lock(self, name):
        self.conn.execute("DELETE FROM locks WHERE name=?", (name,))

    # ---- kv ----
    def get_kv(self, key, default=None):
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_kv(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?, ?)", (key, json.dumps(value)))

    # ---- publications ----
    def get_publication(self, content_id=None, pub_id=None):
        if pub_id is not None:
            row = self.conn.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
        else:
            row = self.conn.execute("SELECT * FROM publications WHERE content_id=?", (content_id,)).fetchone()
        return dict(row) if row else None

    def insert_publication(self, fields):
        now = iso()
        data = dict(fields, status=PublicationStatus.DRAFT.value, created_at=now, updated_at=now)
        data["asset_paths"] = json.dumps(data.get("asset_paths", []))
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        with self.tx() as c:
            cur = c.execute(f"INSERT INTO publications ({cols}) VALUES ({marks})", tuple(data.values()))
            c.execute("INSERT INTO publication_events(publication_id, from_status, to_status, reason, at) "
                      "VALUES (?, NULL, ?, ?, ?)", (cur.lastrowid, "DRAFT", "ingested", now))
            return cur.lastrowid

    def update_publication(self, pub_id, **fields):
        if not fields:
            return
        fields["updated_at"] = iso()
        for k in ("asset_urls", "api_response"):
            if k in fields and not isinstance(fields[k], (str, type(None))):
                fields[k] = json.dumps(fields[k])
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE publications SET {sets} WHERE id=?", (*fields.values(), pub_id))

    def transition_publication(self, pub_id, new_status, reason=None, expect=None, **fields):
        """Atomic, validated status change. `expect` guards against concurrent writers."""
        new_status = PublicationStatus(new_status)
        with self.tx() as c:
            row = c.execute("SELECT status FROM publications WHERE id=?", (pub_id,)).fetchone()
            current = PublicationStatus(row["status"])
            if expect is not None and current not in {PublicationStatus(e) for e in expect}:
                return False
            check_transition(PUBLICATION_TRANSITIONS, current, new_status)
            now = iso()
            fields = dict(fields, status=new_status.value, status_reason=reason, updated_at=now)
            for k in ("asset_urls", "api_response"):
                if k in fields and not isinstance(fields[k], (str, type(None))):
                    fields[k] = json.dumps(fields[k])
            sets = ", ".join(f"{k}=?" for k in fields)
            c.execute(f"UPDATE publications SET {sets} WHERE id=?", (*fields.values(), pub_id))
            c.execute("INSERT INTO publication_events(publication_id, from_status, to_status, reason, at) "
                      "VALUES (?, ?, ?, ?, ?)", (pub_id, current.value, new_status.value, reason, now))
            return True

    def publications(self, statuses=None):
        if statuses:
            marks = ",".join("?" for _ in statuses)
            rows = self.conn.execute(f"SELECT * FROM publications WHERE status IN ({marks}) ORDER BY content_id",
                                     [PublicationStatus(s).value for s in statuses])
        else:
            rows = self.conn.execute("SELECT * FROM publications ORDER BY content_id")
        return [dict(r) for r in rows]

    def published_since(self, since, content_type=None):
        q = "SELECT * FROM publications WHERE status='PUBLISHED' AND published_at >= ?"
        args = [iso(since)]
        if content_type:
            q += " AND content_type=?"
            args.append(content_type)
        return [dict(r) for r in self.conn.execute(q, args)]

    def publication_events(self, pub_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM publication_events WHERE publication_id=? ORDER BY id", (pub_id,))]

    # ---- comments ----
    def get_comment(self, comment_id):
        row = self.conn.execute("SELECT * FROM comments WHERE comment_id=?", (comment_id,)).fetchone()
        return dict(row) if row else None

    def upsert_comment(self, comment_id, **fields):
        now = iso()
        existing = self.get_comment(comment_id)
        if existing:
            fields["updated_at"] = now
            sets = ", ".join(f"{k}=?" for k in fields)
            self.conn.execute(f"UPDATE comments SET {sets} WHERE comment_id=?", (*fields.values(), comment_id))
        else:
            data = dict(fields, comment_id=comment_id, created_at=now, updated_at=now)
            cols = ", ".join(data)
            self.conn.execute(f"INSERT INTO comments ({cols}) VALUES ({', '.join('?' for _ in data)})",
                              tuple(data.values()))

    def claim_comment_reply(self, comment_id):
        """Mark a comment as being replied to, exactly once. Returns False if already replied."""
        cur = self.conn.execute(
            "UPDATE comments SET reply_id='PENDING:'||comment_id, updated_at=? "
            "WHERE comment_id=? AND reply_id IS NULL", (iso(), comment_id))
        return cur.rowcount == 1

    def comments(self, decisions=None):
        if decisions:
            marks = ",".join("?" for _ in decisions)
            rows = self.conn.execute(f"SELECT * FROM comments WHERE decision IN ({marks}) ORDER BY timestamp",
                                     [getattr(d, "value", d) for d in decisions])
        else:
            rows = self.conn.execute("SELECT * FROM comments ORDER BY timestamp")
        return [dict(r) for r in rows]

    def replies_since(self, since, username=None):
        q = "SELECT COUNT(*) FROM comments WHERE replied_at IS NOT NULL AND replied_at >= ?"
        args = [iso(since)]
        if username:
            q += " AND username=?"
            args.append(username)
        return self.conn.execute(q, args).fetchone()[0]

    # ---- opportunities ----
    def insert_opportunity(self, fields):
        now = iso()
        data = dict(fields, status=OpportunityStatus.NEW.value, created_at=now, updated_at=now)
        if isinstance(data.get("signals"), dict):
            data["signals"] = json.dumps(data["signals"])
        cols = ", ".join(data)
        try:
            cur = self.conn.execute(f"INSERT INTO opportunities ({cols}) VALUES ({', '.join('?' for _ in data)})",
                                    tuple(data.values()))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None  # duplicate dedupe_key: already known

    def get_opportunity(self, opp_id):
        row = self.conn.execute("SELECT * FROM opportunities WHERE id=?", (opp_id,)).fetchone()
        return dict(row) if row else None

    def has_opportunity(self, dedupe_key):
        return self.conn.execute("SELECT 1 FROM opportunities WHERE dedupe_key=?", (dedupe_key,)).fetchone() is not None

    def transition_opportunity(self, opp_id, new_status, notes=None):
        new_status = OpportunityStatus(new_status)
        with self.tx() as c:
            row = c.execute("SELECT status, notes FROM opportunities WHERE id=?", (opp_id,)).fetchone()
            if row is None:
                raise KeyError(f"No opportunity #{opp_id}")
            check_transition(OPPORTUNITY_TRANSITIONS, OpportunityStatus(row["status"]), new_status)
            merged = "\n".join(x for x in (row["notes"], notes) if x) or None
            c.execute("UPDATE opportunities SET status=?, notes=?, updated_at=? WHERE id=?",
                      (new_status.value, merged, iso(), opp_id))

    def opportunities(self, statuses=None):
        q = "SELECT * FROM opportunities"
        args = []
        if statuses:
            q += f" WHERE status IN ({','.join('?' for _ in statuses)})"
            args = [OpportunityStatus(s).value for s in statuses]
        q += " ORDER BY score DESC, id"
        return [dict(r) for r in self.conn.execute(q, args)]

    # ---- metrics ----
    def add_metrics(self, subject, values, captured_at=None):
        at = iso(captured_at)
        self.conn.executemany("INSERT INTO metrics(subject, captured_at, metric, value) VALUES (?, ?, ?, ?)",
                              [(subject, at, k, v) for k, v in values.items() if v is not None])

    def last_snapshot_at(self, subject):
        row = self.conn.execute("SELECT MAX(captured_at) FROM metrics WHERE subject=?", (subject,)).fetchone()
        return parse_iso(row[0]) if row and row[0] else None

    def latest_metrics(self, subject):
        rows = self.conn.execute(
            "SELECT metric, value FROM metrics m WHERE subject=? AND captured_at = "
            "(SELECT MAX(captured_at) FROM metrics WHERE subject=m.subject AND metric=m.metric)", (subject,))
        return {r["metric"]: r["value"] for r in rows}

    def metric_history(self, subject, metric):
        return [(parse_iso(r["captured_at"]), r["value"]) for r in self.conn.execute(
            "SELECT captured_at, value FROM metrics WHERE subject=? AND metric=? ORDER BY captured_at",
            (subject, metric))]

    # ---- hashtag search budget ----
    def hashtags_searched_since(self, since):
        return {r["hashtag"]: r["hashtag_id"] for r in self.conn.execute(
            "SELECT hashtag, hashtag_id FROM hashtag_searches WHERE first_searched_at >= ?", (iso(since),))}

    def record_hashtag_search(self, hashtag, hashtag_id):
        self.conn.execute("INSERT OR REPLACE INTO hashtag_searches(hashtag, hashtag_id, first_searched_at) "
                          "VALUES (?, ?, ?)", (hashtag, hashtag_id, iso()))

    # ---- jobs ----
    def start_job(self, job):
        return self.conn.execute("INSERT INTO job_runs(job, started_at, status) VALUES (?, ?, 'RUNNING')",
                                 (job, iso())).lastrowid

    def finish_job(self, run_id, status, summary):
        self.conn.execute("UPDATE job_runs SET finished_at=?, status=?, summary=? WHERE id=?",
                          (iso(), status, summary, run_id))

    def job_runs(self, limit=20):
        return [dict(r) for r in self.conn.execute("SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,))]
