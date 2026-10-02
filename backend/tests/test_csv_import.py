"""CSV sample coverage: enum null-strip, object/array-of-objects parse,
name normalization, and the upsert import path."""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
SCRIPTS = BACKEND / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from app.db.models import Attribute  # noqa: E402
from app.modules.attributes.csv_import import (  # noqa: E402
    group_slug,
    normalize_name,
    parse_array_spec,
    parse_enum_values,
    parse_object_spec,
    parse_row,
    unique_name,
)
from app.modules.runs.router import build_extraction_schema  # noqa: E402
from import_attributes_csv import import_csv  # noqa: E402


def test_normalize_name_samples():
    assert normalize_name("native_city") == "native_city"
    assert normalize_name("Will Executed (Y/N)") == "will_executed_y_n"
    assert normalize_name("Card Name(s)") == "card_name_s"
    assert normalize_name("HUF Source") == "huf_source"
    assert normalize_name("Client's Availability (Weekday, Time, Timezone)") == (
        "client_s_availability_weekday_time_timezone")
    assert normalize_name("  Spaced  Out  ") == "spaced_out"


def test_collision_suffix_uses_group_slug():
    taken = {"stocks"}
    assert unique_name("stocks", "Assets", taken) == "stocks__assets"
    assert group_slug("Banking / Accounts") == "banking_accounts"


def test_enum_strips_null_token():
    assert parse_enum_values("Has an HUF | Recommended | Discussed | null") == [
        "Has an HUF", "Recommended", "Discussed"]
    assert parse_enum_values("Email | WA | Call | NULL") == ["Email", "WA", "Call"]
    assert parse_enum_values("  | null |  ") == []


def test_object_parse_sub_fields_all_nullable():
    props = parse_object_spec('{"score": number, "reason": string}')
    assert props == [{"name": "score", "type": "number", "null_allowed": True,
                      "enum": [], "description": ""},
                    {"name": "reason", "type": "string", "null_allowed": True,
                     "enum": [], "description": ""}]


def test_array_of_objects_parse_enum_like_to_string():
    cfg = parse_array_spec(
        '[{"query": string, "status": "open" | "closed", '
        '"asked_by": "client" | "advisor" | "both"}]')
    assert cfg["kind"] == "object"
    assert cfg["properties"] == [
        {"name": "query", "type": "string", "null_allowed": True,
         "enum": [], "description": ""},
        {"name": "status", "type": "string", "null_allowed": True,
         "enum": [], "description": ""},
        {"name": "asked_by", "type": "string", "null_allowed": True,
         "enum": [], "description": ""},
    ]
    assert parse_array_spec("[string]") == {"kind": "string", "properties": []}
    assert parse_array_spec("[number]") == {"kind": "number", "properties": []}


def test_name_subfield_renamed_to_description():
    cfg = parse_array_spec('[{"name": string, "value": number}]')
    assert cfg["kind"] == "object"
    assert cfg["properties"][0]["name"] == "description"
    assert cfg["properties"][0]["enum"] == []
    props = parse_object_spec('{"name": string, "value": number}')
    assert props[0]["name"] == "description"


def test_total_override_constrains_description():
    from app.modules.attributes.csv_import import TOTAL_ENUM
    parsed = parse_row("Assets", "Total", "array", "Totals.",
                       '[{"description": string, "value": number}]')
    assert parsed["name"] == "total"
    props = parsed["array_items"]["properties"]
    desc_prop = next(p for p in props if p["name"] == "description")
    assert desc_prop["enum"] == TOTAL_ENUM == ["India", "Foreign", "Overall"]
    assert "India" in desc_prop["description"] and "Foreign" in desc_prop["description"]


def test_rent_override_sets_help_text():
    parsed = parse_row("Expenses", "Rent", "array", "Rents.",
                       '[{"name": string, "value": number}]')
    assert parsed["name"] == "rent"
    props = parsed["array_items"]["properties"]
    desc_prop = next(p for p in props if p["name"] == "description")
    assert desc_prop["enum"] == []
    assert "property" in desc_prop["description"].lower()


def test_parse_row_preserves_original_and_group():
    parsed = parse_row("Insurance", "Term Insurance Coverage Adequacy",
                       "boolean", "Is it adequate?", "")
    assert parsed["name"] == "term_insurance_coverage_adequacy"
    assert parsed["group"] == "Insurance"
    assert "[Original: Term Insurance Coverage Adequacy]" in parsed["description"]
    # Already-snake names keep the description untouched.
    same = parse_row("Demographic", "native_city", "string", "The city.", "")
    assert same["name"] == "native_city"
    assert same["description"] == "The city."


def test_import_csv_sample_upsert_and_schema(client, db, tmp_path):
    csv_text = (
        "Group,Property,Type,Description,enum values or object json\n"
        'Insurance,HUF Test,enum,Status.,"Has an HUF | Recommended | null"\n'
        'Karma Conversation,Score Test,object,How polite?,"{""score"": number, ""reason"": string}"\n'
        'Assets,Stocks Test,array,Holdings.,"[{""name"": string, ""value"": number, ""unit"": string}]"\n'
        "Banking,Card Name(s),array,Names.,[string]\n"
    )
    p = tmp_path / "sample.csv"
    p.write_text(csv_text, encoding="utf-8")
    stats = import_csv(str(p), db, agent_name="CSV Agent")
    assert stats["total"] == 4 and stats["created"] == 4
    assert stats["collisions"] == []

    rows = {r["name"]: r for r in client.get("/api/v1/attributes").json()}
    assert rows["huf_test"]["enum_values"] == ["Has an HUF", "Recommended"]
    assert rows["huf_test"]["group"] == "Insurance"
    assert rows["score_test"]["object_properties"] == [
        {"name": "score", "type": "number", "null_allowed": True,
         "enum": [], "description": ""},
        {"name": "reason", "type": "string", "null_allowed": True,
         "enum": [], "description": ""}]
    assert rows["stocks_test"]["array_items"]["kind"] == "object"
    assert rows["stocks_test"]["array_items"]["properties"][0]["name"] == "description"
    assert rows["card_name_s"]["array_items"] == {"kind": "string", "properties": []}
    assert "[Original: Card Name(s)]" in rows["card_name_s"]["description"]

    # Re-run is idempotent (upsert, no dupes, no new collisions).
    stats2 = import_csv(str(p), db)
    assert stats2["total"] == 4 and stats2["created"] == 0
    assert len(client.get("/api/v1/attributes").json()) == 4

    # Every imported row builds a valid wrapper schema.
    attrs = db.query(Attribute).all()
    schema = build_extraction_schema(attrs)
    assert schema is not None and len(schema["required"]) == 4
    for prop in schema["properties"].values():
        assert prop["required"] == ["value", "confidence",
                                    "confidence_type", "evidence"]
