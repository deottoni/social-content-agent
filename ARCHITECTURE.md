# Architecture

What exists in this repo, and where the Instagram operating layer (`instagram_os/`)
attaches to it. For the Instagram layer's own design, see
[`docs/instagram-architecture.md`](docs/instagram-architecture.md).

## 1. The content brain (existed before the Instagram layer)

The content-generation system is a **Claude Code harness**, not a Python application.
Its "agent architecture" is a set of skills (workflows) that call subagents (single-job
specialists). Everything runs inside a Claude Code session; nothing runs on a server.

| Layer | Where | What it does |
|---|---|---|
| Harness rules | `CLAUDE.md` | Pipeline order, the human approval gate, where brand data lives |
| Skills (workflows) | `.claude/skills/*/SKILL.md` | `brand-onboarding`, `design-system-teardown`, `design-system-ideation`, `social-trend-scan`, `social-content-build`, `social-content-remix`, `brand-drift-audit`, `inbox-drain` |
| Agents (specialists) | `.claude/agents/*.md` | `trend-researcher`, `topic-ideator`, `theme-synthesizer`, `caption-writer`, `hashtag-strategist`, `image-prompt-engineer`, `brand-consistency-reviewer` |
| Brand schema | `brands/_template/` | `social-content-system.md` (audience, voice, pillars, mode, channels, format rules, no-go list) and `visual-design-system.md` (palette, type, logo, imagery, `## Rendering` method) |
| Image rendering | `scripts/render_text_card.py` | Pillow renderer for flat-background text cards (1080x1350 PNG by default), used when a brand declares `Rendering → Method: direct` |

### Pipeline

```
brand-onboarding  →  social-trend-scan  →  HUMAN APPROVAL GATE  →  social-content-build
 (once per brand)     (idea board)          (pick clusters)         (captions, hashtags,
                                                                     image prompts/renders)
social-content-remix  — ad-hoc path: reference post in, on-brand post out (no gate)
inbox-drain           — processes phone captures from the private content-inbox repo via remix
brand-drift-audit     — compares remix history to brand files, proposes (never applies) edits
```

### Where things live

- **Brand guidelines / design system:** never inside this repo. Each brand has a sibling
  folder outside the repo, resolved by slug through `.claude/brands.local.json`
  (gitignored, machine-local: `{"<slug>": "/abs/path"}`). Skills read
  `<brand-path>/social-content-system.md` and `<brand-path>/visual-design-system.md`.
- **Content storage / state:** plain files in the brand folder.
  - `<brand-path>/idea-boards/<YYYY-MM-DD>.md` — Stage 2 output.
  - `<brand-path>/content-packages/<YYYY-MM>/<NNN>-<slug>/` — one folder per post:
    `caption.txt` (paste-ready caption + hashtags), `image.png` / `image-<variant>.png` /
    `slide-N.png` or `image-prompt.txt`, optional `source/` and `meta.md` (remixes).
  - `<brand-path>/content-packages/<YYYY-MM>/_index.md` — month calendar with a
    `Status` column (`ready` / `posted`) that a human edited by hand.
  - There was no database, no queue, and no record of anything published.

### What did not exist

| Concern | State before the Instagram layer |
|---|---|
| Publishing / Instagram API | None. `CLAUDE.md` stated the system never posts. |
| Video generation | None. Reels existed only as beat sheets (text). |
| Scheduling / orchestration | None. Every skill is run on demand in a Claude Code session. `social-content-system.md` has an informational "suggested run frequency" only. |
| CLI | None beyond `scripts/render_text_card.py` and the design-teardown asset embedder. |
| Tests | None. |
| Configuration / env vars | None. The only configuration is the brand markdown files plus the two `.local.json` registries. |
| MCP integrations | None configured in the repo. Skills use the session's built-in WebSearch/WebFetch and the Artifact tool. |
| Analytics / learning | None automated. `brand-drift-audit` is the only feedback loop, and it looks at remix history, not performance. |

## 2. The Instagram operating layer (added)

`instagram_os/` is a small Python package that wraps the brain; it does not replace or
duplicate any skill or agent.

- **CREATE stays in Claude Code.** The publisher consumes finished post folders exactly
  as `social-content-build` / `social-content-remix` write them. It never writes
  captions or images itself.
- **The approval gate stays.** The `plan` job produces a planning brief and (only if a
  command is configured) triggers `social-trend-scan`; picking clusters is still human.
- **Brand files stay authoritative.** Comment replies and opportunity suggestions are
  written against `social-content-system.md`. The learning loop writes
  *recommendations* to `<brand-path>/instagram/recommendations/latest.md`, which
  `social-trend-scan` reads as advisory input — it never edits the brand files.
- **All state lives in the brand folder** (`<brand-path>/instagram/`: `config.yaml`,
  `state.db`, `activity.log`, `recommendations/`), consistent with the rule that brand
  data never lives in this repo.
- **Per-brand opt-in.** Nothing runs for a brand unless its
  `<brand-path>/instagram/config.yaml` exists and sets `enabled: true`. The first brand
  to enable it is the maintainer's `presence-project`; no brand name is hard-coded.

| Stage | Module |
|---|---|
| OBSERVE | `opportunities.py` (watchlist via Business Discovery / Hashtag Search) |
| PLAN | `scheduler.py` `plan` job + `learning.py` recommendations |
| CREATE | existing skills (unchanged) → `content.py` ingests their output |
| VALIDATE | `validation.py` |
| PUBLISH | `publisher.py` → `client.py` |
| MONITOR / ENGAGE | `engagement.py` (own-media comments) |
| MEASURE | `analytics.py` |
| LEARN | `learning.py` |
| Cross-cutting | `config.py`, `policy.py` (autonomy boundaries, caps, quiet hours), `store.py` (SQLite), `activity.py` (audit log), `reasoner.py` (Claude), `cli.py` |
