# Vegan Leaf: autonomous Instagram publisher

Publishes one original, branded educational post to the **Vegan Leaf** Instagram account every day at **18:00 Europe/Berlin**. Your PC can be off. There is no runtime AI, no paid service and no custom domain.

```
GitHub Actions (cron 18:00 Europe/Berlin, plus an 18:45 safety net)
  ├─ job "test":    pytest + content validation           ← must pass first
  └─ job "publish": (concurrency group, one at a time)
       1. --build    pick next unused post (state/state.json) → Pillow renders PNG + JPEG → site/
       2. deploy     site/ → GitHub Pages  (https://<you>.github.io/<repo>/images/vl-NNN.jpg)
       3. --publish  verify URL: 200 + image/jpeg + same SHA-256 as rendered
                     → token check → recent-media duplicate check
                     → POST /media → poll status_code → POST /media_publish
                     → record media id in state/state.json
       4. commit     state/state.json → main (bot commit, never contains secrets)
```

**Status:** the code, the 60-post library, the graphics, the workflow and 113 automated tests are done. Instagram calls are mocked in the tests. **Automatic publishing has not been verified against a real account yet.** It is only operational after you complete the setup below and the first live run returns an Instagram media ID (step 12).

---

## Verified free-tier facts (checked 21 Oct 2026)

| Component | Cost | Constraint you must respect |
|---|---|---|
| GitHub Actions | $0. *"free for standard GitHub-hosted runners in public repositories"* | The repo **must be public**. Scheduled runs are best-effort: they can be **delayed**, and at busy times (e.g. the top of the hour) **dropped**. Scheduled workflows in a public repo are **disabled after 60 days without repository activity**; the daily state commit counts as activity. |
| GitHub Pages | $0 on public repos (GitHub Free) | 1 GB site, 100 GB/month *soft* bandwidth, 10-minute deploy timeout. GitHub says Pages is "not intended" as free hosting for commercial businesses such as online stores or SaaS. Hosting one image a day for your own account is low-traffic media hosting, but read GitHub's terms yourself. |
| Instagram Graph API | No fee | Professional (Business/Creator) account only. **JPEG only** for images. 100 API posts per rolling 24 h (one doc section says 50; either way this system uses 1). |
| Custom domain | Not needed | `<user>.github.io/<repo>` is used. |
| Fonts | $0 | Lora and Inter, SIL Open Font License (licenses in `assets/fonts/`). |

**Hashtags:** Instagram began limiting posts to 5 hashtags in December 2025. The API docs still say 30, so captions use the first **5** of each entry's 5–8 hashtags. Change this with the repository variable `MAX_CAPTION_HASHTAGS`.

---

## Project layout

```
.github/workflows/daily-post.yml   scheduler: test → build → Pages deploy → publish → commit state
content/posts.json                 60 original posts (10 categories × 6), with sources for factual claims
src/config.py                      env-only settings; refuses live mode unless complete
src/content_manager.py             schema validation, deterministic selection, caption assembly
src/create_graphic.py              Pillow renderer: 3 templates, auto-fit text, font fallback
src/hosting.py                     builds site/, verifies the public URL (status, type, SHA-256)
src/publish_instagram.py           Graph API client (Bearer header, retries, idempotency, redaction)
src/storage.py                     state/state.json: validated, atomic writes, lock file
src/generate_post.py               CLI
state/state.json                   publication history (committed by the workflow)
tests/                             113 tests; all HTTP is mocked
assets/fonts/                      bundled OFL fonts + licenses
```

Why JSON instead of SQLite for state: runners are temporary, so the state is committed back to git. A JSON file diffs, rebases and can be read on github.com; a binary SQLite file cannot.

---

## Commands

| Command | What it does | Network? | Changes state? |
|---|---|---|---|
| `python -m src.generate_post --validate` | Validate the content library | no | no |
| `python -m src.generate_post --preview` | Render the next post to `out/preview/` | no | no |
| `python -m src.generate_post --preview --id vl-007` | Render one post | no | no |
| `python -m src.generate_post --preview --all` | Render all posts (to review the whole library) | no | no |
| `python -m src.generate_post --dry-run` | Full simulation of today's run (selection, image, caption, alt text) | no | no |
| `python -m src.generate_post --check` | Verify credentials and quota | yes (read-only) | no |
| `python -m src.generate_post --build` | CI step: render and build `site/` | no | no |
| `python -m src.generate_post --publish` | CI step: verify hosting, publish, record | yes | yes |
| `python -m src.generate_post --history` | List published posts | no | no |

