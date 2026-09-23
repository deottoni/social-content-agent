# Instagram operating layer — architecture

The Claude Code pipeline (skills + agents) is the **brain**: it decides what to say and
makes the post. `instagram_os/` is the **operating layer** around it: it publishes what the
brain made, watches what happens, and feeds results back. It never writes captions or
images and never edits brand files.

```
                 EXISTING BRAND BRAIN  (brand files + Claude Code skills/agents)
                          │
                 social-trend-scan ◄──────── recommendations/latest.md, plan-brief.md
                          │   (human approves clusters)                  ▲
                 social-content-build / remix                            │
                          │  post folder + meta.md, _index.md = ready    │
        ┌─────────────────┴──────────────┐                               │
        ▼                                ▼                               │
   CREATE (ingest)                   OBSERVE                             │
   content.py                        opportunities.py ──► opportunity    │
        │                            (Business Discovery,   queue (human)│
        ▼                             Hashtag Search)                    │
   VALIDATE  validation.py                                               │
        │                                                                │
        ▼                                                                │
   QUEUE ── approval? ── policy.py (autonomy, caps, quiet hours)          │
        │                                                                │
        ▼                                                                │
   PUBLISH  publisher.py ─► media_host.py ─► client.py ─► Instagram API  │
        │                                                                │
        ▼                                                                │
   ENGAGE   engagement.py ─► reasoner.py (Claude) ─► reply | suggest | escalate
        │                                                                │
        ▼                                                                │
   MEASURE  analytics.py  (historical snapshots)                         │
        │                                                                │
        ▼                                                                │
   LEARN    learning.py ─────────────────────────────────────────────────┘
```

Cross-cutting: `config.py` (brand resolution, config, secrets), `store.py` (SQLite),
`activity.py` (audit log), `scheduler.py` (jobs), `cli.py` (`bin/instagram`).

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | Resolves a slug via `.claude/brands.local.json`; deep-merges `<brand>/instagram/config.yaml` over `defaults.yaml`; env overrides; reads secrets from env only; `LOCKED_HUMAN_ACTIONS` |
| `client.py` | `InstagramClient` interface. `GraphInstagramClient` (HTTP, retries with capped exponential backoff for transient errors only, typed errors: `AuthError`, `RateLimitError`, `PermissionDenied`, `TransientError`, `MediaError`, `UnsupportedOperation`), `DryRunClient` (reads pass through, writes simulated), `build_client` |
| `fake.py` | In-memory client with failure injection; used by all tests and by credential-less dry runs |
| `content.py` | Reads the existing post-folder convention; `ready`/`posted` from `_index.md` / `meta.md`; detects image / carousel / Reel; derives hook and CTA |
| `validation.py` | JPEG conversion, aspect/width/size, carousel rules, video checks, caption limits, placeholders, markdown leftovers, blocked terms, reviewer `review: flagged` |
| `media_host.py` | Local file → public HTTPS URL (`none`, `url_prefix`, `command`) |
| `policy.py` | Quiet hours (brand timezone), daily/weekly caps, spacing, autonomy table |
| `publisher.py` | State machine, idempotent publish, reconciliation, retries |
| `reasoner.py` | Claude (`AnthropicReasoner`, structured output) or offline `RulesReasoner`; output guardrails |
| `engagement.py` | Own-media comment triage and replies |
| `opportunities.py` | Watchlist → scored human opportunities |
| `analytics.py` | Metric snapshots for published media and the account |
| `learning.py` | Weekly review → recommendations file |
| `scheduler.py` | Jobs, locks, job records, planning brief, token check, crontab output |
| `store.py` | SQLite schema and state transitions |
| `activity.py` | Activity log (SQLite + readable `activity.log`), secret redaction |

## State (all in the brand folder, never in this repo)

```
<brand-path>/instagram/
  config.yaml          brand overrides (no secrets)
  facts.md             approved facts the reply agent may state
  state.db             SQLite: publications (+events), comments, opportunities, metrics,
                       activity, job_runs, locks, hashtag budget, seen posts
  activity.log         human-readable audit trail
  plan-brief.md        latest planning brief
  recommendations/     <date>.md + latest.md from weekly-review
<brand-path>/content-packages/<YYYY-MM>/<NNN-slug>/_instagram/   JPEGs prepared for upload
```

## Publication state machine

```
DRAFT ──validate──► VALIDATED ──(auto | human approve)──► QUEUED ──claim──► PUBLISHING ──► PUBLISHED (terminal)
  │                     │                                    ▲                │
  └──► REQUIRES_REVIEW ◄┘  (errors, reviewer flag,           │                ├──► QUEUED (transient / rate limit / auth / media not synced yet)
          │                 duplicate content, no host)      │                └──► FAILED (permanent, or attempts exhausted)
          └──(fix → DRAFT, or approve --override)────────────┘
```

