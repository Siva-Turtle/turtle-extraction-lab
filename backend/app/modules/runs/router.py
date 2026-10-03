import asyncio
from time import perf_counter
import copy

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.openrouter import REASONING_EFFORTS, build_chat_payload, complete_json, complete_json_payload, get_model_pricing
from app.db.models import Agent, Attribute, Feedback, Run, RunLog, agent_attributes
from app.db.session import get_db
from app.modules.logs.router import _out
from app.modules.meetings.router import get_scrubbed_transcript
from app.modules.runs.schemas import (
    BatchFeedbackIn, CheckExistingIn, CheckExistingOut,
    FeedbackCreate, FeedbackOut, ReuseRunIn, RunCreate, RunDetail, RunOut,
)

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])

RESULT_CONTRACT = (
    'Return a JSON object keyed by attribute name. Each value is an object with "value" '
    '(the extracted value), "confidence" (0-1), "confidence_type" (quoted|inferred|normalized), '
    '"evidence" (exact quote from the input). If an attribute is not found in the input, '
    'omit it from the response — never return null. '
    'quoted = value stated word-for-word (evidence is the exact quote); '
    'inferred = value concluded from the input but not stated verbatim '
    '(evidence is the supporting passage); normalized = value standardized from a stated form '
    'such as phone digits, date formats, or casing (evidence is the original stated form).\n\n'
    '| Confidence Type | Meaning |\n'
    '|---|---|\n'
    '| `quoted` | Value is explicitly stated in the transcript |\n'
    '| `normalized` | Value is explicitly stated but transformed into your canonical representation |\n'
    '| `inferred` | Value was not directly stated; model derived it from evidence |\n'
    '| `not_found` | No sufficient evidence exists |\n'
    '| `calculated` | Mentioned as pieces of info, but model performed calculations to arrive |'
)


def _attrs_for_agent(db: Session, agent_id: str) -> list[Attribute]:
    return (
        db.query(Attribute)
        .join(agent_attributes, agent_attributes.c.attribute_id == Attribute.id)
        .filter(agent_attributes.c.agent_id == agent_id)
        .all()
    )


# --- Agent Identifier meta-agent (kind == "identifier") -------------------
# The identifier lists EVERY extraction agent followed by ALL its
# attributes, then reports which attributes are fillable. An agent is
# selected when at least one of its attributes can be extracted.
# Its prompt is built at prompt/preview/run time, never stored per-run
# except inside the denormalized log snapshots/requests.

IDENTIFIER_KIND = "identifier"
EXTRACTION_KIND = "extraction"
IDENTIFIER_SCHEMA_NAME = "agent_selection"

IDENTIFIER_INSTRUCTION = (
    "You are an agent router. Read the input and, for every candidate agent, "
    "check each of its attributes against the input. Select an agent when "
    "even one of its attributes can be extracted. Return strictly the "
    "selection object."
)


def is_identifier(agent: Agent) -> bool:
    """True when this agent is the router (kind == identifier)."""
    return (getattr(agent, "kind", None) or EXTRACTION_KIND) == IDENTIFIER_KIND


def build_identifier_schema() -> dict:
    """Strict structured-output schema for the identifier.

    Property ORDER matters — fillable_attributes first, then
    selected_agents. No nullable/union fields.
    """
    return {
        "type": "object",
        "properties": {
            "fillable_attributes": {
                "type": "array",
                "description": "Each agent with at least one fillable attribute",
                "items": {
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string", "description": "Agent name"},
                        "attributes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Fillable attribute names",
                        },
                    },
                    "required": ["agent", "attributes"],
                    "additionalProperties": False,
                },
            },
            "selected_agents": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names of the extraction agents to run",
            },
        },
        "required": ["fillable_attributes", "selected_agents"],
        "additionalProperties": False,
    }


def _identifier_candidates(db: Session, exclude_id: str = "") -> list[Agent]:
    """Extraction-kind agents (the identifier itself excluded), ordered by name."""
    q = db.query(Agent).filter(Agent.kind != IDENTIFIER_KIND).order_by(Agent.name.asc())
    rows = q.all()
    if exclude_id:
        rows = [a for a in rows if a.id != exclude_id]
    return rows


