"""Job orchestration. Host-agnostic: cron, launchd, GitHub Actions, or a Claude Code
Routine can all just call `instagram --brand <slug> <job>`. Each job is lock-protected
(no overlapping runs), recorded in job_runs, and logged in the activity log."""
import os
import shlex
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from .activity import ActivityLog
from .analytics import AnalyticsCollector
from .client import AuthError, InstagramAPIError, UnsupportedOperation, build_client
from .engagement import CommentMonitor
from .learning import LearningLoop
from .media_host import build_media_host
from .opportunities import OpportunityMonitor
from .publisher import Publisher
from .reasoner import build_reasoner
from .store import Store, iso, parse_iso, utcnow


class Runtime:
    """Wires the pieces for one brand. Everything is lazy so `status` never needs the API."""

    def __init__(self, ctx, client=None, reasoner=None, media_host=None, store=None):
        self.ctx = ctx
        self.store = store or Store(ctx.db_path)
        self._client, self._reasoner, self._media_host = client, reasoner, media_host
        self.activity = ActivityLog(self.store, ctx.instagram_dir / "activity.log",
                                    dry_run=ctx.config["api"]["mode"] != "live")

    @property
    def client(self):
        if self._client is None:
            self._client = build_client(self.ctx)
        return self._client

    @property
    def reasoner(self):
        if self._reasoner is None:
            self._reasoner = build_reasoner(self.ctx)
        return self._reasoner

    @property
    def media_host(self):
        if self._media_host is None:
            self._media_host = build_media_host(self.ctx, verify=self.ctx.live)
        return self._media_host

    def publisher(self):
        return Publisher(self.ctx, self.store, self.client, self.activity, self.media_host)

    def comments(self):
        return CommentMonitor(self.ctx, self.store, self.client, self.activity, self.reasoner)

    def opportunities(self):
        return OpportunityMonitor(self.ctx, self.store, self.client, self.activity, self.reasoner)

    def analytics(self):
        return AnalyticsCollector(self.ctx, self.store, self.client, self.activity)

    def learning(self):
        return LearningLoop(self.ctx, self.store, self.activity)


# ------------------------------------------------------------------ jobs
def job_publish(rt):
    pub = rt.publisher()
    ingested = pub.ingest()
    validated = pub.validate_pending()
    published = pub.publish_due()
    waiting = len(rt.store.publications(["VALIDATED"]))
    review = len(rt.store.publications(["REQUIRES_REVIEW"]))
    results = ", ".join(f"{cid}: {r}" for cid, r in published) or "nothing published"
    return (f"ingested {len(ingested)}, validated {len(validated)}; {results}; "
            f"awaiting approval {waiting}, needs review {review}")


def job_comments(rt):
    counts = rt.comments().run()
    return ", ".join(f"{k}={v}" for k, v in counts.items() if v) or "no new comments"


def job_opportunities(rt):
    r = rt.opportunities().run()
    return ", ".join(f"{k}={v}" for k, v in r.items())


def job_analytics(rt):
    r = rt.analytics().collect()
    return f"media snapshots {r.get('media', 0)}, errors {r.get('errors', 0)}"


def job_weekly_review(rt):
    return f"wrote {rt.learning().write()}"


def _run_hook(rt, key, action):
    cmd = rt.ctx.config["scheduler"].get(key) or ""
    mode = rt.ctx.autonomy(action)
    if mode == "off":
        return f"{action} is off"
    if not cmd:
        rt.activity.record("PLAN", f"Human action needed: {action.replace('_', ' ')}",
                           reason=f"no scheduler.{key} configured", approval_required=True)
        return "brief written; human action needed"
    cmd = cmd.replace("{brand}", shlex.quote(rt.ctx.slug)).replace("{brand_path}", shlex.quote(str(rt.ctx.path)))
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3600)
    rt.activity.record("PLAN", f"Ran {key}", tool="shell", result=f"exit {out.returncode}",
                       error=(out.stderr or "")[-500:] if out.returncode else None,
                       approval_required=mode != "auto")
    if out.returncode:
        raise RuntimeError(f"{key} exited {out.returncode}")
    return f"{key} ok"


def job_plan(rt):
    """Write a planning brief for the next social-trend-scan, then (optionally) trigger it.
    Picking clusters stays a human decision — the idea-board gate in CLAUDE.md."""
    brief = planning_brief(rt)
    path = rt.ctx.instagram_dir / "plan-brief.md"
    path.write_text(brief)
    rt.activity.record("PLAN", "Planning brief written", result=str(path), tool="scheduler.plan")
    return _run_hook(rt, "plan_command", "plan_content")


def job_generate(rt):
    return _run_hook(rt, "generate_command", "generate_content")


