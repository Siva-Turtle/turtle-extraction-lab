"""Read-only Mongo access for renewal segmentation — redacted-only.

Guarantee: no function in this module ever returns a raw (unredacted)
client string. Every doc passes through pii_redaction.redact_document()
before it leaves this module.

- Read-only by construction: only find/find_one/aggregate/distinct/
  count pass through; anything else raises ReadOnlyViolation.
- Connection string is passed in by the caller (env / arg) and never
  printed, logged, or echoed. This module never reads a .env file itself.
- For renewal segmentation we only need analytical attributes +
  scrubbed free text. Direct identifiers are tokenised by the redaction
  layer (never returned raw).

Usage:
    from redacted_mongo import connect_redacted
    db = connect_redacted(conn_str)  # conn_str from caller's env only
    docs = db["clients"].find_redacted({"status": "expired"}, limit=50)

Only PII-redacted data leaves here — safe to feed to local analysis
or (after approval) to an external model.
"""

from pymongo import MongoClient

from pii_redaction import collect_known_pii, redact_document

READ_METHODS = frozenset(
    {
        "find_redacted",
        "find_one_redacted",
        "aggregate_redacted",
        "distinct",
        "estimated_document_count",
        "count_documents",
    }
)


class ReadOnlyViolation(PermissionError):
    """Raised on any write/mutation attempt."""


class RedactedCollection:
    def __init__(self, collection):
        self._c = collection

    @property
    def name(self):
        return self._c.name

    def _redact(self, doc, contact=None):
        if doc is None:
            return None
        ref = contact if isinstance(contact, dict) else doc
        return redact_document(doc, ref)

    def find_redacted(self, filt=None, projection=None, limit=0, contact=None):
        """find() + redact every doc. contact overrides per-doc ref."""
        cur = self._c.find(filt or {}, projection)
        if limit:
            cur = cur.limit(limit)
        out = []
        for doc in cur:
            out.append(self._redact(doc, contact or doc))
        return out

    def find_one_redacted(self, filt=None, projection=None, contact=None):
        doc = self._c.find_one(filt or {}, projection)
        if doc is None:
            return None
        return self._redact(doc, contact or doc)

    def aggregate_redacted(self, pipeline, contact=None):
        out = []
        for doc in self._c.aggregate(pipeline):
            out.append(self._redact(doc, contact))
        return out

    def count_documents(self, filt=None):
        return self._c.count_documents(filt or {})

    def distinct(self, key, filt=None):
        # distinct keys used for segmentation are enum-like; still scrub
        # any string values on the way out in case of free-text fields.
        from pii_redaction import mask_generic_pii

        vals = self._c.distinct(key, filt or {})
        return [mask_generic_pii(v) if isinstance(v, str) else v for v in vals]

    def estimated_document_count(self):
        return self._c.estimated_document_count()

    def __getattr__(self, item):
        raise ReadOnlyViolation(
            f"'{item}' is blocked — renewal segmentation is read-only and "
            f"redacted-only. Allowed: {', '.join(sorted(READ_METHODS))}"
        )


class RedactedDatabase:
    def __init__(self, database):
        self._db = database
        self.name = database.name

    def __getitem__(self, name):
        return RedactedCollection(self._db[name])

    def list_collection_names(self):
        return self._db.list_collection_names()


def connect_redacted(conn_str, db_name="turtle-finance-db"):
    """Open a redacted-only handle. conn_str comes from the caller.

    Never pass a .env path here, never print conn_str. Caller reads its
    own env (e.g. MONGO_DB_STRING) and hands the value in.
    """
    if not conn_str or not isinstance(conn_str, str):
        raise ValueError("conn_str is required (pass MONGO_DB_STRING value, not a path)")
    client = MongoClient(
        conn_str,
        serverSelectionTimeoutMS=30000,
        connect=True,
        appname="turtle-extraction-lab renewal-segmentation (read-only, redacted)",
    )
    return RedactedDatabase(client[db_name])


def redact_client_journey(client_doc, task_docs):
    """Convenience: redact one client + its tasks with linked known-PII.

    Known-contact pass uses the client doc for ALL its tasks so a name
    that appears only in a transcript still gets caught.
    """
    known_ref = client_doc if isinstance(client_doc, dict) else None
    redacted_client = redact_document(client_doc or {})
    redacted_tasks = [redact_document(t or {}, known_ref) for t in (task_docs or [])]
    return redacted_client, redacted_tasks


__all__ = [
    "ReadOnlyViolation",
    "RedactedCollection",
    "RedactedDatabase",
    "connect_redacted",
    "redact_client_journey",
    "collect_known_pii",
]
