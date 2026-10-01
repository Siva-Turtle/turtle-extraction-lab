"""PII scrubbing layer — Python port of the old CRM's `backend/utils/piiMasking.js`.

Strips client PII out of message/note text before it's ever sent to an LLM
(OpenRouter) for classification or extraction. Two passes, in order:

1. Known-contact pass — exact-match substitution of THIS specific contact's
   own name/phone/email, as already on file in our DB (ground truth).
2. Generic pass — regex redaction for fixed-format identifiers (EMAIL, PAN,
   IFSC, CARD, SSN, AADHAAR) plus any other phone pattern not already caught
   (e.g. a contact number that isn't on file at all). PHONE runs last: it is
   the loosest pattern and must never pre-empt a more specific match above.

Never send raw, unmasked text to OpenRouter — every call site must run its
payload through mask_for_classifier first.
"""

import re

# Same order + semantics as the JS source: EMAIL -> PAN -> IFSC -> CARD ->
# SSN -> AADHAAR -> PHONE (last, with a digit-count guard).
_EMAIL_RE = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
# Bank IFSC: 4 letters, literal 0, 6 alphanumeric (e.g. HDFC0001234).
_IFSC_RE = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b", re.IGNORECASE)
# 16-digit card number, optionally grouped in 4s with space/hyphen. Most
# specific of the digit patterns — must run before SSN/AADHAAR/PHONE.
_CARD_RE = re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")
# US Social Security Number: 3-2-4 digit groups, hyphen or space separated.
_SSN_RE = re.compile(r"\b\d{3}[ -]\d{2}[ -]\d{4}\b")
# 12-digit Aadhaar, optionally grouped 4-4-4.
_AADHAAR_RE = re.compile(r"\b(?:\d{4}[ -]?){2}\d{4}\b")
# Loose on purpose: an optional +country prefix, then 7-14 digits allowing
# spaces/dashes between them. Loosest pattern — runs last.
_PHONE_RE = re.compile(r"(\+?\d[\d\s-]{6,13}\d)")


def mask_known_contact_pii(text, names=None, phones=None, emails=None):
    """Exact-match substitution of one contact's own name/phone/email.

    `names`/`phones`/`emails` are plain lists (falsy entries ignored).
    Falsy `text` passes through unchanged (mirrors the JS `if (!text)`).
    """
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
            continue  # too short to safely match on
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
    """Adapt a Mongo client doc into the {names, phones, emails} shape.

    `contact` is a clients-collection doc (or None). Falsy fields dropped.
    The `email` field may be a single string or a list of strings.
    """
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
    """Single entry point every classifier/extraction call site should use."""
    if not text:
        return text
    known = collect_known_pii(contact)
    masked = mask_known_contact_pii(
        text, known.get("names"), known.get("phones"), known.get("emails")
    )
    return mask_generic_pii(masked)
