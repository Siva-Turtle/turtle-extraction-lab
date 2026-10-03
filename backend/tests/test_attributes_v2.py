"""Attributes v2 (rev 2): wrap_result False, integer/enum/null, migration data."""

import json
from pathlib import Path


def _agent_id(client, name="V2 Agent"):
    return client.post("/api/v1/agents", json={"name": name}).json()["id"]


def test_schema_unwrapped_raw_array_strict_items(client):
    from app.db.models import Attribute
    from app.modules.runs.router import build_extraction_schema

    attr = Attribute(
        name="assets",
        type="array",
        description="Assets list",
        array_items={
            "kind": "object",
            "properties": [
                {"name": "asset_type", "type": "string", "null_allowed": False,
                 "enum": ["stocks", "bonds"], "description": "Type"},
                {"name": "location", "type": "string", "null_allowed": True,
                 "enum": ["India", "Foreign"], "description": "Loc"},
                {"name": "target_year", "type": "integer", "null_allowed": True,
                 "enum": [], "description": "Year"},
                {"name": "amount", "type": "string", "null_allowed": True,
                 "enum": [], "description": "Amt"},
                {"name": "confidence", "type": "number", "null_allowed": False,
                 "enum": [], "description": "Conf"},
                {"name": "confidence_type", "type": "string", "null_allowed": False,
                 "enum": ["quoted", "normalized", "inferred", "calculated", "not_found"],
                 "description": "CT"},
                {"name": "evidence", "type": "string", "null_allowed": True,
                 "enum": [], "description": "Ev"},
            ],
        },
        wrap_result=False,
    )
    schema = build_extraction_schema([attr])
    assert schema["required"] == ["assets"]
    prop = schema["properties"]["assets"]
    # Raw array, no wrapper keys
    assert set(prop.keys()) >= {"type", "items"}
    assert "value" not in prop and "confidence" not in prop
    assert prop["type"] == ["array", "null"]
    items = prop["items"]
    assert items["type"] == "object"
    assert items["additionalProperties"] is False
    assert set(items["required"]) == {
        "asset_type", "location", "target_year", "amount",
        "confidence", "confidence_type", "evidence",
    }
    # integer + null
    assert items["properties"]["target_year"] == {"type": ["integer", "null"], "description": "Year"}
    # non-nullable enum stays inline, description kept
    assert items["properties"]["asset_type"] == {
        "type": "string", "enum": ["stocks", "bonds"],
        "description": "Type",
    }
    # nullable enum uses anyOf, description kept
    assert items["properties"]["location"] == {
        "anyOf": [{"type": "string", "enum": ["India", "Foreign"]}, {"type": "null"}],
        "description": "Loc",
    }
    # non-nullable enum stays inline
    assert items["properties"]["confidence_type"] == {
        "type": "string",
        "enum": ["quoted", "normalized", "inferred", "calculated", "not_found"],
        "description": "CT",
    }
    assert "$ref" not in json.dumps(schema)


def test_wrapper_confidence_type_includes_calculated():
    from app.db.models import Attribute
    from app.modules.runs.router import WRAPPER_CONFIDENCE_TYPES, build_extraction_schema

    assert WRAPPER_CONFIDENCE_TYPES == ["quoted", "inferred", "normalized", "calculated", "not_found"]
    schema = build_extraction_schema([Attribute(name="x", type="string", description="x")])
    assert schema["properties"]["x"]["properties"]["confidence_type"] == {
        "type": "string", "enum": ["quoted", "inferred", "normalized", "calculated", "not_found"]
    }


def test_prompt_note_for_unwrapped():
    from app.db.models import Attribute
    from app.modules.runs.router import _agent_system_content, _attr_line
    from app.db.models import Agent

    wrapped = Attribute(name="w", type="string", description="Wrapped desc")
    unwrapped = Attribute(name="assets", type="array", description="Full definition here", wrap_result=False)
    assert "no value/confidence wrapper" not in _attr_line(wrapped)
    line = _attr_line(unwrapped)
    assert "Full definition here" in line
    assert "return the list directly" in line
    assert "per item" in line
    sys_text = _agent_system_content(Agent(name="a", system_instruction="Base"), [unwrapped])
    assert "Full definition here" in sys_text
    assert "return the list directly" in sys_text


def test_is_filled_entry_raw_list():
    from app.modules.runs.router import _is_filled_entry

    assert _is_filled_entry([]) is False
    assert _is_filled_entry([{"a": 1}]) is True
    # Wrapped still works
    assert _is_filled_entry({"value": None, "confidence_type": "quoted"}) is False
    assert _is_filled_entry({"value": "x", "confidence_type": "quoted"}) is True
    assert _is_filled_entry({"value": [], "confidence_type": "quoted"}) is False
    assert _is_filled_entry({"value": [{"x": 1}], "confidence_type": "quoted"}) is True
    assert _is_filled_entry({"value": "x", "confidence_type": "not_found"}) is False


def test_consistency_v2_raw_list_hit_miss():
    from app.modules.runs.router import IDENTIFIER_QUESTIONS, compute_consistency

    answers = {q["key"]: False for q in IDENTIFIER_QUESTIONS}
    answers["has_assets"] = True
    plan = [
        {"agent_id": "a1", "agent_name": "asset", "reasons": ["has_assets"],
         "scored": True, "attributes": ["assets"]},
        {"agent_id": "a2", "agent_name": "expense", "reasons": ["expenses"],
         "scored": True, "attributes": ["expenses"]},
    ]
    outputs = {
        "ident": dict(answers),
        "a1": {"assets": [{"asset_type": "stocks", "confidence": 0.9,
                           "confidence_type": "quoted", "evidence": "e"}]},
        "a2": {"expenses": []},
    }
    cons = compute_consistency(answers, outputs, "ident", plan)
    assert cons["agents"]["a1"]["status"] == "hit"
    assert cons["agents"]["a2"]["status"] == "miss"
    assert cons["score"] == 0.5


