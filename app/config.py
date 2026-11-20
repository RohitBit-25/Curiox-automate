from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

WEIGHT_KEYS = {"topic", "audience", "conversation", "quality", "recency"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "sqlite+aiosqlite:///./curiox.db"
    REDIS_URL: str = "redis://localhost:6379/0"
    TOKEN_ENCRYPTION_KEY: str = ""
    DASHBOARD_USERS: str = ""

    LLM_PROVIDER: Literal["anthropic", "openai_compat"] = "anthropic"
    LLM_MODEL: str = "claude-opus-5-5"
    ANTHROPIC_API_KEY: str = ""
    OPENAI_COMPAT_BASE_URL: str = ""
    OPENAI_COMPAT_API_KEY: str = ""

    IG_API_VERSION: str = "v26.0"
    IG_APP_SECRET: str = ""
    IG_WEBHOOK_VERIFY_TOKEN: str = ""

    RELEVANCE_THRESHOLD: float = 0.65
    RELEVANCE_WEIGHTS: dict[str, float] = {
        "topic": 0.30, "audience": 0.25, "conversation": 0.20, "quality": 0.15, "recency": 0.10,
    }

    # These flags exist only to be asserted: the app refuses to boot if they are unsafe.
    HUMAN_APPROVAL_REQUIRED: bool = True
    THIRD_PARTY_AUTO_COMMENT: bool = False
    DISCOVERY_MODE: str = "approved_sources"
    TIMEZONE: str = "Asia/Kolkata"

    @property
    def dashboard_users(self) -> dict[str, str]:
        pairs = (p.split(":", 1) for p in self.DASHBOARD_USERS.split(",") if p.strip())
        return {name.strip(): token.strip() for name, token in pairs}

    @model_validator(mode="after")
    def _safe(self):
        problems = []
        if not self.HUMAN_APPROVAL_REQUIRED:
            problems.append("HUMAN_APPROVAL_REQUIRED must be true")
        if self.THIRD_PARTY_AUTO_COMMENT:
            problems.append("THIRD_PARTY_AUTO_COMMENT must be false")
        if self.DISCOVERY_MODE != "approved_sources":
            problems.append("DISCOVERY_MODE must be approved_sources")
        if set(self.RELEVANCE_WEIGHTS) != WEIGHT_KEYS:
            problems.append(f"RELEVANCE_WEIGHTS keys must be {sorted(WEIGHT_KEYS)}")
        if any(not t for t in self.dashboard_users.values()):
            problems.append("DASHBOARD_USERS has an empty token")
        if problems:
            raise ValueError("unsafe config: " + "; ".join(problems))
        return self


settings = Settings()
