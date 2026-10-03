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
    BatchFeedbackIn, CheckExistingAutoOut, CheckExistingIn, CheckExistingOut,
    FeedbackCreate, FeedbackOut, RetryAgentIn, ReuseRunIn, RunCreate, RunDetail, RunOut,
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


AUTO_REMARK_MISSED = "Auto: identifier listed it, agent returned null"
AUTO_REMARK_UNEXPECTED = "Auto: extracted but identifier did not list it"


def _agent_display_name(agent) -> str:
    if isinstance(agent, str):
        return agent
    if isinstance(agent, dict):
        n = agent.get("name", "")
        return n if isinstance(n, str) else ""
    return str(getattr(agent, "name", "") or "")


def _agent_kind_of(agent) -> str:
    if isinstance(agent, dict):
        k = agent.get("kind", "")
        return k if isinstance(k, str) else ""
    return str(getattr(agent, "kind", "") or "")


def _valid_attr_names(value) -> list[str]:
    out: list[str] = []
    if value is None:
        return out
    items = value if isinstance(value, (list, tuple)) else []
    for item in items:
        if isinstance(item, str):
            if item:
                out.append(item)
        elif isinstance(item, dict):
            n = item.get("name", "")
            if isinstance(n, str) and n:
                out.append(n)
        else:
            n = getattr(item, "name", None)
            if isinstance(n, str) and n:
                out.append(n)
    return out


def _is_filled_entry(entry) -> bool:
    if isinstance(entry, dict) and "value" in entry:
        if entry.get("confidence_type") == "not_found":
            return False
        v = entry.get("value")
    elif isinstance(entry, dict) and "confidence_type" in entry:
        if entry.get("confidence_type") == "not_found":
            return False
        v = entry.get("value", entry)
    else:
        v = entry
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, tuple)):
        return len(v) > 0
    if isinstance(v, dict):
        return len(v) > 0
    return True


def compute_consistency(identifier_output, outputs, agents_by_id, attrs_by_agent) -> dict:
    """Pure consistency snapshot for an auto-select run.

    - predicted: identifier fillable list for that agent (valid names only).
    - extracted: output attrs that are filled (value not null/""/[]/{},
      confidence_type != not_found).
    - missed/unexpected + Jaccard score per agent; micro score overall.
    """
    agents_by_id = agents_by_id or {}
    attrs_by_agent = attrs_by_agent or {}
    outputs = outputs if isinstance(outputs, dict) else {}
    # Find identifier id: agent with kind == identifier, else output identity.
    identifier_agent_id = ""
    try:
        for aid, ag in (agents_by_id or {}).items():
            if _agent_kind_of(ag) == IDENTIFIER_KIND:
                identifier_agent_id = str(aid)
                break
    except Exception:
        identifier_agent_id = ""
    if not identifier_agent_id:
        try:
            for aid, out in outputs.items():
                if out is identifier_output:
                    identifier_agent_id = str(aid)
                    break
        except Exception:
            pass
    fillable_by_name: dict[str, list[str]] = {}
    if isinstance(identifier_output, dict) and "_error" not in identifier_output:
        raw_fillable = identifier_output.get("fillable_attributes", [])
        if isinstance(raw_fillable, list):
            for entry in raw_fillable:
                if not isinstance(entry, dict):
                    continue
                an = entry.get("agent")
                al = entry.get("attributes")
                if not isinstance(an, str) or not isinstance(al, list):
                    continue
                seen: set[str] = set()
                kept: list[str] = []
                for n in al:
                    if not isinstance(n, str) or not n:
                        continue
                    if n in seen:
                        continue
                    seen.add(n)
                    kept.append(n)
                if an not in fillable_by_name:
                    fillable_by_name[an] = kept
                else:
                    for n in kept:
                        if n not in fillable_by_name[an]:
                            fillable_by_name[an].append(n)
    agents_out: dict = {}
    total_inter = 0
    total_union = 0
    for aid, out in outputs.items():
        if str(aid) == identifier_agent_id:
            continue
        if not isinstance(out, dict) or "_error" in out:
            continue
        ag = (agents_by_id or {}).get(aid)
        agent_name = _agent_display_name(ag) if ag is not None else ""
        if not agent_name:
            continue
        valid_list = _valid_attr_names((attrs_by_agent or {}).get(aid, []))
        valid_set = set(valid_list)
        raw_pred = fillable_by_name.get(agent_name, [])
        predicted: list[str] = []
        seen_p: set[str] = set()
        for n in raw_pred:
            if n not in valid_set or n in seen_p:
                continue
            seen_p.add(n)
            predicted.append(n)
        extracted: list[str] = []
        try:
            for attr_name, entry in out.items():
                if not isinstance(attr_name, str) or not attr_name:
                    continue
                if attr_name.startswith("_"):
                    continue
                if _is_filled_entry(entry):
                    extracted.append(attr_name)
        except Exception:
            extracted = []
        pred_set = set(predicted)
        extr_set = set(extracted)
        missed = [a for a in predicted if a not in extr_set]
        unexpected = [a for a in extracted if a not in pred_set]
        inter = len(pred_set & extr_set)
        union = len(pred_set | extr_set)
        score = 1.0 if union == 0 else inter / union
        total_inter += inter
        total_union += union
        agents_out[str(aid)] = {
            "agent_name": agent_name,
            "predicted": predicted,
            "extracted": extracted,
            "missed": missed,
            "unexpected": unexpected,
            "score": float(score),
        }
    overall = None if total_union == 0 and not agents_out else (
        1.0 if total_union == 0 else total_inter / total_union)
    if not agents_out:
        overall = None
    return {"auto": True, "score": overall, "identifier_agent_id": identifier_agent_id, "agents": agents_out}


