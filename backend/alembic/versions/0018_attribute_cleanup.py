"""Attribute cleanup: strip [Original:] markers, KC gating, v2 schema fixes.

Revision ID: 0018_attribute_cleanup
Revises: 0017_attributes_v2
Create Date: 2026-10-03

- Strip every "[Original: ...]" marker (regex \\s*\\[Original:[^\\]]*\\]) from
  attributes.description, then trim (20 rows today).
- Remove meeting-type gating text; routing handles it. kc_taker_readiness
  and kc_taker_knowledge start with "Only for Karma Conversations. " -
  remove it. Generic gating-sentence search is case-insensitive and keeps
  extraction rules like "only in aggregate" / "meeting year" untouched.
- list_of_all_queries_asked: array item "asked_by" gets enum
  ["client","advisor"] (null_allowed stays true) + description
  "Who asked the query: client or advisor".
- insurance_policies: rewrite item properties to exactly
  [insurance_type, details, confidence, confidence_type, evidence] + new
  self-contained description (examples condensed into details). Keep
  wrap_result False, group, links, id unchanged. run_logs untouched.
- Downgrade restores descriptions + array_items exactly from
  0018_attribute_cleanup_before.json (frozen, generated from live DB).

Core SQL only (no ORM import).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0018_attribute_cleanup"
down_revision = "0017_attributes_v2"
branch_labels = None
depends_on = None

ORIGINAL_MARKER_RE = re.compile(r"\s*\[Original:[^\]]*\]")

KC_PREFIX = "Only for Karma Conversations. "

# Generic gating-sentence patterns (case-insensitive). Applied sentence-wise;
# sentences containing "aggregate" or "meeting year" are never touched
# (those are extraction rules, not gating).
_GATING_PATTERNS = [
    re.compile(r"^\s*only\s+for\s+.+\.\s*", re.IGNORECASE),
    re.compile(r"^\s*only\s+in\s+.+\bmeetings?\b.*?\.\s*", re.IGNORECASE),
    re.compile(r"^\s*only\s+in\s+.+\bconversations?\b.*?\.\s*", re.IGNORECASE),
    re.compile(r"^\s*applicable\s+only\s+to\s+.+\.\s*", re.IGNORECASE),
    re.compile(r"^\s*applicable\s+only\s+for\s+.+\.\s*", re.IGNORECASE),
    re.compile(r"^\s*for\s+karma\s+conversations\s+only\s*\.\s*", re.IGNORECASE),
]


def _strip_gating_sentence(text: str) -> str:
    """Remove one leading meeting-type gating sentence, keep the rest."""
    t = text or ""
    # Exact KC prefix first (the two known rows start with it).
    if t.startswith(KC_PREFIX):
        return t[len(KC_PREFIX):].lstrip()
    # Generic: only when the FIRST sentence looks like gating AND does not
    # contain extraction-rule phrases.
    try:
        low = t.lower()
        if "aggregate" in low or "meeting year" in low:
            # Could still have a gating prefix before the aggregate sentence;
            # only strip a leading KC-style prefix, never the aggregate part.
            if t.startswith(KC_PREFIX):
                return t[len(KC_PREFIX):].lstrip()
            # Case-insensitive leading KC prefix.
            m = re.match(r"^\s*only\s+for\s+karma\s+conversations\.\s*", t, re.IGNORECASE)
            if m:
                return t[m.end():].lstrip()
            return t
        for pat in _GATING_PATTERNS:
            m = pat.match(t)
            if m:
                return t[m.end():].lstrip()
    except Exception:
        return t
    return t


def clean_description(desc: str) -> str:
    """After-transform for 0018: strip [Original:] markers + KC gating, trim."""
    t = desc or ""
    t = ORIGINAL_MARKER_RE.sub("", t)
    t = _strip_gating_sentence(t)
    # A marker could precede the gating sentence ("... [Original: X] Only for ...")
    # or vice versa; run once more to catch the other order, then trim.
    t = ORIGINAL_MARKER_RE.sub("", t)
    t = _strip_gating_sentence(t)
    return t.strip()


NEW_INSURANCE_DESCRIPTION = (
    "This attribute consolidates every insurance policy the Client mentions into a single list "
    "so cover gaps can be assessed. The two boolean adequacy judgements "
    "(term_insurance_coverage_adequacy, health_insurance_coverage_adequacy) stay as separate attributes. "
    "Return one item per distinct holding/instance mentioned. "
    "If the Client mentions a type only in aggregate, return one item for that type. "
    "Return an empty list when the group is not discussed. "
    "Return the list directly - there is no outer value/confidence wrapper.\n\n"
    "Properties:\n"
    "- insurance_type (enum, required): Type of policy, e.g. 'life_term', 'health'. "
    "Allowed values: life_term, health, ulip, other. Meanings: life_term = pure protection term cover; "
    "health = medical cover; ulip = Unit Linked Insurance Plan (investment-cum-insurance); "
    "other = any policy not listed.\n"
    "- details (string, nullable): One or two short sentences with everything else stated about this policy - "
    "insurer, policy name, who is covered, cover amount, premium amount and frequency, employer or personal. "
    "Amounts follow the amount convention (e.g. 'INR 10000000').\n"
    "- confidence (number, required): How confident the model is in this item (0-1).\n"
    "- confidence_type (enum, required): quoted = stated verbatim; normalized = stated but reformatted "
    "(e.g. '10 lakh' -> 'INR 1000000'); inferred = clearly implied; calculated = derived from several stated pieces; "
    "not_found = no value found for this type. Allowed values: quoted, normalized, inferred, calculated, not_found.\n"
    "- evidence (string, nullable): Short verbatim quote(s) from the transcript supporting this item.\n\n"
    "Examples:\n"
    'Example 1 - Client: "I have a 1 crore HDFC term plan with 12k annual premium, and a 10 lakh Star Health family floater." -> [\n'
    '  {"insurance_type": "life_term", "details": "HDFC Life term plan for self, cover INR 10000000, premium INR 12000 annual, personal.", '
    '"confidence": 0.9, "confidence_type": "normalized", "evidence": "1 crore HDFC term plan with 12k annual premium"},\n'
    '  {"insurance_type": "health", "details": "Star Health family floater for family, cover INR 1000000, personal.", '
    '"confidence": 0.85, "confidence_type": "normalized", "evidence": "a 10 lakh Star Health family floater"}\n'
    "]\n"
    'Example 2 - Client: "My employer gives me a 5 lakh health cover for my family; I pay nothing for it." -> [\n'
    '  {"insurance_type": "health", "details": "Employer health cover for family, cover INR 500000, premium INR 0, employer.", '
    '"confidence": 0.9, "confidence_type": "quoted", "evidence": "My employer gives me a 5 lakh health cover for my family"}\n'
    "]\n"
    'Example 3 - Client: "I do have a ULIP from years ago but I don\'t remember the cover, and I have no separate term cover of my own." -> [\n'
    '  {"insurance_type": "ulip", "details": "ULIP from years ago, cover not remembered, personal.", '
    '"confidence": 0.75, "confidence_type": "quoted", "evidence": "I do have a ULIP from years ago but I don\'t remember the cover"},\n'
    '  {"insurance_type": "life_term", "details": "No separate term cover.", '
    '"confidence": 0.9, "confidence_type": "quoted", "evidence": "I have no separate term cover of my own"}\n'
    "]"
)

NEW_INSURANCE_ARRAY_ITEMS = {
    "kind": "object",
    "properties": [
        {
            "name": "insurance_type",
            "type": "string",
            "null_allowed": False,
            "enum": ["life_term", "health", "ulip", "other"],
            "description": "Type of policy, e.g. 'life_term', 'health'.",
        },
        {
            "name": "details",
            "type": "string",
            "null_allowed": True,
            "enum": [],
            "description": (
                "One or two short sentences with everything else stated about this policy - "
                "insurer, policy name, who is covered, cover amount, premium amount and frequency, "
                "employer or personal. Amounts follow the amount convention (e.g. 'INR 10000000')."
            ),
        },
        {
            "name": "confidence",
            "type": "number",
            "null_allowed": False,
            "enum": [],
            "description": "How confident the model is in this item (0-1).",
        },
        {
            "name": "confidence_type",
            "type": "string",
            "null_allowed": False,
            "enum": ["quoted", "normalized", "inferred", "calculated", "not_found"],
            "description": (
                "quoted = stated verbatim; normalized = stated but reformatted "
                "(e.g. '10 lakh' -> 'INR 1000000'); inferred = clearly implied; "
                "calculated = derived from several stated pieces; not_found = no value found for this type."
            ),
        },
        {
            "name": "evidence",
            "type": "string",
            "null_allowed": True,
            "enum": [],
            "description": "Short verbatim quote(s) from the transcript supporting this item.",
        },
    ],
}

ASKED_BY_ENUM = ["client", "advisor"]
ASKED_BY_DESCRIPTION = "Who asked the query: client or advisor"


def transform_array_items(name: str, array_items: dict | None) -> dict | None:
    """After-transform for array_items; returns new dict or None when unchanged."""
    try:
        cfg = copy.deepcopy(array_items) if isinstance(array_items, dict) else {"kind": "string", "properties": []}
    except Exception:
        return None
    if name == "list_of_all_queries_asked":
        try:
            props = cfg.get("properties", []) if isinstance(cfg, dict) else []
            if not isinstance(props, list):
                return None
            changed = False
            for p in props:
                if isinstance(p, dict) and p.get("name") == "asked_by":
                    if list(p.get("enum", []) or []) != ASKED_BY_ENUM:
                        p["enum"] = list(ASKED_BY_ENUM)
                        changed = True
                    if (p.get("description", "") or "") != ASKED_BY_DESCRIPTION:
                        p["description"] = ASKED_BY_DESCRIPTION
                        changed = True
                    if bool(p.get("null_allowed", True)) is not True:
                        p["null_allowed"] = True
                        changed = True
            return cfg if changed else None
        except Exception:
            return None
    if name == "insurance_policies":
        return copy.deepcopy(NEW_INSURANCE_ARRAY_ITEMS)
    return None


def _data_path(name: str):
    from pathlib import Path as _Path

    return _Path(__file__).resolve().parent / name


def _load_json(name: str):
    p = _data_path(name)
    return json.loads(p.read_text(encoding="utf-8"))


attributes_tbl = sa.table(
    "attributes",
    sa.column("id", sa.String(36)),
    sa.column("name", sa.String(200)),
    sa.column("type", sa.String(64)),
    sa.column("description", sa.Text()),
    sa.column("enum_values", sa.JSON()),
    sa.column("object_properties", sa.JSON()),
    sa.column("group_name", sa.String(200)),
    sa.column("array_items", sa.JSON()),
    sa.column("wrap_result", sa.Boolean()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    conn = op.get_bind()
    try:
        rows = conn.execute(
            sa.select(
                attributes_tbl.c.id,
                attributes_tbl.c.name,
                attributes_tbl.c.description,
                attributes_tbl.c.array_items,
            )
        ).fetchall()
    except Exception:
        rows = []
    for attr_id, name, description, array_items in rows:
        try:
            nm = str(name or "")
        except Exception:
            continue
        # 1+2: description cleanup for every row (marker + gating).
        try:
            old_desc = description if isinstance(description, str) else ""
            new_desc = clean_description(old_desc)
        except Exception:
            new_desc = old_desc
        # 3: asked_by enum fix.
        new_items = None
        try:
            if nm == "list_of_all_queries_asked":
                new_items = transform_array_items(nm, array_items if isinstance(array_items, dict) else {})
                # transform returns None when already correct.
            elif nm == "insurance_policies":
                new_items = copy.deepcopy(NEW_INSURANCE_ARRAY_ITEMS)
        except Exception:
            new_items = None
        updates: dict = {}
        try:
            if isinstance(old_desc, str) and new_desc != old_desc:
                updates["description"] = new_desc
            if nm == "insurance_policies":
                # Always set the new description + schema (idempotent).
                if new_desc != NEW_INSURANCE_DESCRIPTION:
                    updates["description"] = NEW_INSURANCE_DESCRIPTION
                updates["array_items"] = copy.deepcopy(NEW_INSURANCE_ARRAY_ITEMS)
            elif new_items is not None:
                updates["array_items"] = new_items
        except Exception:
            continue
        if not updates:
            continue
        try:
            conn.execute(
                attributes_tbl.update().where(attributes_tbl.c.id == attr_id).values(**updates)
            )
        except Exception:
            continue


def downgrade() -> None:
    conn = op.get_bind()
    try:
        before = _load_json("0018_attribute_cleanup_before.json")
    except Exception:
        before = []
    for item in before or []:
        try:
            if not isinstance(item, dict):
                continue
            attr_id = str(item.get("id") or "")
            if not attr_id:
                continue
            values: dict = {}
            if "description" in item:
                values["description"] = str(item.get("description") or "")
            if "array_items" in item:
                ai = item.get("array_items")
                values["array_items"] = dict(ai) if isinstance(ai, dict) else ai
            if not values:
                continue
            conn.execute(
                attributes_tbl.update().where(attributes_tbl.c.id == attr_id).values(**values)
            )
        except Exception:
            continue
