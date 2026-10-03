"""Export Attributes v2 proposal workbook (read-only DB, no migration).

Run from backend/ with:
    PYTHONPATH=. uv run --with openpyxl python ../scripts/export_attributes_v2.py

Reads current attributes ordered by group_name, name and writes
exports/attributes_v2.xlsx with sheets:
  README, Attributes v2, Dict formats, Old -> New mapping, JSON schema.
"""

import json
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from app.db.models import Attribute, Agent, agent_attributes
from app.db.session import SessionLocal

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "exports" / "attributes_v2.xlsx"

AMOUNT_DESC = (
    "Currency code or unit, one space, digits only - no commas, no symbols, no words: "
    "'INR 1000000', 'USD 5400', '50 units', '0.5 BTC'. Convert lakh/crore to digits "
    "(10 lakh -> INR 1000000). '0' when the Client says they have none of this type. "
    "'Yes' when the Client has it but gave no figure. null when not mentioned."
)

ITEM_RULE = (
    "Return one item per distinct holding/instance mentioned. If the Client mentions "
    "a type only in aggregate, return one item for that type. Return an empty list "
    "when the group is not discussed."
)

CONFIDENCE_DESC = "How confident the model is in this item (0-1)."
CONFIDENCE_TYPE_DESC = (
    "quoted = stated verbatim; normalized = stated but reformatted "
    "(e.g. '10 lakh' -> 'INR 1000000'); inferred = clearly implied; "
    "calculated = derived from several stated pieces; "
    "not_found = no value found for this type."
)
EVIDENCE_DESC = "Short verbatim quote(s) from the transcript supporting this item."

CONFIDENCE_ENUM = ["quoted", "normalized", "inferred", "calculated", "not_found"]

ADEQUACY_NAMES = {
    "term_insurance_coverage_adequacy",
    "health_insurance_coverage_adequacy",
}

REPLACED_GROUPS = {
    "Assets",
    "Banking / Accounts",
    "Expenses",
    "Goals",
    "Income",
    "Insurance",
    "Liabilities",
    "Tax",
    "Tax / Compliance",
}

# ---------------------------------------------------------------------------
# New attribute definitions (proposal). Fields are in display order; the 3
# common fields are appended automatically.
# Each field: name, type (string|number|integer|enum|array), required bool,
#   nullable bool, enum list, description.
# ---------------------------------------------------------------------------

COMMON_FIELDS = [
    {
        "name": "confidence",
        "type": "number",
        "required": True,
        "nullable": False,
        "enum": [],
        "description": CONFIDENCE_DESC,
    },
    {
        "name": "confidence_type",
        "type": "enum",
        "required": True,
        "nullable": False,
        "enum": list(CONFIDENCE_ENUM),
        "description": CONFIDENCE_TYPE_DESC,
    },
    {
        "name": "evidence",
        "type": "string",
        "required": False,
        "nullable": True,
        "enum": [],
        "description": EVIDENCE_DESC,
    },
]


def _f(name, ftype, required, nullable, enum=None, description=""):
    return {
        "name": name,
        "type": ftype,
        "required": required,
        "nullable": nullable,
        "enum": list(enum or []),
        "description": description,
    }


