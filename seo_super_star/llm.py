"""Free-tier LLM waterfall: Gemini, then Groq, then OpenRouter.

With no key configured `generate` returns ok=False and callers fall back to
rule-based output. Nothing here raises.
"""

from __future__ import annotations

import json
import re
import time

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


# Tried in order when the configured model is no longer served (HTTP 404).
# Lite models answer fast; the larger Flash models think first and can time out.
GEMINI_FALLBACKS = ("gemini-3.1-flash-lite", "gemini-flash-lite-latest")
GROQ_FALLBACKS = ("openai/gpt-oss-20b", "llama-3.1-8b-instant")


def _with_fallbacks(first: str, others: tuple[str, ...], call):
    """Run call(model) for each model until one is not answered with HTTP 404."""
    models = list(dict.fromkeys([first, *others]))
    for index, model in enumerate(models):
        try:
            return call(model)
        except ProviderError as exc:
            if str(exc) != "HTTP 404" or index == len(models) - 1:
                raise


def _gemini_call(body: dict, settings: Settings) -> dict:
    def call(model: str) -> dict:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        return _check(requests.post(url, json=body, timeout=60,
                                    headers={"x-goog-api-key": settings.gemini_api_key}))
    data = _with_fallbacks(settings.gemini_model, GEMINI_FALLBACKS, call)
    candidates = data.get("candidates") or []
    if not candidates:
        raise ProviderError("empty response")
    return candidates[0]


def _gemini(prompt: str, want_json: bool, settings: Settings) -> str:
    # The output cap matters: in JSON mode the model can otherwise pad its reply
    # with whitespace until the request times out.
    config = {"temperature": 0.4, "maxOutputTokens": 8192}
    if want_json:
        config["responseMimeType"] = "application/json"
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config}
    if isinstance(want_json, dict):
        # A fixed schema keeps the reply short. A model that rejects it answers
        # HTTP 400; retry with plain JSON mode.
        try:
            candidate = _gemini_call(
                {**body, "generationConfig": {**config, "responseSchema": want_json}}, settings)
        except ProviderError as exc:
            if str(exc) != "HTTP 400":
                raise
            candidate = _gemini_call(body, settings)
    else:
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
    url = "https://api.groq.com/openai/v1/chat/completions"

    def call(model: str) -> str:
        try:
            return _openai_style(url, settings.groq_api_key, model, prompt, want_json)
        except ProviderError as exc:
            # Some models reject JSON mode; the prompt already asks for JSON.
            if str(exc) != "HTTP 400" or not want_json:
                raise
            return _openai_style(url, settings.groq_api_key, model, prompt, False)
    return _with_fallbacks(settings.groq_model, GROQ_FALLBACKS, call)


def _openrouter(prompt: str, want_json: bool, settings: Settings) -> str:
    # Free models do not all accept response_format, so JSON is asked for in the prompt only.
    return _openai_style("https://openrouter.ai/api/v1/chat/completions",
                         settings.openrouter_api_key, settings.openrouter_model, prompt, False)


_PROVIDERS = [("Gemini", "gemini_api_key", _gemini), ("Groq", "groq_api_key", _groq),
              ("OpenRouter", "openrouter_api_key", _openrouter)]


def generate(prompt: str, settings: Settings | None = None,
             want_json: bool | dict = False) -> dict:
    """want_json may be True, or a response schema that Gemini will enforce."""
    settings = settings or Settings.from_env()
    out = {"ok": False, "provider": "", "text": "", "attempts": []}
    for name, key_field, provider in _PROVIDERS:
        if not getattr(settings, key_field):
            continue
        started = time.monotonic()
        try:
            text = provider(prompt, want_json, settings).strip()
        except ProviderError as exc:
            out["attempts"].append({"provider": name, "outcome": str(exc)})
            continue
        except requests.exceptions.RequestException as exc:
            out["attempts"].append({"provider": name, "outcome": (
                f"{exc.__class__.__name__} after {time.monotonic() - started:.0f}s")})
            continue
        except (ValueError, KeyError, IndexError, TypeError) as exc:
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