def apply_auto_feedback(feedback, consistency) -> dict:
    """Return a copy of feedback with auto thumbs for missed/unexpected.

    Deletes that agent's previous auto entries first; never overwrites a
    non-auto (manual) entry.
    """
    fb: dict = {}
    try:
        src = feedback if isinstance(feedback, dict) else {}
        for k, v in src.items():
            if isinstance(v, dict):
                fb[k] = {ak: dict(av) if isinstance(av, dict) else {} for ak, av in v.items()}
            else:
                fb[k] = {}
    except Exception:
        fb = {}
    agents = {}
    try:
        agents = (consistency or {}).get("agents", {}) or {}
    except Exception:
        agents = {}
    if not isinstance(agents, dict):
        agents = {}
    for aid, cons in agents.items():
        if not isinstance(cons, dict):
            continue
        agent_name = cons.get("agent_name", "")
        if not isinstance(agent_name, str) or not agent_name:
            continue
        missed = cons.get("missed", []) if isinstance(cons.get("missed", []), list) else []
        unexpected = cons.get("unexpected", []) if isinstance(cons.get("unexpected", []), list) else []
        agent_map = fb.get(agent_name)
        if not isinstance(agent_map, dict):
            agent_map = {}
            fb[agent_name] = agent_map
        for attr in list(agent_map.keys()):
            try:
                cell = agent_map.get(attr)
                if isinstance(cell, dict) and cell.get("auto") is True:
                    del agent_map[attr]
            except Exception:
                continue
        if not agent_map and agent_name in fb and not agent_map:
            # Keep empty map for now; re-created below if autos apply.
            pass
        seen_attrs: set[str] = set()
        ordered: list[tuple[str, str]] = []
        for a in missed:
            if isinstance(a, str) and a and a not in seen_attrs:
                seen_attrs.add(a)
                ordered.append((a, AUTO_REMARK_MISSED))
        for a in unexpected:
            if isinstance(a, str) and a and a not in seen_attrs:
                seen_attrs.add(a)
                ordered.append((a, AUTO_REMARK_UNEXPECTED))
        for attr, remark in ordered:
            existing = agent_map.get(attr)
            if isinstance(existing, dict) and existing.get("auto") is not True:
                # Manual entry (rating present without auto flag) wins.
                # Empty dicts left from copies count as absent.
                if existing.get("rating") in ("up", "down"):
                    continue
                if "rating" in existing and existing.get("rating"):
                    continue
            agent_map[attr] = {"rating": "down", "remarks": remark, "auto": True}
        if not agent_map and agent_name in fb:
            del fb[agent_name]
    return fb


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


async def _call_single_payload(payload_body: dict):
    """One OpenRouter call + timing (shared by create_run/retry/auto).

    Returns (parsed, prompt_tokens, completion_tokens, total_tokens,
    reasoning_tokens, duration_ms). Errors become ({"_error": ...}, zeros).
    """
    agent_start = perf_counter()
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
        return parsed, prompt_tokens, completion_tokens, total_tokens, reasoning_tokens, duration_ms
    except Exception as exc:
        duration_ms = (perf_counter() - agent_start) * 1000.0
        return {"_error": str(exc)}, 0, 0, 0, 0, duration_ms


