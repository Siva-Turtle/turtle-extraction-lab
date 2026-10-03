"""Minimal OpenRouter client: chat-completions (structured JSON) + model listing."""

import json
import time

import httpx

from app.core.config import settings

# Anthropic strict structured output allows at most 16 schema nodes that
# are unions (a ``type`` list like ["string", "null"] or an ``anyOf``).
ANTHROPIC_UNION_LIMIT = 16

ANTHROPIC_FALLBACK_INSTRUCTION = (
    "Respond with ONLY a single JSON object (no prose, no code fences) that "
    "conforms exactly to this JSON Schema. Include every property; use null "
    "when a value is unknown or not mentioned. Enum fields must use one of "
    "the listed values or null."
)


def count_union_params(schema) -> int:
    """Count union nodes in a JSON schema (Anthropic limit check).

    Walks the whole schema recursively (properties, items, anyOf/oneOf/allOf
    members, $defs/definitions — plus any other nested dict/list) and counts
    every node whose ``type`` is a list with >1 entries, plus every node that
    has ``anyOf`` (or ``oneOf``).
    """
    count = 0

    def _walk(node, _stack: set[int]) -> None:
        nonlocal count
        if isinstance(node, dict):
            nid = id(node)
            if nid in _stack:
                return
            _stack.add(nid)
            try:
                t = node.get("type")
                if isinstance(t, list) and len(t) > 1:
                    count += 1
                anyof = node.get("anyOf")
                if isinstance(anyof, list):
                    count += 1
                elif "anyOf" in node:
                    count += 1
                oneof = node.get("oneOf")
                if isinstance(oneof, list):
                    count += 1
                elif "oneOf" in node:
                    count += 1
                for v in node.values():
                    _walk(v, _stack)
            finally:
                _stack.discard(nid)
        elif isinstance(node, list):
            for v in node:
                _walk(v, _stack)

    if isinstance(schema, (dict, list)):
        _walk(schema, set())
    return count


def _strip_code_fence(text: str) -> str:
    """Strip a leading ```json/``` fence and trailing ``` (fallback parsing)."""
    t = text.strip()
    if not t.startswith("```"):
        return t
    nl = t.find("\n")
    if nl == -1:
        rest = t[3:].lstrip()
        if rest[:4].lower() == "json":
            rest = rest[4:].lstrip()
        if rest.rstrip().endswith("```"):
            rest = rest.rstrip()[:-3]
        return rest.strip()
    rest = t[nl + 1 :]
    r = rest.rstrip()
    if r.endswith("```"):
        r = r[:-3]
    return r.strip()


def _parse_json_content(content):
    """Parse model ``content`` tolerating fences/prose (fallback robustness).

    Valid JSON parses exactly as before. When ``content`` is a string that
    fails ``json.loads``, strips a leading ```json/``` fence and trailing
    ``` and retries; if still failing, extracts the substring from the
    first "{" to the last "}" and retries; otherwise re-raises the original
    error. Non-string content passes through unchanged.
    """
    if not isinstance(content, str):
        return content
    try:
        return json.loads(content)
    except Exception as first_exc:
        stripped = _strip_code_fence(content)
        try:
            return json.loads(stripped)
        except Exception:
            pass
        try:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(stripped[start : end + 1])
        except Exception:
            pass
        if stripped != content:
            try:
                start = content.find("{")
                end = content.rfind("}")
                if start != -1 and end != -1 and end > start:
                    return json.loads(content[start : end + 1])
            except Exception:
                pass
        raise first_exc


