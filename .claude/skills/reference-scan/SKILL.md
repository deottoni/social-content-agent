---
name: reference-scan
description: Harvests real reference posts from a brand's chosen Instagram accounts and hashtags (official API, read-only), has reference-analyst read them in parallel batches, and has theme-synthesizer turn the notes into a numbered post plan (3-6 clusters × concrete post proposals) written to <brand-path>/idea-boards/ — then stops for human approval. Takes a brand slug, plus optionally a shortlist name, extra hashtags, posts per account, and a target number of proposals (default ~20). Trigger on "run the reference scan for <brand>", "harvest references for <brand>", "plan posts from the reference accounts", "what can we learn from <shortlist> this week".
---

Turn dozens of real reference posts into a numbered plan of the brand's own posts, and stop at the approval gate. Like `social-trend-scan`, this is Stage 2: never continue into building posts in the same run, even if the user seems likely to approve everything. Design notes: `docs/reference-scan-plan.md`.

The references are inspiration only. Nothing in this pipeline reproduces another account's copy or images; it learns the idea, format, and structure.

## Step 0 — load the brand

Resolve `<slug>`'s folder from `.claude/brands.local.json` at this repo's root (find the repo root with `git rev-parse --show-toplevel` if not already there). If it isn't registered, offer `brand-onboarding` first.

Read `<brand-path>/social-content-system.md`, `<brand-path>/visual-design-system.md`, and — if it exists — `<brand-path>/references/notes.md` (the user's per-account take/reject calls). Read `research:` in `<brand-path>/instagram/config.yaml` to see the accounts, shortlists, and hashtags. If there's no `research:` section or it's empty, stop and ask the user which accounts (and which shortlist) to use, then add them there — they are brand data and never go into this repo.

The `instagram` CLI is `bin/instagram` in this repo. Run it with `.venv/bin/python bin/instagram` if `.venv/` exists, otherwise `python3 bin/instagram`.

## Step 1 — harvest (new posts only)

```bash
.venv/bin/python bin/instagram --brand <slug> harvest [--shortlist <name>] [--hashtag a,b] [--per-account N]
```

Pass through whatever the user asked for this run (shortlist, extra hashtags, posts per account); otherwise run with no flags (all accounts plus `research.hashtags`). Read-only. Posts already harvested are never downloaded again.

Report the source table briefly — especially accounts marked `unreadable` (personal accounts, or wrong usernames) and anything `skipped` for the hashtag budget. If harvest fails outright (no token, `login_type` not `facebook`, rate limit), say why, and carry on with Step 2 only if there are unanalyzed references already on disk; otherwise stop and point at `docs/instagram-setup.md`.

## Step 2 — pick what to analyze

```bash
.venv/bin/python bin/instagram --brand <slug> references --unanalyzed --json
```

This lists every reference no analyst has read yet, ranked by `outperformance` (engagement ÷ the account's own median). Analyze up to about 60 per run, taking the top of the list; if there are more, say how many were left for the next run.

## Step 3 — reference-analyst, in parallel batches

Split the selection into batches of 8-10 folders and invoke one `reference-analyst` per batch, all in parallel (a single message with several agent calls). Mix accounts within a batch rather than giving one agent a single account, so the `patterns:` line is useful. Give each call:
- the absolute folder paths for its batch,
- the paths to `social-content-system.md`, `visual-design-system.md`, and `references/notes.md` (if present).

Don't open the reference images yourself — keeping them inside the analyst calls is what keeps this session small.

When the analysts return, save their combined notes to `<brand-path>/references/analysis/<YYYY-MM-DD>.md` (append a `-2`, `-3` suffix if the file exists), then mark those media ids analyzed:

```bash
.venv/bin/python bin/instagram --brand <slug> references --mark-analyzed <id,id,...>
```

## Step 4 — the post plan

Invoke `theme-synthesizer` in **post-plan mode** with: all `keep` notes from Step 3, the brand's `social-content-system.md`, `references/notes.md` (if present), and the target number of proposals (the user's number, or about 20). If the user asked to replan from earlier analysis too, also pass the relevant files from `<brand-path>/references/analysis/`.

## Step 5 — save and present (gate)

Write `<brand-path>/idea-boards/<YYYY-MM-DD>-reference-plan.md`:
- a short header: date, which shortlist/hashtags were harvested, how many references were read and kept, unreadable accounts;
- the plan exactly as `theme-synthesizer` returned it (clusters, then continuously numbered post proposals, each with its source links).

Present the plan in chat as a numbered list and ask which post numbers to build (the user may pick several, none, or ask for changes). Stop here. Building is `social-content-build <slug> <post numbers>` from this plan — a separate, explicitly invoked step.
