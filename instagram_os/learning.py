"""Content intelligence: what is working, what isn't, what to make more/less of.

Output is a recommendations file, never an edit to the brand files — those stay
authoritative. `social-trend-scan` reads `<brand-path>/instagram/recommendations/latest.md`
as advisory input when building the next idea board (same spirit as brand-drift-audit:
propose, never auto-apply).
"""
import re
import statistics
from collections import Counter, defaultdict
from datetime import timedelta

from .analytics import engagement_rate
from .policy import _tz
from .store import parse_iso, utcnow


def hook_style(hook):
    h = (hook or "").strip().lower()
    if not h:
        return "none"
    if h.endswith("?"):
        return "question"
    if re.match(r"^(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b", h):
        return "number/list"
    if h.startswith(("how ", "how to", "why ", "what ")):
        return "how/why/what"
    if re.search(r"\b(stop|don't|never|mistake|wrong)\b", h):
        return "contrarian/warning"
    return "statement"


def cta_type(cta):
    c = (cta or "").lower()
    for label, pat in [("save", r"\bsave\b"), ("share", r"\b(share|send)\b"), ("comment", r"\bcomment|\?\s*$"),
                       ("follow", r"\bfollow\b"), ("link", r"link in bio"), ("dm", r"\bdm\b")]:
        if re.search(pat, c):
            return label
    return "none"


def caption_length_bucket(caption):
    n = len(caption or "")
    return "short (<300)" if n < 300 else ("medium (300-1000)" if n < 1000 else "long (1000+)")


def daypart(hour):
    return "morning (5-11)" if 5 <= hour < 11 else "midday (11-15)" if hour < 15 else \
        "afternoon (15-19)" if hour < 19 else "evening (19-23)" if hour < 23 else "night (23-5)"


