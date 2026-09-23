# Instagram setup

Step-by-step from nothing to a brand publishing through the operating layer. The first
brand to do this is `presence-project`; every step is identical for any other brand.

## 1. Instagram account requirements

- The account must be a **professional account** (Business or Creator). Personal accounts
  have no API access at all. Switch in the Instagram app: *Settings → Account type and tools*.
- For **Facebook Login** (needed only for automatic watchlist monitoring) the account must
  be linked to a **Facebook Page** you manage.
- Keep two-factor authentication on the Instagram and Meta accounts; the token you create
  can post as the brand.

## 2. Meta developer setup

1. Create a Meta developer account at <https://developers.facebook.com/> and a new app
   (type **Business**).
2. Add the product:
   - **Instagram → API setup with Instagram login** (simplest; no Page needed), or
   - **Instagram → API setup with Facebook login** (enables Business Discovery / Hashtag Search).
3. Add the brand's Instagram account as a tester/role on the app (App Roles → Roles, or
   the *Instagram testers* list). An app used only for accounts that have a role on it
   works in development mode with Standard Access — **no App Review is needed to operate
   your own account**. App Review + Business Verification are required only to serve
   accounts that don't have a role on your app, and for the *Instagram Public Content
   Access* feature that Hashtag Search needs.
4. Note the **App ID** and **App Secret** (App settings → Basic).

## 3. Required permissions

| Purpose | Instagram Login scope | Facebook Login permission |
|---|---|---|
| Read profile + media | `instagram_business_basic` | `instagram_basic`, `pages_show_list`, `pages_read_engagement` |
| Publish | `instagram_business_content_publish` | `instagram_content_publish` |
| Read / reply / hide comments | `instagram_business_manage_comments` | `instagram_manage_comments` |
| Insights | `instagram_business_manage_insights` | `instagram_manage_insights` |
| Business Discovery | — (unavailable) | `instagram_basic` + `pages_read_engagement` |
| Hashtag Search | — (unavailable) | `instagram_basic` + *Instagram Public Content Access* feature |

Request nothing else: no messaging permissions are needed, and least privilege limits what
a leaked token could do.

## 4. Authentication and token lifecycle

**Instagram Login**

1. Generate a token for the tester account in the App Dashboard (*API setup with Instagram
   login → Generate token*), or run the OAuth flow: authorization code → short-lived token
   (≈1 h) → exchange for a **long-lived token (60 days)** with
   `GET https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret=<APP_SECRET>&access_token=<SHORT_TOKEN>`.
2. `INSTAGRAM_ACCOUNT_ID` is the `user_id` returned by `GET https://graph.instagram.com/v25.0/me?fields=user_id,username`.
3. Long-lived tokens can be refreshed once they are at least 24 h old and still valid;
   each refresh gives another 60 days. `instagram token-check` (daily by default) warns
   `api.token_refresh_days_before_expiry` days before expiry and, **only if you configure
   `scheduler.token_sink_command`**, refreshes the token and pipes the new value on stdin to
   that command (e.g. `gh secret set INSTAGRAM_ACCESS_TOKEN --repo you/private-ops` or
   `security add-generic-password -U -s instagram-os -a presence-project -w "$(cat)"`). The
   code never writes a token to disk or to the log. Set `INSTAGRAM_TOKEN_EXPIRES_AT`
   (ISO date) so expiry can be tracked before the first refresh.

**Facebook Login (recommended for unattended servers: a System User token)**

1. In Meta Business Suite → Business settings → Users → **System users**, create a system
   user, assign it the app and the brand's Page/IG asset, and generate a token with the
   permissions above. System-user tokens do not expire (rotate them yourself on a schedule).
2. `INSTAGRAM_ACCOUNT_ID` is the Page's `instagram_business_account.id`:
   `GET https://graph.facebook.com/v25.0/<PAGE_ID>?fields=instagram_business_account`.
3. With `META_APP_ID` + `META_APP_SECRET` set, `token-check` and `auth-check` call
   `debug_token` to show validity, scopes, and expiry, and every request carries an
   `appsecret_proof`.

An expired or revoked token surfaces as `AuthError`: the job stops immediately (no retries),
nothing is marked failed, queued posts stay queued, and the activity log says what to do.

