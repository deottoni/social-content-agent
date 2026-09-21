---
name: brand-drift-audit
description: On-demand review of a brand's accumulated remix history (social-content-remix outputs) against its declared social-content-system.md pillars/voice and visual-design-system.md rules, flagging recurring patterns (3+ occurrences) the brand files don't yet reflect and proposing specific edits — never auto-applies, always gated by explicit user approval per suggestion. Trigger on "audit my brand", "review my remixes", "does this still match our brand guide", "check for brand drift".
---

Compare what the user has actually been sending/liking (via `social-content-remix`)
against what the brand's own files currently declare, and surface concrete edits worth
considering. This is diagnostic, not corrective — it never writes to
`social-content-system.md` or `visual-design-system.md` without explicit per-suggestion
approval.

## Step 0 — load context

Resolve `<slug>`'s folder from `.claude/brands.local.json` at this repo's root (find
the repo root with `git rev-parse --show-toplevel` if not already there). If the file
doesn't exist, doesn't list `<slug>`, or `<slug>` wasn't given and more than one brand
is registered, ask which brand rather than guessing.

Read `<brand-path>/social-content-system.md` and `<brand-path>/visual-design-system.md`.

## Step 1 — aggregate

Scan `<brand-path>/content-packages/**/meta.md` for entries with `origin: remix`
across every month. For each, pull its `pillar`, `confidence`, and date, and read the
matching `source/` material for context on what the user actually sent.

If there are no remix entries yet, say so and stop — there's nothing to audit until
`social-content-remix` has been used a few times.

## Step 2 — compare

Look for recurring divergence between the aggregated remix pattern and what the brand
files currently declare:
- Topics/pillars that keep showing up as `unmapped` or low-confidence in the remix
  history but aren't in `social-content-system.md`'s content pillars.
- A pillar's declared blend/frequency weighting that the actual remix mix contradicts
  (e.g. the file calls something "occasional" but it's the most-remixed topic).
- Visual choices (colorway, format) the remixes consistently favor over
  `visual-design-system.md`'s stated defaults.

Set a minimum-occurrence bar: **3+ remixes** showing the same pattern before it's
worth flagging. A single outlier post is noise, not drift — don't propose a brand-file
edit off of one example.

## Step 3 — report

A numbered list of concrete suggested edits, each citing the specific post numbers
(`<YYYY-MM>/<NNN>`) as evidence — e.g. "1. Add a pillar for X — posts 004, 009, 012 all
remix this topic and it isn't currently listed." If nothing crosses the bar, say
plainly that no changes are suggested. This audit isn't required to always find
something, and shouldn't manufacture a finding to seem useful.

## Step 4 — gate

Ask which of the numbered suggestions (if any) the user wants applied. Apply only the
ones they approve, one edit per accepted suggestion, and show what changed in each
brand file afterward. Never batch-apply everything by default, and never touch a brand
file for a suggestion that wasn't explicitly accepted.