# Public alias so tests can unit-test the parse helper without network.
parse_json_content = _parse_json_content

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
    provider: str | None = None,
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
    When ``provider`` is a non-blank string, ``{"provider": {"order":
    [value], "allow_fallbacks": False}}`` is added; None/blank leaves the
    payload byte-identical (no ``provider`` key).

    Anthropic union-limit fallback: when ``json_schema`` is given AND the
    model is ``anthropic/*`` AND ``count_union_params(json_schema)`` exceeds
    ``ANTHROPIC_UNION_LIMIT`` (16), the strict json_schema envelope is
    replaced with ``{"type": "json_object"}`` and the schema (serialised
    with ``json.dumps(..., ensure_ascii=False, indent=2)``) plus an
    instruction block is appended to the system message (after the existing
    text, separated by a blank line). All other models, and Anthropic with
    <=16 unions, stay byte-identical. Deterministic: same input ->
    byte-identical payload.
    """
    system_content = system
    if json_schema is None:
        response_format: dict = {"type": "json_object"}
    else:
        try:
            n_unions = count_union_params(json_schema)
        except Exception:
            n_unions = 0
        is_anthropic = isinstance(model, str) and model.lower().startswith("anthropic/")
        if is_anthropic and n_unions > ANTHROPIC_UNION_LIMIT:
            response_format = {"type": "json_object"}
            schema_json = json.dumps(json_schema, ensure_ascii=False, indent=2)
            system_content = f"{system}\n\n{ANTHROPIC_FALLBACK_INSTRUCTION}\n{schema_json}"
        else:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
            }
    payload = {
        "model": model,
        "response_format": response_format,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user},
        ],
    }
    effort = reasoning_effort.strip() if isinstance(reasoning_effort, str) else ""
    if effort:
        payload["reasoning"] = {"effort": effort}
    prov = provider.strip() if isinstance(provider, str) else ""
    if prov:
        payload["provider"] = {"order": [prov], "allow_fallbacks": False}
    return payload


async def complete_json_payload(payload: dict) -> tuple[dict, dict]:
    """POST a prebuilt payload and return (parsed_json, usage).

    usage is always {"prompt_tokens": int, "completion_tokens": int,
    "total_tokens": int, "reasoning_tokens": int, "provider": str};
    missing/partial OpenRouter ``usage`` blocks become zeros and never raise.
    ``provider`` is the top-level OpenRouter response ``provider`` string
    ("" when absent).
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

    content = data["choices"][0]["message"]["content"]
    parsed = _parse_json_content(content)
    usage = _extract_usage(data.get("usage"))
    try:
        prov = data.get("provider") if isinstance(data, dict) else ""
    except Exception:
        prov = ""
    usage["provider"] = prov if isinstance(prov, str) else ""
    return parsed, usage


async def complete_json(
    *, model: str, system: str, user: str, json_schema: dict | None = None,
    reasoning_effort: str | None = None,
    provider: str | None = None,
) -> tuple[dict, dict]:
    """Call OpenRouter and return (parsed_json, usage).

    Thin wrapper over build_chat_payload + complete_json_payload so
    existing call sites/tests keep working.

    usage is always {"prompt_tokens": int, "completion_tokens": int,
    "total_tokens": int, "reasoning_tokens": int, "provider": str};
    missing/partial OpenRouter ``usage`` blocks become zeros and never raise.

    ``json_schema`` is the inner OpenAPI-compatible object schema
    (``{"type": "object", "properties": {...}, ...}``). When given, the call
    uses a ``json_schema`` response_format envelope; when None, it falls back
    to the legacy ``{"type": "json_object"}`` mode.
    """
    return await complete_json_payload(
        build_chat_payload(model=model, system=system, user=user, json_schema=json_schema,
                           reasoning_effort=reasoning_effort, provider=provider)
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


# --- Model endpoints (OpenRouter providers), 1h per-model in-process cache ---

_ENDPOINTS_TTL_S = 3600
_endpoints_cache: dict[str, tuple[float, list[dict]]] = {}


def clear_endpoints_cache() -> None:
    """Reset the in-process endpoints cache (tests)."""
    global _endpoints_cache
    _endpoints_cache = {}


def _parse_float_or_none(v: object) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)  # type: ignore[arg-type]
        return f
    except Exception:
        return None


def _parse_int_or_none(v: object) -> int | None:
    try:
        if v is None:
            return None
        if isinstance(v, bool):
            return None
        return int(v)  # type: ignore[arg-type]
    except Exception:
        return None


def _endpoint_entry(e: dict) -> dict:
    """Normalize one OpenRouter /models/{id}/endpoints entry."""
    try:
        provider_name = e.get("provider_name")
    except Exception:
        provider_name = ""
    if not isinstance(provider_name, str) or not provider_name:
        try:
            fallback = e.get("name")
        except Exception:
            fallback = ""
        provider_name = fallback if isinstance(fallback, str) else ""
    try:
        tag_raw = e.get("tag")
    except Exception:
        tag_raw = ""
    tag = tag_raw if isinstance(tag_raw, str) and tag_raw else ""
    slug = tag if tag else (provider_name.lower() if isinstance(provider_name, str) else "")
    try:
        quant_raw = e.get("quantization")
    except Exception:
        quant_raw = None
    quantization = quant_raw if isinstance(quant_raw, str) and quant_raw else None
    try:
        pricing_raw = e.get("pricing") if isinstance(e.get("pricing"), dict) else {}
    except Exception:
        pricing_raw = {}
    return {
        "slug": slug,
        "name": provider_name,
        "tag": tag,
        "quantization": quantization,
        "context_length": _parse_int_or_none(e.get("context_length")),
        "pricing": {
            "prompt": _parse_price(pricing_raw.get("prompt")),
            "completion": _parse_price(pricing_raw.get("completion")),
        },
        "uptime_last_30m": _parse_float_or_none(e.get("uptime_last_30m")),
        "status": _parse_int_or_none(e.get("status")),
    }


async def fetch_model_endpoints(model_id: str) -> list[dict]:
    """Return normalized provider endpoints for one model, or [].

    GET {base}/models/{model_id}/endpoints (model_id keeps its
    "author/slug" slash as part of the path). Entries come from response
    data.endpoints. Cached per model in-process for 1 hour. On any failure
    (or when not configured) returns [] and never raises.
    """
    global _endpoints_cache
    try:
        mid = (model_id or "").strip()
        if not mid:
            return []
        if not is_configured():
            return []
        now = time.time()
        cached = _endpoints_cache.get(mid)
        if cached is not None:
            try:
                expires_at, items = cached
            except Exception:
                expires_at, items = 0.0, []
            if now < expires_at and isinstance(items, list):
                return items
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{settings.openrouter_base_url.rstrip('/')}/models/{mid}/endpoints",
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            )
            resp.raise_for_status()
            body = resp.json()
        items_raw: object = []
        try:
            if isinstance(body, dict):
                data = body.get("data")
                if isinstance(data, dict) and isinstance(data.get("endpoints"), list):
                    items_raw = data.get("endpoints") or []
                elif isinstance(data, list):
                    items_raw = data
                elif isinstance(body.get("endpoints"), list):
                    items_raw = body.get("endpoints") or []
        except Exception:
            items_raw = []
        out: list[dict] = []
        try:
            for e in items_raw or []:
                if not isinstance(e, dict):
                    continue
                try:
                    out.append(_endpoint_entry(e))
                except Exception:
                    continue
        except Exception:
            return []
        _endpoints_cache[mid] = (now + _ENDPOINTS_TTL_S, out)
        return out
    except Exception:
        return []