## 5. Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `INSTAGRAM_ACCESS_TOKEN` | for any real API call | long-lived user token or system-user token |
| `INSTAGRAM_ACCOUNT_ID` | for any real API call | the professional account's IG user ID |
| `META_APP_ID` | recommended | `debug_token`, `appsecret_proof` (Facebook Login) |
| `META_APP_SECRET` | recommended | same; never commit it |
| `ANTHROPIC_API_KEY` | for Claude replies/assessments | without it the offline rules reasoner is used and **nothing is auto-replied** |
| `INSTAGRAM_BRAND` | optional | default `--brand` for the CLI |
| `PUBLISH_MODE` | optional | `auto` / `approval` — overrides `autonomy.publish_post` |
| `INSTAGRAM_API_MODE` | optional | `dry_run` / `live` — overrides `api.mode` |
| `INSTAGRAM_LOGIN_TYPE` | optional | `instagram` / `facebook` — overrides `api.login_type` |
| `INSTAGRAM_TOKEN_EXPIRES_AT` | optional | ISO date for expiry warnings |
| `BRANDS_REGISTRY` | optional | alternative path to `brands.local.json` |

Keep them in a file only you can read, outside every repo (e.g.
`~/.config/instagram-os/presence-project.env`, `chmod 600`), or in your scheduler's secret
store (GitHub Actions secrets, 1Password CLI, macOS Keychain). Never put them in
`config.yaml`.

## 6. Media hosting

The API downloads media from a public HTTPS URL; it does not accept local image files.
Pick one in `<brand-path>/instagram/config.yaml`:

```yaml
# Option A — an upload command run at publish time (recommended)
media_host:
  type: command
  # {path} is replaced with the local file; the command must print the public URL last.
  command: "aws s3 cp {path} s3://my-bucket/presence/ --acl public-read >/dev/null && echo https://my-bucket.s3.amazonaws.com/presence/$(basename {path})"

# Option B — you already sync content-packages/ to public storage
media_host:
  type: url_prefix
  base_url: "https://cdn.example.com/presence-project/content-packages"
```

With `url_prefix`, a file that isn't reachable yet (sync lag) keeps the post `QUEUED` and it
is retried on the next run without using up an attempt. Validation writes the JPEG
conversions to `<post>/_instagram/`, so those must be synced too. With `none`, publishing
stops at `REQUIRES_REVIEW` with a clear reason. Dry runs never upload anything.

## 7. Enable the brand (presence-project)

```bash
pip install -r requirements.txt            # pyyaml, pillow, anthropic
export PATH="$PWD/bin:$PATH"               # optional: type `instagram` instead of bin/instagram

instagram --brand presence-project init    # creates <brand-path>/instagram/{config.yaml,facts.md}
$EDITOR <brand-path>/instagram/facts.md    # the only facts replies may state
$EDITOR <brand-path>/instagram/config.yaml # timezone, media_host, watchlist; set enabled: true

set -a; . ~/.config/instagram-os/presence-project.env; set +a
instagram --brand presence-project auth-check   # read-only: account, quota, token scopes
instagram --brand presence-project status        # autonomy boundaries, queue, pending actions
instagram --brand presence-project daily-run     # still dry_run: everything simulated
instagram --brand presence-project log
```

Recommended rollout for the first brand:

1. **Dry run for a week** (`api.mode: dry_run`, the default). Reads are real if the token is
   set; every publish/reply/hide is simulated and logged as `[dry-run]`. Review
   `instagram log`, `instagram comments list`, `instagram queue`.
2. **Go live with approval** (`api.mode: live`, `autonomy.publish_post: approval`,
   `autonomy.reply_to_comment: approval`). Approve each post and reply by hand.
3. **Raise autonomy one action at a time**: `reply_to_comment: auto` (still only at
   confidence ≥ 0.95, non-sensitive categories, guardrails passing), then
   `PUBLISH_MODE=auto` once validation and hosting have been reliable.
4. Once it has run cleanly, onboard the next brand the same way.

## 8. Scheduling

`instagram --brand presence-project schedule` prints crontab lines built from
`scheduler.cron`. Any scheduler that can run a command works: cron, launchd, a systemd
timer, a GitHub Actions `schedule:` workflow in a *private* ops repo (brand folder checked
out there), or a Claude Code Routine. The simplest setup is one line:

```
0 9 * * * . ~/.config/instagram-os/presence-project.env && /path/to/social-content-agent/bin/instagram --brand presence-project daily-run
```

plus `weekly-review` and `plan` once a week. Jobs are lock-protected, so overlapping
schedules are safe.
