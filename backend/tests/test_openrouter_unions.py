"""Anthropic union-limit fallback (synthetic only, no network/PII)."""

import json

import pytest

from app.core.openrouter import (
    ANTHROPIC_FALLBACK_INSTRUCTION,
    ANTHROPIC_UNION_LIMIT,
    _parse_json_content,
    build_chat_payload,
    count_union_params,
    parse_json_content,
)


def _twenty_nullable_schema():
    props = {f"p{i}": {"type": ["string", "null"]} for i in range(20)}
    return {
        "type": "object",
        "properties": props,
        "required": sorted(props),
        "additionalProperties": False,
    }


def _three_nullable_schema():
    props = {f"p{i}": {"type": ["string", "null"]} for i in range(3)}
    return {
        "type": "object",
        "properties": props,
        "required": sorted(props),
        "additionalProperties": False,
    }


def test_union_limit_constant():
    assert ANTHROPIC_UNION_LIMIT == 16


def test_count_union_params_nested():
    schema = {
        "type": "object",
        "properties": {
            # 1: type list
            "a": {"type": ["string", "null"]},
            # 1: anyOf
            "b": {"anyOf": [{"type": "string", "enum": ["x", "y"]}, {"type": "null"}]},
            # nested object with one nullable inside (+1)
            "c": {
                "type": "object",
                "properties": {"nested": {"type": ["number", "null"]}},
            },
            # array union (+1) with object items holding a oneOf (+1)
            "d": {
                "type": ["array", "null"],
                "items": {
                    "type": "object",
                    "properties": {"x": {"oneOf": [{"type": "string"}, {"type": "null"}]}},
                },
            },
            # allOf member holding a nullable (+1)
            "e": {"allOf": [{"type": ["boolean", "null"]}]},
        },
        "$defs": {"MyDef": {"type": ["boolean", "null"]}},
        "definitions": {"Other": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
    }
    # a(1) + b(1) + c.nested(1) + d(1) + d.items.x(1) + e.allOf[0](1)
    # + $defs.MyDef(1) + definitions.Other(1) = 8
    assert count_union_params(schema) == 8


def test_count_union_params_empty_and_non_union():
    assert count_union_params({}) == 0
    assert count_union_params({"type": "object", "properties": {"a": {"type": "string"}}}) == 0
    # Single-entry type list is not a union.
    assert count_union_params({"type": ["string"]}) == 0
    assert count_union_params(None) == 0


def test_anthropic_fallback_20_nullable_props():
    schema = _twenty_nullable_schema()
    assert count_union_params(schema) == 20
    p1 = build_chat_payload(
        model="anthropic/claude-sonnet-5.5", system="sys", user="u", json_schema=schema
    )
    assert p1["response_format"] == {"type": "json_object"}
    system_content = p1["messages"][0]["content"]
    assert system_content.startswith("sys\n\n")
    assert ANTHROPIC_FALLBACK_INSTRUCTION in system_content
    assert "Respond with ONLY a single JSON object" in system_content
    assert "use null when a value is unknown" in system_content
    assert json.dumps(schema, ensure_ascii=False, indent=2) in system_content
    assert p1["messages"][1] == {"role": "user", "content": "u"}
    assert p1["model"] == "anthropic/claude-sonnet-5.5"
    # Deterministic: same input -> byte-identical payload.
    p2 = build_chat_payload(
        model="anthropic/claude-sonnet-5.5", system="sys", user="u", json_schema=schema
    )
    assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)
    # Case-insensitive vendor prefix also triggers fallback.
    p3 = build_chat_payload(
        model="Anthropic/claude-sonnet-5.5", system="sys", user="u", json_schema=schema
    )
    assert p3["response_format"] == {"type": "json_object"}


def test_openai_same_schema_strict_unchanged():
    schema = _twenty_nullable_schema()
    p = build_chat_payload(model="openai/gpt-5", system="sys", user="hello", json_schema=schema)
    assert p["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "meeting_extraction", "strict": True, "schema": schema},
    }
    assert p["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]


def test_anthropic_small_schema_strict_unchanged():
    schema = _three_nullable_schema()
    assert count_union_params(schema) == 3
    p = build_chat_payload(
        model="anthropic/claude-sonnet-5.5", system="sys", user="u", json_schema=schema
    )
    assert p["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "meeting_extraction", "strict": True, "schema": schema},
    }
    assert p["messages"][0] == {"role": "system", "content": "sys"}


def test_parse_helper_valid_json_unchanged():
    assert _parse_json_content('{"a": 1}') == {"a": 1}
    assert parse_json_content('{"a": 1}') == {"a": 1}
    assert _parse_json_content({"a": 1}) == {"a": 1}


def test_parse_helper_fenced_and_prose():
    assert _parse_json_content('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json_content('```\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json_content('Here is the result:\n{"a": 1}\nHope this helps') == {"a": 1}
    assert (
        _parse_json_content('Here is the result:\n```json\n{"a": 1}\n```\nDone')
        == {"a": 1}
    )


def test_parse_helper_invalid_still_raises():
    with pytest.raises(Exception):
        _parse_json_content("not json at all")
