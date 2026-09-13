# Social Content Agent

A pipeline that goes from "what should this brand post about" to "here is the finished caption, hashtags, and image prompt" — stopping short of actually posting. A human always reviews the theme choices and does the posting.

Built generic and replicable from the start: onboarding a new brand or client is a scripted interview, not a hand-edit of internal files.

## Quick start

1. **Onboard a brand** (once per brand/client):
   `/brand-onboarding` — interviews you and writes `brands/<slug>/visual-design-system.md` + `brands/<slug>/social-content-system.md`.
   If the brand has a live site and you have the `design-system-teardown` skill available, onboarding will offer to use it for the visual file instead of asking colors/fonts manually.

2. **Get post ideas**:
   `/social-trend-scan <slug>` — researches or ideates (depending on the brand's `mode`), clusters into themes, shows you an idea board. Approve, reject, or redirect.

3. **Build the actual content**:
   `/social-content-build <slug> <cluster #>` — writes captions, hashtags, and image-gen prompts per platform for the cluster(s) you approved. Copy-paste from `brands/<slug>/content-packages/`.

## Layout

```
CLAUDE.md                          harness rules — read this for how the pipeline is meant to run
.claude/skills/                    the three skills above
.claude/agents/                    the specialist agents each skill calls
brands/_template/                  blank schema for the two brand files — copy is done by brand-onboarding, not by hand
brands/<slug>/                     one real folder per onboarded brand
```

## Status

Andre's own accounts are the first test brand — not the design target. If anything in a skill or agent file only makes sense for one brand, that's a bug in this project, not a brand-specific customization.

No image-generation or posting integration is wired in yet. Stage 4's default output is always copy-paste-ready text; see `CLAUDE.md` and `.claude/agents/image-prompt-engineer.md` for the current thinking on when a direct render (Canva autofill, an image-gen API) would be worth adding per brand.

Real brand folders (`brands/<slug>/`, everything but `_template/`) are gitignored — this repo is the public, generic framework; onboarded brand/client data stays local and private.

## Open items

- [ ] Run `/brand-onboarding` on a real brand end-to-end and watch the rest of the pipeline (`social-trend-scan` → approval → `social-content-build`) run against it. Use this to find where the interview questions or agent prompts are wrong or underspecified before onboarding an actual client.
- [ ] Once a few real content packages exist, decide the image path for v2 (manual prompt handoff vs. an image-gen API vs. Canva Brand Template + Autofill) based on which format (photographic vs. text/card) actually dominates.
