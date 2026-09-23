"""Traceable activity log: every decision and API action, in SQLite and a readable text file."""
import re
from pathlib import Path

from .store import iso, utcnow

# Anything shaped like a token or secret is scrubbed before it reaches disk.
_SECRET_RE = re.compile(r"(access_token=|client_secret=|Bearer\s+)[^\s&\"']+", re.I)


def redact(text):
    if text is None:
        return None
    return _SECRET_RE.sub(r"\1***", str(text))


class ActivityLog:
    def __init__(self, store, log_path=None, dry_run=False):
        self.store = store
        self.log_path = Path(log_path) if log_path else None
        self.dry_run = dry_run

    def record(self, category, summary, *, reason=None, tool=None, api_action=None, result=None,
               error=None, confidence=None, approval_required=None, ref=None):
        category = category.upper()
        summary, reason, error, result = map(redact, (summary, reason, error, result))
        at = iso()
        self.store.conn.execute(
            "INSERT INTO activity(at, category, summary, reason, tool, api_action, result, error, confidence, "
            "approval_required, ref, dry_run) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (at, category, summary, reason, tool, api_action, result, error, confidence,
             None if approval_required is None else int(bool(approval_required)), ref, int(self.dry_run)))
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            lines = [utcnow().strftime("%Y-%m-%d %H:%M"), category + (" (dry-run)" if self.dry_run else ""), summary]
            details = []
            if reason:
                details.append(f"why: {reason}")
            if tool:
                details.append(f"tool: {tool}")
            if api_action:
                details.append(f"api: {api_action}")
            if confidence is not None:
                details.append(f"confidence: {confidence:.2f}")
            if approval_required is not None:
                details.append(f"approval required: {'yes' if approval_required else 'no'}")
            if result:
                details.append(f"result: {result}")
            if error:
                details.append(f"error: {error}")
            lines.extend("  " + d for d in details)
            with self.log_path.open("a") as fh:
                fh.write("\n".join(lines) + "\n\n")

    def recent(self, limit=50, category=None):
        q = "SELECT * FROM activity"
        args = []
        if category:
            q += " WHERE category=?"
            args.append(category.upper())
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.store.conn.execute(q, args)]