`--publish` **refuses** (exit 2) unless `PUBLISH_ENABLED=true`, `IG_USER_ID`, `IG_ACCESS_TOKEN` and `PAGES_BASE_URL` are all set and well-formed. It also needs the image from `--build` to be live on Pages, which is why manual publishing goes through the workflow (*Run workflow → publish*).

Exit codes: `0` success or skipped · `1` runtime/API/hosting error · `2` config or content error · `3` state error or locked · `4` invalid/expired credentials.

---

## Setup

Steps marked **REQUIRED** must be done. Steps marked *optional* are improvements.

### 1. Create the GitHub repository (REQUIRED)
1. github.com → **New repository**, name it e.g. `vegan-leaf-automation`, and set visibility to **Public**. Public is required for free Actions and Pages.
2. Push this folder:
   ```bash
   git init && git add . && git commit -m "Vegan Leaf publisher"
   git branch -M main
   git remote add origin https://github.com/<you>/vegan-leaf-automation.git
   git push -u origin main
   ```
3. Before pushing, run `git status` and confirm there is no `.env` file in the list.

### 2. Install Python locally (optional, only for development)
Install Python 3.12+ from python.org. On Windows, tick *Add python.exe to PATH*.

### 3. Install dependencies (optional, local)
```powershell
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

### 4. Content library (REQUIRED to review, already created)
`content/posts.json` holds 60 posts. Each has an `id`, `category`, `headline`, 3–5 `points`, `caption`, `call_to_action`, 5–8 `hashtags`, `alt_text` and a `source_url` (required for food facts, myths and any entry with `"factual": true`).
- Run `--preview --all` and read every post before going live. You are the publisher.
- To add posts, append entries with the next id (`vl-061` …). Then run `--validate`, which checks missing fields, duplicate IDs and headlines, hashtag format, length limits, missing sources and exaggerated wording ("detox", "miracle", "cure", …).
- When fewer than 14 unused posts remain, the workflow shows a warning. When none remain, it **stops**; it does not repeat posts.
- Optionally set `"template": "steps" | "cards" | "spotlight"` on an entry to override the default template for its category.

### 5. Configure GitHub Pages (REQUIRED)
Repo → **Settings → Pages → Build and deployment → Source: GitHub Actions**. Nothing else; the workflow deploys `site/`, which contains only that day's JPEG.
- The `publish` job runs in the `github-pages` environment, which is created automatically. Its default protection only allows deploys from `main`, which is correct.
- Each deploy replaces the site, so only the current image is hosted. Instagram fetches the image while the post is being created, and the code verifies the bytes are live before it calls Instagram.

### 6. Create the Meta developer app (REQUIRED)
1. Sign in at <https://developers.facebook.com> → **My Apps → Create app**.
2. Choose the **Business** app type (or the "Manage messaging & content on Instagram" use case if the dashboard offers it).
3. Add the **Instagram** product. Pick **one** login path:

| | **Path A (recommended): Facebook Login, system user** | **Path B: Instagram Login** |
|---|---|---|
| Host (`IG_GRAPH_HOST`) | `graph.facebook.com` | `graph.instagram.com` |
| Needs a Facebook Page | yes, linked to the IG account | no |
| Permissions | `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`, `pages_show_list` (Meta may also require `business_management`) | `instagram_business_basic`, `instagram_business_content_publish` |
| Token lifetime | system-user token can be set to **never expire** | **60 days**, refreshable |

You are publishing only to your own account, which you hold a role on, so development-mode/standard access should work without App Review. Confirm this in the app dashboard if Meta asks for review.

### 7. Connect the Instagram professional account (REQUIRED)
1. Instagram app → Settings → **Account type and tools → Switch to professional account** (Creator or Business).
2. Path A only: create a Facebook Page for Vegan Leaf, then link it under Page settings → **Linked accounts → Instagram**. Also add the Page and the IG account to a free **Meta Business portfolio** (business.facebook.com).

### 8. Get publishing credentials (REQUIRED)
**Never paste tokens into chat, issues, commits or screenshots.** Put them only in GitHub Secrets (step 9) or your local `.env`.

**Path A: system user token (does not expire)**
1. business.facebook.com → **Settings → Users → System users → Add** (role: Admin or Employee).
2. **Assign assets**: your app, the Facebook Page and the Instagram account (full control or content permissions).
3. **Generate new token** → choose your app → set expiration to **Never** → tick the permissions from the Path A row above.
4. Find `IG_USER_ID`: in Graph API Explorer with that token, call `GET /me/accounts` to get the Page ID, then `GET /{page-id}?fields=instagram_business_account`. The returned `id` is a long number and is your `IG_USER_ID`.
   - Meta notes that non-expiring tokens are riskier if leaked. If one leaks, revoke it under System users → Revoke tokens.

**Path B: Instagram Login token (60 days)**
1. In the app's Instagram settings, generate an access token for your account. Exchange it for a **long-lived** token (`grant_type=ig_exchange_token`), which lasts 60 days.
2. `IG_USER_ID` is the account's Instagram user ID (call `GET https://graph.instagram.com/v21.0/me?fields=user_id,username`).
3. Set the repository variable `IG_GRAPH_HOST=graph.instagram.com`. See step 14 for renewal.