def test_attributes_api_wrap_result_round_trip_and_integer(client):
    aid = _agent_id(client)
    created = client.post("/api/v1/attributes", json={
        "agent_ids": [aid], "name": "goals", "type": "array",
        "description": "Goals list", "wrap_result": False,
        "array_items": {"kind": "object", "properties": [
            {"name": "target_year", "type": "integer", "null_allowed": True},
            {"name": "priority", "type": "string", "null_allowed": True,
             "enum": ["high", "low"], "description": "Pri"},
        ]}}).json()
    assert created["wrap_result"] is False
    assert created["array_items"]["properties"][0]["type"] == "integer"
    assert created["array_items"]["properties"][1]["enum"] == ["high", "low"]
    assert created["array_items"]["properties"][1]["description"] == "Pri"

    got = client.get(f"/api/v1/attributes/{created['id']}").json()
    assert got["wrap_result"] is False

    # Default True when omitted (backward compatible)
    bare = client.post("/api/v1/attributes", json={
        "agent_ids": [aid], "name": "plain"}).json()
    assert bare["wrap_result"] is True

    # Invalid integer misuse still 422 (blank name)
    assert client.post("/api/v1/attributes", json={
        "agent_ids": [aid], "name": "bad", "type": "array",
        "array_items": {"kind": "object", "properties": [
            {"name": " ", "type": "integer", "null_allowed": True}]}}).status_code == 422
    # Bad sub-type rejected
    assert client.post("/api/v1/attributes", json={
        "agent_ids": [aid], "name": "bad2", "type": "array",
        "array_items": {"kind": "object", "properties": [
            {"name": "x", "type": "date", "null_allowed": True}]}}).status_code == 422

    # Patch wrap_result
    patched = client.patch(f"/api/v1/attributes/{created['id']}", json={"wrap_result": True}).json()
    assert patched["wrap_result"] is True


def test_sub_schema_integer_and_enum():
    from app.modules.runs.router import _sub_schema

    assert _sub_schema("integer", True) == {"type": ["integer", "null"]}
    assert _sub_schema("integer", False) == {"type": "integer"}
    assert _sub_schema("integer", True, None, "Y") == {"type": ["integer", "null"], "description": "Y"}


def test_migration_module_and_data_files():
    import importlib.util

    base = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    spec = importlib.util.spec_from_file_location("m0017", str(base / "0017_attributes_v2.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.down_revision == "0016_split_agents"
    assert m.revision == "0017_attributes_v2"

    v2 = json.loads((base / "0017_attributes_v2_data.json").read_text(encoding="utf-8"))
    assert len(v2) == 7
    assert sorted([x["name"] for x in v2]) == sorted([
        "assets", "accounts", "expenses", "goals",
        "income_sources", "insurance_policies", "liabilities",
    ])
    by_name = {x["name"]: x for x in v2}
    assert by_name["assets"]["agents"] == ["asset"]
    assert by_name["accounts"]["agents"] == ["account"]
    assert by_name["expenses"]["agents"] == ["expense"]
    assert by_name["goals"]["agents"] == ["goal"]
    assert by_name["income_sources"]["agents"] == ["income"]
    assert by_name["liabilities"]["agents"] == ["liability"]
    assert by_name["insurance_policies"]["agents"] == ["tax_and_insurance", "insurance"]
    for x in v2:
        assert x["type"] == "array"
        assert x["wrap_result"] is False
        assert x["array_items"]["kind"] == "object"
        assert len(x["array_items"]["properties"]) >= 5
        assert len(x["id"]) == 32

    # Deleted-data file count matches the delete rule on the live backup
    backup_path = Path(__file__).resolve().parents[2] / "exports" / "attributes_v1_backup_2026-10-03.json"
    if backup_path.exists():
        backup = json.loads(backup_path.read_text(encoding="utf-8"))
        groups = {"assets", "banking / accounts", "expenses", "goals", "income", "liabilities", "insurance"}
        keep = {"term_insurance_coverage_adequacy", "health_insurance_coverage_adequacy"}
        expected = [
            a for a in backup["attributes"]
            if (a.get("group_name") or "").strip().lower() in groups and a["name"] not in keep
        ]
        deleted = json.loads((base / "0017_attributes_v1_deleted.json").read_text(encoding="utf-8"))
        assert len(deleted) == len(expected) == 77


def test_plan_auto_insurance_fallback_contains_v2_attrs():
    from app.modules.runs.router import plan_auto_agents

    class _Ag:
        def __init__(self, name, enabled=True, kind="extraction"):
            self.name = name
            self.is_enabled = enabled
            self.kind = kind

    class _At:
        def __init__(self, id, name, group):
            self.id = id
            self.name = name
            self.group_name = group

    ti_attrs = [
        _At("id-pol", "insurance_policies", "Insurance"),
        _At("id-term", "term_insurance_coverage_adequacy", "Insurance"),
        _At("id-health", "health_insurance_coverage_adequacy", "Insurance"),
        _At("id-tax", "advance_tax", "Tax"),
    ]
    agents_by_name = {"tax_and_insurance": _Ag("tax_and_insurance")}
    attrs_by_name = {"tax_and_insurance": ti_attrs}
    # Only insurance needed, split agent missing -> fallback to combined subset
    planned = plan_auto_agents({"insurance": True}, "Other", agents_by_name, attrs_by_name)
    assert len(planned) == 1
    entry = planned[0]
    assert entry["agent"].name == "tax_and_insurance"
    assert set(entry["attribute_ids"]) == {"id-pol", "id-term", "id-health"}