def _per_agent_entry(prompt_tokens: int, completion_tokens: int, total_tokens: int,
                     reasoning_tokens: int, duration_ms: float, model: str) -> dict:
    return {
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


def _fill_pricing(per_agent: dict, prompt_price, completion_price, skip_ids=frozenset()) -> None:
    skip = set(skip_ids or ())
    if prompt_price is not None and completion_price is not None:
        for aid, entry in per_agent.items():
            if aid in skip or not isinstance(entry, dict):
                continue
            try:
                cost = entry["prompt_tokens"] * prompt_price + entry["completion_tokens"] * completion_price
            except Exception:
                continue
            entry["cost_usd"] = round(cost, 6)
    for aid, entry in per_agent.items():
        if aid in skip or not isinstance(entry, dict):
            continue
        try:
            if prompt_price is None:
                entry["input_cost_usd"] = None
            else:
                entry["input_cost_usd"] = round(entry["prompt_tokens"] * prompt_price, 6)
            if completion_price is None:
                entry["output_cost_usd"] = None
            else:
                entry["output_cost_usd"] = round(entry["completion_tokens"] * completion_price, 6)
        except Exception:
            pass


def _totals_from_per_agent(per_agent: dict, prompt_price=None, completion_price=None) -> dict:
    total_in = 0
    total_out = 0
    total_reason = 0
    try:
        for v in (per_agent or {}).values():
            if not isinstance(v, dict):
                continue
            try:
                total_in += int(v.get("prompt_tokens", 0) or 0)
                total_out += int(v.get("completion_tokens", 0) or 0)
                total_reason += int(v.get("reasoning_tokens", 0) or 0)
            except Exception:
                pass
    except Exception:
        pass
    if any(not isinstance(v.get("cost_usd"), (int, float)) for v in (per_agent or {}).values()):
        total_cost = None
    else:
        try:
            total_cost = round(sum((v["cost_usd"] for v in per_agent.values()), 0.0), 6)
        except Exception:
            total_cost = None
    if any(not isinstance(v.get("input_cost_usd"), (int, float)) for v in (per_agent or {}).values()):
        total_input_cost = None
    else:
        try:
            total_input_cost = round(sum((v["input_cost_usd"] for v in per_agent.values()), 0.0), 6)
        except Exception:
            total_input_cost = None
    if any(not isinstance(v.get("output_cost_usd"), (int, float)) for v in (per_agent or {}).values()):
        total_output_cost = None
    else:
        try:
            total_output_cost = round(sum((v["output_cost_usd"] for v in per_agent.values()), 0.0), 6)
        except Exception:
            total_output_cost = None
    if not per_agent:
        if prompt_price is None or completion_price is None:
            total_cost = None
            total_input_cost = None
            total_output_cost = None
        else:
            total_cost = 0.0
            total_input_cost = 0.0
            total_output_cost = 0.0
    return {
        "prompt_tokens": total_in,
        "completion_tokens": total_out,
        "total_tokens": total_in + total_out,
        "reasoning_tokens": total_reason,
        "cost_usd": total_cost,
        "input_cost_usd": total_input_cost,
        "output_cost_usd": total_output_cost,
    }


def _build_usage(per_agent: dict, model: str, totals: dict, duration_ms: float,
                 reused_agents: dict | None = None) -> dict:
    return {
        "prompt_tokens": totals["prompt_tokens"],
        "completion_tokens": totals["completion_tokens"],
        "total_tokens": totals["total_tokens"],
        "reasoning_tokens": totals["reasoning_tokens"],
        "cost_usd": totals["cost_usd"],
        "input_cost_usd": totals["input_cost_usd"],
        "output_cost_usd": totals["output_cost_usd"],
        "duration_ms": duration_ms,
        "model": model,
        "per_agent": per_agent,
        "reused_agents": reused_agents or {},
    }


async def _execute_agents(db: Session, agents: list[Agent], requests: dict, model: str):
    """Run per-agent payloads concurrently (shared machinery).

    Returns (outputs, per_agent) with costs None; caller prices + totals.
    Identifier outputs are normalised.
    """
    async def _run_one(agent, payload_body):
        parsed, pt, ct, tt, rt, dur = await _call_single_payload(copy.deepcopy(payload_body))
        return (agent.id, parsed, pt, ct, tt, rt, dur)

    bodies = [(a, copy.deepcopy(requests[a.id])) for a in agents if a.id in (requests or {})]
    results = await asyncio.gather(*[_run_one(a, b) for a, b in bodies])
    agents_by_id = {a.id: a for a in agents}
    outputs: dict = {}
    per_agent: dict = {}
    for aid, parsed, pt, ct, tt, rt, dur in results:
        agent_row = agents_by_id.get(aid)
        if agent_row is not None and is_identifier(agent_row):
            try:
                cands = identifier_candidates_with_attributes(db, exclude_id=aid)
                parsed = normalize_identifier_output(parsed, cands)
            except Exception:
                pass
        outputs[aid] = parsed
        per_agent[aid] = _per_agent_entry(pt, ct, tt, rt, dur, model)
    return outputs, per_agent


def _snapshot_attr_names(snapshot: dict) -> list[str]:
    try:
        attrs = (snapshot or {}).get("attributes", [])
        return [a.get("name", "") for a in attrs if isinstance(a, dict) and isinstance(a.get("name"), str)]
    except Exception:
        return []


def _consistency_inputs_from_log(log: RunLog):
    """Derive (identifier_output, outputs, agents_by_id, attrs_by_agent) from a log."""
    outputs = log.outputs if isinstance(getattr(log, "outputs", None), dict) else {}
    snaps = log.agent_snapshot if isinstance(getattr(log, "agent_snapshot", None), dict) else {}
    agents_by_id: dict = {}
    attrs_by_agent: dict = {}
    for aid, snap in snaps.items():
        if not isinstance(snap, dict):
            continue
        agents_by_id[str(aid)] = {"name": snap.get("name", "") or "", "kind": snap.get("kind", "") or ""}
        attrs_by_agent[str(aid)] = _snapshot_attr_names(snap)
    identifier_agent_id = ""
    try:
        cons = getattr(log, "consistency", None) or {}
        if isinstance(cons, dict) and isinstance(cons.get("identifier_agent_id"), str):
            identifier_agent_id = cons.get("identifier_agent_id") or ""
    except Exception:
        identifier_agent_id = ""
    if not identifier_agent_id:
        for aid, info in agents_by_id.items():
            if info.get("kind") == IDENTIFIER_KIND:
                identifier_agent_id = str(aid)
                break
    identifier_output = outputs.get(identifier_agent_id, {}) if identifier_agent_id else {}
    return identifier_output, outputs, agents_by_id, attrs_by_agent, identifier_agent_id


def _iso_str(value) -> str:
    """ISO string for datetimes/strings ("" when absent)."""
    from datetime import datetime as _dt

    if isinstance(value, _dt):
        try:
            return value.isoformat()
        except Exception:
            return ""
    if isinstance(value, str):
        return value
    return ""


def _num_or_none(value):
    return value if isinstance(value, (int, float)) else None


def _source_agent_info(log: RunLog, agent_id: str) -> tuple[str, float | None, float | None]:
    """(created_at ISO, cost_usd, duration_ms) for one agent from a source log.

    created_at is the ORIGINAL generation time: the per-agent
    ``reused_from_created_at`` when the source agent was itself reused,
    else the log-level ``reused_from_created_at`` (whole-log reuse chain),
    else the log's ``created_at``. cost/duration come straight from the
    source per-agent entry (None when absent).
    """
    usage = getattr(log, "usage", None) or {}
    if not isinstance(usage, dict):
        usage = {}
    per = usage.get("per_agent", {})
    if not isinstance(per, dict):
        per = {}
    entry = per.get(agent_id)
    if not isinstance(entry, dict):
        entry = {}
    cost = _num_or_none(entry.get("cost_usd"))
    duration = _num_or_none(entry.get("duration_ms"))
    reused_at = entry.get("reused_from_created_at")
    if isinstance(reused_at, str) and reused_at:
        created = reused_at
    elif reused_at is not None and not isinstance(reused_at, str):
        # datetime stored in-memory (SQLite returns datetime); JSON stores str.
        try:
            from datetime import datetime as _dt2

            created = reused_at.isoformat() if isinstance(reused_at, _dt2) else ""
        except Exception:
            created = ""
        if not created:
            created = _iso_str(getattr(log, "created_at", ""))
    else:
        log_reused = getattr(log, "reused_from_created_at", None)
        if isinstance(log_reused, str) and log_reused:
            created = log_reused
        elif log_reused is not None and not isinstance(log_reused, str):
            try:
                from datetime import datetime as _dt3

                created = log_reused.isoformat() if isinstance(log_reused, _dt3) else ""
            except Exception:
                created = ""
            if not created:
                created = _iso_str(getattr(log, "created_at", ""))
        else:
            created = _iso_str(getattr(log, "created_at", ""))
    return created, cost, duration


def _find_existing_log_for_agent(
    db: Session, *, model: str, reasoning_effort: str,
    agent_id: str, expected_messages,
) -> RunLog | None:
    """Newest RunLog (same model+effort) with a usable output for one agent.

    - SQL filter on model + reasoning_effort, newest first, limit 300.
    - ``log.requests[agent_id]["messages"]`` must equal the would-be-sent
      messages. Other agents in the log do not matter (any agent mix).
    - ``log.outputs[agent_id]`` must exist, be a dict, and have no "_error".
    """
    effort = (reasoning_effort or "").strip()
    rows = (
        db.query(RunLog)
        .filter(RunLog.model == model, RunLog.reasoning_effort == effort)
        .order_by(RunLog.created_at.desc())
        .limit(300)
        .all()
    )
    for log in rows:
        stored_reqs = log.requests or {}
        if not isinstance(stored_reqs, dict):
            continue
        stored_body = stored_reqs.get(agent_id)
        if not isinstance(stored_body, dict):
            continue
        if stored_body.get("messages") != expected_messages:
            continue
        outs = log.outputs or {}
        if not isinstance(outs, dict):
            continue
        out = outs.get(agent_id)
        if not isinstance(out, dict):
            continue
        if "_error" in out:
            continue
        return log
    return None


def _is_valid_reuse_source(
    source: RunLog, *, agent_id: str, model: str,
    reasoning_effort: str, expected_messages,
) -> bool:
    """Same rule as per-agent matching (server-side reuse validation)."""
    try:
        if (getattr(source, "model", None) or "") != model:
            return False
        if (getattr(source, "reasoning_effort", None) or "") != (reasoning_effort or ""):
            return False
        stored_reqs = getattr(source, "requests", None) or {}
        if not isinstance(stored_reqs, dict):
            return False
        stored_body = stored_reqs.get(agent_id)
        if not isinstance(stored_body, dict):
            return False
        if stored_body.get("messages") != expected_messages:
            return False
        outs = getattr(source, "outputs", None) or {}
        if not isinstance(outs, dict):
            return False
        out = outs.get(agent_id)
        if not isinstance(out, dict):
            return False
        if "_error" in out:
            return False
        return True
    except Exception:
        return False


def _build_reused_per_agent(source: RunLog, agent_id: str) -> tuple[dict, str, str]:
    """Copy source per-agent entry + reused markers.

    Returns (per_agent_entry, reused_from_log_id, reused_from_created_at).
    When the source entry itself was reused, its original reused_from_*
    values are kept. Whole-log reuse chains (log-level reused_from_*) are
    honoured as the original when the per-agent entry carries no markers.
    """
    usage = getattr(source, "usage", None) or {}
    if not isinstance(usage, dict):
        usage = {}
    per = usage.get("per_agent", {})
    if not isinstance(per, dict):
        per = {}
    raw = per.get(agent_id)
    if isinstance(raw, dict):
        entry = copy.deepcopy(raw)
    else:
        entry = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "reasoning_tokens": 0,
            "cost_usd": None,
            "input_cost_usd": None,
            "output_cost_usd": None,
            "duration_ms": 0.0,
            "model": getattr(source, "model", None) or "",
        }
    existing_id = entry.get("reused_from_log_id")
    existing_at = entry.get("reused_from_created_at")
    if isinstance(existing_id, str) and existing_id:
        rid = existing_id
        rat = existing_at if isinstance(existing_at, str) and existing_at else _iso_str(
            getattr(source, "created_at", ""))
        entry["reused_from_log_id"] = rid
        entry["reused_from_created_at"] = rat
        return entry, rid, rat
    log_rid = getattr(source, "reused_from_log_id", None) or ""
    log_rat = getattr(source, "reused_from_created_at", None)
    if isinstance(log_rid, str) and log_rid:
        rat_iso = _iso_str(log_rat) or _iso_str(getattr(source, "created_at", ""))
        entry["reused_from_log_id"] = log_rid
        entry["reused_from_created_at"] = rat_iso
        return entry, log_rid, rat_iso
    rid2 = getattr(source, "id", "") or ""
    rat2 = _iso_str(getattr(source, "created_at", ""))
    entry["reused_from_log_id"] = rid2
    entry["reused_from_created_at"] = rat2
    return entry, rid2, rat2


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

    # --- Per-agent reuse: validate each reuse entry server-side ---------------
    # Same rule as matching (messages equal, output present without _error,
    # same model+effort). Invalid entries are ignored silently (fresh run).
    reuse_map = getattr(payload, "reuse", None)
    if not isinstance(reuse_map, dict):
        reuse_map = {}
    valid_reuse: dict[str, RunLog] = {}
    for agent in agents:
        try:
            src_id = reuse_map.get(agent.id)
        except Exception:
            src_id = None
        if not isinstance(src_id, str) or not src_id.strip():
            continue
        src_id = src_id.strip()
        try:
            source = db.query(RunLog).filter(RunLog.id == src_id).first()
        except Exception:
            source = None
        if source is None:
            continue
        fresh_body = requests.get(agent.id)
        fresh_msgs = fresh_body.get("messages") if isinstance(fresh_body, dict) else None
        if not _is_valid_reuse_source(
            source, agent_id=agent.id, model=payload.model,
            reasoning_effort=effort, expected_messages=fresh_msgs,
        ):
            continue
        valid_reuse[agent.id] = source

    outputs: dict = {}
    per_agent: dict = {}
    reused_agents: dict = {}
    # Seed reused agents (no OpenRouter call; requests stay freshly built).
    for aid, source in list(valid_reuse.items()):
        try:
            src_outs = getattr(source, "outputs", None) or {}
            if not isinstance(src_outs, dict) or aid not in src_outs:
                raise KeyError(aid)
            outputs[aid] = copy.deepcopy(src_outs.get(aid))
            entry, rid, rat = _build_reused_per_agent(source, aid)
            per_agent[aid] = entry
            reused_agents[aid] = {"log_id": rid, "created_at": rat}
        except Exception:
            # Treat as fresh on unexpected copy failure.
            valid_reuse.pop(aid, None)
            outputs.pop(aid, None)
            per_agent.pop(aid, None)
            reused_agents.pop(aid, None)
            continue

    fresh_agents = [a for a in agents if a.id not in valid_reuse]
    fresh_bodies = [(a, copy.deepcopy(requests[a.id]), payload.model) for a in fresh_agents]
    results = await asyncio.gather(*[_run_one(a, b, m) for a, b, m in fresh_bodies])
    total_in = 0
    total_out = 0
    total_in_reasoning = 0
    # Add reused tokens to totals.
    for aid in valid_reuse:
        try:
            e = per_agent.get(aid, {})
            total_in += int(e.get("prompt_tokens", 0) or 0)
            total_out += int(e.get("completion_tokens", 0) or 0)
            total_in_reasoning += int(e.get("reasoning_tokens", 0) or 0)
        except Exception:
            pass
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
    if fresh_agents or not valid_reuse:
        try:
            prompt_price, completion_price = await get_model_pricing(payload.model)
        except Exception:
            prompt_price, completion_price = None, None
    else:
        # All reused: no OpenRouter calls at all (not even pricing).
        prompt_price, completion_price = None, None
    # Price ONLY fresh agents; reused keep their source numbers verbatim.
    reused_ids = set(valid_reuse.keys())
    if prompt_price is not None and completion_price is not None:
        for aid, entry in per_agent.items():
            if aid in reused_ids:
                continue
            try:
                cost = entry["prompt_tokens"] * prompt_price + entry["completion_tokens"] * completion_price
            except Exception:
                continue
            entry["cost_usd"] = round(cost, 6)
    for aid, entry in per_agent.items():
        if aid in reused_ids:
            continue
        try:
            if prompt_price is None:
                entry["input_cost_usd"] = None
            else:
                entry["input_cost_usd"] = round(entry["prompt_tokens"] * prompt_price, 6)
            if completion_price is None:
                entry["output_cost_usd"] = None
            else:
                entry["output_cost_usd"] = round(entry["completion_tokens"] * completion_price, 6)
        except Exception:
            pass
    # Totals include reused agents' original numbers. Any None -> None,
    # the same way the code treats unknown pricing today.
    if any(not isinstance(v.get("cost_usd"), (int, float)) for v in per_agent.values()):
        total_cost: float | None = None
    else:
        try:
            total_cost = round(sum((v["cost_usd"] for v in per_agent.values()), 0.0), 6)
        except Exception:
            total_cost = None
    if any(not isinstance(v.get("input_cost_usd"), (int, float)) for v in per_agent.values()):
        total_input_cost: float | None = None
    else:
        try:
            total_input_cost = round(sum((v["input_cost_usd"] for v in per_agent.values()), 0.0), 6)
        except Exception:
            total_input_cost = None
    if any(not isinstance(v.get("output_cost_usd"), (int, float)) for v in per_agent.values()):
        total_output_cost: float | None = None
    else:
        try:
            total_output_cost = round(sum((v["output_cost_usd"] for v in per_agent.values()), 0.0), 6)
        except Exception:
            total_output_cost = None
    # Empty-run edge: no agents -> totals mirror the old zero-agent math
    # (0.0 when pricing known, None when unknown). The any() above is
    # False for {}, so sums are 0.0 — but only when pricing is known;
    # when pricing is unknown there are no entries to be None, yet old
    # code left total_cost None. Preserve that.
    if not per_agent:
        if prompt_price is None or completion_price is None:
            total_cost = None
            total_input_cost = None
            total_output_cost = None
        else:
            total_cost = 0.0
            total_input_cost = 0.0
            total_output_cost = 0.0
        # When fresh agents exist but pricing unknown, the any() above is
        # already None-correct; nothing more to do.
        if fresh_agents and (prompt_price is None or completion_price is None):
            # total_cost already None via per-agent Nones; input/output too.
            pass
    wall_ms = (perf_counter() - wall_start) * 1000.0
    usage_duration_ms = wall_ms
    try:
        for aid in valid_reuse:
            d = per_agent.get(aid, {}).get("duration_ms")
            if isinstance(d, (int, float)) and d > usage_duration_ms:
                usage_duration_ms = float(d)
    except Exception:
        pass
    usage = {
        "prompt_tokens": total_in,
        "completion_tokens": total_out,
        "total_tokens": total_in + total_out,
        "reasoning_tokens": total_in_reasoning,
        "cost_usd": total_cost,
        "input_cost_usd": total_input_cost,
        "output_cost_usd": total_output_cost,
        "duration_ms": usage_duration_ms,
        "model": payload.model,
        "per_agent": per_agent,
        "reused_agents": reused_agents,
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
    # Log-level reused markers: set (to the single source) only when EVERY
    # agent was reused from the same source log; otherwise "" / None.
    log_reused_id = ""
    log_reused_at = None
    try:
        if agents and len(valid_reuse) == len(agents) and agents:
            src_ids = set()
            for _aid, _src in valid_reuse.items():
                try:
                    src_ids.add(_src.id)
                except Exception:
                    pass
            if len(src_ids) == 1:
                only_src = next(iter(valid_reuse.values()))
                # Point at the ORIGINAL like POST /runs/reuse does.
                only_orig_id = getattr(only_src, "reused_from_log_id", None) or getattr(
                    only_src, "id", "") or ""
                only_orig_at = getattr(only_src, "reused_from_created_at", None) or getattr(
                    only_src, "created_at", None)
                log_reused_id = only_orig_id or ""
                log_reused_at = only_orig_at
    except Exception:
        log_reused_id = ""
        log_reused_at = None
    # Denormalized log row — snapshots only, no FK to agents/attributes.
    log = RunLog(run_id=run.id, input_type=run.input_type, input_data=run.input_data, model=run.model,
                  agent_snapshot=snapshots, attribute_snapshot=snapshots, outputs=outputs, feedback={},
                  usage=usage, filters=filters, requests=requests,
                  client=client, meeting_type=meeting_type, meeting_title=meeting_title,
                  reasoning_effort=effort, run_group_id=payload.run_group_id or "",
                  reused_from_log_id=log_reused_id or "",
                  reused_from_created_at=log_reused_at, consistency={})
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
        _reuse_cons = copy.deepcopy(getattr(source, "consistency", None) or {})
        if not isinstance(_reuse_cons, dict):
            _reuse_cons = {}
    except Exception:
        _reuse_cons = {}
    # Auto logs carry auto feedback forward (manual ratings are not copied);
    # non-auto logs start empty (unchanged behaviour).
    try:
        _reuse_fb: dict = {}
        if isinstance(_reuse_cons, dict) and _reuse_cons.get("auto") is True:
            _src_fb = getattr(source, "feedback", None) or {}
            if isinstance(_src_fb, dict):
                for _ag_name, _attr_map in _src_fb.items():
                    if not isinstance(_attr_map, dict):
                        continue
                    _kept: dict = {}
                    for _at_name, _cell in _attr_map.items():
                        if isinstance(_cell, dict) and _cell.get("auto") is True:
                            _kept[_at_name] = copy.deepcopy(_cell)
                    if _kept:
                        _reuse_fb[_ag_name] = _kept
    except Exception:
        _reuse_fb = {}
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
        feedback=_reuse_fb,
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
        consistency=_reuse_cons,
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
    slots: list[dict] = []
    for slot in payload.models:
        model = slot.model
        if not isinstance(model, str) or not model.strip():
            raise HTTPException(422, "model must be non-blank")
        effort = (slot.reasoning_effort or "").strip()
        if effort and effort not in REASONING_EFFORTS:
            raise HTTPException(422, "reasoning_effort must be max|xhigh|high|medium|low|minimal|none")
        # Per-slot request bodies (messages may differ per model, e.g. the
        # Anthropic fallback appends the schema to the system message).
        _, expected_requests = _build_agent_requests(
            db, agents=agents, input_data=resolved_data,
            model=model, reasoning_effort=effort)
        slot_agents: list[dict] = []
        for agent in agents:
            exp_body = expected_requests.get(agent.id)
            exp_msgs = exp_body.get("messages") if isinstance(exp_body, dict) else None
            log = _find_existing_log_for_agent(
                db, model=model, reasoning_effort=effort,
                agent_id=agent.id, expected_messages=exp_msgs)
            if log is None:
                continue
            created_iso, cost, duration = _source_agent_info(log, agent.id)
            slot_agents.append({
                "agent_id": agent.id,
                "agent_name": agent.name,
                "log_id": log.id,
                "created_at": created_iso,
                "cost_usd": cost,
                "duration_ms": duration,
            })
        slots.append({"model": model, "reasoning_effort": effort,
                      "agents": slot_agents})
    return {"slots": slots}


def _find_existing_auto_log(
    db: Session, *, model: str, reasoning_effort: str,
    identifier_id: str, expected_messages,
) -> RunLog | None:
    """Newest auto RunLog (same model+effort) matching the identifier request.

    - SQL filter on model + reasoning_effort, newest first, limit 300.
    - ``consistency`` must be a dict with ``auto is True``.
    - ``requests[identifier_id]["messages"]`` must equal the would-be-sent
      identifier messages (other agents in the log do not matter).
    - ``outputs[identifier_id]`` must exist, be a dict, and have no "_error".
    """
    effort = (reasoning_effort or "").strip()
    rows = (
        db.query(RunLog)
        .filter(RunLog.model == model, RunLog.reasoning_effort == effort)
        .order_by(RunLog.created_at.desc())
        .limit(300)
        .all()
    )
    for log in rows:
        try:
            cons = getattr(log, "consistency", None)
        except Exception:
            cons = None
        if not isinstance(cons, dict) or cons.get("auto") is not True:
            continue
        iid = cons.get("identifier_agent_id")
        if not isinstance(iid, str) or not iid:
            iid = identifier_id
        stored_reqs = getattr(log, "requests", None) or {}
        if not isinstance(stored_reqs, dict):
            continue
        stored_body = stored_reqs.get(iid)
        if not isinstance(stored_body, dict):
            # Fall back to the current identifier id (identifier recreation).
            if iid != identifier_id:
                stored_body = stored_reqs.get(identifier_id)
            if not isinstance(stored_body, dict):
                continue
        if stored_body.get("messages") != expected_messages:
            continue
        outs = getattr(log, "outputs", None) or {}
        if not isinstance(outs, dict):
            continue
        out = outs.get(iid)
        if not isinstance(out, dict):
            if iid != identifier_id:
                out = outs.get(identifier_id)
            if not isinstance(out, dict):
                continue
        if "_error" in out:
            continue
        return log
    return None


@router.post("/check-existing-auto", response_model=CheckExistingAutoOut)
def check_existing_auto(payload: CheckExistingIn, db: Session = Depends(get_db)):
    """Auto-run variant of check-existing (agent_ids ignored).

    For each model slot (same order): find the newest auto RunLog with the
    same model + reasoning_effort whose identifier request messages equal
    the identifier request /runs/auto would send now, and whose identifier
    output has no "_error".
    """
    first_effort = (payload.models[0].reasoning_effort or "").strip()
    _, resolved_data, _, _ = _resolve_run_input(
        meeting_id=payload.meeting_id, input_type=payload.input_type,
        input_data=payload.input_data, reasoning_effort=first_effort)
    identifier = (
        db.query(Agent).filter(Agent.kind == IDENTIFIER_KIND)
        .order_by(Agent.name.asc()).first()
    )
    if identifier is None:
        raise HTTPException(400, "No agent identifier defined")
    ident_id = identifier.id
    slots: list[dict] = []
    for slot in payload.models:
        model = slot.model
        if not isinstance(model, str) or not model.strip():
            raise HTTPException(422, "model must be non-blank")
        effort = (slot.reasoning_effort or "").strip()
        if effort and effort not in REASONING_EFFORTS:
            raise HTTPException(422, "reasoning_effort must be max|xhigh|high|medium|low|minimal|none")
        _, expected_requests = _build_agent_requests(
            db, agents=[identifier], input_data=resolved_data,
            model=model, reasoning_effort=effort)
        exp_body = expected_requests.get(ident_id)
        exp_msgs = exp_body.get("messages") if isinstance(exp_body, dict) else None
        log = _find_existing_auto_log(
            db, model=model, reasoning_effort=effort,
            identifier_id=ident_id, expected_messages=exp_msgs)
        slots.append({"model": model, "reasoning_effort": effort,
                      "log": _out(log) if log is not None else None})
    return {"slots": slots}


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


@router.post("/auto")
async def auto_run(payload: RunCreate, db: Session = Depends(get_db)):
    """Auto Select Agents: run identifier, then its selected extraction agents.

    One log per call (one model). Body is RunCreate minus agent_ids/reuse
    (those are ignored). Response shape identical to POST /runs.
    """
    wall_start = perf_counter()
    resolved_type, resolved_data, effort, task_title = _resolve_run_input(
        meeting_id=payload.meeting_id, input_type=payload.input_type,
        input_data=payload.input_data, reasoning_effort=payload.reasoning_effort)
    identifier = (
        db.query(Agent).filter(Agent.kind == IDENTIFIER_KIND)
        .order_by(Agent.name.asc()).first()
    )
    if identifier is None:
        raise HTTPException(400, "No agent identifier defined")
    ident_id = identifier.id
    snapshots_ident, requests_ident = _build_agent_requests(
        db, agents=[identifier], input_data=resolved_data,
        model=payload.model, reasoning_effort=effort)
    ident_body = copy.deepcopy(requests_ident.get(ident_id, {}))
    parsed_ident, ipt, ict, itt, irt, idur = await _call_single_payload(ident_body)
    if isinstance(parsed_ident, dict) and "_error" not in parsed_ident:
        try:
            cands = identifier_candidates_with_attributes(db, exclude_id=ident_id)
            parsed_ident = normalize_identifier_output(parsed_ident, cands)
        except Exception:
            pass
    per_ident = _per_agent_entry(ipt, ict, itt, irt, idur, payload.model)
    ident_errored = not isinstance(parsed_ident, dict) or "_error" in parsed_ident
    if ident_errored:
        outputs: dict = {ident_id: parsed_ident}
        per_agent: dict = {ident_id: per_ident}
        snapshots = snapshots_ident
        requests = requests_ident
        agents = [identifier]
        agent_ids = [ident_id]
        consistency = {"auto": True, "score": None, "identifier_agent_id": ident_id, "agents": {}}
        feedback: dict = {}
    else:
        raw_selected = parsed_ident.get("selected_agents", []) if isinstance(parsed_ident, dict) else []
        selected_names: list[str] = []
        if isinstance(raw_selected, list):
            seen_sel: set[str] = set()
            for n in raw_selected:
                if not isinstance(n, str) or not n or n in seen_sel:
                    continue
                seen_sel.add(n)
                selected_names.append(n)
        rows = (
            db.query(Agent).filter(Agent.name.in_(selected_names)).all()
            if selected_names else []
        )
        by_name: dict[str, Agent] = {}
        for r in rows:
            try:
                if is_identifier(r):
                    continue
                if r.name not in by_name:
                    by_name[r.name] = r
            except Exception:
                continue
        selected_agents: list[Agent] = []
        for n in selected_names:
            ag = by_name.get(n)
            if ag is not None and ag.id not in {a.id for a in selected_agents}:
                selected_agents.append(ag)
        if not selected_agents:
            outputs = {ident_id: parsed_ident}
            per_agent = {ident_id: per_ident}
            snapshots = snapshots_ident
            requests = requests_ident
            agents = [identifier]
            agent_ids = [ident_id]
            # No checked agents -> score None, but still an auto log.
            agents_by_id0 = {ident_id: identifier}
            attrs_by_agent0 = {ident_id: []}
            try:
                attrs_by_agent0.update({
                    k: _snapshot_attr_names(v) for k, v in snapshots.items()
                })
            except Exception:
                pass
            consistency = compute_consistency(parsed_ident, outputs, agents_by_id0, attrs_by_agent0)
            # Ensure identifier id present even when no extraction agents.
            try:
                if not consistency.get("identifier_agent_id"):
                    consistency["identifier_agent_id"] = ident_id
            except Exception:
                pass
            feedback = apply_auto_feedback({}, consistency)
        else:
            snapshots_sel, requests_sel = _build_agent_requests(
                db, agents=selected_agents, input_data=resolved_data,
                model=payload.model, reasoning_effort=effort)
            snapshots = {**snapshots_ident, **snapshots_sel}
            requests = {**requests_ident, **requests_sel}
            outputs_sel, per_agent_sel = await _execute_agents(db, selected_agents, requests_sel, payload.model)
            outputs = {ident_id: parsed_ident, **outputs_sel}
            per_agent = {ident_id: per_ident, **per_agent_sel}
            agents = [identifier] + selected_agents
            agent_ids = [ident_id] + [a.id for a in selected_agents]
            agents_by_id = {a.id: a for a in agents}
            attrs_by_agent = {}
            for aid, snap in snapshots.items():
                attrs_by_agent[str(aid)] = _snapshot_attr_names(snap)
            consistency = compute_consistency(parsed_ident, outputs, agents_by_id, attrs_by_agent)
            feedback = apply_auto_feedback({}, consistency)
    try:
        prompt_price, completion_price = await get_model_pricing(payload.model)
    except Exception:
        prompt_price, completion_price = None, None
    _fill_pricing(per_agent, prompt_price, completion_price)
    totals = _totals_from_per_agent(per_agent, prompt_price, completion_price)
    wall_ms = (perf_counter() - wall_start) * 1000.0
    usage = _build_usage(per_agent, payload.model, totals, wall_ms, {})
    filters = payload.filters if isinstance(payload.filters, dict) else {}
    client_name = (payload.client or "").strip()
    meeting_type = (payload.meeting_type or "").strip() or task_title
    meeting_title = (payload.meeting_title or "").strip() or task_title
    run = Run(input_type=resolved_type, input_data=resolved_data, model=payload.model,
              agent_ids=agent_ids, outputs=outputs)
    db.add(run)
    db.commit()
    db.refresh(run)
    log = RunLog(run_id=run.id, input_type=run.input_type, input_data=run.input_data, model=run.model,
                  agent_snapshot=snapshots, attribute_snapshot=snapshots, outputs=outputs, feedback=feedback,
                  usage=usage, filters=filters, requests=requests,
                  client=client_name, meeting_type=meeting_type, meeting_title=meeting_title,
                  reasoning_effort=effort, run_group_id=payload.run_group_id or "",
                  reused_from_log_id="", reused_from_created_at=None,
                  consistency=consistency)
    db.add(log)
    db.commit()
    db.refresh(log)
    return {"id": run.id, "outputs": outputs, "usage": usage, "requests": requests,
            "log_id": log.id, "run_group_id": log.run_group_id or "", "log": _out(log)}


@router.post("/logs/{log_id}/retry-agent")
async def retry_agent(log_id: str, payload: RetryAgentIn, db: Session = Depends(get_db)):
    """Re-run one agent's stored request and patch the same log in place."""
    from sqlalchemy.orm.attributes import flag_modified

    log = db.query(RunLog).filter(RunLog.id == log_id).first()
    if not log:
        raise HTTPException(404, "log not found")
    agent_id = (payload.agent_id or "").strip() if isinstance(payload.agent_id, str) else ""
    stored_requests = log.requests if isinstance(getattr(log, "requests", None), dict) else {}
    if not agent_id or not isinstance(stored_requests.get(agent_id), dict):
        raise HTTPException(400, "agent_id has no stored request")
    stored_body = copy.deepcopy(stored_requests[agent_id])
    parsed, pt, ct, tt, rt, dur = await _call_single_payload(stored_body)
    # Identifier normalisation when the retried agent is the identifier.
    try:
        snap_kind = ""
        snaps = log.agent_snapshot if isinstance(getattr(log, "agent_snapshot", None), dict) else {}
        snap = snaps.get(agent_id) if isinstance(snaps, dict) else None
        if isinstance(snap, dict):
            snap_kind = str(snap.get("kind", "") or "")
        is_ident = snap_kind == IDENTIFIER_KIND
        if not snap_kind:
            row = db.query(Agent).filter(Agent.id == agent_id).first()
            if row is not None:
                is_ident = is_identifier(row)
        if is_ident and isinstance(parsed, dict) and "_error" not in parsed:
            cands = identifier_candidates_with_attributes(db, exclude_id=agent_id)
            parsed = normalize_identifier_output(parsed, cands)
    except Exception:
        pass
    outputs = log.outputs if isinstance(getattr(log, "outputs", None), dict) else {}
    outputs = dict(outputs)
    outputs[agent_id] = parsed
    log.outputs = outputs
    flag_modified(log, "outputs")
    usage = log.usage if isinstance(getattr(log, "usage", None), dict) else {}
    usage = copy.deepcopy(usage) if isinstance(usage, dict) else {}
    per_agent = usage.get("per_agent", {})
    if not isinstance(per_agent, dict):
        per_agent = {}
    else:
        per_agent = dict(per_agent)
    try:
        prompt_price, completion_price = await get_model_pricing(log.model or "")
    except Exception:
        prompt_price, completion_price = None, None
    entry = _per_agent_entry(pt, ct, tt, rt, dur, log.model or "")
    _fill_pricing({agent_id: entry}, prompt_price, completion_price)
    # Drop any reused markers on the retried agent.
    for k in ("reused_from_log_id", "reused_from_created_at"):
        try:
            entry.pop(k, None)
        except Exception:
            pass
    per_agent[agent_id] = entry
    reused_agents = usage.get("reused_agents", {})
    if isinstance(reused_agents, dict) and agent_id in reused_agents:
        reused_agents = dict(reused_agents)
        reused_agents.pop(agent_id, None)
    else:
        reused_agents = dict(reused_agents) if isinstance(reused_agents, dict) else {}
    totals = _totals_from_per_agent(per_agent)
    old_dur = usage.get("duration_ms", 0)
    try:
        new_dur = float(dur)
        old_f = float(old_dur) if isinstance(old_dur, (int, float)) else 0.0
        duration_ms = old_f if old_f >= new_dur else new_dur
    except Exception:
        duration_ms = dur
    usage["prompt_tokens"] = totals["prompt_tokens"]
    usage["completion_tokens"] = totals["completion_tokens"]
    usage["total_tokens"] = totals["total_tokens"]
    usage["reasoning_tokens"] = totals["reasoning_tokens"]
    usage["cost_usd"] = totals["cost_usd"]
    usage["input_cost_usd"] = totals["input_cost_usd"]
    usage["output_cost_usd"] = totals["output_cost_usd"]
    usage["duration_ms"] = duration_ms
    usage["per_agent"] = per_agent
    usage["reused_agents"] = reused_agents
    if "model" not in usage:
        usage["model"] = log.model or ""
    log.usage = usage
    flag_modified(log, "usage")
    # Clear feedback for the retried agent (it rated the old output).
    fb = log.feedback if isinstance(getattr(log, "feedback", None), dict) else {}
    fb = {k: dict(v) if isinstance(v, dict) else {} for k, v in fb.items()} if isinstance(fb, dict) else {}
    agent_name = ""
    try:
        snaps2 = log.agent_snapshot if isinstance(getattr(log, "agent_snapshot", None), dict) else {}
        s2 = snaps2.get(agent_id) if isinstance(snaps2, dict) else None
        if isinstance(s2, dict) and isinstance(s2.get("name"), str) and s2.get("name"):
            agent_name = s2.get("name")
    except Exception:
        agent_name = ""
    if not agent_name:
        try:
            row2 = db.query(Agent).filter(Agent.id == agent_id).first()
            if row2 is not None:
                agent_name = getattr(row2, "name", "") or ""
        except Exception:
            pass
    if agent_name and agent_name in fb:
        try:
            del fb[agent_name]
        except Exception:
            pass
    # Recompute consistency + auto feedback when this is an auto log.
    try:
        cons_existing = getattr(log, "consistency", None)
    except Exception:
        cons_existing = None
    if isinstance(cons_existing, dict) and bool(cons_existing):
        try:
            ident_out, all_outputs, agents_by_id, attrs_by_agent, _iid = _consistency_inputs_from_log(log)
            # all_outputs already includes the fresh retry output via log.outputs.
            new_cons = compute_consistency(ident_out, dict(log.outputs or {}), agents_by_id, attrs_by_agent)
            # Preserve identifier id when recompute cannot find it.
            try:
                if not new_cons.get("identifier_agent_id"):
                    old_iid = cons_existing.get("identifier_agent_id", "")
                    if isinstance(old_iid, str) and old_iid:
                        new_cons["identifier_agent_id"] = old_iid
            except Exception:
                pass
            log.consistency = new_cons
            flag_modified(log, "consistency")
            fb = apply_auto_feedback(fb, new_cons)
        except Exception:
            pass
    log.feedback = fb
    flag_modified(log, "feedback")
    # Mirror the fresh output onto the Run row.
    try:
        run_row = db.query(Run).filter(Run.id == log.run_id).first()
        if run_row is not None:
            outs = run_row.outputs if isinstance(getattr(run_row, "outputs", None), dict) else {}
            outs = dict(outs) if isinstance(outs, dict) else {}
            outs[agent_id] = parsed
            run_row.outputs = outs
            flag_modified(run_row, "outputs")
    except Exception:
        pass
    db.commit()
    db.refresh(log)
    return {"log": _out(log)}


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
