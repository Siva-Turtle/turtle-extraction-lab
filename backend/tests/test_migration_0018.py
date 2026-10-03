"""Migration 0018: attribute cleanup (markers, KC gating, v2 schema fixes)."""

import importlib.util
import json
from pathlib import Path


def _load_migration():
    p = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0018_attribute_cleanup.py"
    spec = importlib.util.spec_from_file_location("mig_0018", str(p))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_down_revision():
    mod = _load_migration()
    assert mod.down_revision == "0017_attributes_v2"
    assert mod.revision == "0018_attribute_cleanup"


def test_before_file_covers_changed_rows():
    base = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    before = json.loads((base / "0018_attribute_cleanup_before.json").read_text(encoding="utf-8"))
    names = {r["name"] for r in before}
    # 20 marker rows + 2 KC + insurance + queries = 24.
    assert len(before) == 24
    for n in ("kc_taker_readiness", "kc_taker_knowledge",
              "list_of_all_queries_asked", "insurance_policies",
              "advance_tax", "works_at"):
        assert n in names
    for r in before:
        assert r["id"] and r["name"] and isinstance(r["description"], str)
        assert isinstance(r.get("array_items"), dict)


def test_clean_description_strips_markers_and_gating():
    mod = _load_migration()
    assert "[Original:" not in mod.clean_description(
        "Whether advance tax is recommended or not needed. [Original: Advance Tax]")
    assert mod.clean_description(
        "Whether advance tax is recommended or not needed. [Original: Advance Tax]"
    ) == "Whether advance tax is recommended or not needed."
    assert mod.clean_description(
        "Only for Karma Conversations. How prepared was the Turtle's KC Taker?"
    ) == "How prepared was the Turtle's KC Taker?"
    assert "Only for Karma Conversations" not in mod.clean_description(
        "Only for Karma Conversations. How well was the Turtle's KC Taker able to answer?")
    # Extraction rules untouched.
    assert "only in aggregate" in mod.clean_description(
        "If the Client mentions a type only in aggregate, return one item for that type.")
    assert "meeting year" in mod.clean_description(
        "If stated as 'in N years', add N to the meeting year and use confidence_type calculated.")


def test_insurance_schema_shape():
    mod = _load_migration()
    props = mod.NEW_INSURANCE_ARRAY_ITEMS["properties"]
    assert [p["name"] for p in props] == [
        "insurance_type", "details", "confidence", "confidence_type", "evidence"]
    by = {p["name"]: p for p in props}
    assert by["insurance_type"]["enum"] == ["life_term", "health", "ulip", "other"]
    assert by["insurance_type"]["null_allowed"] is False
    assert by["details"]["null_allowed"] is True
    assert "One or two short sentences" in by["details"]["description"]
    assert "INR 10000000" in by["details"]["description"]
    assert "insurance_policies" not in str(by)  # sanity
    assert "no outer value/confidence wrapper" in mod.NEW_INSURANCE_DESCRIPTION
    assert "Properties:" in mod.NEW_INSURANCE_DESCRIPTION
    assert mod.NEW_INSURANCE_DESCRIPTION.count("Example") >= 3


def test_asked_by_transform():
    mod = _load_migration()
    cfg = {"kind": "object", "properties": [
        {"name": "query", "type": "string", "null_allowed": True, "enum": [], "description": ""},
        {"name": "asked_by", "type": "string", "null_allowed": True, "enum": [], "description": ""},
    ]}
    out = mod.transform_array_items("list_of_all_queries_asked", cfg)
    assert out is not None
    asked = next(p for p in out["properties"] if p["name"] == "asked_by")
    assert asked["enum"] == ["client", "advisor"]
    assert asked["null_allowed"] is True
    assert asked["description"] == "Who asked the query: client or advisor"


def test_csv_import_no_markers():
    from app.modules.attributes.csv_import import build_description
    assert build_description("Is it adequate?", "Term Insurance Coverage Adequacy",
                             "term_insurance_coverage_adequacy") == "Is it adequate?"
    assert "[Original:" not in build_description("Names.", "Card Name(s)", "card_name_s")
