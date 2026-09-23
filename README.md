# Social Content Agent

Two layers:

1. **The content brain** — a Claude Code pipeline that goes from "what should this brand
   post about" to "here is the finished caption, hashtags, and image". Generic and
   replicable: onboarding a brand is a scripted interview, not a hand-edit of internal files.
2. **The Instagram operating layer** (`instagram_os/`, opt-in per brand) — publishes what the
   brain made through the official Instagram API, triages comments, surfaces engagement
   opportunities, collects metrics, and feeds recommendations back into planning.

OBSERVE → PLAN → CREATE → VALIDATE → PUBLISH → MONITOR → ENGAGE → MEASURE → LEARN → repeat.

## 1. Architecture

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — what existed before and where the operating layer attaches.
- [`docs/instagram-architecture.md`](docs/instagram-architecture.md) — modules, state machines, decision flows.
- [`CLAUDE.md`](CLAUDE.md) — harness rules (the approval gate, where brand data lives).

```
CLAUDE.md                     harness rules
.claude/skills/               workflows: brand-onboarding, social-trend-scan, social-content-build, remix, ...
.claude/agents/               specialists the skills call
brands/_template/             blank schema for brand files (+ instagram/config.yaml, facts.md templates)
scripts/render_text_card.py   direct image rendering for text-card brands
instagram_os/                 Instagram operating layer (Python)
bin/instagram                 its CLI
tests/                        tests for the operating layer (fake API only)
docs/                         Instagram setup, architecture, API limitations
```

Brand data (voice, palette, posts, Instagram state) never lives in this repo. Each brand has
its own folder elsewhere, registered in the gitignored `.claude/brands.local.json`.

### Content pipeline quick start

1. `/brand-onboarding` — interviews you and creates the brand folder with
   `visual-design-system.md` + `social-content-system.md`.
2. `/social-trend-scan <slug>` — idea board of 3–6 themes. **You pick the clusters.**
3. `/social-content-build <slug> <cluster #>` — finished posts in
   `<brand-path>/content-packages/<YYYY-MM>/<NNN-slug>/`. Mark a post `ready` in that month's
   `_index.md` when you're happy with it.

## 2. Instagram account requirements

