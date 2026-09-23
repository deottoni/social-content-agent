"""`instagram` command line. Every scheduled job can also be run by hand here.

    instagram --brand <slug> <command> [args]

(or set INSTAGRAM_BRAND). Run `instagram --help` for the list.
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

from .config import ConfigError, load_brand, resolve_brand_path
from .models import OpportunityStatus
from .policy import boundaries_table
from .scheduler import JOBS, Runtime, crontab_lines, run_job

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "brands" / "_template" / "instagram"


def _print_table(rows, headers):
    if not rows:
        print("  (none)")
        return
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
    print("  " + "  ".join(str(h).ljust(w) for h, w in zip(headers, widths)))
    for r in rows:
        print("  " + "  ".join(str(c).ljust(w) for c, w in zip(r, widths)))


def cmd_init(args):
    path = resolve_brand_path(args.brand, args.registry)
    target = path / "instagram"
    target.mkdir(exist_ok=True)
    for name in ("config.yaml", "facts.md"):
        dest = target / name
        if dest.exists():
            print(f"exists, left alone: {dest}")
        else:
            shutil.copy(TEMPLATE_DIR / name, dest)
            print(f"created: {dest}")
    print("\nNext: fill in facts.md, review config.yaml, set the env vars from docs/instagram-setup.md,\n"
          f"then set `enabled: true` and run `instagram --brand {args.brand} auth-check`.")


def cmd_status(rt, args):
    ctx, s = rt.ctx, rt.store
    print(f"Brand: {ctx.slug}  ({ctx.path})")
    print(f"API mode: {ctx.config['api']['mode']}   login: {ctx.config['api']['login_type']}   "
          f"credentials: {'present' if ctx.secrets.has_credentials else 'MISSING'}   "
          f"reasoner: {ctx.config['reasoner']['provider']}")
    print("\nAutonomy boundaries:")
    _print_table(boundaries_table(ctx), ["action", "mode", "note"])
    print("\nPublishing queue:")
    counts = {}
    for p in s.publications():
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    _print_table(sorted(counts.items()), ["status", "count"])
    pending = s.comments(["PENDING_APPROVAL", "ESCALATED"])
    print(f"\nComments awaiting a human: {len(pending)}")
    opps = s.opportunities(["NEW", "REVIEWED", "APPROVED"])
    by = {p: sum(1 for o in opps if o["priority"] == p) for p in ("HIGH", "MEDIUM", "LOW")}
    print(f"Open opportunities: HIGH {by['HIGH']} / MEDIUM {by['MEDIUM']} / LOW {by['LOW']}")
    print("\nRecent jobs:")
    _print_table([(r["started_at"], r["job"], r["status"], (r["summary"] or "")[:70]) for r in s.job_runs(8)],
                 ["started", "job", "status", "summary"])


def cmd_auth_check(rt, args):
    c = rt.client
    acct = c.get_account()
    print(f"Account OK: @{acct.get('username')} (id {acct.get('id')}), followers {acct.get('followers_count')}")
    try:
        q = c.get_publishing_limit()
        print(f"Publishing quota: {q['used']} of {q['total']} used in the last 24h")
    except Exception as e:  # noqa: BLE001
        print(f"Publishing quota unavailable: {e}")
    info = c.debug_token()
    if info:
        print(f"Token valid: {info.get('is_valid')}  scopes: {', '.join(info.get('scopes', []))}  "
              f"expires_at: {info.get('expires_at') or 'never'}")
    if getattr(c, "dry_run", False):
        print("Mode: dry_run — writes are simulated." + ("" if rt.ctx.secrets.has_credentials else
                                                         " No credentials: reads come from an empty fake account."))


def cmd_queue(rt, args):
    rows = rt.store.publications(args.status.split(",") if args.status else None)
    _print_table([(p["content_id"], p["content_type"], p["status"], (p["status_reason"] or "")[:60], p["media_id"] or "")
                  for p in rows], ["content id", "type", "status", "reason", "media id"])


def cmd_publish(rt, args):
    pub = rt.publisher()
    if args.action == "approve":
        pub.approve(args.content_id, override=args.override, note=args.note)
        print(f"queued {args.content_id}")
    elif args.action == "reject":
        pub.reject(args.content_id, args.note)
        print(f"rejected {args.content_id}")
    elif args.action == "ingest":
        print(pub.ingest() or "nothing new marked ready")
    elif args.action == "validate":
        print(pub.validate_pending() or "no drafts")
    else:
        print(run_job(rt, "publish"))


def cmd_comments(rt, args):
    m = rt.comments()
    if args.action == "list":
        rows = rt.store.comments(["PENDING_APPROVAL", "ESCALATED"])
        for r in rows:
            print(f"[{r['decision']}] {r['comment_id']}  @{r['username']}  {r['category']} ({(r['confidence'] or 0):.2f})")
            print(f"    comment: {r['text']}")
            if r["suggested_reply"]:
                print(f"    suggested reply: {r['suggested_reply']}")
            print(f"    why: {r['decision_reason']}")
        if not rows:
            print("nothing awaiting a human")
    elif args.action == "approve":
        print(m.approve(args.comment_id, text=args.text))
    elif args.action == "reject":
        m.reject(args.comment_id, args.note)
        print("rejected")
    else:
        print(run_job(rt, "comments"))


def cmd_opportunities(rt, args):
    m = rt.opportunities()
    if args.action == "list":
        statuses = args.status.split(",") if args.status else ["NEW", "REVIEWED", "APPROVED"]
        opps = rt.store.opportunities(statuses)
        for prio in ("HIGH", "MEDIUM", "LOW"):
            group = [o for o in opps if o["priority"] == prio]
            if not group:
                continue
            print(f"\n{prio} PRIORITY")
            for o in group:
                print(f"\n#{o['id']}  [{o['status']}]  score {o['score']:.2f}  expires {o['expires_at']}")
                print(f"  SOURCE: @{o['source_account'] or '?'}  {o['source_url'] or ''}")
                print(f"  CONTENT: {o['content_summary']}")
                print(f"  WHY: {o['relevance_reason']}")
                print(f"  SUGGESTED ACTION: {o['suggested_action']} ({o['opportunity_type']}) — by hand")
                if o["suggested_comment"]:
                    print(f"  SUGGESTED COMMENT: {o['suggested_comment']}")
        if not opps:
            print("no open opportunities")
    elif args.action == "add":
        opp_id = m.add_manual(args.url, args.text, account=args.account)
        print(f"queued opportunity #{opp_id}" if opp_id else "assessed as not worth engaging — not queued")
    elif args.action in ("review", "approve", "dismiss", "complete"):
        status = {"review": "REVIEWED", "approve": "APPROVED", "dismiss": "DISMISSED", "complete": "COMPLETED"}[args.action]
        rt.store.transition_opportunity(int(args.id), OpportunityStatus(status), args.note)
        rt.activity.record("OPPORTUNITY", f"Opportunity #{args.id} -> {status}", reason=args.note,
                           approval_required=True, ref=str(args.id))
        print(f"#{args.id} -> {status}")
    else:
        print(run_job(rt, "opportunities"))


def cmd_log(rt, args):
    for r in reversed(rt.activity.recent(args.limit, args.category)):
        extra = f"  [{r['error']}]" if r["error"] else ""
        print(f"{r['at'][:16].replace('T', ' ')}  {r['category']:<14} {r['summary']}{extra}")


def build_parser():
    p = argparse.ArgumentParser(prog="instagram", description="Instagram operating layer for social-content-agent.")
    p.add_argument("--brand", default=os.environ.get("INSTAGRAM_BRAND"), help="brand slug from .claude/brands.local.json")
    p.add_argument("--registry", default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create <brand>/instagram/config.yaml + facts.md from the template")
    sub.add_parser("status", help="config, autonomy boundaries, queue, pending human actions")
    sub.add_parser("auth-check", help="verify the token and account (read-only)")
    q = sub.add_parser("queue", help="list publications")
    q.add_argument("--status")

    pp = sub.add_parser("publish", help="ingest + validate + publish due posts (or approve/reject one)")
    pp.add_argument("action", nargs="?", choices=["run", "ingest", "validate", "approve", "reject"], default="run")
    pp.add_argument("content_id", nargs="?")
    pp.add_argument("--override", action="store_true", help="approve a REQUIRES_REVIEW post after human review")
    pp.add_argument("--note")

    c = sub.add_parser("comments", help="scan own-media comments (or list/approve/reject suggestions)")
    c.add_argument("action", nargs="?", choices=["run", "list", "approve", "reject"], default="run")
    c.add_argument("comment_id", nargs="?")
    c.add_argument("--text", help="reply text to send instead of the suggestion")
    c.add_argument("--note")

    o = sub.add_parser("opportunities", help="scan the watchlist (or list/add/review/approve/dismiss/complete)")
    o.add_argument("action", nargs="?", default="run",
                   choices=["run", "list", "add", "review", "approve", "dismiss", "complete"])
    o.add_argument("id", nargs="?")
    o.add_argument("--status")
    o.add_argument("--url")
    o.add_argument("--text", help="post caption/text for `add`")
    o.add_argument("--account")
    o.add_argument("--note")

    for name, help_ in [("plan", "write the planning brief; optionally trigger social-trend-scan"),
                        ("generate", "run the configured content-generation hook"),
                        ("analytics", "collect metrics snapshots"),
                        ("weekly-review", "write performance recommendations"),
                        ("token-check", "verify token, warn/refresh before expiry"),
                        ("daily-run", "token-check, publish, comments, opportunities, analytics")]:
        sub.add_parser(name, help=help_)
    sub.add_parser("schedule", help="print crontab lines for this brand")
    lg = sub.add_parser("log", help="show the activity log")
    lg.add_argument("--limit", type=int, default=40)
    lg.add_argument("--category")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.brand:
        print("error: pass --brand <slug> or set INSTAGRAM_BRAND", file=sys.stderr)
        return 2
    try:
        if args.command == "init":
            cmd_init(args)
            return 0
        ctx = load_brand(args.brand, registry_path=args.registry)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    rt = Runtime(ctx)
    try:
        if args.command == "status":
            cmd_status(rt, args)
        elif args.command == "auth-check":
            cmd_auth_check(rt, args)
        elif args.command == "queue":
            cmd_queue(rt, args)
        elif args.command == "publish":
            cmd_publish(rt, args)
        elif args.command == "comments":
            cmd_comments(rt, args)
        elif args.command == "opportunities":
            cmd_opportunities(rt, args)
        elif args.command == "schedule":
            print(crontab_lines(ctx))
        elif args.command == "log":
            cmd_log(rt, args)
        elif args.command in JOBS:
            status, summary = run_job(rt, args.command)
            print(f"{args.command}: {status} — {summary}")
            return 0 if status in ("OK", "SKIPPED") else 1
    except (KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        rt.store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