def _identifier_system_content(
    agent: Agent, candidates: list[tuple[str, str, list[tuple[str, str]]]]
) -> str:
    """System prompt for the identifier: base instruction + full attribute roster.

    ``candidates`` is [(name, description, [(attr_name, attr_description)])]
    in the same order ``_attrs_for_agent`` returns. Deterministic and
    byte-stable for the same DB state. Uses attribute NAMES, not serials.
    """
    base = agent.system_instruction or IDENTIFIER_INSTRUCTION
    if candidates:
        blocks: list[str] = []
        for name, desc, attrs in candidates:
            header = f"## {name}"
            if (desc or "").strip():
                header += f": {desc.strip()}"
            if attrs:
                attr_lines = []
                for attr_name, attr_desc in attrs:
                    if (attr_desc or "").strip():
                        attr_lines.append(f"- {attr_name}: {attr_desc.strip()}")
                    else:
                        attr_lines.append(f"- {attr_name}")
            else:
                attr_lines = ["- (no attributes defined)"]
            blocks.append("\n".join([header] + attr_lines))
        roster = "\n\n".join(blocks)
    else:
        roster = "- (no candidate agents defined)"
    return (
        f"{base}\n\nCandidate agents and their attributes:\n\n{roster}\n\n"
        "Rules:\n"
        "- Go through every agent and every attribute. An attribute is fillable when "
        "the input contains information that answers it (explicitly or clearly implied).\n"
        "- Select an agent if AT LEAST ONE of its attributes is fillable. Even a single "
        "fillable attribute is enough.\n"
        '- In "fillable_attributes", list each selected agent with the exact attribute '
        "names (copy them verbatim from the list above) that can be filled.\n"
        '- "selected_agents" must be exactly the agent names that appear in '
        '"fillable_attributes".\n'
        '- If nothing applies, return {"fillable_attributes": [], "selected_agents": []}.'
    )


def identifier_candidates_with_attributes(
    db: Session, exclude_id: str = "",
) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """Candidate (name, description, [(attr_name, attr_desc)]) rows for prompt building."""
    out: list[tuple[str, str, list[tuple[str, str]]]] = []
    for cand in _identifier_candidates(db, exclude_id):
        attrs = [(a.name, a.description or "") for a in _attrs_for_agent(db, cand.id)]
        out.append((cand.name, cand.description or "", attrs))
    return out


def _candidate_attr_names(attrs) -> list[str]:
    """Real attribute names from a candidate's attr list.

    Accepts the new [(name, description)] shape, plain [name] lists, or
    [{"name": ...}] dicts (defensive — keeps the normaliser pure).
    """
    names: list[str] = []
    for a in attrs or []:
        if isinstance(a, (list, tuple)) and a:
            names.append(str(a[0]))
        elif isinstance(a, dict):
            n = a.get("name", "")
            if isinstance(n, str) and n:
                names.append(n)
        elif isinstance(a, str):
            names.append(a)
    return names


def normalize_identifier_output(parsed, candidates) -> object:
    """Normalise the identifier's parsed output against the candidate roster.

    - Drop fillable entries whose agent is not a candidate name or whose
      attributes list is empty after filtering to that agent's real
      attribute names (keep order, dedupe).
    - selected_agents = de-duplicated union of the model's selected_agents
      (only valid candidate names) and agents with non-empty fillable
      entries, preserving first-seen order.
    - Non-dict inputs (and {"_error": ...} payloads) are returned untouched.
    """
    if not isinstance(parsed, dict):
        return parsed
    if "_error" in parsed:
        return parsed
    valid: dict[str, set[str]] = {}
    candidate_names: set[str] = set()
    for cand in candidates or []:
        try:
            cname, _cdesc, cattrs = cand
        except Exception:
            continue
        candidate_names.add(cname)
        valid[cname] = set(_candidate_attr_names(cattrs))
    raw_fillable = parsed.get("fillable_attributes", [])
    if not isinstance(raw_fillable, list):
        raw_fillable = []
    fillable: list[dict] = []
    for entry in raw_fillable:
        if not isinstance(entry, dict):
            continue
        agent_name = entry.get("agent")
        attr_list = entry.get("attributes")
        if not isinstance(agent_name, str) or agent_name not in candidate_names:
            continue
        if not isinstance(attr_list, list):
            continue
        allowed = valid.get(agent_name, set())
        seen: set[str] = set()
        kept: list[str] = []
        for attr_name in attr_list:
            if not isinstance(attr_name, str):
                continue
            if attr_name not in allowed:
                continue
            if attr_name in seen:
                continue
            seen.add(attr_name)
            kept.append(attr_name)
        if not kept:
            continue
        fillable.append({"agent": agent_name, "attributes": kept})
    raw_selected = parsed.get("selected_agents", [])
    if not isinstance(raw_selected, list):
        raw_selected = []
    selected: list[str] = []
    seen_sel: set[str] = set()
    for name in raw_selected:
        if not isinstance(name, str):
            continue
        if name not in candidate_names:
            continue
        if name in seen_sel:
            continue
        seen_sel.add(name)
        selected.append(name)
    for entry in fillable:
        agent_name = entry["agent"]
        if agent_name not in seen_sel:
            seen_sel.add(agent_name)
            selected.append(agent_name)
    return {"fillable_attributes": fillable, "selected_agents": selected}


def _attr_line(a: Attribute) -> str:
    base = f"- {a.name} ({a.type}): {a.description}"
    if a.type == "enum" and (a.enum_values or []):
        base += f" [{' | '.join(a.enum_values)}]"
    return base


def _agent_system_content(agent: Agent, attrs: list[Attribute]) -> str:
    base = agent.system_instruction or "Extract structured data."
    attr_lines = "\n".join(_attr_line(a) for a in attrs) or "- (no attributes defined)"
    return f"{base}\n\nAttributes to extract:\n{attr_lines}\n\n{RESULT_CONTRACT}"


