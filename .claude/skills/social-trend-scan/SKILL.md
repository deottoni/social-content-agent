---
name: social-trend-scan
description: Stage 1-2 of the social content pipeline for an onboarded brand — researches or ideates (depending on the brand's mode) and clusters results into 3-6 candidate post themes for human approval. Takes a brand slug. Trigger on "scan for post ideas for <brand>", "what should <brand> post about", "run the trend scan for <brand>".
---

Produce an idea board for one brand and stop for human approval. Do not proceed to Stage 4 (content-build) in the same run, even if the user seems likely to approve everything — the approval gate is a separate, explicit step.

## Step 0 — load the brand

Resolve `<slug>`'s folder from `.claude/brands.local.json` at this repo's root (find the repo root with `git rev-parse --show-toplevel` if not already there). If the file doesn't exist or doesn't list `<slug>`, tell the user this brand hasn't been onboarded here yet and offer to run `brand-onboarding` first — don't try to proceed without it.

Read `<brand-path>/social-content-system.md`. Note the `mode` field and the default lookback window.

## Step 1 — source material

- `mode: trend-driven` → invoke the `trend-researcher` agent with this brand's file and the lookback window (or an override if the user gave one this run).
- `mode: evergreen` → invoke the `topic-ideator` agent with this brand's file.
- `mode: hybrid` → invoke both; `topic-ideator` is primary, `trend-researcher`'s findings are supplementary material handed to it.

## Step 2 — cluster

Pass the raw findings/topics (and the brand file) to the `theme-synthesizer` agent. It returns the idea board: 3-6 numbered clusters, each with a theme name, rationale, and per-channel angle sketch.

## Step 3 — save and present

Write the idea board to `<brand-path>/idea-boards/<YYYY-MM-DD>.md`. Present it to the user in the chat as the numbered list theme-synthesizer produced, and ask which cluster(s) to pursue (they may approve several, none, or ask for a redirect/re-run with different guidance).

Stop here. `social-content-build` is a separate, explicitly-invoked next step once the user has picked cluster(s).
