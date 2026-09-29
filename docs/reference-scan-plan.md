# Reference scan — design plan

Status: **planned, not built** (2026-09-28). Build on top of the `instagram_os/` branch; it
reuses its API client, brand config, and CLI.

## Why

Posting isn't the bottleneck. The bottleneck is turning *ideas* into posts, and today the
ideas come from a human finding reference posts one at a time and sending them in. The goal:
the user spends only a few minutes per batch. The system harvests dozens of real posts from
chosen accounts and hashtags, reads them (images and captions), clusters them, and proposes a
numbered plan of the brand's own posts. The user approves by number, and the existing Stage 4
agents build them.

Two human gates stay, as everywhere in this repo:
1. **Plan approval.** Nothing gets built until the user picks post numbers from the plan.
2. **Publish approval.** Built posts are written as `ready`. Only a human sets `approved`, or
   the user posts by hand.

## Pipeline

```
instagram --brand <slug> harvest        (Python, official API, no Claude)
   └─ <brand-path>/references/<account>/<media-id>/  slide-N.jpg, caption.txt, post.json
reference-scan <slug>                    (skill)
   ├─ harvest (new posts only)
   ├─ reference-analyst ×N               (agent; ~8-10 refs per call, in parallel)
   │     reads images + captions → format, hook, structure, core idea, why it works,
   │     pillar fit, on-brand angle, keep/skip
   ├─ theme-synthesizer (post-plan mode) → 3-6 clusters × 3-8 concrete post proposals
   └─ idea-boards/<date>-reference-plan.md   → STOP (gate 1)
social-content-build <slug> <post numbers>  (existing skill, extended)
   └─ content-packages/<YYYY-MM>/<NNN>-<slug>/ as today, origin: reference, status ready
```

## Components

1. **Harvester** (`instagram_os/research.py`, CLI `harvest`). This step is read-only.
   - **Data sources:** accounts go through Business Discovery (Creator and Business accounts only). Topics go through Hashtag Search (`top_media`; 30 unique hashtags per rolling 7 days).
   - **Fields requested:** `caption, media_type, media_url, children{media_url,media_type}, permalink, timestamp, like_count, comments_count`, plus the account's `followers_count`.
   - **Images:** download immediately, because the media URLs expire.
   - **Formats:** images and carousels only for now. Reels are skipped by default (`include_reels: false`).
   - **Deduplication:** by media id. A post harvested once is never downloaded again.
   - **Ranking:** each post's engagement against its own account's median, so posts that beat their account's usual numbers rise to the top.
   - **Connection:** needs `api.login_type: facebook`, meaning a Facebook Page linked to the IG account. Works even while publishing is `enabled: false`.
2. **Brand config** (`<brand-path>/instagram/config.yaml` → `research:`):
   - `accounts`
   - named `shortlists` (e.g. `core: [a, b]`)
   - `hashtags`
   - `per_account` (default 20)
   - `include_reels`
   - `min_outperformance`

   The lists are brand data, so they live in the brand folder. The template in this repo stays empty.
3. **`reference-analyst` agent** (new).
   - Reads a batch of reference folders.
   - Returns structured notes per post. It must never reproduce a post's copy verbatim.
   - Batches keep dozens of images out of the main session's context.
4. **`theme-synthesizer`, post-plan mode.** Same agent, extra output. Under each cluster it lists concrete proposals, numbered across the whole plan:
   - working title
   - format (single or carousel with N slides)
   - draft hook
   - one-line angle
   - pillar
   - source reference links
5. **`reference-scan` skill.** Orchestrates items 1–4, writes the plan and stops. Arguments:
   - a brand slug
   - optional: a shortlist name, extra hashtags, a post count, and a target number of proposals (default about 20)
6. **`social-content-build` extension.** It accepts post numbers from a reference plan and builds each one like a remix:
   - The linked references are inspiration only: extract the idea and structure, never clone text or images.
   - Each post gets `origin: reference` and `source:` links in `meta.md`.
   - Posts that share a cluster are built in one agent pass, not one pass per post.
7. **Later:** a weekly `harvest` entry in `scheduler.cron`, so references pile up with no effort. The plan itself stays on demand, or runs as a headless weekly run that leaves a plan waiting.

## Decisions (2026-09-29)
- The first brand creates a Facebook Page and links it, so it moves to `api.login_type: facebook`.
  Until the token exists, build and test everything against the fake client.
- Defaults are accepted: about 20 proposals per plan and about 20 recent posts per account.
  Images and carousels only; Reels stay out for now.
- The account list, shortlists and per-account copy/reject notes are brand data. They live in the
  brand's private folder, never here.
- `reference-analyst` must be given the brand's copy/reject notes so it doesn't pull in a
  rejected tone or aesthetic from a reference account.

## Limits (official API)
- Personal (non-professional) accounts are unreadable.
- No keyword search: hashtags only, 30 unique per week.
- About 200 Business Discovery calls per hour.
- Reel videos are not downloaded in v1.