def _object_properties(a: Attribute) -> list[dict]:
    """Ordered sub-fields for type=="object" as plain dicts (DB stores JSON)."""
    raw = getattr(a, "object_properties", None) or []
    props: list[dict] = []
    for p in raw:
        if isinstance(p, dict):
            props.append(p)
        else:
            props.append({"name": getattr(p, "name", ""),
                          "type": getattr(p, "type", "string"),
                          "null_allowed": getattr(p, "null_allowed", True),
                          "enum": list(getattr(p, "enum", []) or []),
                          "description": getattr(p, "description", "") or ""})
    return props


def _sub_schema(sub_type: str, null_allowed: bool,
                enum: list[str] | None = None,
                description: str | None = None) -> dict:
    """Map one object sub-field to its JSON-schema fragment (items always string).

    Nullable enums use anyOf (Anthropic rejects {"type": ["string", "null"],
    "enum": [...]}): {"anyOf": [{"type": <base>, "enum": [...]},
    {"type": "null"}], ...}. Non-nullable enums stay
    {"type": <base>, "enum": [...]}. Non-enum fields unchanged.
    """
    if enum:
        enum_vals = list(enum)
        if sub_type == "array":
            base = "array"
        elif sub_type == "number":
            base = "number"
        elif sub_type == "boolean":
            base = "boolean"
        else:
            base = "string"
        if null_allowed:
            out: dict = {"anyOf": [{"type": base, "enum": enum_vals},
                                   {"type": "null"}]}
            if sub_type == "array":
                out["items"] = {"type": "string"}
            if description:
                out["description"] = description
            return out
        out = {"type": base, "enum": enum_vals}
        if sub_type == "array":
            out["items"] = {"type": "string"}
        if description:
            out["description"] = description
        return out
    if sub_type == "array":
        out = {"type": (["array", "null"] if null_allowed else "array"),
               "items": {"type": "string"}}
    elif sub_type == "number":
        out = {"type": (["number", "null"] if null_allowed else "number")}
    elif sub_type == "boolean":
        out = {"type": (["boolean", "null"] if null_allowed else "boolean")}
    else:
        out = {"type": (["string", "null"] if null_allowed else "string")}
    if description:
        out["description"] = description
    return out


def _snapshot_props(a: Attribute) -> list[dict]:
    out: list[dict] = []
    for p in _object_properties(a):
        enum_vals = p.get("enum", []) or []
        if not isinstance(enum_vals, list):
            enum_vals = []
        desc = p.get("description", "") or ""
        if not isinstance(desc, str):
            desc = ""
        out.append({"name": str(p.get("name", "")),
                    "type": p.get("type", "string"),
                    "null_allowed": bool(p.get("null_allowed", True)),
                    "enum": list(enum_vals),
                    "description": desc})
    return out


def _array_items_cfg(a: Attribute) -> dict:
    """Normalized {kind, properties} for type=="array" (defaults to string)."""
    raw = getattr(a, "array_items", None) or {}
    if not isinstance(raw, dict):
        return {"kind": "string", "properties": []}
    kind = str(raw.get("kind", "string") or "string").strip().lower()
    if kind not in ("string", "number", "object"):
        kind = "string"
    if kind != "object":
        return {"kind": kind, "properties": []}
    props: list[dict] = []
    for p in (raw.get("properties", []) or []):
        if not isinstance(p, dict):
            continue
        enum_vals = p.get("enum", []) or []
        if not isinstance(enum_vals, list):
            enum_vals = []
        desc = p.get("description", "") or ""
        if not isinstance(desc, str):
            desc = ""
        props.append({"name": str(p.get("name", "")),
                      "type": p.get("type", "string"),
                      "null_allowed": bool(p.get("null_allowed", True)),
                      "enum": list(enum_vals),
                      "description": desc})
    return {"kind": kind, "properties": props}


def _snapshot_array_items(a: Attribute) -> dict:
    return _array_items_cfg(a)


def _array_item_schema(a: Attribute) -> dict:
    """Map array item-shape config to its JSON-schema items fragment."""
    cfg = _array_items_cfg(a)
    kind = cfg.get("kind", "string")
    if kind == "number":
        return {"type": "number"}
    if kind == "object":
        sub_props: dict = {}
        sub_required: list[str] = []
        for p in cfg.get("properties", []):
            sub_props[str(p.get("name", ""))] = _sub_schema(
                str(p.get("type", "string")), bool(p.get("null_allowed", True)),
                p.get("enum", []) or None,
                p.get("description", "") or None)
            sub_required.append(str(p.get("name", "")))
        return {"type": "object", "properties": sub_props,
                "required": sub_required, "additionalProperties": False}
    return {"type": "string"}


