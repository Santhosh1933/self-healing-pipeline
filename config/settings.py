"""Typed application settings loaded from environment variables."""

from functools import lru_cache
import os
from pydantic import BaseModel, SecretStr


class Settings(BaseModel):
    """Runtime settings for AutoHeal-DataEngine."""

    gemini_api_key: SecretStr
    github_token: SecretStr
    repo_name: str
    github_base_url: str = "https://api.github.com"
    github_branch: str = "main"
    max_repair_attempts: int = 3
    pytest_timeout_seconds: int = 300
    sandbox_image: str = "autoheal-pyspark-validator:local"
    classifier_model: str = "gemini-3.6-flash"
    reasoning_model: str = "gemini-3.6-flash"

    @classmethod
    def from_environment(cls) -> "Settings":
        """Build settings and fail clearly when required secrets are absent."""
        values = {"gemini_api_key": os.getenv("GEMINI_API_KEY"), "github_token": os.getenv("GITHUB_TOKEN"), "repo_name": os.getenv("REPO_NAME")}
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
        values.update({
            "github_base_url": os.getenv("GITHUB_BASE_URL", "https://api.github.com"),
            "github_branch": os.getenv("GITHUB_BRANCH", "main"),
            "sandbox_image": os.getenv("SANDBOX_IMAGE", "autoheal-pyspark-validator:local"),
            "classifier_model": os.getenv("AUTOHEAL_CLASSIFIER_MODEL", "gemini-3.6-flash"),
            "reasoning_model": os.getenv("AUTOHEAL_REASONING_MODEL", "gemini-3.6-flash"),
        })
        return cls(**values)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached environment settings."""
    return Settings.from_environment()