NEW_ATTRIBUTES = [
    {
        "name": "assets",
        "group": "Assets",
        "agent": "asset",
        "description_base": (
            "Consolidated list of all asset holdings. Each item is one "
            "holding/instance with its type, name, location and value."
        ),
        "fields": [
            _f("asset_type", "enum", True, False, [
                "stocks", "unlisted_stocks", "equity_mutual_fund", "equity_etf",
                "commodity_etf", "debt_etf", "deposit", "cash", "debt_mf",
                "personal_debt", "bonds", "reits", "real_estate", "crypto",
                "others", "total",
            ], ""),
            _f("name", "string", False, True, [],
               "Instrument / scheme / property / borrower name, e.g. 'HDFC Flexi Cap', 'Flat in Pune'"),
            _f("location", "enum", False, True, ["India", "Foreign"],
               "Where the asset is held; for asset_type total, null = overall total"),
            _f("amount", "string", False, True, [],
               "Current value, or units held. " + AMOUNT_DESC),
        ],
        "example": {
            "asset_type": "bonds",
            "name": "RBI Floating Rate Bond",
            "location": "India",
            "amount": "INR 500000",
            "confidence": 0.9,
            "confidence_type": "normalized",
            "evidence": "I have about 5 lakh in RBI bonds",
        },
    },
    {
        "name": "accounts",
        "group": "Banking / Accounts",
        "agent": "account",
        "description_base": (
            "Consolidated list of all accounts (bank, investment, government "
            "schemes, brokers, custody). Each item is one account with its "
            "type, provider, name, location and balance."
        ),
        "fields": [
            _f("account_type", "enum", True, False, [
                "investment_account", "bank_account", "ppf", "bank_deposit",
                "nps", "ssy", "epf", "pms", "aif", "crypto_broker",
                "self_custody", "others",
            ], ""),
            _f("provider", "string", False, True, [],
               "Bank / broker / fund house / wallet name, e.g. 'HDFC Bank', 'Zerodha'"),
            _f("account_name", "string", False, True, [],
               "Specific account or product name if given"),
            _f("location", "enum", False, True, ["India", "Foreign"], ""),
            _f("balance", "string", False, True, [], AMOUNT_DESC),
        ],
        "example": {
            "account_type": "bank_account",
            "provider": "HDFC Bank",
            "account_name": "Salary account",
            "location": "India",
            "balance": "INR 250000",
            "confidence": 0.95,
            "confidence_type": "quoted",
            "evidence": "I have around 2.5 lakh in my HDFC salary account",
        },
    },
    {
        "name": "expenses",
        "group": "Expenses",
        "agent": "expense",
        "description_base": (
            "Consolidated list of all recurring and one-time expenses. Each "
            "item is one expense type with amount and frequency."
        ),
        "fields": [
            _f("expense_type", "enum", True, False, [
                "rent", "utilities", "groceries", "household_staff",
                "childcare_school_fees", "entertainment_dining",
                "transport_fuel", "family_support", "shopping_purchases",
                "subscriptions_memberships", "insurance_premiums",
                "travel_vacations", "medical_expenses", "loan_emis_education",
                "loan_emis_personal", "loan_emis_home", "loan_emis_vehicle",
                "loan_emis_others", "loan_emis_total", "charity_donations",
                "miscellaneous", "total",
            ], ""),
            _f("description", "string", False, True, [], ""),
            _f("amount", "string", False, True, [], AMOUNT_DESC),
            _f("frequency", "enum", False, True, ["monthly", "annual", "one_time"],
               "As stated by the Client - do NOT convert annual to monthly"),
        ],
        "example": {
            "expense_type": "rent",
            "description": "2BHK in Whitefield, Bengaluru",
            "amount": "INR 30000",
            "frequency": "monthly",
            "confidence": 0.95,
            "confidence_type": "quoted",
            "evidence": "We pay 30k rent for our 2BHK in Whitefield",
        },
    },
    {
        "name": "goals",
        "group": "Goals",
        "agent": "goal",
        "description_base": (
            "Consolidated list of all client goals. Each item is one goal "
            "with type, description, target amount, target year and priority."
        ),
        "fields": [
            _f("goal_type", "enum", True, False, [
                "planning_a_child", "child_s_college_education",
                "child_s_higher_education", "buying_a_house_india",
                "buying_a_vehicle", "wedding_expenses",
                "parents_healthcare_fund", "vacation_travel_fund",
                "legacy_inheritance", "financial_freedom_fire", "other_goals",
            ], ""),
            _f("description", "string", False, True, [], ""),
            _f("target_amount", "string", False, True, [],
               "Target corpus / cost. " + AMOUNT_DESC),
            _f("target_year", "integer", False, True, [],
               "Calendar year the Client wants to achieve the goal, e.g. 2032. "
               "If stated as 'in N years', add N to the meeting year and use "
               "confidence_type calculated"),
            _f("priority", "enum", False, True, ["high", "medium", "low"],
               "Only when the Client indicates it"),
        ],
        "example": {
            "goal_type": "child_s_higher_education",
            "description": "Daughter's MS in the US",
            "target_amount": "INR 8000000",
            "target_year": 2032,
            "priority": "high",
            "confidence": 0.85,
            "confidence_type": "inferred",
            "evidence": "We want to save around 80 lakh for our daughter's MS, maybe by 2032",
        },
    },
    {
        "name": "income_sources",
        "group": "Income",
        "agent": "income",
        "description_base": (
            "Consolidated list of all income sources. Each item is one "
            "source with type, source name, amount, frequency and location."
        ),
        "fields": [
            _f("income_type", "enum", True, False, [
                "salary", "business", "capital_gains", "rental",
                "speculation", "other_income",
            ], ""),
            _f("source", "string", False, True, [],
               "Employer / business / property name"),
            _f("amount", "string", False, True, [], AMOUNT_DESC),
            _f("frequency", "enum", False, True, ["monthly", "annual", "one_time"], ""),
            _f("location", "enum", False, True, ["India", "Foreign"], ""),
        ],
        "example": {
            "income_type": "salary",
            "source": "Infosys",
            "amount": "INR 200000",
            "frequency": "monthly",
            "location": "India",
            "confidence": 0.95,
            "confidence_type": "quoted",
            "evidence": "My monthly salary at Infosys is 2 lakh",
        },
    },
    {
        "name": "insurance_policies",
        "group": "Insurance",
        "agent": "tax_and_insurance",
        "description_base": (
            "Consolidated list of all insurance policies (life term, health, "
            "ULIP, other). Each item is one policy with cover, premium and "
            "source. The two boolean adequacy judgements stay as separate "
            "attributes (open decision 1)."
        ),
        "fields": [
            _f("insurance_type", "enum", True, False,
               ["life_term", "health", "ulip", "other"], ""),
            _f("insurer", "string", False, True, [], ""),
            _f("policy_name", "string", False, True, [], ""),
            _f("covered_members", "string", False, True, [],
               "Who is covered, e.g. 'self, spouse, 2 children'"),
            _f("cover_amount", "string", False, True, [],
               "Sum assured / cover. " + AMOUNT_DESC),
            _f("premium_amount", "string", False, True, [], AMOUNT_DESC),
            _f("premium_frequency", "enum", False, True,
               ["monthly", "quarterly", "annual", "one_time"], ""),
            _f("source", "enum", False, True, ["employer", "personal"],
               "employer = group / corporate cover"),
        ],
        "example": {
            "insurance_type": "life_term",
            "insurer": "HDFC Life",
            "policy_name": "Click 2 Protect",
            "covered_members": "self",
            "cover_amount": "INR 10000000",
            "premium_amount": "INR 12000",
            "premium_frequency": "annual",
            "source": "personal",
            "confidence": 0.9,
            "confidence_type": "quoted",
            "evidence": "I have a 1 crore HDFC term plan with 12k annual premium",
        },
    },
    {
        "name": "liabilities",
        "group": "Liabilities",
        "agent": "liability",
        "description_base": (
            "Consolidated list of all liabilities/loans. Each item is one "
            "loan with lender, outstanding, EMI, rate and end year."
        ),
        "fields": [
            _f("liability_type", "enum", True, False, [
                "home_loan", "vehicle_loan", "education_loan",
                "personal_loan", "gold_loan", "credit_card_debt",
                "other_loan", "total",
            ], ""),
            _f("lender", "string", False, True, [], ""),
            _f("outstanding_amount", "string", False, True, [], AMOUNT_DESC),
            _f("emi_amount", "string", False, True, [],
               "Monthly EMI. " + AMOUNT_DESC),
            _f("interest_rate", "string", False, True, [], "e.g. '8.5%'"),
            _f("end_year", "integer", False, True, [], "Year the loan ends"),
        ],
        "example": {
            "liability_type": "home_loan",
            "lender": "SBI",
            "outstanding_amount": "INR 4500000",
            "emi_amount": "INR 42000",
            "interest_rate": "8.5%",
            "end_year": 2040,
            "confidence": 0.9,
            "confidence_type": "normalized",
            "evidence": "SBI home loan of 45 lakh, EMI 42 thousand at 8.5%, till 2040",
        },
    },
    {
        "name": "tax_items",
        "group": "Tax",
        "agent": "tax_and_insurance",
        "description_base": (
            "Consolidated list of all tax and compliance items (India / "
            "non-India filing, advance tax, TDS, GST, W-8BEN). Each item is "
            "one type with status, country, years and summary. Merges groups "
            "'Tax' and 'Tax / Compliance'."
        ),
        "fields": [
            _f("tax_type", "enum", True, False, [
                "tax_filing_india", "tax_filing_non_india", "advance_tax",
                "rental_tds", "gst_services", "w8_ben",
            ], ""),
            _f("status", "enum", False, True, [
                "Filed", "Not Filed", "Recommended", "Not Needed",
                "NRI Landlord", "Resident Landlord", "Client has GST",
                "Requires GST", "May need in future",
            ],
                "Allowed per tax_type - tax_filing_*: Filed / Not Filed; "
                "advance_tax: Recommended / Not Needed; rental_tds: NRI Landlord / "
                "Resident Landlord; gst_services: Client has GST / Requires GST / "
                "May need in future; w8_ben: Filed / Not Filed"),
            _f("country", "string", False, True, [],
               "For tax_filing_non_india: the country"),
            _f("years", "array", True, False, [],
               "Financial / tax years mentioned, e.g. 'FY2024-25' for India, '2024' elsewhere"),
            _f("summary", "string", False, True, [],
               "One-line summary of what the Client said"),
        ],
        "example": {
            "tax_type": "tax_filing_india",
            "status": "Filed",
            "country": None,
            "years": ["FY2024-25"],
            "summary": "Filed India return for FY24-25",
            "confidence": 0.95,
            "confidence_type": "quoted",
            "evidence": "We filed our India taxes for FY24-25 in July",
        },
    },
]