def _value_schema_for_attribute(a: Attribute) -> dict:
    """Map attribute type to the OpenAI structured-output value field.

    - string -> {"type": ["string", "null"], "description": ...}
    - number -> {"type": ["number", "null"], ...}
    - boolean -> {"type": ["boolean", "null"], ...}
    - enum (always nullable) -> {"anyOf": [{"type": "string",
      "enum": [...]}, {"type": "null"}], "description": ...}
      (Anthropic rejects {"type": ["string", "null"], "enum": [...]},
      so nullable enums use anyOf without null inside the enum list)
    - array -> {"type": ["array", "null"], "items": <shape>, ...} where
      string->{"type":"string"}, number->{"type":"number"},
      object->{"type":"object","properties":{...},"required":[...all...],
      "additionalProperties":false}
    - object -> {"type": ["object", "null"], "properties": {sub-name: sub-schema},
      "required": [all sub names], "additionalProperties": False, ...}
    Nullable enum sub-fields (inside array items / object properties) use
    the same anyOf pattern via _sub_schema; non-nullable enum sub-fields
    stay {"type": "string", "enum": [...]}. Non-enum fields unchanged.
    """
    desc = f"Extracted value for {a.name}"
    if a.type == "enum" and (a.enum_values or []):
        return {"anyOf": [{"type": "string", "enum": list(a.enum_values)},
                           {"type": "null"}],
                "description": desc}
    if a.type == "number":
        value_schema: dict = {"type": ["number", "null"], "description": desc}
    elif a.type == "boolean":
        value_schema = {"type": ["boolean", "null"], "description": desc}
    elif a.type == "array":
        value_schema = {"type": ["array", "null"],
                        "items": _array_item_schema(a), "description": desc}
    elif a.type == "object":
        sub_props: dict = {}
        sub_required: list[str] = []
        for p in _object_properties(a):
            sub_props[str(p.get("name", ""))] = _sub_schema(
                str(p.get("type", "string")), bool(p.get("null_allowed", True)),
                p.get("enum", []) or None,
                p.get("description", "") or None)
            sub_required.append(str(p.get("name", "")))
        value_schema = {"type": ["object", "null"], "properties": sub_props,
                        "required": sub_required,
                        "additionalProperties": False, "description": desc}
    else:
        # string (and unknown types fall back to string); empty-enum edge
        # also lands here as a plain nullable string (no enum key).
        value_schema = {"type": ["string", "null"], "description": desc}
    return value_schema


def build_extraction_schema(attrs: list[Attribute]) -> dict | None:
    """OpenAI-compatible structured-output object schema for this run's attributes.

    Each attribute becomes ``{"value", "confidence", "confidence_type",
    "evidence"}`` with all four required. Top-level ``required`` lists every
    attribute name (missing = null value + not_found). No $refs. Returns None
    when there are no attributes (caller falls back to json_object mode).
    """
    if not attrs:
        return None
    properties: dict = {}
    required: list[str] = []
    for a in attrs:
        properties[a.name] = {
            "type": "object",
            "description": a.description or a.name,
            "properties": {
                "value": _value_schema_for_attribute(a),
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "confidence_type": {"type": "string",
                                    "enum": ["quoted", "inferred", "normalized", "not_found"]},
                "evidence": {"type": ["string", "null"]},
            },
            "required": ["value", "confidence", "confidence_type", "evidence"],
            "additionalProperties": False,
        }
        required.append(a.name)
    return {"type": "object", "properties": properties,
            "required": required, "additionalProperties": False}


def _resolve_run_input(
    *, meeting_id: str, input_type: str, input_data: str, reasoning_effort: str,
) -> tuple[str, str, str, str]:
    """Validate effort + resolve input exactly like create_run.

    Returns (input_type, input_data, effort, task_title). Raises the same
    HTTPException create_run would (422 for bad effort/blank input/bad
    type, 404/503 from get_scrubbed_transcript). No OpenRouter, no writes.
    """
    effort = (reasoning_effort or "").strip()
    if effort and effort not in REASONING_EFFORTS:
        raise HTTPException(422, "reasoning_effort must be max|xhigh|high|medium|low|minimal|none")
    mid = (meeting_id or "").strip()
    task_title = ""
    if mid:
        # meeting_id wins: server re-fetches the transcript and scrubs it —
        # client-sent input_data/input_type are ignored entirely.
        _task, scrubbed = get_scrubbed_transcript(mid)  # 404/503 propagate
        resolved_type, resolved_data = "transcription", scrubbed
        try:
            task_title = str((_task.get("title") or "")).strip()
        except Exception:
            task_title = ""
    else:
        if not (input_data or "").strip():
            raise HTTPException(422, "input_data must be non-blank or provide meeting_id")
        if input_type not in ("transcription", "messages", "mail"):
            raise HTTPException(422, "input_type must be transcription|messages|mail")
        resolved_type, resolved_data = input_type, input_data
    return resolved_type, resolved_data, effort, task_title


def _load_run_agents(db: Session, agent_ids: list[str]) -> list[Agent]:
    """Load agents exactly like create_run (404 when ids match nothing).

    Order follows the input ``agent_ids`` (deduped) so outputs/requests/
    per_agent insertion order is deterministic and matches the caller's
    agent order, whether runs execute sequentially or via asyncio.gather.
    """
    rows = db.query(Agent).filter(Agent.id.in_(agent_ids)).all() if agent_ids else []
    if agent_ids and not rows:
        raise HTTPException(404, "no matching agents")
    by_id = {a.id: a for a in rows}
    ordered: list[Agent] = []
    seen: set[str] = set()
    for aid in agent_ids or []:
        if aid in by_id and aid not in seen:
            ordered.append(by_id[aid])
            seen.add(aid)
    return ordered if ordered else rows


