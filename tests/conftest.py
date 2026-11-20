import os

from cryptography.fernet import Fernet

# Must run before any app import: settings are read at import time.
os.environ.update(
    DATABASE_URL="sqlite+aiosqlite://",
    TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    DASHBOARD_USERS="tester:t0ken",
    IG_APP_SECRET="app-secret",
    IG_WEBHOOK_VERIFY_TOKEN="verify-me",
)