for _na in NEW_ATTRIBUTES:
    _na["fields"] = list(_na["fields"]) + [dict(c) for c in COMMON_FIELDS]
    _na["description"] = _na["description_base"] + " " + ITEM_RULE

NEW_BY_NAME = {n["name"]: n for n in NEW_ATTRIBUTES}

HEADER_FONT = Font(bold=True)
HEADER_FILL = PatternFill("solid", fgColor="D9D9D9")
NEW_FILL = PatternFill("solid", fgColor="C6EFCE")
KEEP_FILL = PatternFill("solid", fgColor="FFEB9C")
BLOCK_FILL = PatternFill("solid", fgColor="DDEBF7")
WRAP_TOP = Alignment(wrap_text=True, vertical="top")
CENTER_TOP_WRAP = Alignment(wrap_text=True, vertical="top")


def style_header_row(ws, ncols):
    for col in range(1, ncols + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER_TOP_WRAP
    ws.freeze_panes = "A2"


def set_widths(ws, widths):
    from openpyxl.utils import get_column_letter

    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def format_enum_values(values):
    if not values:
        return ""
    return " | ".join(str(v) for v in values)


def format_db_item_fields(attr):
    """One per line as 'name: type [enum] (nullable/required)' for DB rows."""
    props = []
    atype = (attr.type or "").strip().lower()
    if atype == "object":
        props = attr.object_properties or []
    elif atype == "array":
        raw = attr.array_items or {}
        if isinstance(raw, dict) and str(raw.get("kind", "")).lower() == "object":
            props = raw.get("properties", []) or []
        else:
            return ""
    else:
        return ""
    lines = []
    for p in props:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", ""))
        ptype = str(p.get("type", "string"))
        enum = p.get("enum", []) or []
        null_allowed = p.get("null_allowed", True)
        suffix = "(nullable)" if null_allowed else "(required)"
        if enum:
            lines.append(f"{name}: {ptype} [{' | '.join(enum)}] {suffix}")
        else:
            lines.append(f"{name}: {ptype} {suffix}")
    return "\n".join(lines)


def format_proposal_item_fields(fields):
    lines = []
    for f in fields:
        enum = f.get("enum", []) or []
        req = "(required)" if f.get("required") else "(nullable)"
        if f["type"] == "enum" and enum:
            lines.append(f"{f['name']}: enum [{' | '.join(enum)}] {req}")
        elif enum:
            lines.append(f"{f['name']}: {f['type']} [{' | '.join(enum)}] {req}")
        else:
            lines.append(f"{f['name']}: {f['type']} {req}")
    return "\n".join(lines)


def field_to_json_schema(f):
    desc = f.get("description", "")
    ftype = f.get("type", "string")
    nullable = bool(f.get("nullable", True))
    enum = f.get("enum", []) or []
    if ftype == "enum":
        if nullable:
            return {
                "type": ["string", "null"],
                "enum": list(enum) + [None],
                "description": desc,
            }
        return {"type": "string", "enum": list(enum), "description": desc}
    if ftype == "array":
        # years: array of strings
        if nullable:
            return {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "description": desc,
            }
        return {
            "type": "array",
            "items": {"type": "string"},
            "description": desc,
        }
    if ftype == "integer":
        if nullable:
            return {"type": ["integer", "null"], "description": desc}
        return {"type": "integer", "description": desc}
    if ftype == "number":
        if nullable:
            return {"type": ["number", "null"], "description": desc}
        return {"type": "number", "description": desc}
    # string (covers amount/balance/evidence/... stored as strings)
    if nullable:
        return {"type": ["string", "null"], "description": desc}
    return {"type": "string", "description": desc}


def build_json_schema(new_attr):
    props = {}
    required = []
    for f in new_attr["fields"]:
        props[f["name"]] = field_to_json_schema(f)
        required.append(f["name"])
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": props,
            "required": required,
            "additionalProperties": False,
        },
    }


