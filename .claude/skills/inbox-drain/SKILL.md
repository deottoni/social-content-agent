---
name: inbox-drain
description: Pulls the content-inbox repo (phone-captured Instagram references staged by the capture-reference skill) and processes every pending capture into a real post via social-content-remix, per-brand, then marks each one processed. Trigger on "check my inbox", "drain the inbox", "process what I captured", or "did I send anything from my phone".
---

The Mac-side half of the phone-capture workflow. `capture-reference` (in the separate
`content-inbox` repo) stages raw reference material with the computer possibly asleep;
this skill is what turns those captures into real, on-brand posts once you're back.

## Step 0 — locate and sync the inbox

Read `.claude/inbox.local.json` at this repo's root (find the repo root with
`git rev-parse --show-toplevel` if not already there). If it doesn't exist, ask the
user for their `content-inbox` clone path (offer to `gh repo clone deottoni/content-inbox`
to a sibling folder if they don't have one yet), then save `{"path": "<path>"}` there.

`cd` into that path and `git pull` — always fetch fresh before reading, since captures
may have been pushed from a phone session since the last drain.

## Step 1 — find pending captures

Walk `<inbox-path>/inbox/<brand-slug-or-_unsorted>/*/` — every capture folder that is
NOT inside a `processed/` subfolder is pending. If there are none, say so plainly and
stop.

## Step 2 — resolve each capture's brand

Read `.claude/brands.local.json` (same file `social-content-remix` uses) for the list
of real registered brands.
- If the capture's folder-level brand label matches a registered slug, use it directly.
- If it's `_unsorted` or doesn't match any registered brand, show the user the
  capture's `reference.md` content and ask which registered brand it belongs to (or
  whether to skip it). Never guess silently — an unresolved brand means no brand file
  to check tone/pillars against.

Group resolved captures by brand so brand files only need to be read once per brand,
even if there are several captures for it.

## Step 3 — build each capture into a post

For each resolved capture, run the same process as `social-content-remix` Steps 1-5,
using the capture folder as the reference material in place of a live phone message:
- The capture's `screenshot-*.png` files and `reference.md` (url, note, extracted/
  transcribed content) are the "reference" `social-content-remix` Step 1 would
  otherwise ingest directly from the user.
- Steps 2-5 (synthesize mini-cluster → content agents → save into
  `<brand-path>/content-packages/...` with `source/`, `meta.md`, `_index.md` updated →
  present) are unchanged — see `social-content-remix/SKILL.md` for the full detail,
  don't duplicate divergent logic here.
- The capture's own `note` field carries whatever instruction the user gave at capture
  time (e.g. "make it shorter") — treat it the same as a live note.

## Step 4 — mark processed

After a capture is successfully built into a post, move its folder from
`<inbox-path>/inbox/<brand-slug>/<capture>/` to
`<inbox-path>/inbox/<brand-slug>/processed/<capture>/`. Once every pending capture in
this run has been handled (built or explicitly skipped), commit and push the inbox
repo — this is what keeps re-running drain from reprocessing the same captures, even
from a fresh clone.

## Step 5 — summarize

Report what was built, grouped by brand: post name/number and a one-line recap per
capture. Call out anything skipped (unresolved brand, nothing extractable) separately
so it's clear those still need attention.
