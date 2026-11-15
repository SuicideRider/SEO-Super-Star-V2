"""Free-tier LLM waterfall: Gemini, then Groq, then OpenRouter.

With no key configured `generate` returns ok=False and callers fall back to
rule-based output. Nothing here raises.
"""

from __future__ import annotations

import json
import re

import requests

from .config import Settings


class ProviderError(Exception):
    pass


def _check(resp: requests.Response) -> dict:
    if resp.status_code == 429:
        raise ProviderError("rate limit reached")
    if resp.status_code != 200:
        raise ProviderError(f"HTTP {resp.status_code}")
    return resp.json()


def _gemini_call(body: dict, settings: Settings) -> dict:
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{settings.gemini_model}:generateContent")
    data = _check(requests.post(url, json=body, timeout=150,
                                headers={"x-goog-api-key": settings.gemini_api_key}))
    candidates = data.get("candidates") or []
    if not candidates:
        raise ProviderError("empty response")
    return candidates[0]


def _gemini(prompt: str, want_json: bool, settings: Settings) -> str:
    config = {"temperature": 0.4}
    if want_json:
        config["responseMimeType"] = "application/json"
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config}
    # Model "thinking" makes a long brief slow enough to time out, so switch it off.
    # Models that do not accept the setting answer HTTP 400; retry without it.
    try:
        candidate = _gemini_call(
            {**body, "generationConfig": {**config, "thinkingConfig": {"thinkingBudget": 0}}},
            settings)
    except ProviderError as exc:
        if str(exc) != "HTTP 400":
            raise
        candidate = _gemini_call(body, settings)
    return "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []))


def _openai_style(url: str, key: str, model: str, prompt: str, want_json: bool) -> str:
    body = {"model": model, "temperature": 0.4,
            "messages": [{"role": "user", "content": prompt}]}
    if want_json:
        body["response_format"] = {"type": "json_object"}
    data = _check(requests.post(url, json=body, timeout=60,
                                headers={"Authorization": f"Bearer {key}"}))
    return data["choices"][0]["message"]["content"] or ""


def _groq(prompt: str, want_json: bool, settings: Settings) -> str:
    return _openai_style("https://api.groq.com/openai/v1/chat/completions",
                         settings.groq_api_key, settings.groq_model, prompt, want_json)


def _openrouter(prompt: str, want_json: bool, settings: Settings) -> str:
    # Free models do not all accept response_format, so JSON is asked for in the prompt only.
    return _openai_style("https://openrouter.ai/api/v1/chat/completions",
                         settings.openrouter_api_key, settings.openrouter_model, prompt, False)


_PROVIDERS = [("Gemini", "gemini_api_key", _gemini), ("Groq", "groq_api_key", _groq),
              ("OpenRouter", "openrouter_api_key", _openrouter)]


def generate(prompt: str, settings: Settings | None = None, want_json: bool = False) -> dict:
    settings = settings or Settings.from_env()
    out = {"ok": False, "provider": "", "text": "", "attempts": []}
    for name, key_field, provider in _PROVIDERS:
        if not getattr(settings, key_field):
            continue
        try:
            text = provider(prompt, want_json, settings).strip()
        except ProviderError as exc:
            out["attempts"].append({"provider": name, "outcome": str(exc)})
            continue
        except (requests.exceptions.RequestException, ValueError, KeyError,
                IndexError, TypeError) as exc:
            out["attempts"].append({"provider": name, "outcome": exc.__class__.__name__})
            continue
        if not text:
            out["attempts"].append({"provider": name, "outcome": "empty response"})
            continue
        out["attempts"].append({"provider": name, "outcome": "ok"})
        out.update(ok=True, provider=name, text=text)
        return out
    return out


def parse_json(text: str) -> dict | None:
    """Pull a JSON object out of model output that may be wrapped in prose or fences."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def grounded_answer(question: str, settings: Settings) -> dict:
    """A web-grounded answer from Gemini with the sources it cited."""
    if not settings.gemini_api_key:
        return {"ok": False, "error": "no key configured"}
    try:
        candidate = _gemini_call({"contents": [{"parts": [{"text": question}]}],
                                  "tools": [{"google_search": {}}]}, settings)
    except ProviderError as exc:
        return {"ok": False, "error": str(exc)}
    except (requests.exceptions.RequestException, ValueError, KeyError) as exc:
        return {"ok": False, "error": exc.__class__.__name__}
    text = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []))
    sources = []
    for chunk in candidate.get("groundingMetadata", {}).get("groundingChunks", []):
        web = chunk.get("web") or {}
        # The uri is a redirect link; the title carries the real source domain.
        if web.get("uri"):
            sources.append({"url": web["uri"], "domain": (web.get("title") or "").lower()})
    if not text:
        return {"ok": False, "error": "empty response"}
    return {"ok": True, "text": text, "sources": sources}