def mapping_for_old(group, name):
    if group == "Assets":
        if name == "others_asset_type":
            return ("assets", "asset_type=others")
        return ("assets", f"asset_type={name}")
    if group == "Banking / Accounts":
        if name == "others_account_type":
            return ("accounts", "account_type=others")
        return ("accounts", f"account_type={name}")
    if group == "Expenses":
        return ("expenses", f"expense_type={name}")
    if group == "Goals":
        return ("goals", f"goal_type={name}")
    if group == "Income":
        return ("income_sources", f"income_type={name}")
    if group == "Insurance":
        if name == "health":
            return ("insurance_policies", "insurance_type=health")
        if name == "life_term":
            return ("insurance_policies", "insurance_type=life_term")
        if name == "other_ulip":
            return ("insurance_policies", "insurance_type=ulip or other")
    if group == "Liabilities":
        if name == "total_liabilities":
            return ("liabilities", "liability_type=total")
        return ("liabilities", f"liability_type={name}")
    if group == "Tax":
        if name == "advance_tax":
            return ("tax_items", "tax_type=advance_tax")
        if name == "rental_tds":
            return ("tax_items", "tax_type=rental_tds")
        if name == "tax_filing_history_for_india":
            return ("tax_items", "tax_type=tax_filing_india (summary)")
        if name == "tax_filing_years_for_india":
            return ("tax_items", "tax_type=tax_filing_india (years)")
        if name == "tax_filing_history_for_non_india":
            return ("tax_items", "tax_type=tax_filing_non_india (summary)")
        if name == "tax_filing_years_for_non_india":
            return ("tax_items", "tax_type=tax_filing_non_india (years)")
    if group == "Tax / Compliance":
        if name == "gst_services":
            return ("tax_items", "tax_type=gst_services")
        if name == "w8_ben":
            return ("tax_items", "tax_type=w8_ben")
    return ("", "")


