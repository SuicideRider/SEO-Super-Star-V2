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
    gemini_model: str = "gemini-flash-latest"
    groq_model: str = "llama-3.3-70b-versatile"
    openrouter_model: str = "openrouter/free"
    db_path: str = os.path.join("data", "seo_super_star.db")
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
        {"service": "Database", "mode": "Local file",
         "detail": f"SQLite at {settings.db_path}. Cloud database arrives in phase 4."},
    ]
