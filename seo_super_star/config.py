"""Runtime settings. Every key is optional: the engine runs with none set."""

from __future__ import annotations

import os
from dataclasses import dataclass

# Setting name -> environment variable. Also the list of secrets the host may supply.
ENV_KEYS = {
    "pagespeed_api_key": "PAGESPEED_API_KEY",
    "jina_api_key": "JINA_API_KEY",
    "tavily_api_key": "TAVILY_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
    "groq_api_key": "GROQ_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "gemini_model": "GEMINI_MODEL",
    "groq_model": "GROQ_MODEL",
    "openrouter_model": "OPENROUTER_MODEL",
    "db_path": "SEO_DB_PATH",
    "database_url": "DATABASE_URL",
    "google_service_account_json": "GOOGLE_SERVICE_ACCOUNT_JSON",
    "gsc_site_url": "GSC_SITE_URL",
    "google_sheet_id": "GOOGLE_SHEET_ID",
}


@dataclass
class Settings:
    pagespeed_api_key: str = ""
    jina_api_key: str = ""
    tavily_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    # Free-tier model names change; override them with environment variables.
    gemini_model: str = "gemini-3.5-flash-lite"
    groq_model: str = "openai/gpt-oss-120b"
    openrouter_model: str = "openrouter/free"
    db_path: str = os.path.join("data", "seo_super_star.db")
    # Optional cloud Postgres, e.g. postgresql://user:password@host/dbname
    database_url: str = ""
    # Optional Google service account (the whole JSON key file as text). It is
    # used for live Search Console data and for syncing history to a Google Sheet.
    google_service_account_json: str = ""
    gsc_site_url: str = ""
    google_sheet_id: str = ""
    timeout: int = 15
    # Only tests and local development should turn this on.
    allow_private_hosts: bool = False

    @classmethod
    def from_env(cls, overrides: dict | None = None) -> "Settings":
        """Build settings from environment variables, then apply overrides.

        Blank values are ignored so an empty sidebar field does not wipe out a
        key that was configured in the environment.
        """
        settings = cls()
        for name, variable in ENV_KEYS.items():
            if os.environ.get(variable):
                setattr(settings, name, os.environ[variable])
        for key, value in (overrides or {}).items():
            if value and hasattr(settings, key):
                setattr(settings, key, value)
        return settings

    @property
    def llm_providers(self) -> list[str]:
        names = [("Gemini", self.gemini_api_key), ("Groq", self.groq_api_key),
                 ("OpenRouter", self.openrouter_api_key)]
        return [name for name, key in names if key]


def service_status(settings: Settings) -> list[dict]:
    """Which services run at full quota and which run in fallback mode."""
    llms = settings.llm_providers
    google = bool(settings.google_service_account_json)
    grounded = [n for n, k in (("Tavily", settings.tavily_api_key),
                               ("Gemini", settings.gemini_api_key)) if k]
    return [
        {"service": "HTML / DOM auditor", "mode": "Full", "detail": "No key needed."},
        {"service": "JS-rendered extraction",
         "mode": "Full quota" if settings.jina_api_key else "Keyless",
         "detail": "Reader key set." if settings.jina_api_key
         else "Shared keyless rate limit; falls back to static HTML."},
        {"service": "Core Web Vitals (PageSpeed)",
         "mode": "Full quota" if settings.pagespeed_api_key else "Keyless",
         "detail": "API key set." if settings.pagespeed_api_key
         else "Shared keyless quota; may be rate-limited."},
        {"service": "Keyword autocomplete and trends", "mode": "Keyless",
         "detail": "No key exists for these; trends are best effort."},
        {"service": "Live search results",
         "mode": "Full quota" if settings.tavily_api_key else "Keyless",
         "detail": "Tavily key set; scraping is the fallback." if settings.tavily_api_key
         else "Scraping DuckDuckGo, then Bing; can be blocked."},
        {"service": "AI writing (briefs)",
         "mode": "Full" if llms else "Fallback",
         "detail": f"Using {', then '.join(llms)}." if llms
         else "No AI key: briefs use a rule-based template."},
        {"service": "AI citation monitor",
         "mode": "Full" if grounded else "Fallback",
         "detail": f"AI answers from {', then '.join(grounded)}." if grounded
         else "No AI key: checks search results only, not AI answers."},
        {"service": "Live Search Console data",
         "mode": "Full" if google and settings.gsc_site_url else "Fallback",
         "detail": f"Connected to {settings.gsc_site_url}." if google and settings.gsc_site_url
         else "No Google service account or property set: CSV upload and demo data."},
        {"service": "Google Sheet sync",
         "mode": "Full" if google and settings.google_sheet_id else "Off",
         "detail": "Sheet configured." if google and settings.google_sheet_id
         else "No sheet configured: CSV downloads only."},
        {"service": "Database",
         "mode": "Cloud" if settings.database_url else "Local file",
         "detail": "Cloud Postgres; falls back to the local file if unreachable."
         if settings.database_url
         else f"SQLite at {settings.db_path}. Resets when a free host restarts."},
    ]
