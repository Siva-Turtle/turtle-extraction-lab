"""Small copy of the PII redaction layer for renewal segmentation.

Source: backend/app/core/scrub.py (Python port of CRM-v1-old
`backend/utils/piiMasking.js`). Intentionally a copy, not an import, so
renewal scripts keep working even if the backend scrub evolves — diff
against the source when in doubt, source wins on regex semantics.

Two passes, in order (same as source):
1. Known-contact pass — exact-match substitution of THIS contact's own
   name/phone/email already on file (ground truth).
2. Generic pass — regex redaction for EMAIL, PAN, IFSC, CARD, SSN,
   AADHAAR, then PHONE last (loosest pattern, must never pre-empt above).

Rule for renewal work: never return or log raw Mongo strings — every
string that leaves redacted_mongo.py has passed through here first.

Small extension vs source (documented, not silent):
- redact_document() — recursive dict/list scrubber for Mongo docs.
- Direct-identifier keys (fullName, phone, email, address, ...) are
  replaced wholesale with tokens, never scrubbed-and-kept, so the raw
  value never leaves the wrapper even if regex would miss it.
"""

import re

_EMAIL_RE = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_IFSC_RE = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b", re.IGNORECASE)
_CARD_RE = re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")
_SSN_RE = re.compile(r"\b\d{3}[ -]\d{2}[ -]\d{4}\b")
_AADHAAR_RE = re.compile(r"\b(?:\d{4}[ -]?){2}\d{4}\b")
_PHONE_RE = re.compile(r"(\+?\d[\d\s-]{6,13}\d)")


def mask_known_contact_pii(text, names=None, phones=None, emails=None):
    """Exact-match substitution of one contact's own name/phone/email."""
    if not text:
        return text
    masked = text

    for email in (emails or []):
        if not email:
            continue
        masked = masked.replace(email, "[EMAIL]")

    for phone in (phones or []):
        if not phone:
            continue
        digits = re.sub(r"\D", "", phone)
        if len(digits) < 7:
            continue
        digits_pattern = "[\\s-]*".join(digits)
        masked = re.sub(digits_pattern, "[PHONE]", masked)

    for name in (names or []):
        if not name:
            continue
        cleaned = name.strip()
        if len(cleaned) < 2:
            continue
        masked = re.sub(re.escape(cleaned), "[CLIENT_NAME]", masked, flags=re.IGNORECASE)

    return masked


def mask_generic_pii(text):
    """Regex redaction pass. Falsy `text` passes through unchanged."""
    if not text:
        return text

    def _phone_repl(match):
        digits = re.sub(r"\D", "", match.group(0))
        return "[PHONE]" if len(digits) >= 7 else match.group(0)

    return _PHONE_RE.sub(
        _phone_repl,
        _AADHAAR_RE.sub(
            "[AADHAAR]",
            _SSN_RE.sub(
                "[SSN]",
                _CARD_RE.sub(
                    "[CARD]",
                    _IFSC_RE.sub("[IFSC]", _PAN_RE.sub("[PAN]", _EMAIL_RE.sub("[EMAIL]", text))),
                ),
            ),
        ),
    )


def collect_known_pii(contact):
    """Adapt a Mongo client doc into the {names, phones, emails} shape."""
    if not contact:
        return {"names": [], "phones": [], "emails": []}
    if not isinstance(contact, dict):
        return {"names": [], "phones": [], "emails": []}
    raw_email = contact.get("email")
    if isinstance(raw_email, (list, tuple)):
        emails = [e for e in raw_email if e]
    elif raw_email:
        emails = [raw_email]
    else:
        emails = []
    return {
        "names": [v for v in [contact.get("fullName"), contact.get("spouseName")] if v],
        "phones": [
            v
            for v in [contact.get("phone"), contact.get("phone2"), contact.get("spousePhone")]
            if v
        ],
        "emails": emails,
    }


def mask_for_classifier(text, contact=None):
    """Single entry point every renewal call site should use."""
    if not text:
        return text
    known = collect_known_pii(contact)
    masked = mask_known_contact_pii(
        text, known.get("names"), known.get("phones"), known.get("emails")
    )
    return mask_generic_pii(masked)


# Direct-identifier keys are never returned raw — replaced wholesale.
_DIRECT_PII_TOKEN = {
    "fullName": "[CLIENT_NAME]",
    "spouseName": "[CLIENT_NAME]",
    "clientName": "[CLIENT_NAME]",
    "name": "[CLIENT_NAME]",
    "phone": "[PHONE]",
    "phone2": "[PHONE]",
    "spousePhone": "[PHONE]",
    "mobile": "[PHONE]",
    "email": "[EMAIL]",
    "address": "[ADDRESS]",
    "fullAddress": "[ADDRESS]",
    "pan": "[PAN]",
    "aadhaar": "[AADHAAR]",
    "ssn": "[SSN]",
}

# Analytical keys that are safe to keep verbatim (enums / dates / counts).
# Anything not in here AND not in _DIRECT_PII_TOKEN still gets scrubbed
# as a string — this list only controls what skips the scrub for speed.
_SAFE_VERBATIM_KEYS = frozenset(
    {
        "_id",
        "clientId",
        "status",
        "clientType",
        "nriStatus",
        "advisor",
        "plan",
        "subscriptionDate",
        "renewalDate",
        "joinDate",
        "callCount",
        "createdAt",
        "updatedAt",
    }
)


def redact_document(doc, contact=None):
    """Return a PII-redacted copy of a Mongo doc. Never mutates input.

    - dicts/lists recursed; numbers/bools/None pass through.
    - direct-identifier keys replaced with tokens (raw never leaves).
    - all other strings go through mask_for_classifier (known-contact +
      generic passes). `contact` defaults to the doc itself so the
      known-contact pass works without a second fetch.
    """
    if doc is None:
        return None
    if isinstance(doc, list):
        return [redact_document(v, contact) for v in doc]
    if not isinstance(doc, dict):
        if isinstance(doc, str):
            return mask_for_classifier(doc, contact if isinstance(contact, dict) else None)
        return doc
    ref = contact if isinstance(contact, dict) else doc
    out = {}
    for k, v in doc.items():
        if k in _DIRECT_PII_TOKEN:
            out[k] = _DIRECT_PII_TOKEN[k]
            continue
        if isinstance(v, dict):
            out[k] = redact_document(v, ref)
        elif isinstance(v, list):
            out[k] = [redact_document(x, ref) if isinstance(x, dict) else redact_value(x, ref) for x in v]
        else:
            out[k] = redact_value(v, ref)
    return out


def redact_value(value, contact=None):
    """Scrub a single scalar. Non-strings pass through untouched."""
    if isinstance(value, str):
        return mask_for_classifier(value, contact)
    return value