def _build_agent_requests(
    db: Session, *, agents: list[Agent], input_data: str,
    model: str, reasoning_effort: str,
) -> tuple[dict, dict]:
    """Build (snapshots, requests) exactly like create_run's loop prelude.

    Pure DB reads + prompt building. No OpenRouter call, no writes.
    requests[agent.id] is the exact payload_body create_run would POST.
    """
    effort = (reasoning_effort or "").strip()
    snapshots: dict = {}
    requests: dict = {}
    for agent in agents:
        attrs = _attrs_for_agent(db, agent.id)
        snapshots[agent.id] = {
            "name": agent.name, "description": agent.description or "",
            "kind": getattr(agent, "kind", None) or EXTRACTION_KIND,
            "system_instruction": agent.system_instruction,
            "attributes": [{"name": a.name, "type": a.type, "description": a.description,
                            "group": getattr(a, "group_name", "") or "",
                            "enum_values": a.enum_values or [],
                            "object_properties": _snapshot_props(a),
                            "array_items": _snapshot_array_items(a)} for a in attrs],
        }
        user_content = input_data
        if is_identifier(agent):
            # Router path: full attribute list per candidate; strict
            # agent_selection envelope with fillable_attributes + selected_agents.
            candidates = identifier_candidates_with_attributes(db, exclude_id=agent.id)
            system_content = _identifier_system_content(agent, candidates)
            schema: dict | None = build_identifier_schema()
            payload_body = build_chat_payload(
                model=model, system=system_content, user=user_content,
                json_schema=schema, schema_name=IDENTIFIER_SCHEMA_NAME,
                reasoning_effort=effort)
        else:
            system_content = _agent_system_content(agent, attrs)
            schema = build_extraction_schema(attrs)
            payload_body = build_chat_payload(
                model=model, system=system_content, user=user_content, json_schema=schema,
                reasoning_effort=effort)
        requests[agent.id] = copy.deepcopy(payload_body)
    return snapshots, requests


def _plan_run(
    db: Session, *, meeting_id: str, input_type: str, input_data: str,
    agent_ids: list[str], model: str, reasoning_effort: str,
) -> tuple[str, str, str, str, list[Agent], dict, dict]:
    """Resolve input + agents + per-agent request bodies without side effects.

    Returns (input_type, input_data, effort, task_title, agents,
    snapshots, requests). Raises the same HTTPException create_run would.
    """
    resolved_type, resolved_data, effort, task_title = _resolve_run_input(
        meeting_id=meeting_id, input_type=input_type,
        input_data=input_data, reasoning_effort=reasoning_effort)
    agents = _load_run_agents(db, agent_ids)
    snapshots, requests = _build_agent_requests(
        db, agents=agents, input_data=resolved_data,
        model=model, reasoning_effort=effort)
    return resolved_type, resolved_data, effort, task_title, agents, snapshots, requests


def _find_existing_log(
    db: Session, *, model: str, reasoning_effort: str, expected_requests: dict,
) -> RunLog | None:
    """Newest RunLog with this model/effort whose requests messages match.

    - SQL filter on model + reasoning_effort, newest first, limit 200.
    - set(log.requests keys) must equal set(expected agent ids).
    - every agent's stored messages must equal the would-be-sent messages.
    - not every output may carry _error (at least one success required).
    """
    effort = (reasoning_effort or "").strip()
    rows = (
        db.query(RunLog)
        .filter(RunLog.model == model, RunLog.reasoning_effort == effort)
        .order_by(RunLog.created_at.desc())
        .limit(200)
        .all()
    )
    expected_ids = set(expected_requests.keys())
    for log in rows:
        stored_reqs = log.requests or {}
        if not isinstance(stored_reqs, dict):
            continue
        if set(stored_reqs.keys()) != expected_ids:
            continue
        same = True
        for aid, expected_body in expected_requests.items():
            stored_body = stored_reqs.get(aid)
            if not isinstance(stored_body, dict) or not isinstance(expected_body, dict):
                same = False
                break
            if stored_body.get("messages") != expected_body.get("messages"):
                same = False
                break
        if not same:
            continue
        outs = log.outputs or {}
        if not isinstance(outs, dict) or not outs:
            continue
        all_errored = True
        for v in outs.values():
            if not isinstance(v, dict) or "_error" not in v:
                all_errored = False
                break
        if all_errored:
            continue
        return log
    return None


