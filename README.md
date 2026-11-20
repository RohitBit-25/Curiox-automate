# Curiox Growth Engine

A review queue for Curiox's social engagement. It finds posts worth replying to, drafts up to three comments in the post's language (English, Hindi, Hinglish) and waits for a human to approve one.

**Safety rules (enforced in code, not just config):**
- Nothing is posted without human approval. The app refuses to start if `HUMAN_APPROVAL_REQUIRED=false`, `THIRD_PARTY_AUTO_COMMENT=true` or `DISCOVERY_MODE` is changed.
- Third-party posts are never commented on automatically. You copy the approved text, post it yourself, then click **Mark posted**.
- The only automated writes are replies to comments on Curiox's **own** Instagram posts, through the official API, after approval.
- There is no scraping. A third-party post enters the system only when you paste its link and caption.
- The same text is never posted twice, and every action is written to the audit log.
- Access tokens are encrypted at rest (Fernet) and never returned by the API. A permission or token error pauses the account.

## How it works

1. **Sources:** links you submit (with caption) and comments on your own media (polled every 30 minutes, or pushed by webhook).
2. **Pipeline** (`app/pipeline.py`): the LLM analyses the post, then a relevance score is computed (topic 30%, audience 25%, conversation 20%, quality 15%, recency 10%; threshold 0.65). Giveaways, bait and sensitive posts are rejected. Then the LLM drafts comments using brand knowledge, and deterministic checks run (`app/rules.py`).
3. **Review** (dashboard → Queue): edit, approve or reject. "Why this comment?" shows the reasoning.
4. **Publish** (`app/publisher.py`): own-account replies only, re-validated before every call.

## Run it

```bash
cp .env.example .env
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # -> TOKEN_ENCRYPTION_KEY
# set ANTHROPIC_API_KEY, and DASHBOARD_USERS=admin:<long random token>
docker compose up --build -d
```

- Dashboard: http://localhost:5173. Go to Settings and paste your dashboard token.
- API: http://localhost:8000/docs
- For production on a VPS, put an HTTPS reverse proxy in front. The ports are bound to localhost, and the webhook URL must be public HTTPS.

## Instagram setup

- **Account type:** Curiox must be a professional (Creator) account, using the Instagram API with Instagram Login.
- **Scopes:** `instagram_business_basic` and `instagram_business_manage_comments`.
- **Connect:** paste a long-lived token in Settings. It refreshes automatically every night.
- **Webhook:** `https://<your-host>/api/webhooks/instagram`, with verify token `IG_WEBHOOK_VERIFY_TOKEN`, subscribed to the `comments` field.

## Tests

No Postgres, Redis or LLM needed:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pytest -q
```

## Adding a platform

Implement `app/platforms/base.py:PlatformAdapter` and register it in `app/platforms/__init__.py:ADAPTERS`. The AI pipeline, database and analytics don't depend on any platform.