class LearningLoop:
    def __init__(self, ctx, store, activity):
        self.ctx, self.store, self.log = ctx, store, activity
        self.cfg = ctx.config["learning"]

    def dataset(self, now=None):
        now = now or utcnow()
        tz = _tz(self.ctx.config["publishing"].get("timezone", "UTC"))
        cutoff = now - timedelta(days=self.cfg["lookback_days"])
        rows = []
        for pub in self.store.publications(["PUBLISHED"]):
            published = parse_iso(pub["published_at"])
            if not pub["media_id"] or published < cutoff:
                continue
            m = self.store.latest_metrics(pub["media_id"])
            if not m:
                continue
            er = m.get("engagement_rate") or engagement_rate(m)
            local = published.astimezone(tz)
            rows.append({
                "content_id": pub["content_id"], "er": er, "reach": m.get("reach"), "saved": m.get("saved"),
                "shares": m.get("shares"), "views": m.get("views"), "comments": m.get("comments"),
                "features": {
                    "topic": pub["pillar"] or pub["topic"] or "unknown",
                    "format": (pub["content_type"] or "").lower(),
                    "hook style": hook_style(pub["hook"]),
                    "CTA": cta_type(pub["cta"]),
                    "caption length": caption_length_bucket(pub["caption"]),
                    "time of day": daypart(local.hour),
                    "weekday": local.strftime("%A"),
                    "origin": pub["origin"] or "pipeline",
                },
                "published": published,
            })
        return rows

    def analyze(self, now=None):
        rows = [r for r in self.dataset(now) if r["er"] is not None]
        out = {"n": len(rows), "groups": {}, "winners": [], "losers": [], "untested": [], "median_er": None}
        if not rows:
            return out
        median = statistics.median(r["er"] for r in rows)
        out["median_er"] = median
        min_n = self.cfg["min_posts_per_group"]
        for dim in rows[0]["features"]:
            groups = defaultdict(list)
            for r in rows:
                groups[r["features"][dim]].append(r["er"])
            stats = {}
            for value, ers in groups.items():
                mean = statistics.mean(ers)
                lift = (mean / median) if median else None
                stats[value] = {"n": len(ers), "mean_er": mean, "lift": lift}
                if len(ers) >= min_n and lift is not None and len(groups) > 1:
                    if lift >= 1.2:
                        out["winners"].append((dim, value, lift, len(ers)))
                    elif lift <= 0.8:
                        out["losers"].append((dim, value, lift, len(ers)))
            out["groups"][dim] = stats
        out["winners"].sort(key=lambda w: -w[2])
        out["losers"].sort(key=lambda w: w[2])
        seen_formats = set(out["groups"].get("format", {}))
        out["untested"] = [f for f in ("image", "carousel", "reels") if f not in seen_formats]
        out["top_posts"] = sorted(rows, key=lambda r: -r["er"])[:3]
        out["bottom_posts"] = sorted(rows, key=lambda r: r["er"])[:3]
        return out

    def audience_signals(self, days=30):
        since = (utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
        rows = self.store.conn.execute(
            "SELECT category, text FROM comments WHERE created_at >= ? AND category IS NOT NULL", (since,)).fetchall()
        cats = Counter(r["category"] for r in rows)
        questions = [r["text"] for r in rows if r["category"] == "question"][:8]
        return cats, questions

    def cadence(self, days=28):
        pubs = self.store.published_since(utcnow() - timedelta(days=days))
        return len(pubs) / (days / 7)

    def report(self, now=None):
        now = now or utcnow()
        a = self.analyze(now)
        cats, questions = self.audience_signals()
        per_week = self.cadence()
        lines = [f"# Instagram recommendations — {now.date().isoformat()}", "",
                 "*Advisory input for `social-trend-scan` / `topic-ideator`. Generated by `instagram weekly-review` "
                 "from published-post metrics. The brand files remain authoritative: nothing here changes "
                 "voice, pillars, or visual rules — propose those edits through `brand-drift-audit` if a pattern holds.*", ""]
        enough = a["n"] >= self.cfg["min_posts_total"]
        lines += ["## Data", f"- Posts with metrics in the last {self.cfg['lookback_days']} days: {a['n']}",
                  f"- Median engagement rate (likes+comments+shares+saves / reach): "
                  f"{a['median_er']:.2%}" if a["median_er"] is not None else "- Median engagement rate: n/a",
                  f"- Posting cadence (last 4 weeks): {per_week:.1f} posts/week", ""]
        if not enough:
            lines += [f"**Not enough data yet** — conclusions start at {self.cfg['min_posts_total']} posts with metrics. "
                      "Keep posting within the brand's normal mix; the sections below are observations, not advice.", ""]

        def fmt(items):
            return [f"- **{dim}: {value}** — {lift:.1f}x the median engagement rate ({n} posts)" for dim, value, lift, n in items]

        lines += ["## What is working — do more of", *(fmt(a["winners"][:6]) or ["- Nothing clears the bar yet (≥1.2x median, ≥"
                                                                              f"{self.cfg['min_posts_per_group']} posts)."]), ""]
        lines += ["## What is not working — do less of", *(fmt(a["losers"][:6]) or ["- Nothing clearly underperforms yet."]), ""]

        topics_more = [v for d, v, _, _ in a["winners"] if d == "topic"]
        hooks = [v for d, v, _, _ in a["winners"] if d == "hook style"]
        ctas = [v for d, v, _, _ in a["winners"] if d == "CTA"]
        lines += ["## Suggestions for the next idea board",
                  f"- Topics to explore further: {', '.join(topics_more) or 'no clear leader — keep rotating pillars'}",
                  f"- Formats to test: {', '.join(a['untested']) or 'all core formats have data'}",
                  f"- Hooks to test: {', '.join(hooks) if hooks else 'try a question hook vs. a number/list hook on the same pillar'}",
                  f"- CTA patterns: {', '.join(ctas) if ctas else 'test one save-CTA vs. one comment-CTA'}"]
        best_time = a["groups"].get("time of day", {})
        if best_time:
            top = max(best_time.items(), key=lambda kv: kv[1]["mean_er"])
            lines.append(f"- Posting time observation: {top[0]} has the best mean engagement ({top[1]['n']} posts) — "
                         f"observation only, not yet a rule")
        lines.append("")
        lines += ["## Audience response (own-post comments, last 30 days)"]
        if cats:
            lines += [f"- {k.replace('_', ' ')}: {v}" for k, v in cats.most_common()]
            if questions:
                lines += ["", "Recurring questions (candidate post topics):", *[f"- “{q[:140]}”" for q in questions]]
        else:
            lines.append("- No classified comments yet.")
        lines.append("")
        if a.get("top_posts"):
            lines += ["## Top posts", *[f"- {r['content_id']} — {r['er']:.2%}" for r in a["top_posts"]], ""]
        return "\n".join(lines)

    def write(self, now=None):
        now = now or utcnow()
        text = self.report(now)
        out_dir = self.ctx.instagram_dir / "recommendations"
        out_dir.mkdir(parents=True, exist_ok=True)
        dated = out_dir / f"{now.date().isoformat()}.md"
        dated.write_text(text)
        (out_dir / "latest.md").write_text(text)
        self.log.record("LEARNING", "Weekly performance review written", tool="learning.weekly_review",
                        result=str(dated))
        return dated
