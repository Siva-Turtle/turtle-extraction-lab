"""Synthetic-only smoke test for the renewal redaction copy.

No live Mongo, no real PII — all fixtures are fake. Run:
    python ./test_redaction_smoke.py
"""

from pii_redaction import mask_generic_pii, redact_document
from redacted_mongo import ReadOnlyViolation, RedactedCollection, redact_client_journey


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_generic_mirrors_source():
    _assert("[PAN]" in mask_generic_pii("PAN ABCDE1234F here"), "PAN not redacted")
    _assert("[EMAIL]" in mask_generic_pii("mail fake.person@example.com x"), "EMAIL missed")
    _assert("[PHONE]" in mask_generic_pii("call +91 98765 43210 now"), "PHONE missed")
    _assert(mask_generic_pii("plain NRI renewal") == "plain NRI renewal", "over-redaction")


def test_direct_identifiers_never_raw():
    doc = {
        "fullName": "Fake Name",
        "phone": "+91 90000 11111",
        "email": "fake.name@example.com",
        "status": "expired",
        "clientType": "NRI",
        "notes": "Fake Name mailed fake.name@example.com, PAN ABCDE1234F",
    }
    out = redact_document(doc)
    for v in out.values():
        _assert("Fake Name" not in str(v), "name leaked")
        _assert("fake.name@example.com" not in str(v), "email leaked")
        _assert("ABCDE1234F" not in str(v), "PAN leaked")
    _assert(out["fullName"] == "[CLIENT_NAME]", "name token wrong")
    _assert(out["phone"] == "[PHONE]", "phone token wrong")
    _assert(out["status"] == "expired", "analytical field changed")


def test_known_contact_across_tasks():
    client = {"fullName": "Synthetic Person", "phone": "+91 91111 22222", "email": "a@b.co"}
    tasks = [{"summary": "Synthetic Person discussed RNOR on +91 91111 22222"}]
    rc, rt = redact_client_journey(client, tasks)
    _assert("Synthetic Person" not in rt[0]["summary"], "known name leaked in task")
    _assert("[CLIENT_NAME]" in rt[0]["summary"], "known name not tokenised")
    _assert(rc["fullName"] == "[CLIENT_NAME]", "client name raw")


def test_readonly_block():
    c = RedactedCollection.__new__(RedactedCollection)
    for banned in ("insert_one", "update_one", "delete_one", "drop"):
        try:
            getattr(c, banned)
            raise AssertionError(f"{banned} should be blocked")
        except ReadOnlyViolation:
            pass


if __name__ == "__main__":
    test_generic_mirrors_source()
    test_direct_identifiers_never_raw()
    test_known_contact_across_tasks()
    test_readonly_block()
    print("redaction smoke: OK (synthetic only, no live data touched)")