def main():
    db = SessionLocal()
    try:
        attrs = (
            db.query(Attribute)
            .order_by(Attribute.group_name, Attribute.name)
            .all()
        )
        agent_map = {a.id: a.name for a in db.query(Agent).all()}
        attr_agents = {}
        for r in attrs:
            rows = db.execute(
                agent_attributes.select().where(
                    agent_attributes.c.attribute_id == r.id
                )
            ).all()
            attr_agents[r.id] = sorted(
                {agent_map.get(x.agent_id, x.agent_id) for x in rows}
            )
    finally:
        db.close()

    total_now = len(attrs)
    deleted_rows = [
        r for r in attrs
        if r.group_name in REPLACED_GROUPS and r.name not in ADEQUACY_NAMES
    ]
    n_deleted = len(deleted_rows)
    n_after = total_now - n_deleted + len(NEW_ATTRIBUTES)

    wb = Workbook()

    # ---------------- README ----------------
    ws = wb.active
    ws.title = "README"
    ws.append(["Section", "Text"])
    style_header_row(ws, 2)
    set_widths(ws, [26, 130])
    readme_rows = [
        ("About this file",
         "Attributes v2 - a PROPOSAL for Siva to approve BEFORE any DB migration. "
         "Nothing here is in the DB yet; the 8 NEW list attributes are proposed additions."),
        ("What changes",
         "The attributes of 9 groups (Assets, Banking / Accounts, Expenses, Goals, "
         "Income, Insurance, Liabilities, Tax, Tax / Compliance) are replaced by ONE "
         "list-of-dicts attribute per group (8 new attributes - Tax and Tax / Compliance "
         "merge into tax_items). Every other attribute stays exactly as it is in the DB. "
         "Group 'Banking' (credit_cards, basic_info agent) and every other group are NOT replaced."),
        ("New attributes (8)",
         "assets (Assets, agent asset); accounts (Banking / Accounts, agent account); "
         "expenses (Expenses, agent expense); goals (Goals, agent goal); "
         "income_sources (Income, agent income); insurance_policies (Insurance, agent "
         "tax_and_insurance); liabilities (Liabilities, agent liability); "
         "tax_items (Tax, agent tax_and_insurance). All new attributes: type array, "
         "array_items kind object."),
        ("Amount convention",
         "Every field whose name ends in amount or is called balance: type string, "
         "nullable. " + AMOUNT_DESC),
        ("Common fields (added LAST to every item)",
         "confidence: number 0-1, required. " + CONFIDENCE_DESC + " | "
         "confidence_type: enum [quoted | normalized | inferred | calculated | not_found], "
         "required. " + CONFIDENCE_TYPE_DESC + " | "
         "evidence: string, nullable. " + EVIDENCE_DESC),
        ("Item-level rule",
         "Appended to each new attribute's description: " + ITEM_RULE),
        ("OPEN DECISION 1",
         "Insurance has two boolean judgements (term_insurance_coverage_adequacy, "
         "health_insurance_coverage_adequacy) that are not list items - proposed to KEEP "
         "them unchanged next to insurance_policies; delete only if Siva confirms."),
        ("OPEN DECISION 2",
         "Every attribute is already wrapped by the result contract "
         "{value, confidence, confidence_type, evidence}. With per-item confidence the "
         "wrapper on these 8 list attributes becomes a group-level summary - proposed: "
         "keep the wrapper (overall confidence; evidence may be null)."),
        ("OPEN DECISION 3",
         "Group 'Tax / Compliance' (gst_services, w8_ben) is merged into tax_items under "
         "group 'Tax'; Auto Select routing (groups 'Tax' + 'Tax / Compliance') keeps working."),
        ("OPEN DECISION 4",
         "Fields beyond Siva's minimum (name, provider, account_name, location, "
         "description, frequency, priority, insurer, policy_name, covered_members, "
         "premium_*, source, lender, emi_amount, interest_rate, end_year, country, "
         "years, summary) are proposals - remove any not wanted."),
        ("OPEN DECISION 5",
         "The current extraction schema enum for confidence_type is "
         "[quoted, inferred, normalized, not_found] while the prompt also documents "
         "'calculated'; v2 uses all five values for both the wrapper and items."),
        ("Counts",
         f"Attributes in DB now: {total_now}. Attributes deleted by this proposal: "
         f"{n_deleted} (every attribute in the 9 groups except the 2 adequacy booleans). "
         f"Attributes after (proposed catalogue): {n_after} "
         f"({total_now - n_deleted} unchanged incl. 2 KEEP? + {len(NEW_ATTRIBUTES)} NEW)."),
        ("Sheets",
         "Attributes v2 = full proposed catalogue sorted by Group then Name. "
         "Dict formats = one block per new attribute with field table + JSON example. "
         "Old -> New mapping = one row per deleted attribute + 2 KEEP? rows. "
         "JSON schema = strict OpenRouter json_schema fragment per new attribute."),
    ]
    for section, text in readme_rows:
        ws.append([section, text])
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=2):
        row[0].alignment = WRAP_TOP
        row[1].alignment = WRAP_TOP
    ws.auto_filter.ref = f"A1:B1"
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    # ---------------- Attributes v2 ----------------
    ws2 = wb.create_sheet("Attributes v2")
    headers2 = ["Status", "Group", "Name", "Type", "Description / Definition",
                "Enum values", "Object / array item fields", "Agents", "ID"]
    ws2.append(headers2)
    style_header_row(ws2, len(headers2))
    set_widths(ws2, [24, 22, 32, 12, 60, 30, 62, 20, 38])

    # Build combined sorted rows
    combined = []
    for r in attrs:
        if r.name in ADEQUACY_NAMES:
            status = "KEEP? (open decision 1)"
        else:
            status = "UNCHANGED" if r.group_name not in REPLACED_GROUPS else "UNCHANGED"
            # Deleted groups still show as UNCHANGED? No - deleted attrs are NOT in v2.
            # Only keep attrs NOT deleted.
            if r.group_name in REPLACED_GROUPS:
                continue  # deleted, skip (except adequacy handled above)
        combined.append({
            "status": status,
            "group": r.group_name or "",
            "name": r.name or "",
            "type": r.type or "",
            "desc": r.description or "",
            "enum": format_enum_values(r.enum_values or []),
            "items": format_db_item_fields(r),
            "agents": ", ".join(attr_agents.get(r.id, [])),
            "id": r.id,
            "is_new": False,
        })
    for n in NEW_ATTRIBUTES:
        combined.append({
            "status": "NEW",
            "group": n["group"],
            "name": n["name"],
            "type": "array",
            "desc": n["description"],
            "enum": "",
            "items": format_proposal_item_fields(n["fields"]),
            "agents": n["agent"],
            "id": "",
            "is_new": True,
        })
    combined.sort(key=lambda d: ((d["group"] or "").lower(), (d["name"] or "").lower()))

    for d in combined:
        ws2.append([d["status"], d["group"], d["name"], d["type"], d["desc"],
                    d["enum"], d["items"], d["agents"], d["id"]])
    # Wrap long columns + fills
    for idx, d in enumerate(combined, start=2):
        for col in (5, 6, 7):
            ws2.cell(row=idx, column=col).alignment = WRAP_TOP
        for col in (1, 2, 3, 4, 8, 9):
            ws2.cell(row=idx, column=col).alignment = Alignment(vertical="top", wrap_text=True)
        # Row height heuristic based on item-field lines
        lines = max(1, str(ws2.cell(row=idx, column=7).value or "").count("\n") + 1,
                    str(ws2.cell(row=idx, column=5).value or "").count("\n") + 1)
        # also account for long single-line text
        longest = max(len(str(ws2.cell(row=idx, column=5).value or "")),
                      len(str(ws2.cell(row=idx, column=7).value or "")))
        est = max(lines * 15, min(120, 15 + longest // 60 * 15))
        ws2.row_dimensions[idx].height = min(180, est)
        if d["status"] == "NEW":
            for col in range(1, len(headers2) + 1):
                ws2.cell(row=idx, column=col).fill = NEW_FILL
        elif d["status"].startswith("KEEP?"):
            for col in range(1, len(headers2) + 1):
                ws2.cell(row=idx, column=col).fill = KEEP_FILL
    ws2.auto_filter.ref = ws2.dimensions

    # ---------------- Dict formats ----------------
    ws3 = wb.create_sheet("Dict formats")
    headers3 = ["Field", "Type", "Required/Nullable", "Enum values", "Description"]
    ws3.append(headers3)
    style_header_row(ws3, len(headers3))
    set_widths(ws3, [22, 14, 18, 52, 85])
    from openpyxl.utils import get_column_letter
    for n in NEW_ATTRIBUTES:
        # block header
        r = ws3.max_row + 1
        ws3.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
        cell = ws3.cell(row=r, column=1)
        cell.value = f"{n['name']}  (Group: {n['group']} - Agent: {n['agent']} - Type: array of objects)"
        cell.font = Font(bold=True)
        cell.fill = BLOCK_FILL
        cell.alignment = Alignment(vertical="center")
        for f in n["fields"]:
            ws3.append([
                f["name"],
                f["type"],
                "required" if f["required"] else "nullable",
                format_enum_values(f.get("enum", [])),
                f.get("description", ""),
            ])
        # Example row
        ex_row = ws3.max_row + 1
        ws3.append(["Example", "", "", "",
                    json.dumps(n["example"], indent=2, ensure_ascii=False)])
        for col in range(1, 6):
            ws3.cell(row=ex_row, column=col).alignment = WRAP_TOP
        ws3.row_dimensions[ex_row].height = 110
        # blank row between blocks
        ws3.append(["", "", "", "", ""])
    # wrap all
    for row in ws3.iter_rows(min_row=2, max_row=ws3.max_row, max_col=5):
        for c in row:
            if c.alignment is None or c.alignment.wrap_text is False:
                pass
            c.alignment = WRAP_TOP
    # re-apply block header fills (wrap loop overwrote alignment only, fine)
    ws3.auto_filter.ref = f"A1:E1"

    # ---------------- Old -> New mapping ----------------
    ws4 = wb.create_sheet("Old -> New mapping")
    headers4 = ["Old group", "Old name", "Old type", "Old agent(s)",
                "New attribute", "New type value"]
    ws4.append(headers4)
    style_header_row(ws4, len(headers4))
    set_widths(ws4, [20, 34, 12, 18, 20, 48])
    for r in deleted_rows:
        new_attr, new_val = mapping_for_old(r.group_name, r.name)
        ws4.append([r.group_name, r.name, r.type,
                    ", ".join(attr_agents.get(r.id, [])),
                    new_attr, new_val])
    # 2 extra rows for adequacy booleans
    for r in attrs:
        if r.name in ADEQUACY_NAMES:
            ws4.append([r.group_name, r.name, r.type,
                        ", ".join(attr_agents.get(r.id, [])),
                        "kept as-is (open decision 1)", ""])
    for row in ws4.iter_rows(min_row=2, max_row=ws4.max_row, max_col=6):
        for c in row:
            c.alignment = WRAP_TOP
    ws4.auto_filter.ref = ws4.dimensions

    # ---------------- JSON schema ----------------
    ws5 = wb.create_sheet("JSON schema")
    headers5 = ["Attribute", "Schema"]
    ws5.append(headers5)
    style_header_row(ws5, len(headers5))
    set_widths(ws5, [20, 135])
    for n in NEW_ATTRIBUTES:
        schema = build_json_schema(n)
        ws5.append([n["name"], json.dumps(schema, indent=2, ensure_ascii=False)])
    for idx in range(2, ws5.max_row + 1):
        ws5.cell(row=idx, column=1).alignment = Alignment(vertical="top")
        ws5.cell(row=idx, column=2).alignment = WRAP_TOP
        ws5.row_dimensions[idx].height = 260
    ws5.auto_filter.ref = f"A1:B1"

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT_PATH)

    # ---------------- Verify ----------------
    wb2 = load_workbook(OUT_PATH)
    print("Sheets:", wb2.sheetnames)
    for name in wb2.sheetnames:
        wsx = wb2[name]
        print(f"{name}: {wsx.max_row} rows x {wsx.max_column} cols (incl. header)")
    wsx = wb2["Attributes v2"]
    status_idx = 1
    counts = {"NEW": 0, "UNCHANGED": 0, "KEEP?": 0}
    for row in wsx.iter_rows(min_row=2, values_only=True):
        s = str(row[0] or "")
        if s == "NEW":
            counts["NEW"] += 1
        elif s.startswith("KEEP?"):
            counts["KEEP?"] += 1
        elif s == "UNCHANGED":
            counts["UNCHANGED"] += 1
    print(f"Status counts: NEW={counts['NEW']} UNCHANGED={counts['UNCHANGED']} "
          f"KEEP?={counts['KEEP?']}")
    print(f"DB now={total_now} deleted={n_deleted} after={n_after}")
    print(f"Wrote {OUT_PATH}")
    print(f"Summary: v2 proposal with {len(NEW_ATTRIBUTES)} NEW list attributes replacing "
          f"{n_deleted} attributes across 9 groups; {total_now - n_deleted} kept "
          f"(incl. 2 KEEP? adequacy booleans); catalogue after = {n_after}.")


if __name__ == "__main__":
    main()
