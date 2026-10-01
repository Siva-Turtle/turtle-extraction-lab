"""Minimal OpenRouter chat-completions client (structured JSON output)."""

import httpx

from app.core.config import settings


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
