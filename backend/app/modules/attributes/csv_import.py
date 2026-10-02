"""Pure CSV parsing for the meeting-transcript extraction schema.

Row mapping (per spec):
- Group -> group (trimmed free text)
- Property -> normalized unique snake_case name; original preserved in
  description as ` [Original: <Property>]` when it differs from the name
- Type (lowercased): string|number|boolean as-is; enum split on `|` with
  case-insensitive `null` stripped; array `[string]`/`[number]`/`[{...}]`;
  object `{...}` with sub-fields parsed the same way (all null_allowed=True)
"""

import re

_WS_UNDER = re.compile(r"[^0-9a-z]+")
_MULTI_UNDER = re.compile(r"_+")


def normalize_name(prop: str) -> str:
    """lowercase, non-alnum to _, collapse, strip _."""
    s = (prop or "").strip().lower()
    s = _WS_UNDER.sub("_", s)
    s = _MULTI_UNDER.sub("_", s).strip("_")
    return s or "attr"


def group_slug(group: str) -> str:
    return normalize_name(group) or "group"


def unique_name(base: str, group: str, taken: set[str]) -> str:
    """Base if free, else base__groupslug, else base__groupslug__2, ..."""
    base = base or "attr"
    if base not in taken:
        return base
    slug = group_slug(group)
    cand = f"{base}__{slug}"
    if cand not in taken:
        return cand
    i = 2
    while f"{cand}__{i}" in taken:
        i += 1
    return f"{cand}__{i}"


def parse_enum_values(raw: str | None) -> list[str]:
    """Split on `|`, trim, drop empty + case-insensitive `null`."""
    if not raw:
        return []
    out: list[str] = []
    for part in str(raw).split("|"):
        t = part.strip()
        if not t:
            continue
        if t.lower() == "null":
            continue
        out.append(t)
    return out


def _strip_quotes(s: str) -> str:
    t = (s or "").strip()
    # Strip matching outer quotes/backticks repeatedly (handles ""x"" artefacts).
    while len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"', "`"):
        t = t[1:-1].strip()
    return t.strip("'\"` ").strip()


def _split_top_commas(s: str) -> list[str]:
    """Split on commas NOT inside single/double quotes."""
    parts: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == "'" and not in_double:
            in_single = not in_single
            buf.append(c)
        elif c == '"' and not in_single:
            # Handle doubled quotes "" inside a quoted span as literal.
            if in_double and i + 1 < len(s) and s[i + 1] == '"':
                buf.append(c)
                buf.append(s[i + 1])
                i += 1
            else:
                in_double = not in_double
                buf.append(c)
        elif c == "," and not in_single and not in_double:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf))
    return parts


def _split_first_colon(s: str) -> tuple[str, str] | None:
    in_single = False
    in_double = False
    for i, c in enumerate(s):
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == ":" and not in_single and not in_double:
            return s[:i], s[i + 1:]
    return None


def parse_object_properties(inner: str) -> list[dict]:
    """Parse `name: type` pairs; enum-like (`a|b`) -> string; all null_allowed.

    Sub-field literally named `name` is renamed to `description` (if the
    model cannot get the exact name it provides a description instead).
    """
    inner = (inner or "").strip()
    if not inner:
        return []
    props: list[dict] = []
    for chunk in _split_top_commas(inner):
        chunk = chunk.strip()
        if not chunk:
            continue
        split = _split_first_colon(chunk)
        if not split:
            continue
        left, right = split
        name = _strip_quotes(left.strip())
        type_raw = right.strip()
        if not name:
            continue
        if name == "name":
            name = "description"
        if "|" in type_raw:
            sub_type = "string"
        else:
            t = _strip_quotes(type_raw).lower()
            sub_type = t if t in ("string", "number", "boolean", "array") else "string"
        props.append({"name": name, "type": sub_type, "null_allowed": True,
                      "enum": [], "description": ""})
    return props


def parse_array_spec(raw: str) -> dict:
    """`[string]`->string, `[number]`->number, `[{...}]`->object (+sub-fields)."""
    s = (raw or "").strip()
    if not (s.startswith("[") and s.endswith("]")):
        return {"kind": "string", "properties": []}
    inner = s[1:-1].strip()
    low = inner.lower()
    if low == "string":
        return {"kind": "string", "properties": []}
    if low == "number":
        return {"kind": "number", "properties": []}
    if inner.startswith("{") and inner.endswith("}"):
        return {"kind": "object",
                "properties": parse_object_properties(inner[1:-1])}
    return {"kind": "string", "properties": []}