### 9. Add GitHub Actions secrets and variables (REQUIRED)
Repo → **Settings → Secrets and variables → Actions**

| Kind | Name | Value |
|---|---|---|
| Secret | `IG_USER_ID` | numeric IG account ID |
| Secret | `IG_ACCESS_TOKEN` | token from step 8 |
| Variable | `PUBLISH_ENABLED` | leave **unset** until step 12, then `true` |
| Variable *(optional)* | `IG_GRAPH_HOST` | `graph.instagram.com` for Path B (default `graph.facebook.com`) |
| Variable *(optional)* | `IG_API_VERSION` | default `v21.0`; raise it when Meta deprecates the version |
| Variable *(optional)* | `MAX_CAPTION_HASHTAGS` | default `5` |

Actions automatically masks secret values in logs, and the code never prints them, keeps them out of URLs (they go in an `Authorization: Bearer` header), and redacts them from error messages.

### 10. Enable the scheduled workflow (REQUIRED)
Open the **Actions** tab. If GitHub asks, click **"I understand my workflows, go ahead and enable them"**. The schedule is in `.github/workflows/daily-post.yml`:
```yaml
- cron: "0 18 * * *"
  timezone: "Europe/Berlin"     # GitHub's IANA timezone support handles CET/CEST
- cron: "45 18 * * *"           # safety net if the 18:00 run is delayed or dropped; no-op if already posted
  timezone: "Europe/Berlin"
```
- GitHub only runs schedules from the **default branch**.
- Until `PUBLISH_ENABLED=true`, scheduled runs do a **dry run** and post nothing.
- Expect posts at 18:00–18:20 most days, occasionally later. GitHub documents that scheduled events can be delayed under load.
- Fallback: if the `timezone` key ever stops being honoured, replace both entries with UTC crons and add a guard. In winter (CET) 18:00 is `0 17 * * *` UTC; in summer (CEST) it is `0 16 * * *` UTC. Running both and letting the duplicate-day guard skip the extra one also works.

### 11. Test with dry-run mode (REQUIRED)
1. Actions → **Daily Instagram post → Run workflow → mode: dry-run**. Both jobs should go green. Download the **rendered-post** artifact and check the image.
2. **Run workflow → mode: check**. The log must show `Credentials OK: account @<your handle>`. If not, see step 13.

### 12. Go live and verify the first post (REQUIRED)
1. Set the repository variable `PUBLISH_ENABLED=true`.
2. **Run workflow → mode: publish**.
3. In the log, look for `Verifying hosted image https://<you>.github.io/.../images/vl-001.jpg` and then `Published vl-001 as Instagram media 17…`.
4. Open Instagram and check the post, caption and alt text (Advanced settings → Accessibility).
5. Check that a bot commit `state: record post for <date>` appeared on `main`, and that `--history` lists the post.
6. **Only after this** is automatic publishing confirmed operational. From then on the 18:00 schedule takes over.

