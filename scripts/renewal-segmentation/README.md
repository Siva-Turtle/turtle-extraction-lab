# Renewal segmentation — redacted-only Mongo reads

All renewal analysis in this folder reads **only PII-redacted data**.

## Files

- `pii_redaction.py` — small copy of `backend/app/core/scrub.py`
  (same regex order + semantics: EMAIL → PAN → IFSC → CARD → SSN →
  AADHAAR → PHONE last). Adds `redact_document()` / `redact_value()`
  for Mongo docs. If backend scrub changes, diff and port deliberately.
- `redacted_mongo.py` — read-only wrapper. Only
  `find_redacted` / `find_one_redacted` / `aggregate_redacted` /
  `distinct` / counts exist. Every returned doc is redacted first.
  Connection string is passed in by the caller — this folder never
  reads a `.env` file and never prints the string.
- `test_redaction_smoke.py` — synthetic-only checks (no live Mongo,
  no real PII). Run before any segmentation run.

## Rules

1. Import from `redacted_mongo`, never pymongo / `dbsafe` directly.
2. Never log or export unredacted fields. Direct identifiers
   (`fullName`, `phone`, `email`, `address`, ...) come back as
   `[CLIENT_NAME]` / `[PHONE]` / `[EMAIL]` / `[ADDRESS]` tokens.
3. Free-text (transcripts, notes, summaries) comes back scrubbed but
   analysable — safe for local clustering / LLM summarisation.
4. Analytical fields (`status`, `clientType`, dates, counts, advisor)
   pass through verbatim; they carry no PII.

## Quick check (no DB needed)

```powershell
cd scripts/renewal-segmentation
python ./test_redaction_smoke.py
```
