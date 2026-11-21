# Curiox OS

The command center for growing the **@curiox.os** Instagram account: plan and publish content, turn comments into conversations, reply to DMs, find small-business clients, and see what's working.

It automates everything Instagram's official API allows on **your own account**. Anything involving other people's accounts stays manual by design.

| Runs on its own | You do it, the AI prepares it | Never |
|---|---|---|
| Publishing approved posts at their scheduled time | Comments on other people's posts (AI drafts, you post) | Auto-commenting on others' posts |
| Keyword DMs: comment "GUIDE" on your post, get a DM | Outreach to small businesses (AI audits + drafts the pitch) | Follow/unfollow automation |
| Replies to comments on your posts, and to DMs people send you (when set to Auto) | Approving content | Cold DMs, scraping, browser bots |
| Daily Insights snapshots and post analytics | | |

Why the line is there: Instagram's API has no way to act on other accounts, and bot tools that do get accounts action-blocked or disabled, which is the opposite of growth.

## What's inside

- **Today**: what needs you now, the next 48 hours of posts, follow-ups due, setup checklist.
- **Content**: AI plans a week from your pillars and a brief, then drafts each post with scored hooks (26 formulas), a lint-checked caption, and sized hashtags. You approve, add media, and it publishes.
- **Engagement**: a daily round of creators and posts to comment on, with AI-drafted comments you post yourself (one click copies the comment and opens the post), plus a streak. Every morning at 6:30 the creators on your list are read with Instagram's Business Discovery API and their new posts are drafted. That needs the `instagram_manage_insights` permission on the token; without it, paste posts by hand. A **Follow list** gives you 15 accounts a day to follow by hand and tracks who followed back.
- **Inbox**: DMs with drafted replies, sent only inside Instagram's 24-hour window; comments on your posts.
- **Keyword DMs**: comment-to-DM rules. One DM per person per keyword, within 7 days of the comment, capped per hour.
- **Leads**: kanban for small businesses. Add one or paste many handles at once. Their public business profile is read when Instagram allows it (otherwise paste it); the AI scores it on a 12-part rubric, finds a problem Curiox can solve, and drafts the pitch and two follow-ups.
- **Analytics**: followers, reach, engagement rate, best pillars and formats, keyword DMs, leads by stage.
- **Brand & voice**: who Curiox is, pillars, always/never rules, example captions, and verified facts. Every prompt uses it.

## Safety model

- **Third-party content is never automated.** The publisher refuses it, and the Instagram adapter has no method to comment on others' media, follow, or message someone who hasn't messaged or commented first.
- **Auto-reply needs two keys:** the mode set to Auto in Settings (every change is audited), and `AUTOMATION_PAUSED=false`. Even then, a reply only goes out if every check passes: a simple bucket (question, thanks, useful point; never leads, complaints or sensitive topics), AI confidence above your threshold, no quality flags or facts to verify, AI-tell check, hourly cap, and per-person cap. Otherwise it waits in the queue.
- **Content always needs a human approval.**
- **No duplicates:** the same text is never posted twice, and each keyword DM goes to a person once.
- **Account protection:** tokens are Fernet-encrypted and never returned by the API. A dead token or a permission error pauses the account.
- **Startup checks:** the app refuses to start if `HUMAN_APPROVAL_REQUIRED=false`, `THIRD_PARTY_AUTO_COMMENT=true`, or `DISCOVERY_MODE` is changed.

## Run it

```bash
cp .env.example .env
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # -> TOKEN_ENCRYPTION_KEY
# set DASHBOARD_USERS=admin:<long random token>, ANTHROPIC_API_KEY, and later the Instagram values
docker compose up --build -d
```

Open http://localhost:5173, go to **Settings**, and paste your dashboard token. Everything works without keys: features that need a key explain exactly what to add.

## Keys and where they go

| What | Env / place | Unlocks |
|---|---|---|
| Anthropic API key | `ANTHROPIC_API_KEY` | Planning, drafts, comment and DM replies, lead research |
| Instagram long-lived token | Settings → Instagram account | Comments, publishing, insights, keyword DMs |
| Meta app secret + verify token | `IG_APP_SECRET`, `IG_WEBHOOK_VERIFY_TOKEN` | Real-time comments and DMs (webhooks) |
| Public https URL | `PUBLIC_BASE_URL` | Publishing (Instagram fetches media from `/media/…`) |

**Meta app (new apps use Instagram API with Unified Login; older Instagram-Login tokens still work):**
- Token: Use cases → Instagram API → API setup with Unified login → Generate access tokens (add @curiox.os as an Instagram tester first). It starts with `EAA`.
- Permissions: `instagram_basic`, `instagram_manage_comments`, `instagram_content_publish`, `instagram_manage_messages`, `instagram_manage_insights` (add insights via Edit permissions).
- `IG_APP_SECRET` is the **Meta app secret** (App settings → Basic).
- Webhook URL: `https://<your-host>/api/webhooks/instagram`, subscribed to `comments` and `messages`. Connecting the account in Settings subscribes it automatically.
- Comment webhooks need the app Live with Advanced Access.
- Media formats: images must be JPEG. Video must be MP4 or MOV.

**Production:** put an HTTPS reverse proxy in front and route `/api/*` and `/media/*` to the API (port 8000) and everything else to the dashboard (port 5173). Ports are bound to localhost.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pytest -q
cd dashboard && npm ci && npm run build
```

The tests need no Postgres, Redis, network, or keys.

## Layout

- `app/`: FastAPI and Celery.
  - Domain modules: `content.py`, `automation.py` (funnels and auto gate), `inbox.py`, `growth.py` (leads and engagement round), `insights.py`, `pipeline.py` (comment drafting), `publisher.py`.
  - Rules: `rules.py` and `craft.py`.
  - Instagram: `platforms/instagram.py`.
- `prompts/`: every prompt, as plain text.
- `app/vendor/ig_agent/`: MIT-licensed hook scorer, AI-tell detector, caption lint and profile rubric (see `NOTICE`).
- `dashboard/`: React and TypeScript, with no UI library.
- New platforms: implement `app/platforms/base.py:PlatformAdapter` and register it in `app/platforms/__init__.py`.