@router.post("")
async def create_run(payload: RunCreate, db: Session = Depends(get_db)):
    input_type, input_data, effort, task_title, agents, snapshots, requests = _plan_run(
        db, meeting_id=payload.meeting_id, input_type=payload.input_type,
        input_data=payload.input_data, agent_ids=payload.agent_ids,
        model=payload.model, reasoning_effort=payload.reasoning_effort)
    filters = payload.filters if isinstance(payload.filters, dict) else {}
    wall_start = perf_counter()

    async def _run_one(agent, payload_body, model: str):
        # No DB access in here — pure OpenRouter call + timing so concurrent
        # tasks never share the request's DB session.
        agent_start = perf_counter()
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        reasoning_tokens = 0
        try:
            parsed, usage = await complete_json_payload(payload_body)
            try:
                prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
                completion_tokens = int(usage.get("completion_tokens", 0) or 0)
                total_tokens = int(usage.get("total_tokens", 0) or 0)
                reasoning_tokens = int(usage.get("reasoning_tokens", 0) or 0)
            except Exception:
                prompt_tokens, completion_tokens, total_tokens, reasoning_tokens = 0, 0, 0, 0
            if prompt_tokens < 0:
                prompt_tokens = 0
            if completion_tokens < 0:
                completion_tokens = 0
            if total_tokens < 0:
                total_tokens = 0
            if reasoning_tokens < 0:
                reasoning_tokens = 0
            duration_ms = (perf_counter() - agent_start) * 1000.0
            return (agent.id, parsed, prompt_tokens, completion_tokens,
                    total_tokens, reasoning_tokens, duration_ms, model)
        except Exception as exc:
            duration_ms = (perf_counter() - agent_start) * 1000.0
            return (agent.id, {"_error": str(exc)}, 0, 0, 0, 0,
                    duration_ms, model)

    bodies = [(a, copy.deepcopy(requests[a.id]), payload.model) for a in agents]
    results = await asyncio.gather(*[_run_one(a, b, m) for a, b, m in bodies])
    outputs: dict = {}
    per_agent: dict = {}
    total_in = 0
    total_out = 0
    total_in_reasoning = 0
    agents_by_id = {a.id: a for a in agents}
    for (aid, parsed, prompt_tokens, completion_tokens,
         total_tokens, reasoning_tokens, duration_ms, model) in results:
        agent_row = agents_by_id.get(aid)
        if agent_row is not None and is_identifier(agent_row):
            try:
                cands = identifier_candidates_with_attributes(db, exclude_id=aid)
                parsed = normalize_identifier_output(parsed, cands)
            except Exception:
                pass
        outputs[aid] = parsed
        total_in += prompt_tokens
        total_out += completion_tokens
        total_in_reasoning += reasoning_tokens
        per_agent[aid] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "reasoning_tokens": reasoning_tokens,
            "cost_usd": None,
            "input_cost_usd": None,
            "output_cost_usd": None,
            "duration_ms": duration_ms,
            "model": model,
        }
    try:
        prompt_price, completion_price = await get_model_pricing(payload.model)
    except Exception:
        prompt_price, completion_price = None, None
    total_cost: float | None = None
    if prompt_price is not None and completion_price is not None:
        total_cost = 0.0
        for entry in per_agent.values():
            cost = entry["prompt_tokens"] * prompt_price + entry["completion_tokens"] * completion_price
            entry["cost_usd"] = round(cost, 6)
            total_cost += cost
        total_cost = round(total_cost, 6)
    for entry in per_agent.values():
        if prompt_price is None:
            entry["input_cost_usd"] = None
        else:
            entry["input_cost_usd"] = round(entry["prompt_tokens"] * prompt_price, 6)
        if completion_price is None:
            entry["output_cost_usd"] = None
        else:
            entry["output_cost_usd"] = round(entry["completion_tokens"] * completion_price, 6)
    if prompt_price is None or any(v["input_cost_usd"] is None for v in per_agent.values()):
        total_input_cost: float | None = None
    else:
        total_input_cost = round(sum((v["input_cost_usd"] for v in per_agent.values()), 0.0), 6)
    if completion_price is None or any(v["output_cost_usd"] is None for v in per_agent.values()):
        total_output_cost: float | None = None
    else:
        total_output_cost = round(sum((v["output_cost_usd"] for v in per_agent.values()), 0.0), 6)
    wall_ms = (perf_counter() - wall_start) * 1000.0
    usage = {
        "prompt_tokens": total_in,
        "completion_tokens": total_out,
        "total_tokens": total_in + total_out,
        "reasoning_tokens": total_in_reasoning,
        "cost_usd": total_cost,
        "input_cost_usd": total_input_cost,
        "output_cost_usd": total_output_cost,
        "duration_ms": wall_ms,
        "model": payload.model,
        "per_agent": per_agent,
    }
    # Denormalized meeting snapshot — plain strings from the Test Lab
    # picker, never FKs. When meeting_id is present the server-known task
    # title fills blanks so old/direct API clients still get a snapshot.
    client = (payload.client or "").strip()
    meeting_type = (payload.meeting_type or "").strip() or task_title
    meeting_title = (payload.meeting_title or "").strip() or task_title
    run = Run(input_type=input_type, input_data=input_data, model=payload.model,
              agent_ids=payload.agent_ids, outputs=outputs)
    db.add(run)
    db.commit()
    db.refresh(run)
    # Denormalized log row — snapshots only, no FK to agents/attributes.
    log = RunLog(run_id=run.id, input_type=run.input_type, input_data=run.input_data, model=run.model,
                  agent_snapshot=snapshots, attribute_snapshot=snapshots, outputs=outputs, feedback={},
                  usage=usage, filters=filters, requests=requests,
                  client=client, meeting_type=meeting_type, meeting_title=meeting_title,
                  reasoning_effort=effort, run_group_id=payload.run_group_id or "")
    db.add(log)
    db.commit()
    db.refresh(log)
    return {"id": run.id, "outputs": outputs, "usage": usage, "requests": requests,
            "log_id": log.id, "run_group_id": log.run_group_id or "", "log": _out(log)}