### 13. Diagnosing failures
GitHub emails you when a workflow fails. Open the run and read the `ERROR` line.

| Message / exit code | Cause | Fix |
|---|---|---|
| `Live publishing refused: …` (2) | variable or secret missing or malformed | follow the list in the message |
| `ERROR (credentials)` / code 190 (4) | token expired, revoked or for another account | step 14 |
| `code=10` / `code=200` / "permission" | missing `instagram_content_publish` or asset not assigned | step 8, re-generate with the right permissions |
| `image not publicly available … content-type` / `404` | Pages not set to "GitHub Actions", or deploy failed | step 5; re-run |
| `served bytes differ` | Pages CDN still serving an older image | re-run; the check waits ~5 min automatically |
| `container … status ERROR` | Instagram could not fetch or parse the image | open the image URL in a browser; it must be a JPEG |
| `content library invalid` (2) | someone edited `posts.json` badly | run `--validate` locally |
| `every post … has been published` (2) | library exhausted | add posts (step 4) |
| `state file … corrupt` (3) | `state/state.json` damaged | restore it from git history; the run refuses to guess |
| `Could not push state` | the state commit raced another push | harmless: the next run sees the post on Instagram (exact caption match) and records it without posting again |
| Workflow didn't run at all | schedule delayed or dropped, or disabled after 60 days without activity | the 18:45 run catches drops; re-enable it in the Actions tab |

Duplicate protection, in layers: a GitHub concurrency group (one publisher at a time), a lock file, a "this date already recorded" guard, a "post ID already recorded" guard, and a live Instagram check for an identical caption before posting. `media_publish` is never blindly retried; if a publish call fails ambiguously, the code checks the container status first.

### 14. Renewing tokens
- **Path A (non-expiring system user):** nothing to do unless you revoke it. To rotate: generate a new token → update the `IG_ACCESS_TOKEN` secret → revoke the old one.
- **Path B (60 days):** before day 60, call
  `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=<current token>`
  (the token must be at least 24 h old and still valid), then paste the new token into the secret. Put a calendar reminder every 50 days. If it expires, generate a new one (step 8B).
- *Optional improvement:* automate the Path B refresh with a monthly workflow that calls the endpoint and updates the secret using `gh secret set` with a fine-grained personal access token scoped to this repository's secrets. It's still $0, but it adds another credential to protect.
- After any renewal, run **Run workflow → check**.

### 15. Staying at $0 per month
- Keep the repository **public**. Private repos lose free Pages and use metered Actions minutes.
- Use only GitHub-hosted **standard** runners (`ubuntu-24.04`). Larger runners are billed.
- Don't add paid APIs. Content comes from `posts.json` and images from Pillow.
- Don't commit images or large files. Images live only in the per-run Pages artifact; the repo grows by one small JSON line per day.
- The workflow uses about 2–3 runner-minutes per day (free on public repos), and artifacts are kept for 14 days.

---

## Optional improvements
- Write more posts with the same schema and review them with `--preview --all`.
- Automate Path B token refresh (see step 14).
- Add carousel posts (Graph API `CAROUSEL` containers). Not implemented, to keep the first version simple.
- Add a weekly `--check` schedule so token problems surface before the 18:00 run.

## Content and accuracy policy
Posts are educational graphics generated with Pillow; they are not food photography or AI imagery. Nutrition statements are general and cite NHS, NIH Office of Dietary Supplements, Harvard T.H. Chan School of Public Health and the Academy of Nutrition and Dietetics. Sustainability statements cite Our World in Data. There are no invented statistics, product claims, restaurants or reviews. Captions encourage asking a health professional before taking supplements.

## References
- GitHub Actions schedule syntax (timezone, delays, 60-day rule): <https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule>
- GitHub Actions billing: <https://docs.github.com/en/actions/reference/usage-limits-billing-and-administration>
- GitHub Pages limits: <https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits>
- Instagram content publishing: <https://developers.facebook.com/docs/instagram-platform/content-publishing>
- IG User media (`alt_text`, caption limits): <https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media>
- Instagram token refresh: <https://developers.facebook.com/docs/instagram-platform/reference/refresh_access_token>
- System user tokens: <https://developers.facebook.com/docs/business-management-apis/system-users/install-apps-and-generate-tokens>