Professional account (Business or Creator). A linked Facebook Page only if you want automatic
watchlist monitoring (Facebook Login). Details: [setup §1](docs/instagram-setup.md#1-instagram-account-requirements).

## 3. Meta developer setup

A Meta app of type Business with the Instagram product, the brand account added as a
tester/role. No App Review is needed to operate your own account.
[Setup §2](docs/instagram-setup.md#2-meta-developer-setup).

## 4. Required permissions

Instagram Login: `instagram_business_basic`, `instagram_business_content_publish`,
`instagram_business_manage_comments`, `instagram_business_manage_insights`.
Facebook Login equivalents plus `pages_show_list` / `pages_read_engagement`.
[Setup §3](docs/instagram-setup.md#3-required-permissions).

## 5. Authentication setup

Long-lived Instagram Login token (60 days, refreshable) or a non-expiring Facebook system-user
token. `token-check` warns before expiry and can hand a refreshed token to your secret store;
it never writes tokens to disk. [Setup §4](docs/instagram-setup.md#4-authentication-and-token-lifecycle).

## 6. Environment variables

```
INSTAGRAM_ACCESS_TOKEN=     # required for real API calls
INSTAGRAM_ACCOUNT_ID=       # required for real API calls
META_APP_ID=                # recommended (debug_token, appsecret_proof)
META_APP_SECRET=            # recommended
ANTHROPIC_API_KEY=          # Claude replies/assessments; without it nothing auto-replies
PUBLISH_MODE=               # optional: auto | approval
INSTAGRAM_API_MODE=         # optional: dry_run | live
```

Full list: [setup §5](docs/instagram-setup.md#5-environment-variables). Never put secrets in
`config.yaml` or in this repo.

## 7. Local development

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests          # 100+ tests, fake API client only, no network
```

Everything defaults to `dry_run`: with no credentials, reads come from an empty fake account
and every write is simulated and logged.

## 8. Running the agent

```bash
export PATH="$PWD/bin:$PATH"
instagram --brand presence-project init          # config.yaml + facts.md in the brand folder
instagram --brand presence-project auth-check    # read-only token/account check
instagram --brand presence-project status        # boundaries, queue, pending human actions
instagram --brand presence-project daily-run     # token-check, publish, comments, opportunities, analytics
```

| Command | What it does |
|---|---|
| `plan` | writes `plan-brief.md`; optionally triggers `social-trend-scan` via `scheduler.plan_command` |
| `generate` | runs `scheduler.generate_command` (e.g. a headless Claude Code build) |
| `publish` | ingest `ready` posts → validate → queue → publish due posts |
| `publish approve <content-id> [--override]` / `publish reject` | human publishing decisions |
| `queue [--status ...]` | publication states |
| `comments` / `comments list` / `comments approve <id> [--text]` / `comments reject <id>` | own-media engagement |
| `opportunities` / `opportunities list` / `opportunities add --url --text` / `review|approve|dismiss|complete <id>` | watchlist queue |
| `analytics` | metrics snapshots |
| `weekly-review` | recommendations file |
| `token-check` | token validity / expiry |
| `daily-run` | the daily bundle |
| `log [--category PUBLISH]` | activity log |
| `schedule` | crontab lines |

## 9. Scheduling

Host-agnostic: cron, launchd, GitHub Actions in a private ops repo, or a Claude Code Routine —
anything that can run `bin/instagram --brand <slug> <job>`. Jobs are lock-protected and
recorded. `instagram schedule` prints crontab lines from `scheduler.cron`.
[Setup §8](docs/instagram-setup.md#8-scheduling).

## 10. Publishing modes

`autonomy.publish_post: approval` (default) or `auto`, overridable per run with
`PUBLISH_MODE`. Independent of that: `api.mode: dry_run | live`. Caps and quiet hours are
config (`publishing.max_posts_per_day`, `max_reels_per_week`, `min_hours_between_posts`,
`quiet_hours`, `timezone`). Supported formats: single image, carousel, Reel (the video file
must exist — this project writes beat sheets, not video).

## 11. Comment automation

Claude classifies each new comment on the brand's own posts (positive, question,
disagreement, criticism, spam, sales inquiry, potential customer, partnership, ambiguous,
sensitive) using the brand file and `facts.md`. Confidence ≥ 0.95 → reply automatically;
0.70–0.95 → suggested reply awaiting approval; < 0.70 → escalated, no reply. Human-only
categories, output guardrails (no promises, links, or other @mentions), reply caps, and a
per-thread loop guard apply on top. Every response is persisted and logged.

## 12. Opportunity monitoring

`opportunities.watchlist` (accounts, hashtags, topics). With Facebook Login the monitor reads
watched professional accounts (Business Discovery) and hashtag recent media; with Instagram
Login you add posts by hand. Each opportunity has source, link, summary, why, score, priority,
suggested action/comment, expiry, and status (NEW → REVIEWED → APPROVED → COMPLETED, or
DISMISSED / EXPIRED). **The system never comments on anyone else's post** — you do.

## 13. Analytics

Historical snapshots (never overwritten) of reach, views, likes, comments, shares, saves,
total interactions, engagement rate, and Reels watch time per published post; followers and
account insights per run. Joined to topic, format, hook, CTA, caption, and publish time.

## 14. Human approval workflow

```bash
instagram --brand presence-project status             # what's waiting on you
instagram --brand presence-project queue --status VALIDATED,REQUIRES_REVIEW
instagram --brand presence-project publish approve 2026-09/004-five-ways
instagram --brand presence-project comments list
instagram --brand presence-project comments approve 17912345 --text "Edited reply"
instagram --brand presence-project opportunities list
instagram --brand presence-project opportunities complete 12 --note "commented"
```

Idea-board clusters are still approved in Claude Code, as before.

## 15. Known API limitations

No liking, no commenting on other accounts' posts, no following, no cold DMs, no editing
published posts, no licensed music, no keyword search, media must be at a public HTTPS URL,
JPEG only, 100 API posts per 24 h, 30 hashtag searches per week, Business Discovery and
Hashtag Search only with Facebook Login.
Full list: [`docs/instagram-api-limitations.md`](docs/instagram-api-limitations.md).

## Status

- Content brain: in use; the maintainer's own accounts are the first test brand.
- Instagram operating layer: implemented and tested against a fake API. Not yet run against a
  real account — first rollout is `presence-project` (dry run → approval → selective autonomy),
  then other brands. See [setup §7](docs/instagram-setup.md#7-enable-the-brand-presence-project).

## Open items

- [ ] Run `presence-project` in dry run against the real account for a week; then live with approvals.
- [ ] Decide media hosting for `presence-project` (`command` upload vs `url_prefix` sync).
- [ ] Reels: decide how `reel.mp4` gets produced (the pipeline stops at a beat sheet).
- [ ] Consider comment webhooks (needs a public endpoint) if 30-minute polling is too slow.
