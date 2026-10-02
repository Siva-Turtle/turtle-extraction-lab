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

# OpenRouter reasoning effort levels (highest-first, matching OpenRouter docs).
REASONING_EFFORTS = ("max", "xhigh", "high", "medium", "low", "minimal", "none")


def is_configured() -> bool:
    return bool(settings.openrouter_api_key)


def build_chat_payload(
    *, model: str, system: str, user: str, json_schema: dict | None = None,
    schema_name: str = "meeting_extraction",
    reasoning_effort: str | None = None,
) -> dict:
    """Build the EXACT JSON body POSTed to OpenRouter chat-completions.

    Pure function (no I/O, no secrets): ``{"model", "response_format",
    "messages": [{"role": "system", ...}, {"role": "user", ...}]}``.
    ``json_schema`` is the inner OpenAPI-compatible object schema; when
    given, the envelope is a strict ``schema_name`` json_schema
    response_format (``meeting_extraction`` for attribute extraction,
    ``agent_selection`` for the identifier meta-agent), else the legacy
    ``{"type": "json_object"}`` mode. When ``reasoning_effort`` is a
    non-blank string, ``{"reasoning": {"effort": value}}`` is added;
    None/blank leaves the payload byte-identical (no ``reasoning`` key).
    """
    if json_schema is None:
        response_format: dict = {"type": "json_object"}
    else:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
        }
    payload = {
        "model": model,
        "response_format": response_format,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    effort = reasoning_effort.strip() if isinstance(reasoning_effort, str) else ""
    if effort:
        payload["reasoning"] = {"effort": effort}
    return payload


async def complete_json_payload(payload: dict) -> tuple[dict, dict]:
    """POST a prebuilt payload and return (parsed_json, usage).

    usage is always {"prompt_tokens": int, "completion_tokens": int,
    "total_tokens": int, "reasoning_tokens": int}; missing/partial OpenRouter
    ``usage`` blocks become zeros and never raise.
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
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Preserve OpenRouter's JSON error body ({"error": {"code", "message"}})
            # which names the real cause (invalid key, credits, guardrail,
            # moderation, model access). httpx's default message drops it.
            try:
                detail = (resp.text or "").strip()
            except Exception:
                detail = ""
            if detail:
                raise RuntimeError(
                    f"OpenRouter error {resp.status_code}: {detail[:2000]}"
                ) from exc
            raise
        data = resp.json()
    import json as _json

    content = data["choices"][0]["message"]["content"]
    parsed = _json.loads(content) if isinstance(content, str) else content
    return parsed, _extract_usage(data.get("usage"))


async def complete_json(
    *, model: str, system: str, user: str, json_schema: dict | None = None,
    reasoning_effort: str | None = None,
) -> tuple[dict, dict]:
    """Call OpenRouter and return (parsed_json, usage).

    Thin wrapper over build_chat_payload + complete_json_payload so
    existing call sites/tests keep working.

    usage is always {"prompt_tokens": int, "completion_tokens": int,
    "total_tokens": int, "reasoning_tokens": int}; missing/partial OpenRouter
    ``usage`` blocks become zeros and never raise.

    ``json_schema`` is the inner OpenAPI-compatible object schema
    (``{"type": "object", "properties": {...}, ...}``). When given, the call
    uses a ``json_schema`` response_format envelope; when None, it falls back
    to the legacy ``{"type": "json_object"}`` mode.
    """
    return await complete_json_payload(
        build_chat_payload(model=model, system=system, user=user, json_schema=json_schema,
                           reasoning_effort=reasoning_effort)
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
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "reasoning_tokens": 0}
    prompt = _safe_int(usage.get("prompt_tokens"))
    completion = _safe_int(usage.get("completion_tokens"))
    total = _safe_int(usage.get("total_tokens"))
    details = usage.get("completion_tokens_details")
    reasoning: int | None = None
    if isinstance(details, dict) and "reasoning_tokens" in details:
        try:
            n = int(details.get("reasoning_tokens"))  # type: ignore[arg-type]
            if n >= 0:
                reasoning = n
        except Exception:
            reasoning = None
    if reasoning is None:
        try:
            n = int(usage.get("reasoning_tokens"))  # type: ignore[arg-type]
            reasoning = n if n >= 0 else 0
        except Exception:
            reasoning = 0
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total,
            "reasoning_tokens": reasoning}


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


def _model_entry(m: dict) -> dict:
    """Normalize one OpenRouter /models entry to the API shape.

    Returns {"id", "name", "reasoning", "pricing"} where pricing is
    {"prompt": float|None, "completion": float|None} (USD per token).
    """
    pricing_raw = m.get("pricing") if isinstance(m.get("pricing"), dict) else {}
    return {
        "id": m.get("id", ""),
        "name": m.get("name") or m.get("id", ""),
        # Per-model reasoning options (OpenRouter docs: per-model
        # reasoning options; supported_efforts descending, null = all
        # gateway efforts accepted, omitted = no effort selection).
        "reasoning": m.get("reasoning") if isinstance(m.get("reasoning"), dict) else None,
        "pricing": {
            "prompt": _parse_price(pricing_raw.get("prompt")),
            "completion": _parse_price(pricing_raw.get("completion")),
        },
    }


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
    """Return (models, live). Live list from OpenRouter, else curated fallback.

    Each live entry is {"id", "name", "reasoning", "pricing"} where reasoning
    passes through the per-model `reasoning` object when it is a dict, else
    None, and pricing is {"prompt", "completion"} USD per token. Fallback
    entries carry pricing None.
    Per OpenRouter docs: supported_efforts is descending (null = all gateway
    efforts accepted); omitted reasoning = no effort selection exposed.
    """
    if not is_configured():
        return [{**m, "pricing": None} for m in CURATED_MODELS], False
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{settings.openrouter_base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            )
            resp.raise_for_status()
            items = resp.json().get("data", [])
        models = [
            _model_entry(m)
            for m in items if isinstance(m, dict) and m.get("id")]
        models.sort(key=lambda m: m["id"])
        try:
            global _pricing_cache, _pricing_expires_at
            import time as _time
            fresh: dict[str, tuple[float | None, float | None]] = {}
            for entry in models:
                pricing = entry.get("pricing") or {}
                fresh[entry["id"]] = (pricing.get("prompt"), pricing.get("completion"))
            _pricing_cache = fresh
            _pricing_expires_at = _time.time() + _PRICING_TTL_S
        except Exception:
            pass
        return models, True
    except Exception:
        return [{**m, "pricing": None} for m in CURATED_MODELS], False