@router.get("", response_model=list[RunOut])
def list_runs(db: Session = Depends(get_db)):
    rows = db.query(Run).order_by(Run.created_at.desc()).limit(100).all()
    return [RunOut(id=r.id, input_type=r.input_type, model=r.model,
                   agent_ids=r.agent_ids or [], created_at=r.created_at) for r in rows]


def _cell_existing(fb: dict, agent_key: str, attr_key: str) -> dict:
    """Existing feedback cell as a plain dict ({} when absent/malformed)."""
    try:
        agent_map = fb.get(agent_key, {})
        if not isinstance(agent_map, dict):
            return {}
        cell = agent_map.get(attr_key, {})
        return dict(cell) if isinstance(cell, dict) else {}
    except Exception:
        return {}


def _apply_feedback_cell(
    fb: dict, agent_name: str, attribute_name: str,
    rating: str, remarks: str | None,
) -> tuple[str, str]:
    """Mutate denormalized ``fb`` like add_feedback; return (history_rating, history_remarks).

    - rating "" removes the cell (drops the agent key when empty) and the
      history row is written with rating "clear".
    - remarks None keeps the cell's existing remarks ("" when absent);
      a string replaces them.
    """
    agent_key = agent_name or "agent"
    attr_key = attribute_name or "attribute"
    existing = _cell_existing(fb, agent_key, attr_key)
    existing_remarks = existing.get("remarks", "")
    if not isinstance(existing_remarks, str):
        existing_remarks = ""
    new_remarks = existing_remarks if remarks is None else (remarks or "")
    if rating == "":
        agent_map = fb.get(agent_key)
        if isinstance(agent_map, dict) and attr_key in agent_map:
            del agent_map[attr_key]
            if not agent_map:
                del fb[agent_key]
        return ("clear", new_remarks)
    fb.setdefault(agent_key, {})[attr_key] = {"rating": rating, "remarks": new_remarks}
    return (rating, new_remarks)


def _cell_is_rated(fb: dict, agent_name: str, attribute_name: str) -> bool:
    cell = _cell_existing(fb, agent_name or "agent", attribute_name or "attribute")
    rating = cell.get("rating", "")
    return isinstance(rating, str) and bool(rating.strip())


