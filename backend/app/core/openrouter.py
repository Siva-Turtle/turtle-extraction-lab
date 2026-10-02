"""Minimal OpenRouter client: chat-completions (structured JSON) + model listing."""

import time

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


def build_chat_payload(
    *, model: str, system: str, user: str, json_schema: dict | None = None,
    schema_name: str = "meeting_extraction",
) -> dict:
    """Build the EXACT JSON body POSTed to OpenRouter chat-completions.

    Pure function (no I/O, no secrets): ``{"model", "response_format",
    "messages": [{"role": "system", ...}, {"role": "user", ...}]}``.
    ``json_schema`` is the inner OpenAPI-compatible object schema; when
    given, the envelope is a strict ``schema_name`` json_schema
    response_format (``meeting_extraction`` for attribute extraction,
    ``agent_selection`` for the identifier meta-agent), else the legacy
    ``{"type": "json_object"}`` mode.
    """
    if json_schema is None:
        response_format: dict = {"type": "json_object"}
    else:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
        }
    return {
        "model": model,
        "response_format": response_format,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }


async def complete_json_payload(payload: dict) -> tuple[dict, dict]:
    """POST a prebuilt payload and return (parsed_json, usage).

    usage is always {"prompt_tokens": int, "completion_tokens": int,
    "total_tokens": int}; missing/partial OpenRouter ``usage`` blocks become
    zeros and never raise.
    """
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{settings.openrouter_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
    import json as _json

    content = data["choices"][0]["message"]["content"]
    parsed = _json.loads(content) if isinstance(content, str) else content
    return parsed, _extract_usage(data.get("usage"))


async def complete_json(
    *, model: str, system: str, user: str, json_schema: dict | None = None,
) -> tuple[dict, dict]:
    """Call OpenRouter and return (parsed_json, usage).

    Thin wrapper over build_chat_payload + complete_json_payload so
    existing call sites/tests keep working.

    usage is always {"prompt_tokens": int, "completion_tokens": int,
    "total_tokens": int}; missing/partial OpenRouter ``usage`` blocks become
    zeros and never raise.

    ``json_schema`` is the inner OpenAPI-compatible object schema
    (``{"type": "object", "properties": {...}, ...}``). When given, the call
    uses a ``json_schema`` response_format envelope; when None, it falls back
    to the legacy ``{"type": "json_object"}`` mode.
    """
    return await complete_json_payload(
        build_chat_payload(model=model, system=system, user=user, json_schema=json_schema)
    )


def _extract_usage(usage: object) -> dict:
    """Normalize an OpenRouter ``usage`` block to ints; never raises."""

    def _safe_int(v: object) -> int:
        try:
            n = int(v)  # type: ignore[arg-type]
            return n if n >= 0 else 0
        except Exception:
            return 0

    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt = _safe_int(usage.get("prompt_tokens"))
    completion = _safe_int(usage.get("completion_tokens"))
    total = _safe_int(usage.get("total_tokens"))
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


# --- Model pricing (USD per token), 24h in-process cache -------------------

_PRICING_TTL_S = 24 * 3600
_pricing_cache: dict[str, tuple[float | None, float | None]] = {}
_pricing_expires_at: float = 0.0


def clear_pricing_cache() -> None:
    """Reset the in-process pricing cache (tests)."""
    global _pricing_cache, _pricing_expires_at
    _pricing_cache = {}
    _pricing_expires_at = 0.0


def _parse_price(v: object) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)  # type: ignore[arg-type]
        return f if f >= 0 else None
    except Exception:
        return None


async def get_model_pricing(model: str) -> tuple[float | None, float | None]:
    """Return (prompt_price, completion_price) USD per token, or (None, None).

    Resolved via a cached ``GET /models`` call (entries carry
    ``pricing.prompt``/``pricing.completion`` decimal strings per token).
    On ANY failure returns (None, None) — cost becomes null, never blocks.
    """
    global _pricing_cache, _pricing_expires_at
    try:
        if not settings.openrouter_api_key:
            return (None, None)
        now = time.time()
        if now < _pricing_expires_at and model in _pricing_cache:
            return _pricing_cache[model]
        if now < _pricing_expires_at and _pricing_cache and model not in _pricing_cache:
            # Cache valid but model absent — genuinely unknown, no refetch.
            return (None, None)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{settings.openrouter_base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            )
            resp.raise_for_status()
            items = resp.json().get("data", [])
        fresh: dict[str, tuple[float | None, float | None]] = {}
        for m in items or []:
            if not isinstance(m, dict) or not m.get("id"):
                continue
            pricing = m.get("pricing") if isinstance(m.get("pricing"), dict) else {}
            fresh[m["id"]] = (_parse_price(pricing.get("prompt")), _parse_price(pricing.get("completion")))
        _pricing_cache = fresh
        _pricing_expires_at = now + _PRICING_TTL_S
        return _pricing_cache.get(model, (None, None))
    except Exception:
        return (None, None)


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
