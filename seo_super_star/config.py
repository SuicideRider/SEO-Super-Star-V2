"""Runtime settings. Every key is optional: the engine runs with none set."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    pagespeed_api_key: str = ""
    jina_api_key: str = ""
    db_path: str = os.path.join("data", "seo_super_star.db")
    timeout: int = 15
    # Only tests and local development should turn this on.
    allow_private_hosts: bool = False

    @classmethod
    def from_env(cls, overrides: dict | None = None) -> "Settings":
        """Build settings from environment variables, then apply overrides.

        Blank override values are ignored so an empty sidebar field does not
        wipe out a key that was configured in the environment.
        """
        settings = cls(
            pagespeed_api_key=os.environ.get("PAGESPEED_API_KEY", ""),
            jina_api_key=os.environ.get("JINA_API_KEY", ""),
            db_path=os.environ.get("SEO_DB_PATH", cls.db_path),
        )
        for key, value in (overrides or {}).items():
            if value and hasattr(settings, key):
                setattr(settings, key, value)
        return settings


def service_status(settings: Settings) -> list[dict]:
    """Which services run at full quota and which run in fallback mode."""
    return [
        {
            "service": "HTML / DOM auditor",
            "mode": "Full",
            "detail": "No key needed.",
        },
        {
            "service": "JS-rendered extraction",
            "mode": "Full quota" if settings.jina_api_key else "Keyless",
            "detail": "Reader key set." if settings.jina_api_key
            else "Shared keyless rate limit; falls back to static HTML.",
        },
        {
            "service": "Core Web Vitals (PageSpeed)",
            "mode": "Full quota" if settings.pagespeed_api_key else "Keyless",
            "detail": "API key set." if settings.pagespeed_api_key
            else "Shared keyless quota; may be rate-limited.",
        },
        {
            "service": "Database",
            "mode": "Local file",
            "detail": f"SQLite at {settings.db_path}. Cloud database arrives in phase 4.",
        },
    ]