@router.post("/reuse")
def reuse_run(payload: ReuseRunIn, db: Session = Depends(get_db)):
    source = db.query(RunLog).filter(RunLog.id == payload.log_id).first()
    if not source:
        raise HTTPException(404, "log not found")
    # If the source itself is a reuse, point at the ORIGINAL source.
    original_id = getattr(source, "reused_from_log_id", None) or source.id
    original_created_at = getattr(source, "reused_from_created_at", None) or source.created_at
    agent_snapshot = copy.deepcopy(source.agent_snapshot or {})
    attribute_snapshot = copy.deepcopy(source.attribute_snapshot or {})
    outputs = copy.deepcopy(source.outputs or {})
    usage = copy.deepcopy(source.usage or {})
    filters = copy.deepcopy(source.filters or {})
    requests = copy.deepcopy(source.requests or {})
    try:
        agent_ids = list(agent_snapshot.keys()) if isinstance(agent_snapshot, dict) else []
    except Exception:
        agent_ids = []
    run = Run(input_type=source.input_type or "", input_data=source.input_data or "",
              model=source.model or "", agent_ids=agent_ids, outputs=outputs)
    db.add(run)
    db.commit()
    db.refresh(run)
    log = RunLog(
        run_id=run.id,
        input_type=source.input_type or "",
        input_data=source.input_data or "",
        model=source.model or "",
        agent_snapshot=agent_snapshot,
        attribute_snapshot=attribute_snapshot,
        outputs=outputs,
        feedback={},
        usage=usage,
        filters=filters,
        requests=requests,
        client=getattr(source, "client", None) or "",
        meeting_type=getattr(source, "meeting_type", None) or "",
        meeting_title=getattr(source, "meeting_title", None) or "",
        reasoning_effort=getattr(source, "reasoning_effort", None) or "",
        run_group_id=payload.run_group_id or "",
        reused_from_log_id=original_id,
        reused_from_created_at=original_created_at,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return {"id": run.id, "outputs": log.outputs or {}, "usage": log.usage or {},
            "requests": log.requests or {}, "log_id": log.id,
            "run_group_id": log.run_group_id or "", "log": _out(log)}


@router.post("/check-existing", response_model=CheckExistingOut)
def check_existing(payload: CheckExistingIn, db: Session = Depends(get_db)):
    # Resolve the input once (same errors as create_run: 422/404/503).
    # Efforts are already validated by CheckModelSlot; use the first slot's
    # effort for input resolution (resolution is effort-independent).
    first_effort = (payload.models[0].reasoning_effort or "").strip()
    resolved_type, resolved_data, _, _ = _resolve_run_input(
        meeting_id=payload.meeting_id, input_type=payload.input_type,
        input_data=payload.input_data, reasoning_effort=first_effort)
    agents = _load_run_agents(db, payload.agent_ids)
    matches: list[dict] = []
    for slot in payload.models:
        model = slot.model
        if not isinstance(model, str) or not model.strip():
            raise HTTPException(422, "model must be non-blank")
        effort = (slot.reasoning_effort or "").strip()
        if effort and effort not in REASONING_EFFORTS:
            raise HTTPException(422, "reasoning_effort must be max|xhigh|high|medium|low|minimal|none")
        _, expected_requests = _build_agent_requests(
            db, agents=agents, input_data=resolved_data,
            model=model, reasoning_effort=effort)
        log = _find_existing_log(
            db, model=model, reasoning_effort=effort,
            expected_requests=expected_requests)
        if log is not None:
            matches.append({"model": model, "reasoning_effort": effort,
                            "log": _out(log)})
    return {"matches": matches}


@router.post("/feedback-batch")
def batch_feedback(payload: BatchFeedbackIn, db: Session = Depends(get_db)):
    # Validate everything before mutating: unknown run_id -> 404, nothing applied.
    logs_by_run: dict[str, RunLog] = {}
    for item in payload.items:
        run = db.query(Run).filter(Run.id == item.run_id).first()
        if not run:
            raise HTTPException(404, "run not found")
        log = db.query(RunLog).filter(RunLog.run_id == item.run_id).first()
        if not log:
            raise HTTPException(404, "run not found")
        logs_by_run[item.run_id] = log
    from sqlalchemy.orm.attributes import flag_modified
    applied = 0
    skipped = 0
    for item in payload.items:
        log = logs_by_run[item.run_id]
        fb = dict(log.feedback or {}) if isinstance(log.feedback, dict) else {}
        # Rebuild nested dicts so mutation is visible even without flag_modified.
        fb = {k: dict(v) if isinstance(v, dict) else {} for k, v in fb.items()}
        if payload.only_unrated and _cell_is_rated(fb, item.agent_name, item.attribute_name):
            skipped += 1
            continue
        history_rating, history_remarks = _apply_feedback_cell(
            fb, item.agent_name, item.attribute_name, item.rating, item.remarks)
        log.feedback = fb
        flag_modified(log, "feedback")
        db.add(Feedback(run_id=item.run_id, agent_name=item.agent_name,
                        attribute_name=item.attribute_name,
                        rating=history_rating, remarks=history_remarks))
        applied += 1
    db.commit()
    return {"ok": True, "applied": applied, "skipped": skipped}


@router.get("/{run_id}", response_model=RunDetail)
def get_run(run_id: str, db: Session = Depends(get_db)):
    r = db.query(Run).filter(Run.id == run_id).first()
    if not r:
        raise HTTPException(404, "run not found")
    return RunDetail(id=r.id, input_type=r.input_type, model=r.model,
                     agent_ids=r.agent_ids or [], created_at=r.created_at,
                     input_data=r.input_data, outputs=r.outputs or {})


@router.post("/{run_id}/feedback")
def add_feedback(run_id: str, payload: FeedbackCreate, db: Session = Depends(get_db)):
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(404, "run not found")
    if payload.rating not in ("up", "down", ""):
        raise HTTPException(422, "rating must be up|down|''")
    log = db.query(RunLog).filter(RunLog.run_id == run_id).first()
    if log:
        fb = dict(log.feedback or {}) if isinstance(log.feedback, dict) else {}
        fb = {k: dict(v) if isinstance(v, dict) else {} for k, v in fb.items()}
        history_rating, history_remarks = _apply_feedback_cell(
            fb, payload.agent_name, payload.attribute_name, payload.rating, payload.remarks)
        log.feedback = fb
        # Re-assign so SQLAlchemy flags the JSON column dirty on every backend.
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(log, "feedback")
    else:
        existing_remarks = ""
        new_remarks = existing_remarks if payload.remarks is None else (payload.remarks or "")
        history_rating = "clear" if payload.rating == "" else payload.rating
        history_remarks = new_remarks
    db.add(Feedback(run_id=run_id, agent_name=payload.agent_name, attribute_name=payload.attribute_name,
                    rating=history_rating, remarks=history_remarks))
    db.commit()
    return {"ok": True}


@router.get("/{run_id}/feedback", response_model=list[FeedbackOut])
def list_feedback(run_id: str, db: Session = Depends(get_db)):
    if not db.query(Run).filter(Run.id == run_id).first():
        raise HTTPException(404, "run not found")
    rows = db.query(Feedback).filter(Feedback.run_id == run_id).order_by(Feedback.created_at.asc()).all()
    return [FeedbackOut(id=r.id, run_id=r.run_id, agent_name=r.agent_name,
                        attribute_name=r.attribute_name, rating=r.rating, remarks=r.remarks) for r in rows]
