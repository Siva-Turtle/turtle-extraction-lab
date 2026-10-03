"""Seed the 16 lab agents (15 extraction + 1 identifier router).

Usage:
    python scripts/seed_agents.py [--database-url URL]

Upsert only (never wipes): agents are matched by name; missing agents are
created, existing ones keep their system_instruction/input_types/links and
only get blank description/kind backfilled. Mapped attributes are linked
(additive — existing links are never removed).

Mapping labels below are CSV display labels (the `[Original: ...]` text).
Each label is resolved to the normalized DB attribute name by, in order:
  1. exact `[Original: <label>]` marker match in the attribute description,
  2. `normalize_name(label)` direct match on the attribute name
     (csv_import normalization logic, reused here),
  3. normalized comparison against each attribute's Original marker.
Unresolvable labels are reported (and cause a non-zero exit) — run the
attribute CSV import first, then re-run this script.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python backend/scripts/...` from repo root or backend/.
_HERE = Path(__file__).resolve()
for _cand in (_HERE.parents):
    if (_cand / "app" / "db" / "models.py").exists():
        if str(_cand) not in sys.path:
            sys.path.insert(0, str(_cand))
        break

from app.db.models import Agent, Attribute, agent_attributes  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.db.session import Base  # noqa: E402
from app.modules.attributes.csv_import import normalize_name  # noqa: E402

try:
    from app.modules.runs.router import IDENTIFIER_INSTRUCTION  # noqa: E402
except Exception:
    IDENTIFIER_INSTRUCTION = (
        "You are an agent router. Read the input transcript and decide which "
        "candidate extraction agents are relevant to it."
    )

INPUT_TYPES = ["transcription"]

# agent name -> (description, kind, system_instruction, [CSV display labels]).
# kind="identifier" agents take no attribute links.
AGENT_DEFS: list[tuple[str, str, str, str, list[str]]] = [
    ("basic_info",
     "Extracts identity, residence, family (HUF), will status, cards, work, alumni, travel and contact preferences.",
     "extraction", "",
     ["native_city", "native_state", "current_residence_city",
      "current_residence_state", "HUF", "HUF Source", "Will Executed (Y/N)",
      "Card Name(s)", "Works at", "Alumni (Institute)", "Alumni (Organization)",
      "From (Destination)", "From (Date)", "To (Destination)", "To (Date)",
      "Client's Availability (Weekday, Time, Timezone)",
      "Preferred mode of communication (Email/WA/Call)"]),
    ("kc_and_feedback",
     "Captures Karma Conversation taker attitude/readiness/knowledge, overall sentiment and feedback on the advisor.",
     "extraction", "",
     ["kc_taker_attitude", "kc_taker_readiness", "kc_taker_knowledge",
      "overall_sentiment", "sentiment_reasoning",
      "Client's feedback on Advisor"]),
    ("kc",
     "Karma Conversation attributes only",
     "extraction", "",
     ["kc_taker_attitude", "kc_taker_readiness", "kc_taker_knowledge"]),
    ("feedback",
     "Feedback and sentiment attributes only",
     "extraction", "",
     ["overall_sentiment", "sentiment_reasoning",
      "Client's feedback on Advisor"]),
    ("tax_and_insurance",
     "Extracts tax filing history, advance tax, rental TDS, GST, W8-BEN and insurance coverage adequacy.",
     "extraction", "",
     ["Term Insurance Coverage Adequacy", "Health Insurance Coverage Adequacy",
      "Tax Filing History (For India)", "Tax Filing Years (For India)",
      "Tax Filing History (For Non-India)", "Tax Filing Years (For Non-India)",
      "Advance Tax", "Rental TDS", "GST services", "W8-BEN",
      "Life/Term", "Health", "Other (ULIP)"]),
    ("tax",
     "Tax and compliance attributes only",
     "extraction", "",
     ["Tax Filing History (For India)", "Tax Filing Years (For India)",
      "Tax Filing History (For Non-India)", "Tax Filing Years (For Non-India)",
      "Advance Tax", "Rental TDS", "GST services", "W8-BEN"]),
    ("insurance",
     "Insurance attributes only",
     "extraction", "",
     ["Term Insurance Coverage Adequacy", "Health Insurance Coverage Adequacy",
      "Life/Term", "Health", "Other (ULIP)"]),
    ("query",
     "Collects every query the client asked during the conversation.",
     "extraction", "",
     ["List of All Queries asked"]),
    ("behavioral",
     "Profiles prospect attitude, financial acumen, biases, interests and investing style.",
     "extraction", "",
     ["Prospect's Attitude", "Prospect's Financial Acumen",
      "Prospect's Financial Background (Y/N)",
      "Prospect's Price Sensitivity (Y/N)",
      "Areas of Interest", "Financial Biases", "Investing Style"]),
    ("account",
     "Lists investment, bank, PPF, NPS, EPF, PMS, AIF and crypto accounts mentioned.",
     "extraction", "",
     ["Investment Account", "Bank Account", "PPF", "Bank Deposit", "NPS",
      "SSY", "EPF", "PMS", "AIF", "Crypto Broker", "Self Custody",
      "Others (Account Type)"]),
    ("asset",
     "Extracts holdings across stocks, mutual funds, ETFs, deposits, bonds, real estate, crypto and totals.",
     "extraction", "",
     ["Stocks", "Unlisted Stocks", "Equity Mutual Fund", "Equity ETF",
      "Commodity ETF", "Debt ETF", "Deposit", "Cash", "Debt MF",
      "Personal Debt", "Bonds", "REITs", "Real Estate", "Crypto",
      "Others (Asset Type)", "Total"]),
    ("income",
     "Captures salary, business, capital gains, rental, speculation and other income.",
     "extraction", "",
     ["Salary", "Business", "Capital Gains", "Rental", "Speculation",
      "Other Income"]),
    ("expense",
     "Extracts household, lifestyle, loan EMI and other expense line items.",
     "extraction", "",
     ["Rent", "Utilities", "Groceries", "Household Staff",
      "Childcare / School Fees", "Entertainment & Dining", "Transport / Fuel",
      "Family Support", "Shopping & Purchases", "Subscriptions & Memberships",
      "Insurance Premiums", "Travel / Vacations", "Medical Expenses",
      "Loan EMIs (Education)", "Loan EMIs (Personal)", "Loan EMIs (Home)",
      "Loan EMIs (Vehicle)", "Loan EMIs (Others)", "Loan EMIs (Total)",
      "Charity / Donations", "Miscellaneous"]),
    ("liability",
     "Lists home, vehicle, education, personal, gold, credit-card and other loans plus total liabilities.",
     "extraction", "",
     ["Home Loan", "Vehicle Loan", "Education Loan", "Personal Loan",
      "Gold Loan", "Credit Card Debt", "Other Loan", "Total Liabilities"]),
    ("goal",
     "Extracts life goals such as education, housing, wedding, healthcare, travel, legacy and FIRE.",
     "extraction", "",
     ["Planning a Child", "Child's College Education",
      "Child's Higher Education", "Buying a House (India)",
      "Buying a Vehicle", "Wedding Expenses", "Parents Healthcare Fund",
      "Vacation / Travel Fund", "Legacy / Inheritance",
      "Financial Freedom / FIRE", "Other Goals"]),
    ("agent_identifier",
     "Router: reads a transcript and selects which extraction agents should run (no attributes of its own).",
     "identifier", IDENTIFIER_INSTRUCTION,
     []),
]


def _original_marker(description: str) -> str:
    """Extract the `[Original: ...]` marker (csv_import convention)."""
    desc = description or ""
    idx = desc.rfind("[Original: ")
    if idx != -1 and desc.endswith("]"):
        return desc[idx + len("[Original: "):-1].strip()
    return ""


def resolve_label(label: str, by_marker: dict[str, str],
                   by_name: dict[str, object],
                   by_normalized_marker: dict[str, str]) -> str | None:
    """Resolve one CSV display label to a DB attribute name (or None)."""
    if label in by_marker:
        return by_marker[label]
    norm = normalize_name(label)
    if norm in by_name:
        return norm
    if norm in by_normalized_marker:
        return by_normalized_marker[norm]
    return None


def seed(db) -> dict:
    attrs = db.query(Attribute).all()
    by_name: dict[str, Attribute] = {a.name: a for a in attrs}
    by_marker: dict[str, str] = {}
    by_normalized_marker: dict[str, str] = {}
    for a in attrs:
        orig = _original_marker(getattr(a, "description", "") or "")
        if orig:
            by_marker.setdefault(orig, a.name)
            by_normalized_marker.setdefault(normalize_name(orig), a.name)

    created_agents = 0
    linked = 0
    unresolved: list[tuple[str, str]] = []
    for name, description, kind, instruction, labels in AGENT_DEFS:
        agent = db.query(Agent).filter(Agent.name == name).first()
        if agent is None:
            agent = Agent(name=name, description=description, kind=kind,
                          system_instruction=instruction,
                          input_types=list(INPUT_TYPES))
            db.add(agent)
            db.flush()
            created_agents += 1
        else:
            # Backfill only blanks — never overwrite existing config.
            changed = False
            if not (agent.description or "").strip():
                agent.description = description
                changed = True
            if not (getattr(agent, "kind", None) or "").strip():
                agent.kind = kind
                changed = True
            if changed:
                db.flush()
        if kind == "identifier":
            continue  # router takes no attribute links
        have = {r.attribute_id for r in db.execute(
            agent_attributes.select().where(
                agent_attributes.c.agent_id == agent.id)).all()}
        for label in labels:
            attr_name = resolve_label(label, by_marker, by_name,
                                      by_normalized_marker)
            if attr_name is None:
                unresolved.append((name, label))
                continue
            row_obj = by_name.get(attr_name)
            if row_obj is None or row_obj.id in have:
                continue
            db.execute(agent_attributes.insert().values(
                agent_id=agent.id, attribute_id=row_obj.id))
            have.add(row_obj.id)
            linked += 1
    db.commit()
    return {"agents": len(AGENT_DEFS), "created_agents": created_agents,
            "linked": linked, "unresolved": unresolved}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url", default=None)
    args = ap.parse_args()
    if args.database_url:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        eng = create_engine(args.database_url)
        Base.metadata.create_all(bind=eng)
        Sess = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
        db = Sess()
    else:
        db = SessionLocal()
    try:
        stats = seed(db)
    finally:
        db.close()
    print(f"seed-agents ok: agents={stats['agents']} "
          f"created={stats['created_agents']} linked={stats['linked']} "
          f"unresolved={len(stats['unresolved'])}")
    for agent_name, label in stats["unresolved"]:
        print(f"  unresolved: agent={agent_name!r} label={label!r}")
    if stats["unresolved"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