Every transition is validated (`models.py`) and recorded in `publication_events`.

### Duplicate-publish protection

1. `content_id` = `<YYYY-MM>/<NNN-slug>` is unique; `PUBLISHED` is terminal.
2. Posts marked `posted` anywhere are never ingested; after a real publish the `_index.md`
   Status cell is flipped to `posted`.
3. Identical caption + media already published under another folder → `REQUIRES_REVIEW`.
4. `QUEUED → PUBLISHING` is an atomic compare-and-set (two runners can't both claim a post);
   jobs are also lock-protected.
5. `media_publish` is never auto-retried. If it errors, recent media are checked for the
   same caption before anything else happens; a crash-stuck `PUBLISHING` row is reconciled
   the same way.
6. Content edited after approval goes back to `DRAFT`.
7. Dry runs never write `PUBLISHED`.

## Comment decision flow

```
new comment on own media
  ├─ own comment / already handled ───────────────────────────────► skip
  ├─ thread already answered by the account ──────────────────────► IGNORED
  ├─ Claude: category, confidence, rationale, reply, risk flags
  │    (brand file sections + facts.md + parent caption + thread; comment is untrusted data)
  ├─ reasoner error ──────────────────────────────────────────────► ESCALATED
  ├─ spam ─────────────────────────────────────────► IGNORED (+ hide if hide_spam_comment: auto)
  ├─ confidence < suggest (0.70) ─────────────────────────────────► ESCALATED (no draft)
  ├─ any blocker ─────────────────────────────────────────────────► PENDING_APPROVAL (draft kept)
  │    autonomy ≠ auto · confidence < auto_reply (0.95) · human-only category
  │    (criticism, disagreement, sensitive, partnership, sales, potential customer, ambiguous)
  │    · risk flags · guardrails (promises, links, hashtags, other @mentions, length)
  │    · thread loop guard · hourly/daily/per-user caps · same text used twice today
  └─ otherwise ───────────────────────────────────────────────────► AUTO_REPLY (sent, persisted, logged)
```

`instagram comments approve <id> [--text ...]` sends a human-approved reply exactly once.

## Opportunity scoring

`score = Σ weight·signal / Σ weight` over `topical_relevance`, `audience_overlap`,
`brand_value`, `comment_confidence` (from Claude), `discussion_activity` (log-scaled comment
count), `recency` (linear decay over the TTL), and `relationship` (your per-account table).
Weights and the HIGH/MEDIUM thresholds are config. Posts below `min_score` or that Claude
marks `skip` are remembered and not re-assessed; at most `max_new_per_run` enter the queue,
highest score first — volume is capped on purpose.

## Autonomy boundaries

| Action | Default | Configurable to |
|---|---|---|
| generate content | approval (hook command) | auto / off |
| validate content | always automatic | — |
| publish supported content | approval | auto (`PUBLISH_MODE=auto`) / off |
| monitor own comments | auto | off |
| reply to qualifying comments | auto, gated per comment | approval / off |
| hide spam | approval (logged only) | auto |
| collect analytics | auto | off |
| generate opportunity suggestions | auto | off |
| plan content (idea board) | approval — clusters are always picked by a human | off |
| comment on third-party posts, like, follow, first DM | **human (locked)** | cannot be changed |
| sensitive / uncertain / partnership comments | human (category + threshold rules) | thresholds and category list |

`instagram status` prints the effective table for a brand.

## Observability

Every decision writes an activity row (`at, category, summary, reason, tool, api_action,
result, error, confidence, approval_required, ref, dry_run`) and a readable block in
`activity.log`:

```
2026-09-23 09:05
PUBLISH
Published “2026-09/004-five-ways-claude” — Instagram media ID: 17900000000000000
  api: POST /{ig-user-id}/media_publish
  approval required: yes
  result: https://www.instagram.com/p/XXXX/

2026-09-23 11:22
CLASSIFICATION
Question / confidence 0.98
  why: asks which step the caption covers; answered by the caption
  tool: reasoner:anthropic
```

Tokens and secrets are redacted before anything reaches the database or log.

## Reasoner

`AnthropicReasoner` calls Claude through the Anthropic SDK (`messages.parse` with a Pydantic
schema, `reasoner.model` default `claude-opus-5`, `effort: low`). A refusal or unparseable
response raises and the comment is escalated. `RulesReasoner` is the offline fallback when
the SDK or key is missing; its confidence is capped at 0.85, so it never auto-replies.
