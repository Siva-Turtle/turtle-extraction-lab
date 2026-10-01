"""Minimal OpenRouter client: chat-completions (structured JSON) + model listing."""

import httpx

from app.core.config import settings

# Sensible defaults shown when no API key is configured (or the live list
# cannot be reached). Free text is always allowed — this list is a shortcut.
CURATED_MODELS = [
    {"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet 4"},
    {"id": "anthropic/claude-haiku-4", "name": "Claude Haiku 4"},
    {"id": "openai/gpt-4o", "name": "GPT-4o"},
    {"id": "openai/gpt-4o-mini", "name": "GPT-4o mini"},
    {"id": "google/gemini-2.0-flash-001", "name": "Gemini 2.0 Flash"},
    {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat"},
    {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Llama 3.3 70B"},
    {"id": "qwen/qwen-2.5-72b-instruct", "name": "Qwen 2.5 72B"},
]


def is_configured() -> bool:
    return bool(settings.openrouter_api_key)


async def complete_json(*, model: str, system: str, user: str) -> dict:
    """Call OpenRouter and return the parsed JSON object from the response."""
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{settings.openrouter_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()
    import json as _json

    content = data["choices"][0]["message"]["content"]
    return _json.loads(content) if isinstance(content, str) else content


async def fetch_models() -> tuple[list[dict], bool]:
    """Return (models, live). Live list from OpenRouter, else curated fallback."""
    if not is_configured():
        return CURATED_MODELS, False
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{settings.openrouter_base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            )
            resp.raise_for_status()
            items = resp.json().get("data", [])
        models = [{"id": m.get("id", ""), "name": m.get("name") or m.get("id", "")}
                  for m in items if m.get("id")]
        models.sort(key=lambda m: m["id"])
        return models, True
    except Exception:
        return CURATED_MODELS, False
