"""Idempotent seed: one demo agent with attributes so the Test Lab works out of the box."""

from app.db.models import Agent, Attribute
from app.db.session import SessionLocal

AGENT_NAME = "Contact Facts"
ATTRIBUTES = [
    ("full_name", "string", "Person's full name as stated", True),
    ("phone", "string", "Phone number in any format mentioned", False),
    ("email", "string", "Email address if mentioned", False),
]


def main() -> None:
    db = SessionLocal()
    try:
        agent = db.query(Agent).filter(Agent.name == AGENT_NAME).first()
        if not agent:
            agent = Agent(
                name=AGENT_NAME,
                system_instruction="Extract structured contact facts. Only use information stated in the input.",
                prompt="Extract the contact facts listed as attributes.",
                input_types=["transcription", "messages", "mail"],
            )
            db.add(agent)
            db.commit()
            db.refresh(agent)
        existing = {a.name for a in db.query(Attribute).filter(Attribute.agent_id == agent.id).all()}
        for name, type_, desc, required in ATTRIBUTES:
            if name not in existing:
                db.add(Attribute(agent_id=agent.id, name=name, type=type_,
                                 description=desc, json_schema={}, required=required))
        db.commit()
        print(f"seed ok: agent '{AGENT_NAME}'")
    finally:
        db.close()


if __name__ == "__main__":
    main()