def parse_object_spec(raw: str) -> list[dict]:
    s = (raw or "").strip()
    if s.startswith("{") and s.endswith("}"):
        return parse_object_properties(s[1:-1])
    return parse_object_properties(s)


def build_description(csv_desc: str, original: str, normalized: str) -> str:
    """CSV Description + original Property preserved alongside (suffix)."""
    desc = (csv_desc or "").strip()
    orig = (original or "").strip()
    if orig and orig != normalized:
        suffix = f"[Original: {orig}]"
        return f"{desc} {suffix}".strip() if desc else suffix
    return desc


# Single source of truth for constrained/annotated item sub-fields so
# CSV re-imports preserve the overrides (import script calls parse_row).
TOTAL_ENUM = ["India", "Foreign", "Overall"]
TOTAL_DESCRIPTION = (
    "Total assets bucket: extract total assets in India, "
    "total assets outside India (Foreign), and overall total."
)
RENT_DESCRIPTION = "Name/location of the property the rent expense relates to."


def _constrain_description_prop(props: list[dict], enum: list[str] | None,
                                description: str) -> list[dict]:
    """Set enum/description help-text on the `description` sub-field in place."""
    for p in props:
        if isinstance(p, dict) and p.get("name") == "description":
            if enum is not None:
                p["enum"] = list(enum)
            p["description"] = description
            return props
    # Missing (unexpected shape): append so the contract still holds.
    props.append({"name": "description", "type": "string",
                  "null_allowed": True,
                  "enum": list(enum) if enum is not None else [],
                  "description": description})
    return props


def apply_special_cases(base: str, parsed: dict) -> dict:
    """Override total/rent item `description` sub-fields (re-import safe)."""
    if base == "total":
        if parsed.get("type") == "array":
            items = parsed.get("array_items") or {}
            if isinstance(items, dict) and items.get("kind") == "object":
                _constrain_description_prop(
                    items.get("properties") or [], TOTAL_ENUM, TOTAL_DESCRIPTION)
        elif parsed.get("type") == "object":
            _constrain_description_prop(
                parsed.get("object_properties") or [], TOTAL_ENUM, TOTAL_DESCRIPTION)
    elif base == "rent":
        if parsed.get("type") == "array":
            items = parsed.get("array_items") or {}
            if isinstance(items, dict) and items.get("kind") == "object":
                _constrain_description_prop(
                    items.get("properties") or [], None, RENT_DESCRIPTION)
        elif parsed.get("type") == "object":
            _constrain_description_prop(
                parsed.get("object_properties") or [], None, RENT_DESCRIPTION)
    return parsed


def parse_row(group: str, prop: str, typ: str,
              desc: str, enum_raw: str) -> dict:
    """Parse one CSV row to an attribute dict (name not yet de-duped)."""
    grp = (group or "").strip()
    original = (prop or "").strip()
    base = normalize_name(original)
    type_lower = (typ or "").strip().lower()
    description = build_description(desc, original, base)
    if type_lower in ("string", "number", "boolean"):
        return {"group": grp, "name": base, "original": original,
                "type": type_lower, "description": description,
                "enum_values": [], "object_properties": [],
                "array_items": {"kind": "string", "properties": []}}
    if type_lower == "enum":
        return {"group": grp, "name": base, "original": original,
                "type": "enum", "description": description,
                "enum_values": parse_enum_values(enum_raw),
                "object_properties": [],
                "array_items": {"kind": "string", "properties": []}}
    if type_lower == "array":
        parsed = {"group": grp, "name": base, "original": original,
                  "type": "array", "description": description,
                  "enum_values": [], "object_properties": [],
                  "array_items": parse_array_spec(enum_raw)}
        return apply_special_cases(base, parsed)
    if type_lower == "object":
        parsed = {"group": grp, "name": base, "original": original,
                  "type": "object", "description": description,
                  "enum_values": [],
                  "object_properties": parse_object_spec(enum_raw),
                  "array_items": {"kind": "string", "properties": []}}
        return apply_special_cases(base, parsed)
    # Unknown types fall back to string (minimal, never crash the import).
    return {"group": grp, "name": base, "original": original,
            "type": "string", "description": description,
            "enum_values": [], "object_properties": [],
            "array_items": {"kind": "string", "properties": []}}
