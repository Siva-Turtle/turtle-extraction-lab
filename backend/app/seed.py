"""Idempotent seed: one demo agent with attributes so the Test Lab works out of the box."""

from app.db.models import Agent, Attribute, agent_attributes
from app.db.session import SessionLocal

AGENT_NAME = "Contact Facts"
ATTRIBUTES = [
    ("full_name", "string", "Person's full name as stated", []),
    ("phone", "string", "Phone number in any format mentioned", []),
    ("email", "string", "Email address if mentioned", []),
]


def main() -> None:
    db = SessionLocal()
    try:
        agent = db.query(Agent).filter(Agent.name == AGENT_NAME).first()
        if not agent:
            agent = Agent(
                name=AGENT_NAME,
                system_instruction="Extract structured contact facts. Only use information stated in the input.",
                input_types=["transcription", "messages", "mail"],
            )
            db.add(agent)
            db.commit()
            db.refresh(agent)
        linked_ids = {r.attribute_id for r in db.execute(
            agent_attributes.select().where(agent_attributes.c.agent_id == agent.id)).all()}
        existing = {a.name: a for a in db.query(Attribute).all()}
        for name, type_, desc, enum_values in ATTRIBUTES:
            attr = existing.get(name)
            if attr is None:
                attr = Attribute(name=name, type=type_, description=desc,
                                 enum_values=enum_values)
                db.add(attr)
                db.flush()
                existing[name] = attr
            if attr.id not in linked_ids:
                db.execute(agent_attributes.insert().values(
                    agent_id=agent.id, attribute_id=attr.id))
        db.commit()
        print(f"seed ok: agent '{AGENT_NAME}'")
    finally:
        db.close()


if __name__ == "__main__":
    main()