def planning_brief(rt):
    s = rt.store
    queued = s.publications(["QUEUED", "VALIDATED"])
    review = s.publications(["REQUIRES_REVIEW"])
    ideas = [o for o in s.opportunities(["NEW", "REVIEWED"]) if o["opportunity_type"] in ("content_idea", "trend_to_join")]
    questions = [r["text"] for r in s.comments() if r["category"] == "question"][-8:]
    per_day = rt.ctx.config["publishing"]["max_posts_per_day"]
    lines = [f"# Planning brief — {rt.ctx.slug} — {utcnow().date().isoformat()}", "",
             "Input for the next `social-trend-scan` run. Advisory only: the brand files are authoritative "
             "and a human still approves idea-board clusters.", "",
             "## Pipeline", f"- Ready to publish (validated/queued): {len(queued)} — about "
             f"{len(queued) / max(per_day, 1):.0f} day(s) of posts at the current cap",
             f"- Needs review: {len(review)}", ""]
    rec = rt.ctx.instagram_dir / "recommendations" / "latest.md"
    lines += ["## Performance recommendations",
              f"- See `{rec}`" if rec.exists() else "- None yet (run `instagram weekly-review` once posts have metrics)", ""]
    lines += ["## Audience questions worth answering in a post", *([f"- “{q[:140]}”" for q in questions] or ["- none yet"]), ""]
    lines += ["## Content ideas from monitored accounts", *([f"- {o['content_summary']} ({o['source_url'] or 'no link'})"
                                                             for o in ideas[:8]] or ["- none"]), ""]
    return "\n".join(lines)


def job_token_check(rt):
    ctx = rt.ctx
    warn_days = ctx.config["api"]["token_refresh_days_before_expiry"]
    expires = None
    info = None
    try:
        info = rt.client.debug_token()
    except InstagramAPIError:
        info = None
    if info and info.get("expires_at"):
        from datetime import datetime, timezone
        expires = datetime.fromtimestamp(info["expires_at"], tz=timezone.utc) if info["expires_at"] else None
        if info.get("is_valid") is False:
            raise AuthError("access token is invalid (debug_token)")
    elif os.environ.get("INSTAGRAM_TOKEN_EXPIRES_AT"):
        expires = parse_iso(os.environ["INSTAGRAM_TOKEN_EXPIRES_AT"])
    elif rt.store.get_kv("token_expires_at"):
        expires = parse_iso(rt.store.get_kv("token_expires_at"))
    rt.client.get_account()  # proves the token works right now
    if expires is None:
        return "token valid; expiry unknown (system-user tokens don't expire; set INSTAGRAM_TOKEN_EXPIRES_AT to track)"
    left = expires - utcnow()
    if left > timedelta(days=warn_days):
        return f"token valid; expires in {left.days} days"
    sink = ctx.config["scheduler"].get("token_sink_command")
    if ctx.config["api"]["login_type"] == "instagram" and sink and not rt.client.dry_run:
        new = rt.client.refresh_token()
        subprocess.run(sink, shell=True, input=new["access_token"], text=True, check=True, timeout=120)
        new_exp = utcnow() + timedelta(seconds=int(new.get("expires_in") or 0))
        rt.store.set_kv("token_expires_at", iso(new_exp))
        rt.activity.record("AUTH", "Refreshed Instagram access token", result=f"new expiry {new_exp.date()}")
        return f"token refreshed; new expiry {new_exp.date()}"
    rt.activity.record("AUTH", f"Access token expires in {left.days} day(s)", approval_required=True,
                       reason="refresh it (docs/instagram-setup.md#token-lifecycle)")
    return f"WARNING: token expires in {left.days} day(s)"


def job_daily_run(rt):
    parts = []
    for name in ("token-check", "publish", "comments", "opportunities", "analytics"):
        status, summary = run_job(rt, name)
        parts.append(f"{name}: {status} ({summary})")
        if status == "FAILED" and name == "token-check" and "AuthError" in summary:
            parts.append("stopped: fix authentication first")
            break
    return " | ".join(parts)


JOBS = {
    "plan": job_plan,
    "generate": job_generate,
    "publish": job_publish,
    "comments": job_comments,
    "opportunities": job_opportunities,
    "analytics": job_analytics,
    "weekly-review": job_weekly_review,
    "token-check": job_token_check,
    "daily-run": job_daily_run,
}


def run_job(rt, name):
    """Returns (status, summary). Never raises: a scheduled run must always exit cleanly and leave a record."""
    fn = JOBS[name]
    lock = f"job:{name}"
    if not rt.store.acquire_lock(lock):
        rt.activity.record("SCHEDULER", f"Skipped {name}: previous run still in progress")
        return "SKIPPED", "already running"
    run_id = rt.store.start_job(name)
    try:
        summary = fn(rt)
        status = "OK"
    except AuthError as e:
        status, summary = "FAILED", f"AuthError: {e} — check INSTAGRAM_ACCESS_TOKEN (docs/instagram-setup.md)"
    except UnsupportedOperation as e:
        status, summary = "FAILED", f"Unsupported: {e}"
    except Exception as e:  # noqa: BLE001 — recorded, never swallowed silently
        status, summary = "FAILED", f"{type(e).__name__}: {e}"
    finally:
        rt.store.release_lock(lock)
    rt.store.finish_job(run_id, status, summary)
    rt.activity.record("SCHEDULER", f"Job {name}: {status}", result=summary if status == "OK" else None,
                       error=summary if status != "OK" else None)
    return status, summary


def crontab_lines(ctx, repo_root=None):
    repo_root = Path(repo_root or Path(__file__).resolve().parents[1])
    exe = repo_root / "bin" / "instagram"
    lines = [f"# Instagram OS — {ctx.slug}. Secrets must be in the environment cron runs with",
             f"# (e.g. source a file readable only by you: . $HOME/.config/instagram-os/{ctx.slug}.env).",
             f"# Or run everything once a day: 0 9 * * * {exe} --brand {ctx.slug} daily-run"]
    for job, expr in ctx.config["scheduler"]["cron"].items():
        lines.append(f"{expr} {sys.executable} {exe} --brand {ctx.slug} {job} >> {ctx.instagram_dir}/cron.log 2>&1")
    return "\n".join(lines)
