import asyncio
from time import perf_counter
import copy
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.chunks import chunk_transcript, format_chunks_numbered
from app.core.openrouter import REASONING_EFFORTS, build_chat_payload, build_decisions_payload, complete_decisions_payload, complete_json, complete_json_payload, get_model_pricing, is_decision_model
from app.db.models import Agent, Attribute, Feedback, Run, RunLog, agent_attributes
from app.db.session import get_db
from app.modules.logs.router import (
    _agent_id_for_name,
    _agent_name_for_id,
    _merged_feedback,
    _out,
    _out_resolved,
    _resolve_original_log,
)
from app.modules.meetings.router import get_scrubbed_transcript
from app.modules.runs.schemas import (
    BatchFeedbackIn, CheckExistingAutoOut, CheckExistingIn, CheckExistingOut,
    FeedbackCreate, FeedbackOut, RetryAgentIn, ReuseRunIn, RunCreate, RunDetail, RunOut,
)

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])

RESULT_CONTRACT = (
    'Return a JSON object keyed by attribute name. Each value is an object with "value" '
    '(the extracted value), "confidence" (0-1), "confidence_type" (quoted|normalized|inferred|calculated|not_found), '
    '"evidence" (exact quote from the input). If an attribute is not found in the input, '
    'still return it with confidence_type "not_found", confidence 0, an empty evidence string '
    'and the emptiest value the schema allows (null when nullable, [] for lists, false for booleans). '
    'quoted = value stated word-for-word (evidence is the exact quote); '
    'inferred = value concluded from the input but not stated verbatim '
    '(evidence is the supporting passage); normalized = value standardized from a stated form '
    'such as phone digits, date formats, or casing (evidence is the original stated form).\n\n'
    '| Confidence Type | Meaning |\n'
    '|---|---|\n'
    '| `quoted` | Value is explicitly stated in the transcript |\n'
    '| `normalized` | Value is explicitly stated but transformed into your canonical representation |\n'
    '| `inferred` | Value was not directly stated; model derived it from evidence |\n'
    '| `not_found` | No sufficient evidence exists |\n'
    '| `calculated` | Mentioned as pieces of info, but model performed calculations to arrive |\n\n'
    'How to score confidence: confidence is your probability (0.0 to 1.0) that the extracted value is correct. '
    'It is NOT a flag for whether the value was stated verbatim. An inferred value with good supporting evidence must still get a high confidence. '
    'Use these ranges:\n'
    '- quoted: 0.90 to 1.00\n'
    '- normalized: 0.85 to 0.98\n'
    '- calculated: 0.70 to 0.95 (lower when the calculation needs assumptions)\n'
    '- inferred: 0.50 to 0.90 - several clear supporting statements 0.80 to 0.90; one clear statement 0.65 to 0.80; a weak or indirect hint 0.50 to 0.65\n'
    '- not_found: always exactly 0\n'
    'Never return confidence 0 for a value you extracted with evidence, and never return a confidence above 0 for not_found. '
    'For list attributes, score each item on its own using the same ranges.'
)


def _attrs_for_agent(db: Session, agent_id: str) -> list[Attribute]:
    return (
        db.query(Attribute)
        .join(agent_attributes, agent_attributes.c.attribute_id == Attribute.id)
        .filter(agent_attributes.c.agent_id == agent_id)
        .all()
    )


# --- Agent Identifier meta-agent (kind == "identifier") -------------------
# The identifier answers 11 fixed yes/no questions about the CLIENT's own
# situation, then deterministic routing rules (plan_auto_agents) select
# extraction agents. The prompt is code-owned: the identifier agent's stored
# system_instruction in the DB is IGNORED. Prompt is built at
# prompt/preview/run time, never stored per-run except inside the
# denormalized log snapshots/requests.

IDENTIFIER_KIND = "identifier"
EXTRACTION_KIND = "extraction"
IDENTIFIER_SCHEMA_NAME = "agent_selection"

IDENTIFIER_QUESTIONS: list[dict] = [
    {"key": "has_assets",
     "question": "Did the Client mention about their assets? (Any mention of asset type like - bonds, cash, commodity, ETFs, mutual funds, crypto, debt instruments, deposits, equity, personal debt that they have given to someone, real estate or property, REITS, unlisted stocks or any other asset type)"},
    {"key": "has_accounts",
     "question": "Did the Client mention about any of their accounts that they hold? (Any mention of accounts like - alternate investment fund accounts, bank account, bank deposit, crypto broker, EPF, investment account, NPS, PMS, PPF, Self-custody investment/crypto accounts or wallets, SSY (Sukanya Samriddhi Yojana) or any other account type)"},
    {"key": "credit_cards",
     "question": "Did the Client mention that they have or don't have credit card(s)?"},
    {"key": "employment_changed",
     "question": "Did the Client mention that their employment status or current employer has changed (they have switched company)?"},
    {"key": "alumni",
     "question": "Did the Client mention anything regarding their old organisation or institution - them being any sort of alumni?"},
    {"key": "expenses",
     "question": "Did the Client mention anything related to their current general monthly expenses, family expenses or annual expenses? (like charity donations, childcare school fees, entertainment dining, family support, groceries, household staff, insurance premiums, Loan EMIs, medical expenses, miscellaneous, rent, shopping purchases, subscriptions memberships, transport fuel, travel vacations, utilities)"},
    {"key": "goals",
     "question": "Did the Client mention any of their goals or aspirations (where money is linked)? (Like buying a house, buying a vehicle, children college or higher education, financial freedom fire, legacy inheritance, parents healthcare fund, planning a child, vacation travel fund, wedding, any other goals)"},
    {"key": "income",
     "question": "Did the Client mention anything related to their Income? (Like salary, business, capital gains, rentals, speculative income or any other income)"},
    {"key": "insurance",
     "question": "Did the Client mention anything related to their insurance?"},
    {"key": "liabilities",
     "question": "Did the Client mention anything related to their Liabilities? (Like credit card debt, education loan, gold loan, home loan, personal loan, vehicle loan or any other loan)"},
    {"key": "tax",
     "question": 'Did the Client mention anything related to "Advance Tax, Rental TDS, tax filing in India, tax filing outside India, GST services, W8 BEN"'},
]

IDENTIFIER_INSTRUCTION = (
    "You are the Agent Identifier for a financial advisory firm (Turtle Finance). "
    "The input is a conversation between the firm's advisors/team and a client, "
    "used for financial advisory purposes. "
    "Read the input and answer each question with true or false. "
    "Answer true only when the CLIENT's own situation is mentioned "
    "(explicitly or clearly implied). "
    "Return strictly the JSON object."
)


def is_identifier(agent: Agent) -> bool:
    """True when this agent is the router (kind == identifier)."""
    return (getattr(agent, "kind", None) or EXTRACTION_KIND) == IDENTIFIER_KIND


def build_identifier_schema() -> dict:
    """Strict structured-output schema for the identifier.

    One {value, evidence} object per question key in IDENTIFIER_QUESTIONS
    order, each with the question text as description. All required, no
    additional props. ``evidence`` is the 1-based transcript chunk number
    supporting a true answer (null when the answer is false).
    """
    props: dict = {}
    for q in IDENTIFIER_QUESTIONS:
        props[q["key"]] = {
            "type": "object",
            "description": q["question"],
            "properties": {
                "value": {"type": "boolean"},
                "evidence": {"type": ["integer", "null"]},
            },
            "required": ["value", "evidence"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": props,
        "required": [q["key"] for q in IDENTIFIER_QUESTIONS],
        "additionalProperties": False,
    }


IDENTIFIER_EVIDENCE_INSTRUCTION = (
    "For each question also give evidence: the 1-based number of the "
    "transcript chunk that best supports a true answer (null when the answer "
    "is false). Reply with ONLY the chunk number, never quoted text."
)


def _identifier_system_content(agent: Agent, _ignored=None) -> str:
    """System prompt for the identifier: code-owned instruction + 11 questions.

    The identifier agent's stored system_instruction is IGNORED. The
    optional second arg exists only for backward compatibility with old
    callers (candidate roster dropped).
    """
    lines: list[str] = []
    for i, q in enumerate(IDENTIFIER_QUESTIONS, start=1):
        lines.append(f"Q{i} ({q['key']}): {q['question']}")
    return f"{IDENTIFIER_INSTRUCTION}\n\n" + "\n".join(lines) + "\n\n" + IDENTIFIER_EVIDENCE_INSTRUCTION


def normalize_identifier_output(parsed, _candidates=None) -> object:
    """Normalise the identifier's parsed output to {key: bool} answers.

    - Returns {key: bool} for every question key; missing/non-bool values
      become False (accepts "true"/"false" strings case-insensitively).
    - Non-dict inputs (and {"_error": ...} payloads) are returned untouched.
    - The optional second arg is ignored (backward compat with the old
      candidate-roster signature).
    """
    if not isinstance(parsed, dict):
        return parsed
    if "_error" in parsed:
        return parsed
    out: dict[str, bool] = {}
    for q in IDENTIFIER_QUESTIONS:
        k = q["key"]
        v = parsed.get(k, False)
        # Chat {value, evidence} object form: the bool lives in .value.
        if isinstance(v, dict):
            try:
                v = v.get("value", False)
            except Exception:
                v = False
        if isinstance(v, bool):
            out[k] = v
        elif isinstance(v, str):
            s = v.strip().lower()
            if s == "true":
                out[k] = True
            elif s == "false":
                out[k] = False
            else:
                out[k] = False
        else:
            out[k] = False
    return out


IDENTIFIER_EVIDENCE_SUFFIX = "__evidence"


def build_identifier_decisions_questions(chunks=None) -> dict:
    """One noul question per IDENTIFIER_QUESTIONS entry (decisions API).

    Keyed by question key: ``{"type": "noul", "instructions": "Q{i} (key):
    question", "criteria": {"true": ..., "false": ...}}``. When ``chunks``
    (``[{"n": 1-based, "text": ...}]``) is given and non-empty, each base
    key also gets a ``{key}__evidence`` choice question whose criteria map
    chunk numbers (``{"1": "chunk 1", ...}``). Pure function.
    """
    questions: dict = {}
    for i, q in enumerate(IDENTIFIER_QUESTIONS, start=1):
        key = q["key"]
        questions[key] = {
            "type": "noul",
            "instructions": f"Q{i} ({key}): {q['question']}",
            "criteria": {
                "true": "The CLIENT's own situation includes this, explicitly stated or clearly implied.",
                "false": "Not mentioned for the client, or only someone else's situation.",
            },
        }
    try:
        n = len(list(chunks or []))
    except Exception:
        n = 0
    if n > 0:
        criteria = {str(i): f"chunk {i}" for i in range(1, n + 1)}
        for q in IDENTIFIER_QUESTIONS:
            key = q["key"]
            questions[f"{key}{IDENTIFIER_EVIDENCE_SUFFIX}"] = {
                "type": "choice",
                "instructions": f"Which transcript chunk (1..{n}) best supports a true answer to {key}? Reply with the chunk number.",
                "criteria": dict(criteria),
            }
    return questions


def _coerce_n_chunks(n_chunks) -> int:
    """Chunk count as a non-negative int (0 when unknown/unusable)."""
    try:
        if isinstance(n_chunks, bool):
            return 0
        if isinstance(n_chunks, (list, tuple)):
            return max(0, len(n_chunks))
        n = int(n_chunks)
    except Exception:
        return 0
    return n if n > 0 else 0


def split_identifier_result(parsed, transport="chat", n_chunks=0) -> tuple:
    """Split a raw identifier answer into (bools, evidence, probs).

    - decisions: ``parsed`` is the RAW answers dict. Bools come from
      ``decisions_answers_to_bools`` on the 11 base keys (``__evidence``
      keys are ignored there). ``evidence[key]`` is ``int(choice)`` from
      ``{key}__evidence`` (``type == "choice"`` with a string choice in
      1..N) when the bool is True, else None (missing/wrong-type /
      out-of-range -> None). The old ``score`` shape (numeric score,
      ``clamp(round(score), 0, N-1) + 1``) is still accepted as a
      fallback. ``probs[key]`` is the noul float when
      ``type == "noul"`` and numeric, else None.
    - chat: ``parsed`` is ``{key: bool}`` (legacy) or
      ``{key: {"value": bool, "evidence": int|null}}``. Evidence ints are
      kept only when inside 1..N, else None. Probs are all None.
    - Never raises; non-dict/``_error`` inputs pass through as
      ``(parsed, {}, {})`` and unknown shapes degrade to False/None.
    """
    n = _coerce_n_chunks(n_chunks)
    if not isinstance(parsed, dict):
        return (parsed, {}, {})
    if "_error" in parsed:
        return (parsed, {}, {})
    try:
        if transport == "decisions":
            bools = decisions_answers_to_bools(parsed)
            if not isinstance(bools, dict) or "_error" in bools:
                bools = {q["key"]: False for q in IDENTIFIER_QUESTIONS}
            evidence: dict = {}
            probs: dict = {}
            for q in IDENTIFIER_QUESTIONS:
                k = q["key"]
                try:
                    b = bools.get(k, False)
                except Exception:
                    b = False
                b = b is True
                ev = None
                if b and n >= 1:
                    try:
                        a = parsed.get(f"{k}{IDENTIFIER_EVIDENCE_SUFFIX}")
                        if isinstance(a, dict):
                            if a.get("type") == "choice":
                                c = a.get("choice")
                                if isinstance(c, str):
                                    c = c.strip()
                                if isinstance(c, bool):
                                    pass
                                elif isinstance(c, int):
                                    if 1 <= c <= n:
                                        ev = c
                                elif isinstance(c, str) and c != "":
                                    try:
                                        iv = int(c)
                                    except Exception:
                                        iv = None
                                    if iv is not None and 1 <= iv <= n:
                                        ev = iv
                            elif a.get("type") == "score":
                                s = a.get("score")
                                if isinstance(s, (int, float)) and not isinstance(s, bool):
                                    idx = round(float(s))
                                    idx = max(0, min(idx, n - 1))
                                    ev = idx + 1
                    except Exception:
                        ev = None
                evidence[k] = ev
                p = None
                try:
                    a = parsed.get(k)
                    if isinstance(a, dict) and a.get("type") == "noul":
                        v = a.get("noul")
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            p = float(v)
                except Exception:
                    p = None
                probs[k] = p
            return (bools, evidence, probs)
        bools = normalize_identifier_output(parsed)
        if not isinstance(bools, dict) or "_error" in bools:
            bools = {q["key"]: False for q in IDENTIFIER_QUESTIONS}
        evidence = {}
        for q in IDENTIFIER_QUESTIONS:
            k = q["key"]
            ev = None
            try:
                raw = parsed.get(k)
                if isinstance(raw, dict):
                    e = raw.get("evidence")
                    if isinstance(e, int) and not isinstance(e, bool) and 1 <= e <= n:
                        ev = e
            except Exception:
                ev = None
            evidence[k] = ev
        probs = {q["key"]: None for q in IDENTIFIER_QUESTIONS}
        return (bools, evidence, probs)
    except Exception:
        return ({q["key"]: False for q in IDENTIFIER_QUESTIONS},
                {q["key"]: None for q in IDENTIFIER_QUESTIONS},
                {q["key"]: None for q in IDENTIFIER_QUESTIONS})


def fireflies_url_from_task(task) -> str:
    """Denormalized Fireflies URL from a Mongo task doc ("" when absent).

    Only strings starting with http are kept; anything else (missing,
    non-string, relative) becomes "".
    """
    try:
        if not isinstance(task, dict):
            return ""
        url = task.get("transcriptUrl")
        if isinstance(url, str) and url.strip().lower().startswith("http"):
            return url.strip()
    except Exception:
        pass
    return ""


def decisions_answers_to_bools(answers) -> object:
    """Normalise a decisions answers dict to {key: bool} identifier answers.

    True when the answer for a question key is a dict with
    ``type == "noul"`` and a numeric ``noul >= 0.5``; missing keys and
    unexpected types are False (never crash, never default True).
    Non-dict inputs (and {"_error": ...} payloads) are returned untouched,
    the same convention as ``normalize_identifier_output`` — so bools pass
    through ``normalize_identifier_output`` unchanged downstream.
    """
    if not isinstance(answers, dict):
        return answers
    if "_error" in answers:
        return answers
    out: dict[str, bool] = {}
    for q in IDENTIFIER_QUESTIONS:
        k = q["key"]
        try:
            a = answers.get(k)
            if isinstance(a, dict) and a.get("type") == "noul":
                v = a.get("noul")
                if isinstance(v, bool):
                    out[k] = False
                elif isinstance(v, (int, float)) and v >= 0.5:
                    out[k] = True
                else:
                    out[k] = False
            else:
                out[k] = False
        except Exception:
            out[k] = False
    return out


def _all_false_answers() -> dict[str, bool]:
    return {q["key"]: False for q in IDENTIFIER_QUESTIONS}


def _answer_true(answers, key: str) -> bool:
    try:
        v = (answers or {}).get(key, False)
    except Exception:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return False


def _norm_group_name(value) -> str:
    try:
        return str(value or "").strip().lower()
    except Exception:
        return ""


def _group_of_attr(attr) -> str:
    try:
        g = getattr(attr, "group_name", "")
    except Exception:
        g = ""
    if isinstance(attr, dict):
        try:
            g = attr.get("group_name", attr.get("group", ""))
        except Exception:
            g = ""
    return _norm_group_name(g)


def plan_auto_agents(
    answers: dict,
    meeting_type: str,
    agents_by_name: dict,
    attrs_by_agent_name: dict,
) -> list[dict]:
    """Deterministic routing from identifier answers + meeting type.

    Returns ordered [{agent, attribute_ids (list[str]) or None (None = all),
    reasons: list[str], scored: bool}]. Skips agents that don't exist, are
    the identifier, have is_enabled False, or whose subset is empty.
    """
    answers = answers if isinstance(answers, dict) else {}
    mt = str(meeting_type or "")
    mt_low = mt.lower()
    is_kc = "karma conversation" in mt_low
    is_kickoff = ("kick-off" in mt_low) or ("kickoff" in mt_low) or ("kick off" in mt_low)
    agents_by_name = agents_by_name or {}
    attrs_by_agent_name = attrs_by_agent_name or {}

    def _get(name: str):
        try:
            ag = agents_by_name.get(name)
        except Exception:
            return None
        if ag is None:
            return None
        try:
            if is_identifier(ag):
                return None
        except Exception:
            pass
        try:
            if getattr(ag, "is_enabled", True) is False:
                return None
        except Exception:
            pass
        return ag

    def _attrs(name: str) -> list:
        try:
            lst = attrs_by_agent_name.get(name, [])
        except Exception:
            return []
        return list(lst or [])

    result: list[dict] = []

    def _push_all(name: str, reasons: list[str], scored: bool) -> None:
        ag = _get(name)
        if ag is None:
            return
        attrs = _attrs(name)
        if len(attrs) == 0:
            return
        result.append({"agent": ag, "attribute_ids": None,
                       "reasons": list(reasons), "scored": bool(scored)})

    def _push_subset(name: str, ids: list[str], reasons: list[str], scored: bool) -> None:
        ag = _get(name)
        if ag is None:
            return
        if not ids:
            return
        result.append({"agent": ag, "attribute_ids": list(ids),
                       "reasons": list(reasons), "scored": bool(scored)})

    # a. behavioral: always, all.
    _push_all("behavioral", ["always"], False)
    # b. query: always, all.
    _push_all("query", ["always"], False)
    # c. Feedback/KC: KC meeting -> combined full; else split feedback full
    # (fallback to combined subset when feedback is missing/disabled/empty).
    # The "kc" agent is never auto-selected (manual runs only).
    if is_kc:
        _kc_ag = _get("kc_and_feedback")
        if _kc_ag is not None:
            _kc_attrs = _attrs("kc_and_feedback")
            if len(_kc_attrs) > 0:
                result.append({"agent": _kc_ag, "attribute_ids": None,
                               "reasons": ["always", "meeting:karma_conversation"],
                               "scored": False})
    else:
        _pushed_fb = False
        _fb_ag = _get("feedback")
        if _fb_ag is not None:
            _fb_attrs = _attrs("feedback")
            if len(_fb_attrs) > 0:
                result.append({"agent": _fb_ag, "attribute_ids": None,
                               "reasons": ["always"], "scored": False})
                _pushed_fb = True
        if not _pushed_fb:
            _kc_ag = _get("kc_and_feedback")
            if _kc_ag is not None:
                _kc_attrs = _attrs("kc_and_feedback")
                if len(_kc_attrs) > 0:
                    sub = [a.id for a in _kc_attrs
                           if _group_of_attr(a) != "karma conversation"]
                    if sub:
                        result.append({"agent": _kc_ag, "attribute_ids": sub,
                                       "reasons": ["always"], "scored": False})
    # d. basic_info.
    _bi_ag = _get("basic_info")
    if _bi_ag is not None:
        _bi_attrs = _attrs("basic_info")
        if len(_bi_attrs) > 0:
            if is_kc or is_kickoff:
                reasons: list[str] = []
                if is_kc:
                    reasons.append("meeting:karma_conversation")
                if is_kickoff:
                    reasons.append("meeting:kick_off")
                result.append({"agent": _bi_ag, "attribute_ids": None,
                               "reasons": reasons, "scored": False})
            else:
                need_cc = _answer_true(answers, "credit_cards")
                need_emp = _answer_true(answers, "employment_changed")
                need_al = _answer_true(answers, "alumni")
                if need_cc or need_emp or need_al:
                    allowed: set[str] = set()
                    if need_cc:
                        allowed.add("banking")
                    if need_emp:
                        allowed.add("employment")
                    if need_al:
                        allowed.add("education / alumni")
                    sub_ids = [a.id for a in _bi_attrs if _group_of_attr(a) in allowed]
                    if sub_ids:
                        rs: list[str] = []
                        if need_cc:
                            rs.append("credit_cards")
                        if need_emp:
                            rs.append("employment_changed")
                        if need_al:
                            rs.append("alumni")
                        result.append({"agent": _bi_ag, "attribute_ids": sub_ids,
                                       "reasons": rs, "scored": True})
    # e-j. single-question full agents.
    _single_map = [
        ("has_assets", "asset"),
        ("has_accounts", "account"),
        ("expenses", "expense"),
        ("goals", "goal"),
        ("income", "income"),
        ("liabilities", "liability"),
    ]
    for qkey, aname in _single_map:
        if _answer_true(answers, qkey):
            _push_all(aname, [qkey], True)
    # k. Tax/Insurance: combined only when both halves are needed,
    # otherwise only the split agent (fewer tokens).
    need_ins = _answer_true(answers, "insurance")
    need_tax = _answer_true(answers, "tax")
    if need_ins or need_tax:
        if need_ins and need_tax:
            _ti_ag = _get("tax_and_insurance")
            _ti_attrs = _attrs("tax_and_insurance") if _ti_ag is not None else []
            if _ti_ag is not None and len(_ti_attrs) > 0:
                result.append({"agent": _ti_ag, "attribute_ids": None,
                               "reasons": ["insurance", "tax"], "scored": True})
            else:
                # Combined missing/disabled/empty: run both splits.
                _push_all("insurance", ["insurance", "tax"], True)
                _push_all("tax", ["insurance", "tax"], True)
        elif need_ins:
            _ins_ag = _get("insurance")
            _ins_attrs = _attrs("insurance") if _ins_ag is not None else []
            if _ins_ag is not None and len(_ins_attrs) > 0:
                result.append({"agent": _ins_ag, "attribute_ids": None,
                               "reasons": ["insurance"], "scored": True})
            else:
                _ti_ag = _get("tax_and_insurance")
                if _ti_ag is not None:
                    _ti_attrs = _attrs("tax_and_insurance")
                    if len(_ti_attrs) > 0:
                        sub_ins = [a.id for a in _ti_attrs
                                   if _group_of_attr(a) == "insurance"]
                        if sub_ins:
                            result.append({"agent": _ti_ag, "attribute_ids": sub_ins,
                                           "reasons": ["insurance"], "scored": True})
        elif need_tax:
            _tax_ag = _get("tax")
            _tax_attrs = _attrs("tax") if _tax_ag is not None else []
            if _tax_ag is not None and len(_tax_attrs) > 0:
                result.append({"agent": _tax_ag, "attribute_ids": None,
                               "reasons": ["tax"], "scored": True})
            else:
                _ti_ag = _get("tax_and_insurance")
                if _ti_ag is not None:
                    _ti_attrs = _attrs("tax_and_insurance")
                    if len(_ti_attrs) > 0:
                        sub_tax = [a.id for a in _ti_attrs
                                   if _group_of_attr(a) in ("tax", "tax / compliance")]
                        if sub_tax:
                            result.append({"agent": _ti_ag, "attribute_ids": sub_tax,
                                           "reasons": ["tax"], "scored": True})
    return result


AUTO_REMARK_MISSED = "Auto: identifier listed it, agent returned null"
AUTO_REMARK_UNEXPECTED = "Auto: extracted but identifier did not list it"


def _agent_display_name(agent) -> str:
    if isinstance(agent, str):
        return agent
    if isinstance(agent, dict):
        n = agent.get("name", "")
        return n if isinstance(n, str) else ""
    return str(getattr(agent, "name", "") or "")


def _agent_kind_of(agent) -> str:
    if isinstance(agent, dict):
        k = agent.get("kind", "")
        return k if isinstance(k, str) else ""
    return str(getattr(agent, "kind", "") or "")


def _valid_attr_names(value) -> list[str]:
    out: list[str] = []
    if value is None:
        return out
    items = value if isinstance(value, (list, tuple)) else []
    for item in items:
        if isinstance(item, str):
            if item:
                out.append(item)
        elif isinstance(item, dict):
            n = item.get("name", "")
            if isinstance(n, str) and n:
                out.append(n)
        else:
            n = getattr(item, "name", None)
            if isinstance(n, str) and n:
                out.append(n)
    return out


def _is_filled_entry(entry) -> bool:
    # Unwrapped list attributes return the raw JSON array (no {value,...}
    # wrapper): filled when it is a non-empty list. Wrapped attributes
    # use the {value, confidence_type} contract as before.
    if isinstance(entry, (list, tuple)):
        return len(entry) > 0
    if isinstance(entry, dict) and "value" in entry:
        if entry.get("confidence_type") == "not_found":
            return False
        v = entry.get("value")
    elif isinstance(entry, dict) and "confidence_type" in entry:
        if entry.get("confidence_type") == "not_found":
            return False
        v = entry.get("value", entry)
    else:
        v = entry
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, tuple)):
        return len(v) > 0
    if isinstance(v, dict):
        return len(v) > 0
    return True


def compute_consistency(answers, outputs, identifier_agent_id="", plan=None) -> dict:
    """Pure consistency v2 snapshot for an auto-select run.

    - answers: normalized {key: bool} (11 questions).
    - outputs: {agent_id: output dict}.
    - identifier_agent_id: str.
    - plan: [{agent_id, agent_name, reasons, scored, attributes: [names]}].

    Per agent: hit (score 1.0) if >=1 filled attr, miss (0.0) if none;
    errored outputs -> "error" excluded from score; unscored -> "not_scored".
    Overall = hits / (hits + misses), None when no scored agents.
    Old positional callers (identifier_output, outputs, agents_by_id,
    attrs_by_agent) are detected and handled defensively: if ``plan`` looks
    like an old attrs map, fall back to a minimal v2 with empty plan.
    """
    # Backward-compat shim: old call shape
    # compute_consistency(identifier_output, outputs, agents_by_id, attrs_by_agent)
    # where 3rd arg is a dict and 4th is a dict/None. Detect and degrade.
    try:
        if isinstance(identifier_agent_id, dict) or isinstance(plan, dict):
            # Old shape: return empty v2 (callers are being migrated; tests updated).
            _old_outputs = outputs if isinstance(outputs, dict) else {}
            return {"auto": True, "version": 2, "score": None,
                    "identifier_agent_id": "",
                    "answers": _all_false_answers(),
                    "plan": [],
                    "agents": {}}
    except Exception:
        pass
    try:
        answers_norm = normalize_identifier_output(
            answers if isinstance(answers, dict) else {})
        if not isinstance(answers_norm, dict) or "_error" in answers_norm:
            answers_norm = _all_false_answers()
    except Exception:
        answers_norm = _all_false_answers()
    try:
        outputs_d = outputs if isinstance(outputs, dict) else {}
    except Exception:
        outputs_d = {}
    try:
        iid = str(identifier_agent_id or "")
    except Exception:
        iid = ""
    try:
        plan_list = list(plan or [])
    except Exception:
        plan_list = []
    # Deep-copy plan for storage safety.
    plan_copy: list[dict] = []
    for entry in plan_list:
        if not isinstance(entry, dict):
            continue
        try:
            aid = str(entry.get("agent_id", "") or "")
        except Exception:
            continue
        if not aid:
            continue
        aname = entry.get("agent_name", "")
        if not isinstance(aname, str):
            aname = ""
        reasons = entry.get("reasons", [])
        if not isinstance(reasons, list):
            reasons = []
        reasons = [r for r in reasons if isinstance(r, str) and r]
        try:
            scored = bool(entry.get("scored", False))
        except Exception:
            scored = False
        attrs = entry.get("attributes", [])
        if not isinstance(attrs, list):
            attrs = []
        attrs = [a for a in attrs if isinstance(a, str) and a]
        plan_copy.append({"agent_id": aid, "agent_name": aname,
                          "reasons": reasons, "scored": scored,
                          "attributes": attrs})
    agents_out: dict = {}
    hits = 0
    misses = 0
    for entry in plan_copy:
        aid = entry["agent_id"]
        aname = entry["agent_name"]
        reasons = entry["reasons"]
        scored = entry["scored"]
        out = outputs_d.get(aid)
        if isinstance(out, dict) and "_error" in out:
            agents_out[aid] = {"agent_name": aname, "reasons": reasons,
                               "scored": scored, "extracted": [],
                               "status": "error", "score": None}
            continue
        if not isinstance(out, dict):
            agents_out[aid] = {"agent_name": aname, "reasons": reasons,
                               "scored": scored, "extracted": [],
                               "status": "error", "score": None}
            continue
        extracted: list[str] = []
        try:
            for attr_name, ent in out.items():
                if not isinstance(attr_name, str) or not attr_name:
                    continue
                if attr_name.startswith("_"):
                    continue
                if _is_filled_entry(ent):
                    extracted.append(attr_name)
        except Exception:
            extracted = []
        if not scored:
            agents_out[aid] = {"agent_name": aname, "reasons": reasons,
                               "scored": False, "extracted": extracted,
                               "status": "not_scored", "score": None}
        else:
            if len(extracted) >= 1:
                hits += 1
                agents_out[aid] = {"agent_name": aname, "reasons": reasons,
                                   "scored": True, "extracted": extracted,
                                   "status": "hit", "score": 1.0}
            else:
                misses += 1
                agents_out[aid] = {"agent_name": aname, "reasons": reasons,
                                   "scored": True, "extracted": [],
                                   "status": "miss", "score": 0.0}
    denom = hits + misses
    overall = None if denom == 0 else (hits / denom)
    try:
        overall_f = None if overall is None else float(overall)
    except Exception:
        overall_f = None
    return {"auto": True, "version": 2, "score": overall_f,
            "identifier_agent_id": iid,
            "answers": dict(answers_norm),
            "plan": plan_copy,
            "agents": agents_out}


def apply_auto_feedback(feedback, consistency) -> dict:
    """Return a copy of feedback with v2 auto thumbs-down on missed agents.

    For each miss, feedback[agent_name]["__agent__"] = {rating down,
    remarks "Auto: identifier said <reasons> but agent extracted nothing",
    auto True}. Removes previous auto entries for that agent first; never
    overwrites manual entries. No attribute-level autos. Old consistency
    shapes (no version==2) return a copy unchanged (never crash).
    """
    fb: dict = {}
    try:
        src = feedback if isinstance(feedback, dict) else {}
        for k, v in src.items():
            if isinstance(v, dict):
                fb[k] = {ak: dict(av) if isinstance(av, dict) else {} for ak, av in v.items()}
            else:
                fb[k] = {}
    except Exception:
        fb = {}
    try:
        cons_d = consistency if isinstance(consistency, dict) else {}
    except Exception:
        return fb
    try:
        if cons_d.get("version") != 2:
            return fb
    except Exception:
        return fb
    agents = {}
    try:
        agents = cons_d.get("agents", {}) or {}
    except Exception:
        agents = {}
    if not isinstance(agents, dict):
        return fb
    for aid, cons in agents.items():
        if not isinstance(cons, dict):
            continue
        agent_name = cons.get("agent_name", "")
        if not isinstance(agent_name, str) or not agent_name:
            continue
        # Remove previous auto entries for this agent first.
        agent_map = fb.get(agent_name)
        if not isinstance(agent_map, dict):
            agent_map = {}
            fb[agent_name] = agent_map
        for attr in list(agent_map.keys()):
            try:
                cell = agent_map.get(attr)
                if isinstance(cell, dict) and cell.get("auto") is True:
                    del agent_map[attr]
            except Exception:
                continue
        status = cons.get("status", "")
        if status != "miss":
            if not agent_map and agent_name in fb:
                try:
                    del fb[agent_name]
                except Exception:
                    pass
            continue
        reasons = cons.get("reasons", [])
        if not isinstance(reasons, list):
            reasons = []
        reasons = [r for r in reasons if isinstance(r, str) and r]
        remark = f"Auto: identifier said {', '.join(reasons)} but agent extracted nothing"
        existing = agent_map.get("__agent__")
        if isinstance(existing, dict) and existing.get("auto") is not True:
            if existing.get("rating") in ("up", "down"):
                if not agent_map and agent_name in fb:
                    # Keep manual-only map.
                    pass
                continue
            if "rating" in existing and existing.get("rating"):
                continue
        agent_map["__agent__"] = {"rating": "down", "remarks": remark, "auto": True}
        if not agent_map and agent_name in fb:
            try:
                del fb[agent_name]
            except Exception:
                pass
    return fb


def _is_unwrapped(a) -> bool:
    """True when this attribute returns its raw value (no wrapper).

    Backward compatible: missing/None wrap_result means wrapped (True).
    Dict snapshots (logs) are also accepted.
    """
    try:
        if isinstance(a, dict):
            v = a.get("wrap_result", True)
        else:
            v = getattr(a, "wrap_result", True)
    except Exception:
        return False
    if v is None:
        return False
    return bool(v) is False


def _attr_line(a: Attribute) -> str:
    base = f"- {a.name} ({a.type}): {a.description}"
    if a.type == "enum" and (a.enum_values or []):
        base += f" [{' | '.join(a.enum_values)}]"
    if _is_unwrapped(a):
        base += " (return the list directly - no value/confidence wrapper; confidence, confidence_type and evidence are per item)"
    return base


def _agent_system_content(agent: Agent, attrs: list[Attribute]) -> str:
    base = agent.system_instruction or "Extract structured data."
    attr_lines = "\n".join(_attr_line(a) for a in attrs) or "- (no attributes defined)"
    return f"{base}\n\nAttributes to extract:\n{attr_lines}\n\n{RESULT_CONTRACT}"


def _object_properties(a: Attribute) -> list[dict]:
    """Ordered sub-fields for type=="object" as plain dicts (DB stores JSON)."""
    raw = getattr(a, "object_properties", None) or []
    props: list[dict] = []
    for p in raw:
        if isinstance(p, dict):
            props.append(p)
        else:
            props.append({"name": getattr(p, "name", ""),
                          "type": getattr(p, "type", "string"),
                          "null_allowed": getattr(p, "null_allowed", True),
                          "enum": list(getattr(p, "enum", []) or []),
                          "description": getattr(p, "description", "") or ""})
    return props


def _sub_schema(sub_type: str, null_allowed: bool,
                enum: list[str] | None = None,
                description: str | None = None) -> dict:
    """Map one object sub-field to its JSON-schema fragment (items always string).

    Nullable enums use anyOf (Anthropic rejects {"type": ["string", "null"],
    "enum": [...]}): {"anyOf": [{"type": <base>, "enum": [...]},
    {"type": "null"}], ...}. Non-nullable enums stay
    {"type": <base>, "enum": [...]}. Non-enum fields unchanged.
    """
    if enum:
        enum_vals = list(enum)
        if sub_type == "array":
            base = "array"
        elif sub_type == "number":
            base = "number"
        elif sub_type == "integer":
            base = "integer"
        elif sub_type == "boolean":
            base = "boolean"
        else:
            base = "string"
        if null_allowed:
            out: dict = {"anyOf": [{"type": base, "enum": enum_vals},
                                   {"type": "null"}]}
            if sub_type == "array":
                out["items"] = {"type": "string"}
            if description:
                out["description"] = description
            return out
        out = {"type": base, "enum": enum_vals}
        if sub_type == "array":
            out["items"] = {"type": "string"}
        if description:
            out["description"] = description
        return out
    if sub_type == "array":
        out = {"type": (["array", "null"] if null_allowed else "array"),
               "items": {"type": "string"}}
    elif sub_type == "number":
        out = {"type": (["number", "null"] if null_allowed else "number")}
    elif sub_type == "integer":
        out = {"type": (["integer", "null"] if null_allowed else "integer")}
    elif sub_type == "boolean":
        out = {"type": (["boolean", "null"] if null_allowed else "boolean")}
    else:
        out = {"type": (["string", "null"] if null_allowed else "string")}
    if description:
        out["description"] = description
    return out


def _snapshot_props(a: Attribute) -> list[dict]:
    out: list[dict] = []
    for p in _object_properties(a):
        enum_vals = p.get("enum", []) or []
        if not isinstance(enum_vals, list):
            enum_vals = []
        desc = p.get("description", "") or ""
        if not isinstance(desc, str):
            desc = ""
        out.append({"name": str(p.get("name", "")),
                    "type": p.get("type", "string"),
                    "null_allowed": bool(p.get("null_allowed", True)),
                    "enum": list(enum_vals),
                    "description": desc})
    return out


def _array_items_cfg(a: Attribute) -> dict:
    """Normalized {kind, properties} for type=="array" (defaults to string)."""
    raw = getattr(a, "array_items", None) or {}
    if not isinstance(raw, dict):
        return {"kind": "string", "properties": []}
    kind = str(raw.get("kind", "string") or "string").strip().lower()
    if kind not in ("string", "number", "object"):
        kind = "string"
    if kind != "object":
        return {"kind": kind, "properties": []}
    props: list[dict] = []
    for p in (raw.get("properties", []) or []):
        if not isinstance(p, dict):
            continue
        enum_vals = p.get("enum", []) or []
        if not isinstance(enum_vals, list):
            enum_vals = []
        desc = p.get("description", "") or ""
        if not isinstance(desc, str):
            desc = ""
        props.append({"name": str(p.get("name", "")),
                      "type": p.get("type", "string"),
                      "null_allowed": bool(p.get("null_allowed", True)),
                      "enum": list(enum_vals),
                      "description": desc})
    return {"kind": kind, "properties": props}


def _snapshot_array_items(a: Attribute) -> dict:
    return _array_items_cfg(a)


def _array_item_schema(a: Attribute) -> dict:
    """Map array item-shape config to its JSON-schema items fragment."""
    cfg = _array_items_cfg(a)
    kind = cfg.get("kind", "string")
    if kind == "number":
        return {"type": "number"}
    if kind == "object":
        sub_props: dict = {}
        sub_required: list[str] = []
        for p in cfg.get("properties", []):
            sub_props[str(p.get("name", ""))] = _sub_schema(
                str(p.get("type", "string")), bool(p.get("null_allowed", True)),
                p.get("enum", []) or None,
                p.get("description", "") or None)
            sub_required.append(str(p.get("name", "")))
        return {"type": "object", "properties": sub_props,
                "required": sub_required, "additionalProperties": False}
    return {"type": "string"}


def _value_schema_for_attribute(a: Attribute) -> dict:
    """Map attribute type to the OpenAI structured-output value field.

    - string -> {"type": ["string", "null"], "description": ...}
    - number -> {"type": ["number", "null"], ...}
    - boolean -> {"type": ["boolean", "null"], ...}
    - enum (always nullable) -> {"anyOf": [{"type": "string",
      "enum": [...]}, {"type": "null"}], "description": ...}
      (Anthropic rejects {"type": ["string", "null"], "enum": [...]},
      so nullable enums use anyOf without null inside the enum list)
    - array -> {"type": ["array", "null"], "items": <shape>, ...} where
      string->{"type":"string"}, number->{"type":"number"},
      object->{"type":"object","properties":{...},"required":[...all...],
      "additionalProperties":false}
    - object -> {"type": ["object", "null"], "properties": {sub-name: sub-schema},
      "required": [all sub names], "additionalProperties": False, ...}
    Nullable enum sub-fields (inside array items / object properties) use
    the same anyOf pattern via _sub_schema; non-nullable enum sub-fields
    stay {"type": "string", "enum": [...]}. Non-enum fields unchanged.
    """
    desc = f"Extracted value for {a.name}"
    if a.type == "enum" and (a.enum_values or []):
        return {"anyOf": [{"type": "string", "enum": list(a.enum_values)},
                           {"type": "null"}],
                "description": desc}
    if a.type == "number":
        value_schema: dict = {"type": ["number", "null"], "description": desc}
    elif a.type == "boolean":
        value_schema = {"type": ["boolean", "null"], "description": desc}
    elif a.type == "array":
        value_schema = {"type": ["array", "null"],
                        "items": _array_item_schema(a), "description": desc}
    elif a.type == "object":
        sub_props: dict = {}
        sub_required: list[str] = []
        for p in _object_properties(a):
            sub_props[str(p.get("name", ""))] = _sub_schema(
                str(p.get("type", "string")), bool(p.get("null_allowed", True)),
                p.get("enum", []) or None,
                p.get("description", "") or None)
            sub_required.append(str(p.get("name", "")))
        value_schema = {"type": ["object", "null"], "properties": sub_props,
                        "required": sub_required,
                        "additionalProperties": False, "description": desc}
    else:
        # string (and unknown types fall back to string); empty-enum edge
        # also lands here as a plain nullable string (no enum key).
        value_schema = {"type": ["string", "null"], "description": desc}
    return value_schema


WRAPPER_CONFIDENCE_TYPES = ["quoted", "inferred", "normalized", "calculated", "not_found"]


def build_extraction_schema(attrs: list[Attribute]) -> dict | None:
    """OpenAI-compatible structured-output object schema for this run's attributes.

    Wrapped attributes become ``{"value", "confidence", "confidence_type",
    "evidence"}`` with all four required. Unwrapped attributes
    (wrap_result False, the 7 v2 list attributes) use the value schema
    directly (array of strict item objects, no wrapper). Top-level
    ``required`` lists every attribute name. No $refs. Returns None when
    there are no attributes (caller falls back to json_object mode).
    """
    if not attrs:
        return None
    properties: dict = {}
    required: list[str] = []
    for a in attrs:
        if _is_unwrapped(a):
            properties[a.name] = _value_schema_for_attribute(a)
            # Keep the full attribute description on the raw schema so the
            # model still sees the self-contained definition.
            try:
                if getattr(a, "description", None):
                    properties[a.name]["description"] = a.description or a.name
            except Exception:
                pass
        else:
            properties[a.name] = {
                "type": "object",
                "description": a.description or a.name,
                "properties": {
                    "value": _value_schema_for_attribute(a),
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "confidence_type": {"type": "string",
                                        "enum": list(WRAPPER_CONFIDENCE_TYPES)},
                    "evidence": {"type": ["string", "null"]},
                },
                "required": ["value", "confidence", "confidence_type", "evidence"],
                "additionalProperties": False,
            }
        required.append(a.name)
    return {"type": "object", "properties": properties,
            "required": required, "additionalProperties": False}


def _resolve_run_input(
    *, meeting_id: str, input_type: str, input_data: str, reasoning_effort: str,
) -> tuple[str, str, str, str, str]:
    """Validate effort + resolve input exactly like create_run.

    Returns (input_type, input_data, effort, task_title, fireflies_url).
    fireflies_url is the Mongo task's transcriptUrl ("" for non-meeting
    runs or when absent). Raises the same HTTPException create_run would
    (422 for bad effort/blank input/bad type, 404/503 from
    get_scrubbed_transcript). No OpenRouter, no writes.
    """
    effort = (reasoning_effort or "").strip()
    if effort and effort not in REASONING_EFFORTS:
        raise HTTPException(422, "reasoning_effort must be max|xhigh|high|medium|low|minimal|none")
    mid = (meeting_id or "").strip()
    task_title = ""
    fireflies_url = ""
    if mid:
        # meeting_id wins: server re-fetches the transcript and scrubs it —
        # client-sent input_data/input_type are ignored entirely.
        _task, scrubbed = get_scrubbed_transcript(mid)  # 404/503 propagate
        resolved_type, resolved_data = "transcription", scrubbed
        try:
            task_title = str((_task.get("title") or "")).strip()
        except Exception:
            task_title = ""
        fireflies_url = fireflies_url_from_task(_task)
    else:
        if not (input_data or "").strip():
            raise HTTPException(422, "input_data must be non-blank or provide meeting_id")
        if input_type not in ("transcription", "messages", "mail"):
            raise HTTPException(422, "input_type must be transcription|messages|mail")
        resolved_type, resolved_data = input_type, input_data
    return resolved_type, resolved_data, effort, task_title, fireflies_url


def _load_run_agents(db: Session, agent_ids: list[str]) -> list[Agent]:
    """Load agents exactly like create_run (404 when ids match nothing).

    Order follows the input ``agent_ids`` (deduped) so outputs/requests/
    per_agent insertion order is deterministic and matches the caller's
    agent order, whether runs execute sequentially or via asyncio.gather.
    """
    rows = db.query(Agent).filter(Agent.id.in_(agent_ids)).all() if agent_ids else []
    if agent_ids and not rows:
        raise HTTPException(404, "no matching agents")
    by_id = {a.id: a for a in rows}
    ordered: list[Agent] = []
    seen: set[str] = set()
    for aid in agent_ids or []:
        if aid in by_id and aid not in seen:
            ordered.append(by_id[aid])
            seen.add(aid)
    return ordered if ordered else rows


def _build_agent_requests(
    db: Session, *, agents: list[Agent], input_data: str,
    model: str, reasoning_effort: str,
    attr_subsets: dict | None = None,
    provider: str = "",
    chunks: list[dict] | None = None,
) -> tuple[dict, dict]:
    """Build (snapshots, requests) exactly like create_run's loop prelude.

    Pure DB reads + prompt building. No OpenRouter call, no writes.
    requests[agent.id] is the exact payload_body create_run would POST.

    attr_subsets: optional {agent_id: list[attribute_id] | None} (None = all).
    When a subset is given, the snapshot, system prompt and schema include
    only those attributes (in DB order). Default None keeps create_run and
    every other caller byte-identical for extraction agents.

    chunks: precomputed ``chunk_transcript(input_data)`` (chunked ONCE per
    run by the caller and shared across agents/models). When None, chunked
    here deterministically (same input -> identical bodies). The identifier
    ONLY sees the numbered chunks — chat user content becomes
    ``format_chunks_numbered(chunks)`` and decisions state becomes
    ``{"chunks": [...]}`` — while extraction agents keep the raw input
    byte-identical.
    """
    effort = (reasoning_effort or "").strip()
    prov = (provider or "").strip() if isinstance(provider, str) else ""
    try:
        shared_chunks = list(chunks) if chunks is not None else chunk_transcript(input_data)
    except Exception:
        shared_chunks = []
    if not isinstance(shared_chunks, list):
        shared_chunks = []
    snapshots: dict = {}
    requests: dict = {}
    for agent in agents:
        all_attrs = _attrs_for_agent(db, agent.id)
        attrs = all_attrs
        try:
            if isinstance(attr_subsets, dict) and agent.id in attr_subsets:
                sub = attr_subsets.get(agent.id)
                if sub is None:
                    attrs = all_attrs
                elif isinstance(sub, (list, tuple)):
                    wanted = set(sub)
                    attrs = [a for a in all_attrs if a.id in wanted]
                else:
                    attrs = all_attrs
        except Exception:
            attrs = all_attrs
        snapshots[agent.id] = {
            "name": agent.name, "description": agent.description or "",
            "kind": getattr(agent, "kind", None) or EXTRACTION_KIND,
            "system_instruction": agent.system_instruction,
            "attributes": [{"name": a.name, "type": a.type, "description": a.description,
                            "group": getattr(a, "group_name", "") or "",
                            "enum_values": a.enum_values or [],
                            "object_properties": _snapshot_props(a),
                            "array_items": _snapshot_array_items(a),
                            "wrap_result": (False if _is_unwrapped(a) else True)} for a in attrs],
        }
        user_content = input_data
        transport = ""
        if is_identifier(agent):
            if is_decision_model(model):
                # Decisions-model path: noul questions over the SAME numbered
                # chunks the chat path sees, plus one __evidence choice
                # question per identifier question. Reasoning effort is never
                # sent here (it stays in the log column only). The stored
                # body carries a "transport" marker that is stripped before
                # POSTing.
                payload_body = build_decisions_payload(
                    model=model, state={"chunks": [dict(c) for c in shared_chunks
                                                  if isinstance(c, dict)]},
                    questions=build_identifier_decisions_questions(shared_chunks),
                    provider=(prov if prov else None))
                transport = "decisions"
            else:
                # Router path: code-owned 11-question prompt; strict
                # {value, evidence} envelope over the numbered chunks.
                system_content = _identifier_system_content(agent)
                schema: dict | None = build_identifier_schema()
                payload_body = build_chat_payload(
                    model=model, system=system_content,
                    user=format_chunks_numbered(shared_chunks),
                    json_schema=schema, schema_name=IDENTIFIER_SCHEMA_NAME,
                    reasoning_effort=effort, provider=prov,
                    max_tokens=2048)
        else:
            system_content = _agent_system_content(agent, attrs)
            schema = build_extraction_schema(attrs)
            payload_body = build_chat_payload(
                model=model, system=system_content, user=user_content, json_schema=schema,
                reasoning_effort=effort, provider=prov)
        stored_body = copy.deepcopy(payload_body)
        if transport:
            stored_body["transport"] = transport
        requests[agent.id] = stored_body
    return snapshots, requests


def _plan_run(
    db: Session, *, meeting_id: str, input_type: str, input_data: str,
    agent_ids: list[str], model: str, reasoning_effort: str,
    provider: str = "",
) -> tuple[str, str, str, str, list[Agent], dict, dict]:
    """Resolve input + agents + per-agent request bodies without side effects.

    Returns (input_type, input_data, effort, task_title, agents,
    snapshots, requests, chunks, fireflies_url). The transcript is chunked
    ONCE here from the resolved (scrubbed) input and the same chunks are
    shared across all agents/models in this run. Raises the same
    HTTPException create_run would.
    """
    resolved_type, resolved_data, effort, task_title, fireflies_url = _resolve_run_input(
        meeting_id=meeting_id, input_type=input_type,
        input_data=input_data, reasoning_effort=reasoning_effort)
    try:
        chunks = chunk_transcript(resolved_data)
    except Exception:
        chunks = []
    agents = _load_run_agents(db, agent_ids)
    snapshots, requests = _build_agent_requests(
        db, agents=agents, input_data=resolved_data,
        model=model, reasoning_effort=effort, provider=provider,
        chunks=chunks)
    return (resolved_type, resolved_data, effort, task_title, agents,
            snapshots, requests, chunks, fireflies_url)


async def _call_single_payload(payload_body: dict):
    """One OpenRouter call + timing (shared by create_run/retry/auto).

    Returns (parsed, prompt_tokens, completion_tokens, total_tokens,
    reasoning_tokens, duration_ms, served_provider, actual_cost,
    cost_details, served_model). Errors become ({"_error": ...}, zeros, "",
    None, None, ""). ``actual_cost`` is usage.cost (None when unknown);
    preferred over tokens x catalog price for dynamic routers.
    ``served_model`` is the top-level response model ("" when absent).
    Decisions bodies (``transport == "decisions"``) go to
    ``complete_decisions_payload`` (marker stripped before POST) and return
    the same 10-tuple shape with ``reasoning_tokens = 0`` and
    ``cost_details = None``.
    """
    if isinstance(payload_body, dict) and payload_body.get("transport") == "decisions":
        decide_start = perf_counter()
        try:
            clean_body = {k: v for k, v in payload_body.items() if k != "transport"}
            answers, usage = await complete_decisions_payload(clean_body)
            # RAW answers dict: every identifier consumer
            # (_stream_fresh_results._one, auto_run, retry_agent, create
            # path) calls split_identifier_result then
            # normalize_identifier_output(bools) itself.
            parsed = answers if isinstance(answers, dict) else {}
            try:
                prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
                completion_tokens = int(usage.get("completion_tokens", 0) or 0)
                total_tokens = int(usage.get("total_tokens", 0) or 0)
            except Exception:
                prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
            if prompt_tokens < 0:
                prompt_tokens = 0
            if completion_tokens < 0:
                completion_tokens = 0
            if total_tokens < 0:
                total_tokens = 0
            reasoning_tokens = 0
            try:
                served = usage.get("provider", "") if isinstance(usage, dict) else ""
            except Exception:
                served = ""
            if not isinstance(served, str):
                served = ""
            try:
                actual = usage.get("cost") if isinstance(usage, dict) else None
            except Exception:
                actual = None
            if not isinstance(actual, (int, float)):
                actual = None
            else:
                try:
                    import math as _math
                    if not (_math.isfinite(float(actual)) and float(actual) >= 0):
                        actual = None
                    else:
                        actual = float(actual)
                except Exception:
                    actual = None
            details = None
            try:
                served_model = usage.get("served_model", "") if isinstance(usage, dict) else ""
            except Exception:
                served_model = ""
            if not isinstance(served_model, str):
                served_model = ""
            duration_ms = (perf_counter() - decide_start) * 1000.0
            return (parsed, prompt_tokens, completion_tokens, total_tokens,
                    reasoning_tokens, duration_ms, served, actual, details, served_model)
        except Exception as exc:
            duration_ms = (perf_counter() - decide_start) * 1000.0
            return {"_error": str(exc)}, 0, 0, 0, 0, duration_ms, "", None, None, ""
    agent_start = perf_counter()
    try:
        parsed, usage = await complete_json_payload(payload_body)
        try:
            prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens = int(usage.get("completion_tokens", 0) or 0)
            total_tokens = int(usage.get("total_tokens", 0) or 0)
            reasoning_tokens = int(usage.get("reasoning_tokens", 0) or 0)
        except Exception:
            prompt_tokens, completion_tokens, total_tokens, reasoning_tokens = 0, 0, 0, 0
        if prompt_tokens < 0:
            prompt_tokens = 0
        if completion_tokens < 0:
            completion_tokens = 0
        if total_tokens < 0:
            total_tokens = 0
        if reasoning_tokens < 0:
            reasoning_tokens = 0
        try:
            served = usage.get("provider", "") if isinstance(usage, dict) else ""
        except Exception:
            served = ""
        if not isinstance(served, str):
            served = ""
        try:
            actual = usage.get("cost") if isinstance(usage, dict) else None
        except Exception:
            actual = None
        if not isinstance(actual, (int, float)):
            actual = None
        else:
            try:
                import math as _math
                if not (_math.isfinite(float(actual)) and float(actual) >= 0):
                    actual = None
                else:
                    actual = float(actual)
            except Exception:
                actual = None
        try:
            details = usage.get("cost_details") if isinstance(usage, dict) else None
        except Exception:
            details = None
        if not isinstance(details, dict):
            details = None
        try:
            served_model = usage.get("served_model", "") if isinstance(usage, dict) else ""
        except Exception:
            served_model = ""
        if not isinstance(served_model, str):
            served_model = ""
        duration_ms = (perf_counter() - agent_start) * 1000.0
        return (parsed, prompt_tokens, completion_tokens, total_tokens,
                reasoning_tokens, duration_ms, served, actual, details, served_model)
    except Exception as exc:
        duration_ms = (perf_counter() - agent_start) * 1000.0
        return {"_error": str(exc)}, 0, 0, 0, 0, duration_ms, "", None, None, ""


def _per_agent_entry(prompt_tokens: int, completion_tokens: int, total_tokens: int,
                     reasoning_tokens: int, duration_ms: float, model: str,
                     provider: str = "", actual_cost=None,
                     cost_details=None, served_model: str = "") -> dict:
    """Per-agent usage entry. ``actual_cost`` (usage.cost) becomes cost_usd
    when not None (preferred over catalog pricing, e.g. dynamic routers);
    ``served_model`` is stored only when it differs from the requested
    ``model``. Negative catalog-derived prices are never stored (callers
    treat them as unknown)."""
    try:
        cost = round(float(actual_cost), 6) if isinstance(actual_cost, (int, float)) else None
    except Exception:
        cost = None
    entry: dict = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "reasoning_tokens": reasoning_tokens,
        "cost_usd": cost,
        "input_cost_usd": None,
        "output_cost_usd": None,
        "duration_ms": duration_ms,
        "model": model,
        "provider": provider if isinstance(provider, str) else "",
    }
    try:
        sm = (served_model or "").strip() if isinstance(served_model, str) else ""
    except Exception:
        sm = ""
    try:
        req = (model or "").strip() if isinstance(model, str) else ""
    except Exception:
        req = ""
    if sm and sm != req:
        entry["served_model"] = sm
    if isinstance(cost_details, dict) and cost_details:
        try:
            entry["cost_details"] = dict(cost_details)
        except Exception:
            pass
    return entry


def _valid_price(v) -> float | None:
    """Catalog price when known and >= 0, else None (never negative)."""
    try:
        if v is None:
            return None
        f = float(v)  # type: ignore[arg-type]
        import math as _math
        if not _math.isfinite(f) or f < 0:
            return None
        return f
    except Exception:
        return None


def _fill_pricing(per_agent: dict, prompt_price, completion_price, skip_ids=frozenset()) -> None:
    """Fill catalog-derived costs, preferring actual usage.cost.

    - cost_usd: kept when already a number (actual usage.cost); otherwise
      tokens x catalog price when BOTH prices are known and >= 0.
    - input/output splits: from catalog when that side is known and >= 0,
      else None. Negative ("-1" dynamic) is unknown, never a price.
    """
    skip = set(skip_ids or ())
    prompt_price = _valid_price(prompt_price)
    completion_price = _valid_price(completion_price)
    if prompt_price is not None and completion_price is not None:
        for aid, entry in per_agent.items():
            if aid in skip or not isinstance(entry, dict):
                continue
            try:
                if isinstance(entry.get("cost_usd"), (int, float)):
                    continue
                cost = entry["prompt_tokens"] * prompt_price + entry["completion_tokens"] * completion_price
            except Exception:
                continue
            entry["cost_usd"] = round(cost, 6)
    for aid, entry in per_agent.items():
        if aid in skip or not isinstance(entry, dict):
            continue
        try:
            if prompt_price is None:
                entry["input_cost_usd"] = None
            else:
                entry["input_cost_usd"] = round(entry["prompt_tokens"] * prompt_price, 6)
            if completion_price is None:
                entry["output_cost_usd"] = None
            else:
                entry["output_cost_usd"] = round(entry["completion_tokens"] * completion_price, 6)
        except Exception:
            pass


def _totals_from_per_agent(per_agent: dict, prompt_price=None, completion_price=None) -> dict:
    total_in = 0
    total_out = 0
    total_reason = 0
    try:
        for v in (per_agent or {}).values():
            if not isinstance(v, dict):
                continue
            try:
                total_in += int(v.get("prompt_tokens", 0) or 0)
                total_out += int(v.get("completion_tokens", 0) or 0)
                total_reason += int(v.get("reasoning_tokens", 0) or 0)
            except Exception:
                pass
    except Exception:
        pass
    if any(not isinstance(v.get("cost_usd"), (int, float)) for v in (per_agent or {}).values()):
        total_cost = None
    else:
        try:
            total_cost = round(sum((v["cost_usd"] for v in per_agent.values()), 0.0), 6)
        except Exception:
            total_cost = None
    if any(not isinstance(v.get("input_cost_usd"), (int, float)) for v in (per_agent or {}).values()):
        total_input_cost = None
    else:
        try:
            total_input_cost = round(sum((v["input_cost_usd"] for v in per_agent.values()), 0.0), 6)
        except Exception:
            total_input_cost = None
    if any(not isinstance(v.get("output_cost_usd"), (int, float)) for v in (per_agent or {}).values()):
        total_output_cost = None
    else:
        try:
            total_output_cost = round(sum((v["output_cost_usd"] for v in per_agent.values()), 0.0), 6)
        except Exception:
            total_output_cost = None
    if not per_agent:
        if _valid_price(prompt_price) is None or _valid_price(completion_price) is None:
            total_cost = None
            total_input_cost = None
            total_output_cost = None
        else:
            total_cost = 0.0
            total_input_cost = 0.0
            total_output_cost = 0.0
    return {
        "prompt_tokens": total_in,
        "completion_tokens": total_out,
        "total_tokens": total_in + total_out,
        "reasoning_tokens": total_reason,
        "cost_usd": total_cost,
        "input_cost_usd": total_input_cost,
        "output_cost_usd": total_output_cost,
    }


def _build_usage(per_agent: dict, model: str, totals: dict, duration_ms: float,
                 reused_agents: dict | None = None,
                 provider_requested: str = "") -> dict:
    return {
        "prompt_tokens": totals["prompt_tokens"],
        "completion_tokens": totals["completion_tokens"],
        "total_tokens": totals["total_tokens"],
        "reasoning_tokens": totals["reasoning_tokens"],
        "cost_usd": totals["cost_usd"],
        "input_cost_usd": totals["input_cost_usd"],
        "output_cost_usd": totals["output_cost_usd"],
        "duration_ms": duration_ms,
        "model": model,
        "provider_requested": provider_requested if isinstance(provider_requested, str) else "",
        "per_agent": per_agent,
        "reused_agents": reused_agents or {},
    }


async def _execute_agents(db: Session, agents: list[Agent], requests: dict, model: str,
                          n_chunks: int = 0):
    """Run per-agent payloads concurrently (shared machinery).

    Returns (outputs, per_agent, evidence, probs) with costs None; caller
    prices + totals. Identifier outputs are normalised to 11 yes/no answers
    via split_identifier_result + normalize_identifier_output; the
    per-agent chunk-evidence / noul-probability sidecars land in
    ``evidence``/``probs`` ({agent_id: {question_key: ...}}, identifier
    agents with clean answers only). Uses the shared as_completed generator
    so streaming and non-streaming share the same run loop; final dict
    order follows ``agents`` order.
    """
    agents_by_id = {a.id: a for a in agents}
    collected: dict = {}
    async for res in _stream_fresh_results(agents, requests, model, agents_by_id,
                                           n_chunks=n_chunks):
        collected[res["agent_id"]] = res
    outputs: dict = {}
    per_agent: dict = {}
    evidence: dict = {}
    probs: dict = {}
    for a in agents:
        res = collected.get(a.id)
        if res is None:
            continue
        outputs[a.id] = res["output"]
        per_agent[a.id] = res["per_entry"]
        try:
            if isinstance(res.get("evidence"), dict):
                evidence[a.id] = res["evidence"]
            if isinstance(res.get("probs"), dict):
                probs[a.id] = res["probs"]
        except Exception:
            pass
    return outputs, per_agent, evidence, probs


def _request_match_key(body):
    """Comparable request identity for reuse/check-existing matching.

    Chat bodies compare by ``messages`` (exactly as before); decisions
    bodies (no ``messages`` key) compare by
    ``(model, state, questions)``. The decisions ``transport`` marker and
    ``provider`` never affect matching (provider/effort are already
    SQL-filtered per row). Non-dict inputs compare as-is.
    """
    try:
        if isinstance(body, dict) and "messages" in body:
            return body.get("messages")
        if isinstance(body, dict):
            return (body.get("model"), body.get("state"), body.get("questions"))
        return body
    except Exception:
        return None


def _resolve_valid_reuse(db: Session, agents: list[Agent], requests: dict,
                          reuse_map, model: str, effort: str, provider: str) -> dict:
    """Validate per-agent reuse entries like create_run (invalid ignored)."""
    if not isinstance(reuse_map, dict):
        reuse_map = {}
    valid: dict[str, RunLog] = {}
    for agent in agents:
        try:
            src_id = reuse_map.get(agent.id)
        except Exception:
            src_id = None
        if not isinstance(src_id, str) or not src_id.strip():
            continue
        src_id = src_id.strip()
        try:
            source = db.query(RunLog).filter(RunLog.id == src_id).first()
        except Exception:
            source = None
        if source is None:
            continue
        fresh_body = requests.get(agent.id)
        if not _is_valid_reuse_source(
            source, agent_id=agent.id, model=model,
            reasoning_effort=effort, expected_body=fresh_body,
            provider=provider,
        ):
            continue
        valid[agent.id] = source
    return valid


def _seed_reused_state(valid_reuse: dict) -> tuple[dict, dict, dict]:
    """Seed (outputs, per_agent, reused_agents) from validated reuse sources.

    Mutates ``valid_reuse`` in place on copy failure (drops that agent to
    fresh), matching create_run's behaviour.
    """
    outputs: dict = {}
    per_agent: dict = {}
    reused_agents: dict = {}
    for aid, source in list(valid_reuse.items()):
        try:
            src_outs = getattr(source, "outputs", None) or {}
            if not isinstance(src_outs, dict) or aid not in src_outs:
                raise KeyError(aid)
            outputs[aid] = copy.deepcopy(src_outs.get(aid))
            entry, rid, rat = _build_reused_per_agent(source, aid)
            per_agent[aid] = entry
            reused_agents[aid] = {"log_id": rid, "created_at": rat}
        except Exception:
            valid_reuse.pop(aid, None)
            outputs.pop(aid, None)
            per_agent.pop(aid, None)
            reused_agents.pop(aid, None)
            continue
    return outputs, per_agent, reused_agents


def _merge_sidecars(evidence_all: dict, probs_all: dict, agent_id: str,
                    ev, pr) -> None:
    """Merge one agent's chunk-evidence/probability sidecars (never raises).

    Only dict sidecars are stored; other agents' keys are never touched.
    """
    try:
        if isinstance(ev, dict):
            evidence_all[agent_id] = ev
        if isinstance(pr, dict):
            probs_all[agent_id] = pr
    except Exception:
        pass


def _copy_reuse_sidecars(valid_reuse: dict) -> tuple[dict, dict]:
    """Copy identifier sidecars from per-agent reuse sources (never raises).

    Returns (evidence, probs) seeded from each source log's own columns so
    a reused identifier answer keeps the sidecars rendered from GET /logs.
    """
    evidence: dict = {}
    probs: dict = {}
    try:
        for aid, source in (valid_reuse or {}).items():
            try:
                sev = getattr(source, "evidence", None) or {}
                if isinstance(sev, dict) and isinstance(sev.get(aid), dict):
                    evidence[aid] = copy.deepcopy(sev.get(aid))
                spr = getattr(source, "probabilities", None) or {}
                if isinstance(spr, dict) and isinstance(spr.get(aid), dict):
                    probs[aid] = copy.deepcopy(spr.get(aid))
            except Exception:
                continue
    except Exception:
        pass
    return evidence, probs


def _agent_event_for(agent_id: str, agent_name: str, output, per_entry: dict,
                     reused: bool = False) -> dict:
    """One NDJSON agent event, output exactly as in the final log."""
    if isinstance(output, dict) and "_error" in output:
        try:
            err = str(output.get("_error", ""))
        except Exception:
            err = "agent failed"
        ev: dict = {"type": "agent", "agent_id": agent_id, "agent_name": agent_name,
                    "output": output, "usage": per_entry, "status": "error",
                    "error": err}
    else:
        ev = {"type": "agent", "agent_id": agent_id, "agent_name": agent_name,
              "output": output, "usage": per_entry, "status": "done"}
    if reused:
        ev["reused"] = True
    return ev


async def _stream_fresh_results(fresh_agents: list[Agent], requests: dict,
                                model: str, agents_by_id: dict | None = None,
                                n_chunks: int = 0):
    """Shared run loop: run fresh agents concurrently, yield in completion order.

    Each yield is {"agent_id", "agent_name", "output", "per_entry",
    "status", "error", "reused": False, "evidence", "probs"}. Identifier
    outputs are split (split_identifier_result) then normalised to 11
    yes/no answers; the chunk-evidence / noul-probability sidecars ride
    along as ``evidence``/``probs`` (dicts for clean identifier answers,
    else None). Both the non-streaming paths (via _execute_agents) and the
    streaming NDJSON paths consume this generator so the two paths don't
    duplicate the run logic.
    """
    by_id = agents_by_id or {a.id: a for a in fresh_agents}

    async def _one(agent):
        # Decision models serve the identifier agent only: extraction agents
        # fail with the identifier-only error WITHOUT an API call (no spend).
        try:
            _one_is_ident = bool(is_identifier(agent))
        except Exception:
            _one_is_ident = False
        try:
            _one_is_dec = bool(is_decision_model(model))
        except Exception:
            _one_is_dec = False
        if _one_is_dec and not _one_is_ident:
            _err_start = perf_counter()
            _err_parsed = {"_error": "decision models support the identifier agent only"}
            _err_dur = (perf_counter() - _err_start) * 1000.0
            _err_entry = _per_agent_entry(0, 0, 0, 0, _err_dur, model, "", None, None, "")
            try:
                _err_msg = str(_err_parsed.get("_error", ""))
            except Exception:
                _err_msg = "agent failed"
            return {"agent_id": agent.id,
                    "agent_name": _agent_display_name(agent),
                    "output": _err_parsed, "per_entry": _err_entry,
                    "status": "error", "error": _err_msg, "reused": False,
                    "evidence": None, "probs": None}
        body = copy.deepcopy(requests.get(agent.id, {}))
        (parsed, pt, ct, tt, rt, dur, served,
         actual, details, served_model) = await _call_single_payload(body)
        ev_side = None
        pr_side = None
        try:
            row = by_id.get(agent.id)
            if row is not None and is_identifier(row):
                if isinstance(parsed, dict) and "_error" not in parsed:
                    try:
                        transport = ("decisions"
                                     if isinstance(body, dict) and body.get("transport") == "decisions"
                                     else "chat")
                        bools, ev, pr = split_identifier_result(parsed, transport, n_chunks)
                        parsed = normalize_identifier_output(bools)
                        ev_side = ev if isinstance(ev, dict) else None
                        pr_side = pr if isinstance(pr, dict) else None
                    except Exception:
                        pass
        except Exception:
            pass
        per_entry = _per_agent_entry(pt, ct, tt, rt, dur, model, served,
                                     actual, details, served_model)
        if isinstance(parsed, dict) and "_error" in parsed:
            try:
                err = str(parsed.get("_error", ""))
            except Exception:
                err = "agent failed"
            return {"agent_id": agent.id,
                    "agent_name": _agent_display_name(agent),
                    "output": parsed, "per_entry": per_entry,
                    "status": "error", "error": err, "reused": False,
                    "evidence": None, "probs": None}
        return {"agent_id": agent.id,
                "agent_name": _agent_display_name(agent),
                "output": parsed, "per_entry": per_entry,
                "status": "done", "error": None, "reused": False,
                "evidence": ev_side, "probs": pr_side}

    if not fresh_agents:
        return
        yield  # make this an async generator
    tasks = [asyncio.create_task(_one(a)) for a in fresh_agents]
    try:
        for fut in asyncio.as_completed(tasks):
            try:
                res = await fut
            except Exception as exc:
                continue
            yield res
    finally:
        for t in tasks:
            try:
                if not t.done():
                    t.cancel()
            except Exception:
                pass


def _ndjson_line(obj: dict) -> bytes:
    return (json.dumps(obj, default=str) + "\n").encode("utf-8")


def _new_run_ids() -> tuple[str, str]:
    try:
        rid = uuid.uuid4().hex
    except Exception:
        import secrets
        rid = secrets.token_hex(16)
    try:
        lid = uuid.uuid4().hex
    except Exception:
        import secrets as _s
        lid = _s.token_hex(16)
    return rid, lid


async def _create_run_stream(db: Session, *, payload, agents, snapshots,
                             requests, input_type, input_data, effort,
                             task_title, filters, provider_requested,
                             wall_start, n_chunks: int = 0,
                             fireflies_url: str = "") -> StreamingResponse:
    """Streaming NDJSON variant of POST /runs (stream=true).

    Events: start -> agent* (completion order, reused first) -> done.
    The log row is written exactly once at the end with the same contents
    as the non-streaming path. Fatal errors after start become
    {"type":"error","detail":...}; validation errors before start still
    raise HTTPException via _plan_run (handled by the caller).
    """
    run_id, log_id = _new_run_ids()
    model = payload.model
    start_agents = [{"agent_id": a.id, "agent_name": _agent_display_name(a)}
                    for a in agents]

    async def _gen():
        try:
            reuse_map = getattr(payload, "reuse", None)
            valid_reuse = _resolve_valid_reuse(
                db, agents, requests, reuse_map, model, effort, provider_requested)
            outputs, per_agent, reused_agents = _seed_reused_state(valid_reuse)
            evidence_all, probs_all = _copy_reuse_sidecars(valid_reuse)
            # start after plan + reuse known (agents list fixed)
            yield _ndjson_line({"type": "start", "run_id": run_id,
                                "log_id": log_id, "model": model,
                                "agents": start_agents})
            # Reused agents immediately (in agent order).
            for a in agents:
                if a.id not in valid_reuse:
                    continue
                try:
                    out = outputs.get(a.id)
                    entry = per_agent.get(a.id, {})
                    yield _ndjson_line(_agent_event_for(
                        a.id, _agent_display_name(a), out, entry, reused=True))
                except Exception:
                    continue
            fresh_agents = [a for a in agents if a.id not in valid_reuse]
            agents_by_id = {a.id: a for a in agents}
            async for res in _stream_fresh_results(
                    fresh_agents, requests, model, agents_by_id,
                    n_chunks=n_chunks):
                try:
                    outputs[res["agent_id"]] = res["output"]
                    per_agent[res["agent_id"]] = res["per_entry"]
                    _merge_sidecars(evidence_all, probs_all, res["agent_id"],
                                    res.get("evidence"), res.get("probs"))
                    yield _ndjson_line(_agent_event_for(
                        res["agent_id"], res["agent_name"],
                        res["output"], res["per_entry"], reused=False))
                except Exception:
                    continue
            # Reorder to agent order so the final log matches non-streaming.
            try:
                _oo: dict = {}
                _pp: dict = {}
                for _a in agents:
                    if _a.id in outputs:
                        _oo[_a.id] = outputs[_a.id]
                    if _a.id in per_agent:
                        _pp[_a.id] = per_agent[_a.id]
                outputs = _oo
                per_agent = _pp
            except Exception:
                pass
            # --- Finalize exactly like the non-streaming path ----------------
            if fresh_agents or not valid_reuse:
                try:
                    prompt_price, completion_price = await get_model_pricing(model)
                except Exception:
                    prompt_price, completion_price = None, None
            else:
                prompt_price, completion_price = None, None
            reused_ids = set(valid_reuse.keys())
            try:
                _fill_pricing(per_agent, prompt_price, completion_price,
                              skip_ids=reused_ids)
            except Exception:
                pass
            totals = _totals_from_per_agent(per_agent, prompt_price, completion_price)
            total_in = totals["prompt_tokens"]
            total_out = totals["completion_tokens"]
            total_reason = totals["reasoning_tokens"]
            total_cost = totals["cost_usd"]
            total_input_cost = totals["input_cost_usd"]
            total_output_cost = totals["output_cost_usd"]
            wall_ms = (perf_counter() - wall_start) * 1000.0
            usage_duration_ms = wall_ms
            try:
                for aid in valid_reuse:
                    d = per_agent.get(aid, {}).get("duration_ms")
                    if isinstance(d, (int, float)) and d > usage_duration_ms:
                        usage_duration_ms = float(d)
            except Exception:
                pass
            usage = {
                "prompt_tokens": total_in,
                "completion_tokens": total_out,
                "total_tokens": total_in + total_out,
                "reasoning_tokens": total_reason,
                "cost_usd": total_cost,
                "input_cost_usd": total_input_cost,
                "output_cost_usd": total_output_cost,
                "duration_ms": usage_duration_ms,
                "model": model,
                "provider_requested": provider_requested,
                "per_agent": per_agent,
                "reused_agents": reused_agents,
            }
            client = (getattr(payload, "client", "") or "").strip()
            meeting_type = (getattr(payload, "meeting_type", "") or "").strip() or task_title
            meeting_title = (getattr(payload, "meeting_title", "") or "").strip() or task_title
            run = Run(id=run_id, input_type=input_type, input_data=input_data,
                      model=model, agent_ids=getattr(payload, "agent_ids", []) or [],
                      outputs=outputs)
            db.add(run)
            db.commit()
            db.refresh(run)
            log_reused_id = ""
            log_reused_at = None
            try:
                if agents and len(valid_reuse) == len(agents) and agents:
                    src_ids = set()
                    for _aid, _src in valid_reuse.items():
                        try:
                            src_ids.add(_src.id)
                        except Exception:
                            pass
                    if len(src_ids) == 1:
                        only_src = next(iter(valid_reuse.values()))
                        only_orig_id = getattr(only_src, "reused_from_log_id", None) or getattr(
                            only_src, "id", "") or ""
                        only_orig_at = getattr(only_src, "reused_from_created_at", None) or getattr(
                            only_src, "created_at", None)
                        log_reused_id = only_orig_id or ""
                        log_reused_at = only_orig_at
            except Exception:
                log_reused_id = ""
                log_reused_at = None
            log = RunLog(id=log_id, run_id=run.id, input_type=run.input_type,
                         input_data=run.input_data, model=run.model,
                         agent_snapshot=snapshots, attribute_snapshot=snapshots,
                         outputs=outputs, feedback={},
                         usage=usage, filters=filters, requests=requests,
                         client=client, meeting_type=meeting_type,
                         meeting_title=meeting_title,
                         reasoning_effort=effort, provider=provider_requested,
                         run_group_id=getattr(payload, "run_group_id", "") or "",
                         reused_from_log_id=log_reused_id or "",
                         reused_from_created_at=log_reused_at, consistency={},
                         fireflies_url=fireflies_url,
                         evidence=evidence_all, probabilities=probs_all)
            db.add(log)
            db.commit()
            db.refresh(log)
            resp = {"id": run.id, "outputs": outputs, "usage": usage,
                    "requests": requests, "log_id": log.id,
                    "run_group_id": log.run_group_id or "", "log": _out_resolved(log, db)}
            yield _ndjson_line({"type": "done", "log": resp["log"],
                                "id": resp["id"], "log_id": resp["log_id"],
                                "run_group_id": resp["run_group_id"]})
        except HTTPException as exc:
            try:
                detail = str(exc.detail)
            except Exception:
                detail = "request failed"
            yield _ndjson_line({"type": "error", "detail": detail})
        except Exception as exc:
            try:
                detail = str(exc)
            except Exception:
                detail = "run failed"
            yield _ndjson_line({"type": "error", "detail": detail})

    return StreamingResponse(_gen(), media_type="application/x-ndjson")


async def _auto_run_stream(db: Session, *, payload, identifier, ident_id,
                           resolved_type, resolved_data, effort, task_title,
                           meeting_type, snapshots_ident, requests_ident,
                           parsed_ident, per_ident, answers, planned,
                           planned_agents, attr_subsets, snapshots_sel,
                           requests_sel, snapshots, requests, valid_reuse,
                           outputs, per_agent, reused_agents, plan_stored,
                           provider_requested, filters, wall_start,
                           n_chunks: int = 0,
                           evidence_all=None, probs_all=None,
                           fireflies_url: str = "") -> StreamingResponse:
    """Streaming NDJSON variant of POST /runs/auto (stream=true).

    Events: start (after identifier+plan known) -> identifier ->
    agent* (reused immediately, then fresh in completion order) -> done.
    Log row written exactly once at the end, same contents as non-stream.
    """
    run_id, log_id = _new_run_ids()
    model = payload.model
    all_agents = [identifier] + planned_agents
    start_agents = [{"agent_id": a.id, "agent_name": _agent_display_name(a)}
                    for a in all_agents]
    try:
        answers_out = dict(answers) if isinstance(answers, dict) else {}
    except Exception:
        answers_out = {}
    try:
        plan_out = [dict(p) for p in (plan_stored or [])]
    except Exception:
        plan_out = []
    ident_errored = not isinstance(parsed_ident, dict) or "_error" in parsed_ident
    ident_status = "error" if ident_errored else "done"
    try:
        ident_err = str(parsed_ident.get("_error", "")) if ident_errored and isinstance(parsed_ident, dict) else None
    except Exception:
        ident_err = None

    async def _gen():
        try:
            yield _ndjson_line({"type": "start", "run_id": run_id,
                                "log_id": log_id, "model": model,
                                "agents": start_agents})
            ident_ev: dict = {"type": "identifier", "agent_id": ident_id,
                              "agent_name": _agent_display_name(identifier),
                              "output": parsed_ident, "usage": per_ident,
                              "answers": answers_out, "plan": plan_out,
                              "status": ident_status}
            if ident_err:
                ident_ev["error"] = ident_err
            yield _ndjson_line(ident_ev)
            # Identifier also gets a normal agent event so "one agent event
            # per agent" holds literally (frontend merging is idempotent).
            try:
                yield _ndjson_line(_agent_event_for(
                    ident_id, _agent_display_name(identifier),
                    parsed_ident, per_ident, reused=False))
            except Exception:
                pass
            # Reused planned agents immediately.
            for a in planned_agents:
                if a.id not in valid_reuse:
                    continue
                try:
                    out = outputs.get(a.id)
                    entry = per_agent.get(a.id, {})
                    yield _ndjson_line(_agent_event_for(
                        a.id, _agent_display_name(a), out, entry, reused=True))
                except Exception:
                    continue
            try:
                stream_evidence = dict(evidence_all) if isinstance(evidence_all, dict) else {}
                stream_probs = dict(probs_all) if isinstance(probs_all, dict) else {}
            except Exception:
                stream_evidence, stream_probs = {}, {}
            fresh_agents = [a for a in planned_agents if a.id not in valid_reuse]
            agents_by_id = {a.id: a for a in all_agents}
            async for res in _stream_fresh_results(
                    fresh_agents, requests, model, agents_by_id,
                    n_chunks=n_chunks):
                try:
                    outputs[res["agent_id"]] = res["output"]
                    per_agent[res["agent_id"]] = res["per_entry"]
                    _merge_sidecars(stream_evidence, stream_probs, res["agent_id"],
                                    res.get("evidence"), res.get("probs"))
                    yield _ndjson_line(_agent_event_for(
                        res["agent_id"], res["agent_name"],
                        res["output"], res["per_entry"], reused=False))
                except Exception:
                    continue
            # Reorder to identifier + planned order like non-streaming.
            # Mutate in place (no rebinding) so the closure keeps working.
            try:
                _oo2: dict = {}
                _pp2: dict = {}
                if ident_id in outputs:
                    _oo2[ident_id] = outputs[ident_id]
                if ident_id in per_agent:
                    _pp2[ident_id] = per_agent[ident_id]
                for _a in planned_agents:
                    if _a.id in outputs:
                        _oo2[_a.id] = outputs[_a.id]
                    if _a.id in per_agent:
                        _pp2[_a.id] = per_agent[_a.id]
                outputs.clear()
                outputs.update(_oo2)
                per_agent.clear()
                per_agent.update(_pp2)
            except Exception:
                pass
            # --- Finalize exactly like non-streaming auto --------------------
            try:
                consistency = compute_consistency(answers, outputs, ident_id, plan_stored)
            except Exception:
                consistency = {"auto": True, "version": 2, "score": None,
                               "identifier_agent_id": ident_id,
                               "answers": dict(answers) if isinstance(answers, dict) else {},
                               "plan": plan_stored, "agents": {}}
            try:
                feedback = apply_auto_feedback({}, consistency)
            except Exception:
                feedback = {}
            try:
                feedback = _redirect_auto_feedback_for_reuse(
                    db, valid_reuse, feedback, snapshots=snapshots)
            except Exception:
                pass
            try:
                prompt_price, completion_price = await get_model_pricing(model)
            except Exception:
                prompt_price, completion_price = None, None
            try:
                _fill_pricing(per_agent, prompt_price, completion_price,
                              skip_ids=set(valid_reuse.keys()))
            except Exception:
                pass
            totals = _totals_from_per_agent(per_agent, prompt_price, completion_price)
            wall_ms = (perf_counter() - wall_start) * 1000.0
            try:
                for aid in valid_reuse:
                    d = per_agent.get(aid, {}).get("duration_ms")
                    if isinstance(d, (int, float)) and d > wall_ms:
                        wall_ms = float(d)
            except Exception:
                pass
            usage = _build_usage(per_agent, model, totals, wall_ms, reused_agents,
                                 provider_requested=provider_requested)
            client_name = (getattr(payload, "client", "") or "").strip()
            meeting_title = (getattr(payload, "meeting_title", "") or "").strip() or task_title
            agent_ids = [ident_id] + [a.id for a in planned_agents]
            run = Run(id=run_id, input_type=resolved_type,
                      input_data=resolved_data, model=model,
                      agent_ids=agent_ids, outputs=outputs)
            db.add(run)
            db.commit()
            db.refresh(run)
            log = RunLog(id=log_id, run_id=run.id, input_type=run.input_type,
                         input_data=run.input_data, model=run.model,
                         agent_snapshot=snapshots, attribute_snapshot=snapshots,
                         outputs=outputs, feedback=feedback,
                         usage=usage, filters=filters, requests=requests,
                         client=client_name, meeting_type=meeting_type,
                         meeting_title=meeting_title,
                         reasoning_effort=effort, provider=provider_requested,
                         run_group_id=getattr(payload, "run_group_id", "") or "",
                         reused_from_log_id="", reused_from_created_at=None,
                         consistency=consistency,
                         fireflies_url=fireflies_url,
                         evidence=stream_evidence, probabilities=stream_probs)
            db.add(log)
            db.commit()
            db.refresh(log)
            resp_log = _out_resolved(log, db)
            yield _ndjson_line({"type": "done", "log": resp_log,
                                "id": run.id, "log_id": log.id,
                                "run_group_id": log.run_group_id or ""})
        except HTTPException as exc:
            try:
                detail = str(exc.detail)
            except Exception:
                detail = "request failed"
            yield _ndjson_line({"type": "error", "detail": detail})
        except Exception as exc:
            try:
                detail = str(exc)
            except Exception:
                detail = "run failed"
            yield _ndjson_line({"type": "error", "detail": detail})

    return StreamingResponse(_gen(), media_type="application/x-ndjson")


def _agents_by_name_for_planning(db: Session) -> tuple[dict, dict]:
    """All agents keyed by name + attrs keyed by agent name (DB order)."""
    try:
        rows = db.query(Agent).all()
    except Exception:
        return {}, {}
    by_name: dict = {}
    attrs_by_name: dict = {}
    for a in rows or []:
        try:
            n = getattr(a, "name", "") or ""
        except Exception:
            continue
        if not isinstance(n, str) or not n:
            continue
        if n not in by_name:
            by_name[n] = a
        try:
            attrs = _attrs_for_agent(db, a.id)
        except Exception:
            attrs = []
        attrs_by_name[n] = list(attrs or [])
    return by_name, attrs_by_name


def _snapshot_attr_names(snapshot: dict) -> list[str]:
    try:
        attrs = (snapshot or {}).get("attributes", [])
        return [a.get("name", "") for a in attrs if isinstance(a, dict) and isinstance(a.get("name"), str)]
    except Exception:
        return []


def _consistency_inputs_from_log(log: RunLog):
    """Derive (identifier_output, outputs, agents_by_id, attrs_by_agent) from a log."""
    outputs = log.outputs if isinstance(getattr(log, "outputs", None), dict) else {}
    snaps = log.agent_snapshot if isinstance(getattr(log, "agent_snapshot", None), dict) else {}
    agents_by_id: dict = {}
    attrs_by_agent: dict = {}
    for aid, snap in snaps.items():
        if not isinstance(snap, dict):
            continue
        agents_by_id[str(aid)] = {"name": snap.get("name", "") or "", "kind": snap.get("kind", "") or ""}
        attrs_by_agent[str(aid)] = _snapshot_attr_names(snap)
    identifier_agent_id = ""
    try:
        cons = getattr(log, "consistency", None) or {}
        if isinstance(cons, dict) and isinstance(cons.get("identifier_agent_id"), str):
            identifier_agent_id = cons.get("identifier_agent_id") or ""
    except Exception:
        identifier_agent_id = ""
    if not identifier_agent_id:
        for aid, info in agents_by_id.items():
            if info.get("kind") == IDENTIFIER_KIND:
                identifier_agent_id = str(aid)
                break
    identifier_output = outputs.get(identifier_agent_id, {}) if identifier_agent_id else {}
    return identifier_output, outputs, agents_by_id, attrs_by_agent, identifier_agent_id


def _iso_str(value) -> str:
    """ISO string for datetimes/strings ("" when absent)."""
    from datetime import datetime as _dt

    if isinstance(value, _dt):
        try:
            return value.isoformat()
        except Exception:
            return ""
    if isinstance(value, str):
        return value
    return ""


def _num_or_none(value):
    return value if isinstance(value, (int, float)) else None


def _source_agent_info(log: RunLog, agent_id: str) -> tuple[str, float | None, float | None]:
    """(created_at ISO, cost_usd, duration_ms) for one agent from a source log.

    created_at is the ORIGINAL generation time: the per-agent
    ``reused_from_created_at`` when the source agent was itself reused,
    else the log-level ``reused_from_created_at`` (whole-log reuse chain),
    else the log's ``created_at``. cost/duration come straight from the
    source per-agent entry (None when absent).
    """
    usage = getattr(log, "usage", None) or {}
    if not isinstance(usage, dict):
        usage = {}
    per = usage.get("per_agent", {})
    if not isinstance(per, dict):
        per = {}
    entry = per.get(agent_id)
    if not isinstance(entry, dict):
        entry = {}
    cost = _num_or_none(entry.get("cost_usd"))
    duration = _num_or_none(entry.get("duration_ms"))
    reused_at = entry.get("reused_from_created_at")
    if isinstance(reused_at, str) and reused_at:
        created = reused_at
    elif reused_at is not None and not isinstance(reused_at, str):
        # datetime stored in-memory (SQLite returns datetime); JSON stores str.
        try:
            from datetime import datetime as _dt2

            created = reused_at.isoformat() if isinstance(reused_at, _dt2) else ""
        except Exception:
            created = ""
        if not created:
            created = _iso_str(getattr(log, "created_at", ""))
    else:
        log_reused = getattr(log, "reused_from_created_at", None)
        if isinstance(log_reused, str) and log_reused:
            created = log_reused
        elif log_reused is not None and not isinstance(log_reused, str):
            try:
                from datetime import datetime as _dt3

                created = log_reused.isoformat() if isinstance(log_reused, _dt3) else ""
            except Exception:
                created = ""
            if not created:
                created = _iso_str(getattr(log, "created_at", ""))
        else:
            created = _iso_str(getattr(log, "created_at", ""))
    return created, cost, duration


def _find_existing_log_for_agent(
    db: Session, *, model: str, reasoning_effort: str,
    agent_id: str, expected_body,
    provider: str = "",
) -> RunLog | None:
    """Newest RunLog (same model+effort+provider) with a usable output for one agent.

    - SQL filter on model + reasoning_effort + provider, newest first, limit 300.
    - The stored request for this agent must match the would-be-sent body
      (``_request_match_key``: chat compares ``messages``, decisions
      compares ``(model, state, questions)``). Other agents in the log do
      not matter (any agent mix).
    - ``log.outputs[agent_id]`` must exist, be a dict, and have no "_error".
    """
    effort = (reasoning_effort or "").strip()
    prov = (provider or "").strip() if isinstance(provider, str) else ""
    rows = (
        db.query(RunLog)
        .filter(RunLog.model == model, RunLog.reasoning_effort == effort,
                RunLog.provider == prov)
        .order_by(RunLog.created_at.desc())
        .limit(300)
        .all()
    )
    for log in rows:
        try:
            if (getattr(log, "provider", None) or "") != prov:
                continue
        except Exception:
            continue
        stored_reqs = log.requests or {}
        if not isinstance(stored_reqs, dict):
            continue
        stored_body = stored_reqs.get(agent_id)
        if not isinstance(stored_body, dict):
            continue
        if _request_match_key(stored_body) != _request_match_key(expected_body):
            continue
        outs = log.outputs or {}
        if not isinstance(outs, dict):
            continue
        out = outs.get(agent_id)
        if not isinstance(out, dict):
            continue
        if "_error" in out:
            continue
        return log
    return None


def _is_valid_reuse_source(
    source: RunLog, *, agent_id: str, model: str,
    reasoning_effort: str, expected_body,
    provider: str = "",
) -> bool:
    """Same rule as per-agent matching (server-side reuse validation)."""
    try:
        if (getattr(source, "model", None) or "") != model:
            return False
        if (getattr(source, "reasoning_effort", None) or "") != (reasoning_effort or ""):
            return False
        prov = (provider or "").strip() if isinstance(provider, str) else ""
        if (getattr(source, "provider", None) or "") != prov:
            return False
        stored_reqs = getattr(source, "requests", None) or {}
        if not isinstance(stored_reqs, dict):
            return False
        stored_body = stored_reqs.get(agent_id)
        if not isinstance(stored_body, dict):
            return False
        if _request_match_key(stored_body) != _request_match_key(expected_body):
            return False
        outs = getattr(source, "outputs", None) or {}
        if not isinstance(outs, dict):
            return False
        out = outs.get(agent_id)
        if not isinstance(out, dict):
            return False
        if "_error" in out:
            return False
        return True
    except Exception:
        return False


def _build_reused_per_agent(source: RunLog, agent_id: str) -> tuple[dict, str, str]:
    """Copy source per-agent entry + reused markers.

    Returns (per_agent_entry, reused_from_log_id, reused_from_created_at).
    When the source entry itself was reused, its original reused_from_*
    values are kept. Whole-log reuse chains (log-level reused_from_*) are
    honoured as the original when the per-agent entry carries no markers.
    """
    usage = getattr(source, "usage", None) or {}
    if not isinstance(usage, dict):
        usage = {}
    per = usage.get("per_agent", {})
    if not isinstance(per, dict):
        per = {}
    raw = per.get(agent_id)
    if isinstance(raw, dict):
        entry = copy.deepcopy(raw)
        try:
            if "provider" not in entry or not isinstance(entry.get("provider"), str):
                entry["provider"] = ""
        except Exception:
            pass
    else:
        entry = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "reasoning_tokens": 0,
            "cost_usd": None,
            "input_cost_usd": None,
            "output_cost_usd": None,
            "duration_ms": 0.0,
            "model": getattr(source, "model", None) or "",
            "provider": "",
        }
    existing_id = entry.get("reused_from_log_id")
    existing_at = entry.get("reused_from_created_at")
    if isinstance(existing_id, str) and existing_id:
        rid = existing_id
        rat = existing_at if isinstance(existing_at, str) and existing_at else _iso_str(
            getattr(source, "created_at", ""))
        entry["reused_from_log_id"] = rid
        entry["reused_from_created_at"] = rat
        return entry, rid, rat
    log_rid = getattr(source, "reused_from_log_id", None) or ""
    log_rat = getattr(source, "reused_from_created_at", None)
    if isinstance(log_rid, str) and log_rid:
        rat_iso = _iso_str(log_rat) or _iso_str(getattr(source, "created_at", ""))
        entry["reused_from_log_id"] = log_rid
        entry["reused_from_created_at"] = rat_iso
        return entry, log_rid, rat_iso
    rid2 = getattr(source, "id", "") or ""
    rat2 = _iso_str(getattr(source, "created_at", ""))
    entry["reused_from_log_id"] = rid2
    entry["reused_from_created_at"] = rat2
    return entry, rid2, rat2


def _find_existing_log(
    db: Session, *, model: str, reasoning_effort: str, expected_requests: dict,
    provider: str = "",
) -> RunLog | None:
    """Newest RunLog with this model/effort/provider whose requests match.

    - SQL filter on model + reasoning_effort + provider, newest first, limit 200.
    - set(log.requests keys) must equal set(expected agent ids).
    - every agent's stored body must match the would-be-sent body
      (``_request_match_key``).
    - not every output may carry _error (at least one success required).
    """
    effort = (reasoning_effort or "").strip()
    prov = (provider or "").strip() if isinstance(provider, str) else ""
    rows = (
        db.query(RunLog)
        .filter(RunLog.model == model, RunLog.reasoning_effort == effort,
                RunLog.provider == prov)
        .order_by(RunLog.created_at.desc())
        .limit(200)
        .all()
    )
    expected_ids = set(expected_requests.keys())
    for log in rows:
        try:
            if (getattr(log, "provider", None) or "") != prov:
                continue
        except Exception:
            continue
        stored_reqs = log.requests or {}
        if not isinstance(stored_reqs, dict):
            continue
        if set(stored_reqs.keys()) != expected_ids:
            continue
        same = True
        for aid, expected_body in expected_requests.items():
            stored_body = stored_reqs.get(aid)
            if not isinstance(stored_body, dict) or not isinstance(expected_body, dict):
                same = False
                break
            if _request_match_key(stored_body) != _request_match_key(expected_body):
                same = False
                break
        if not same:
            continue
        outs = log.outputs or {}
        if not isinstance(outs, dict) or not outs:
            continue
        all_errored = True
        for v in outs.values():
            if not isinstance(v, dict) or "_error" not in v:
                all_errored = False
                break
        if all_errored:
            continue
        return log
    return None


@router.post("")
async def create_run(payload: RunCreate, db: Session = Depends(get_db)):
    provider_requested = (payload.provider or "").strip() if isinstance(payload.provider, str) else ""
    _stream_flag = bool(getattr(payload, "stream", False))
    (input_type, input_data, effort, task_title, agents, snapshots,
     requests, chunks, fireflies_url) = _plan_run(
        db, meeting_id=payload.meeting_id, input_type=payload.input_type,
        input_data=payload.input_data, agent_ids=payload.agent_ids,
        model=payload.model, reasoning_effort=payload.reasoning_effort,
        provider=provider_requested)
    try:
        n_chunks = len(chunks) if isinstance(chunks, list) else 0
    except Exception:
        n_chunks = 0
    filters = payload.filters if isinstance(payload.filters, dict) else {}
    wall_start = perf_counter()
    if _stream_flag:
        return await _create_run_stream(
            db, payload=payload, agents=agents, snapshots=snapshots,
            requests=requests, input_type=input_type, input_data=input_data,
            effort=effort, task_title=task_title, filters=filters,
            provider_requested=provider_requested, wall_start=wall_start,
            n_chunks=n_chunks, fireflies_url=fireflies_url)

    # --- Per-agent reuse (shared helper) + shared run loop -------------------
    # Non-streaming and streaming share _resolve_valid_reuse,
    # _seed_reused_state and _stream_fresh_results (as_completed) so the two
    # paths don't duplicate the run logic. Final dicts keep the old order
    # (reused seeded first, then fresh in agent order) for byte-identical
    # responses when stream=false.
    reuse_map = getattr(payload, "reuse", None)
    valid_reuse = _resolve_valid_reuse(
        db, agents, requests, reuse_map, payload.model, effort, provider_requested)
    outputs, per_agent, reused_agents = _seed_reused_state(valid_reuse)
    evidence_all, probs_all = _copy_reuse_sidecars(valid_reuse)
    fresh_agents = [a for a in agents if a.id not in valid_reuse]
    agents_by_id = {a.id: a for a in agents}
    _collected: dict = {}
    async for _res in _stream_fresh_results(
            fresh_agents, requests, payload.model, agents_by_id,
            n_chunks=n_chunks):
        _collected[_res["agent_id"]] = _res
    total_in = 0
    total_out = 0
    total_in_reasoning = 0
    # Add reused tokens to totals.
    for aid in valid_reuse:
        try:
            e = per_agent.get(aid, {})
            total_in += int(e.get("prompt_tokens", 0) or 0)
            total_out += int(e.get("completion_tokens", 0) or 0)
            total_in_reasoning += int(e.get("reasoning_tokens", 0) or 0)
        except Exception:
            pass
    for _ag in fresh_agents:
        _res = _collected.get(_ag.id)
        if _res is None:
            continue
        outputs[_ag.id] = _res["output"]
        _merge_sidecars(evidence_all, probs_all, _ag.id,
                        _res.get("evidence"), _res.get("probs"))
        _pe = _res["per_entry"]
        try:
            total_in += int(_pe.get("prompt_tokens", 0) or 0)
            total_out += int(_pe.get("completion_tokens", 0) or 0)
            total_in_reasoning += int(_pe.get("reasoning_tokens", 0) or 0)
        except Exception:
            pass
        per_agent[_ag.id] = _pe
    if fresh_agents or not valid_reuse:
        try:
            prompt_price, completion_price = await get_model_pricing(payload.model)
        except Exception:
            prompt_price, completion_price = None, None
    else:
        # All reused: no OpenRouter calls at all (not even pricing).
        prompt_price, completion_price = None, None
    # Price ONLY fresh agents; reused keep their source numbers verbatim.
    # Actual usage.cost (if present) is preferred over catalog pricing.
    reused_ids = set(valid_reuse.keys())
    try:
        _fill_pricing(per_agent, prompt_price, completion_price,
                      skip_ids=reused_ids)
    except Exception:
        pass
    _tot = _totals_from_per_agent(per_agent, prompt_price, completion_price)
    total_cost = _tot["cost_usd"]
    total_input_cost = _tot["input_cost_usd"]
    total_output_cost = _tot["output_cost_usd"]
    wall_ms = (perf_counter() - wall_start) * 1000.0
    usage_duration_ms = wall_ms
    try:
        for aid in valid_reuse:
            d = per_agent.get(aid, {}).get("duration_ms")
            if isinstance(d, (int, float)) and d > usage_duration_ms:
                usage_duration_ms = float(d)
    except Exception:
        pass
    usage = {
        "prompt_tokens": total_in,
        "completion_tokens": total_out,
        "total_tokens": total_in + total_out,
        "reasoning_tokens": total_in_reasoning,
        "cost_usd": total_cost,
        "input_cost_usd": total_input_cost,
        "output_cost_usd": total_output_cost,
        "duration_ms": usage_duration_ms,
        "model": payload.model,
        "provider_requested": provider_requested,
        "per_agent": per_agent,
        "reused_agents": reused_agents,
    }
    # Denormalized meeting snapshot — plain strings from the Test Lab
    # picker, never FKs. When meeting_id is present the server-known task
    # title fills blanks so old/direct API clients still get a snapshot.
    client = (payload.client or "").strip()
    meeting_type = (payload.meeting_type or "").strip() or task_title
    meeting_title = (payload.meeting_title or "").strip() or task_title
    run = Run(input_type=input_type, input_data=input_data, model=payload.model,
              agent_ids=payload.agent_ids, outputs=outputs)
    db.add(run)
    db.commit()
    db.refresh(run)
    # Log-level reused markers: set (to the single source) only when EVERY
    # agent was reused from the same source log; otherwise "" / None.
    log_reused_id = ""
    log_reused_at = None
    try:
        if agents and len(valid_reuse) == len(agents) and agents:
            src_ids = set()
            for _aid, _src in valid_reuse.items():
                try:
                    src_ids.add(_src.id)
                except Exception:
                    pass
            if len(src_ids) == 1:
                only_src = next(iter(valid_reuse.values()))
                # Point at the ORIGINAL like POST /runs/reuse does.
                only_orig_id = getattr(only_src, "reused_from_log_id", None) or getattr(
                    only_src, "id", "") or ""
                only_orig_at = getattr(only_src, "reused_from_created_at", None) or getattr(
                    only_src, "created_at", None)
                log_reused_id = only_orig_id or ""
                log_reused_at = only_orig_at
    except Exception:
        log_reused_id = ""
        log_reused_at = None
    # Denormalized log row — snapshots only, no FK to agents/attributes.
    log = RunLog(run_id=run.id, input_type=run.input_type, input_data=run.input_data, model=run.model,
                  agent_snapshot=snapshots, attribute_snapshot=snapshots, outputs=outputs, feedback={},
                  usage=usage, filters=filters, requests=requests,
                  client=client, meeting_type=meeting_type, meeting_title=meeting_title,
                  reasoning_effort=effort, provider=provider_requested,
                  run_group_id=payload.run_group_id or "",
                  reused_from_log_id=log_reused_id or "",
                  reused_from_created_at=log_reused_at, consistency={},
                  fireflies_url=fireflies_url,
                  evidence=evidence_all, probabilities=probs_all)
    db.add(log)
    db.commit()
    db.refresh(log)
    return {"id": run.id, "outputs": outputs, "usage": usage, "requests": requests,
            "log_id": log.id, "run_group_id": log.run_group_id or "", "log": _out_resolved(log, db)}


@router.get("", response_model=list[RunOut])
def list_runs(db: Session = Depends(get_db)):
    rows = db.query(Run).order_by(Run.created_at.desc()).limit(100).all()
    return [RunOut(id=r.id, input_type=r.input_type, model=r.model,
                   agent_ids=r.agent_ids or [], created_at=r.created_at) for r in rows]


def _cell_existing(fb: dict, agent_key: str, attr_key: str) -> dict:
    """Existing feedback cell as a plain dict ({} when absent/malformed)."""
    try:
        agent_map = fb.get(agent_key, {})
        if not isinstance(agent_map, dict):
            return {}
        cell = agent_map.get(attr_key, {})
        return dict(cell) if isinstance(cell, dict) else {}
    except Exception:
        return {}


def _apply_feedback_cell(
    fb: dict, agent_name: str, attribute_name: str,
    rating: str, remarks: str | None,
) -> tuple[str, str]:
    """Mutate denormalized ``fb`` like add_feedback; return (history_rating, history_remarks).

    - rating "" removes the cell (drops the agent key when empty) and the
      history row is written with rating "clear".
    - remarks None keeps the cell's existing remarks ("" when absent);
      a string replaces them.
    """
    agent_key = agent_name or "agent"
    attr_key = attribute_name or "attribute"
    existing = _cell_existing(fb, agent_key, attr_key)
    existing_remarks = existing.get("remarks", "")
    if not isinstance(existing_remarks, str):
        existing_remarks = ""
    new_remarks = existing_remarks if remarks is None else (remarks or "")
    if rating == "":
        agent_map = fb.get(agent_key)
        if isinstance(agent_map, dict) and attr_key in agent_map:
            del agent_map[attr_key]
            if not agent_map:
                del fb[agent_key]
        return ("clear", new_remarks)
    fb.setdefault(agent_key, {})[attr_key] = {"rating": rating, "remarks": new_remarks}
    return (rating, new_remarks)


def _redirect_auto_feedback_for_reuse(db, valid_reuse, feedback, snapshots=None):
    """Move auto feedback for reused agents to their ORIGINAL logs.

    - Removes reused agents' entries from ``feedback`` (new log stores no
      copy; display merges source feedback).
    - Writes each auto ``__agent__`` cell to the original log when that log
      has no rating there yet (skip when already rated, manual or auto).
    - Caller commits. Never raises.
    """
    try:
        if not isinstance(feedback, dict) or not valid_reuse:
            return feedback
        from sqlalchemy.orm.attributes import flag_modified as _fm
        snaps = snapshots if isinstance(snapshots, dict) else {}
        for aid, src in list(valid_reuse.items()):
            try:
                # Agent name in the new log.
                aname = ""
                try:
                    s = snaps.get(aid) if isinstance(snaps, dict) else None
                    if isinstance(s, dict) and isinstance(s.get("name"), str) and s.get("name"):
                        aname = s.get("name")
                except Exception:
                    aname = ""
                if not aname:
                    try:
                        ag = None
                        # valid_reuse values are RunLogs; name via _agent_name_for_id fallback.
                        aname = _agent_name_for_id(src, aid, fallback="") or ""
                    except Exception:
                        aname = ""
                if not aname:
                    continue
                cell_map = feedback.get(aname)
                if not isinstance(cell_map, dict):
                    continue
                auto_cell = cell_map.get("__agent__")
                if not isinstance(auto_cell, dict) or auto_cell.get("auto") is not True:
                    # Only auto cells are redirected; drop any legacy copy.
                    try:
                        if aname in feedback:
                            del feedback[aname]
                    except Exception:
                        pass
                    continue
                # Resolve to the ORIGINAL (chain) log.
                try:
                    orig = _resolve_original_log(db, src, aid)
                except Exception:
                    orig = src
                if orig is None:
                    try:
                        del feedback[aname]
                    except Exception:
                        pass
                    continue
                try:
                    orig_name = _agent_name_for_id(orig, aid, fallback=aname)
                except Exception:
                    orig_name = aname
                try:
                    ofb = getattr(orig, "feedback", None) or {}
                    if not isinstance(ofb, dict):
                        ofb = {}
                    else:
                        ofb = {k: dict(v) if isinstance(v, dict) else {} for k, v in ofb.items()}
                except Exception:
                    ofb = {}
                # Skip when the original already has feedback there.
                try:
                    if _cell_is_rated(ofb, orig_name, "__agent__"):
                        pass
                    else:
                        import copy as _cpy
                        ofb.setdefault(orig_name, {})["__agent__"] = _cpy.deepcopy(auto_cell)
                        orig.feedback = ofb
                        try:
                            _fm(orig, "feedback")
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    if aname in feedback:
                        del feedback[aname]
                except Exception:
                    pass
            except Exception:
                continue
        return feedback
    except Exception:
        return feedback


def _resolve_write_target(db, log, agent_name: str):
    """Resolve a feedback write to the original log for reused agents.

    Returns (target_log, target_agent_name, agent_id). Chain-resolved with
    cycle guard; non-reused cells return (log, agent_name, aid-or-None).
    """
    try:
        aid = _agent_id_for_name(log, agent_name or "")
    except Exception:
        aid = None
    if not aid:
        return log, agent_name, None
    try:
        orig = _resolve_original_log(db, log, aid)
    except Exception:
        return log, agent_name, aid
    if orig is None:
        return log, agent_name, aid
    try:
        if getattr(orig, "id", "") == getattr(log, "id", ""):
            return log, agent_name, aid
    except Exception:
        return log, agent_name, aid
    try:
        target_name = _agent_name_for_id(orig, aid, fallback=agent_name or "")
    except Exception:
        target_name = agent_name
    return orig, target_name, aid


def _cell_is_rated(fb: dict, agent_name: str, attribute_name: str) -> bool:
    cell = _cell_existing(fb, agent_name or "agent", attribute_name or "attribute")
    rating = cell.get("rating", "")
    return isinstance(rating, str) and bool(rating.strip())


@router.post("/reuse")
def reuse_run(payload: ReuseRunIn, db: Session = Depends(get_db)):
    source = db.query(RunLog).filter(RunLog.id == payload.log_id).first()
    if not source:
        raise HTTPException(404, "log not found")
    # If the source itself is a reuse, point at the ORIGINAL source.
    original_id = getattr(source, "reused_from_log_id", None) or source.id
    original_created_at = getattr(source, "reused_from_created_at", None) or source.created_at
    agent_snapshot = copy.deepcopy(source.agent_snapshot or {})
    attribute_snapshot = copy.deepcopy(source.attribute_snapshot or {})
    outputs = copy.deepcopy(source.outputs or {})
    usage = copy.deepcopy(source.usage or {})
    filters = copy.deepcopy(source.filters or {})
    requests = copy.deepcopy(source.requests or {})
    try:
        _reuse_cons = copy.deepcopy(getattr(source, "consistency", None) or {})
        if not isinstance(_reuse_cons, dict):
            _reuse_cons = {}
    except Exception:
        _reuse_cons = {}
    # Reuse-from-source copy: identifier sidecars + Fireflies URL ride along.
    try:
        _reuse_ev = copy.deepcopy(getattr(source, "evidence", None) or {})
        if not isinstance(_reuse_ev, dict):
            _reuse_ev = {}
    except Exception:
        _reuse_ev = {}
    try:
        _reuse_pr = copy.deepcopy(getattr(source, "probabilities", None) or {})
        if not isinstance(_reuse_pr, dict):
            _reuse_pr = {}
    except Exception:
        _reuse_pr = {}
    # Reused outputs share ONE source of truth: the new row stores NO
    # feedback copy (display merges the source's feedback with a
    # source_log_id marker). Legacy rows holding a copy are ignored for
    # display in favour of the source (see _merged_feedback).
    _reuse_fb: dict = {}
    try:
        agent_ids = list(agent_snapshot.keys()) if isinstance(agent_snapshot, dict) else []
    except Exception:
        agent_ids = []
    run = Run(input_type=source.input_type or "", input_data=source.input_data or "",
              model=source.model or "", agent_ids=agent_ids, outputs=outputs)
    db.add(run)
    db.commit()
    db.refresh(run)
    log = RunLog(
        run_id=run.id,
        input_type=source.input_type or "",
        input_data=source.input_data or "",
        model=source.model or "",
        agent_snapshot=agent_snapshot,
        attribute_snapshot=attribute_snapshot,
        outputs=outputs,
        feedback=_reuse_fb,
        usage=usage,
        filters=filters,
        requests=requests,
        client=getattr(source, "client", None) or "",
        meeting_type=getattr(source, "meeting_type", None) or "",
        meeting_title=getattr(source, "meeting_title", None) or "",
        reasoning_effort=getattr(source, "reasoning_effort", None) or "",
        provider=getattr(source, "provider", None) or "",
        run_group_id=payload.run_group_id or "",
        reused_from_log_id=original_id,
        reused_from_created_at=original_created_at,
        consistency=_reuse_cons,
        fireflies_url=getattr(source, "fireflies_url", None) or "",
        evidence=_reuse_ev,
        probabilities=_reuse_pr,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return {"id": run.id, "outputs": log.outputs or {}, "usage": log.usage or {},
            "requests": log.requests or {}, "log_id": log.id,
            "run_group_id": log.run_group_id or "", "log": _out_resolved(log, db)}


@router.post("/check-existing", response_model=CheckExistingOut)
def check_existing(payload: CheckExistingIn, db: Session = Depends(get_db)):
    # Resolve the input once (same errors as create_run: 422/404/503).
    # Efforts are already validated by CheckModelSlot; use the first slot's
    # effort for input resolution (resolution is effort-independent).
    first_effort = (payload.models[0].reasoning_effort or "").strip()
    resolved_type, resolved_data, _, _, _ = _resolve_run_input(
        meeting_id=payload.meeting_id, input_type=payload.input_type,
        input_data=payload.input_data, reasoning_effort=first_effort)
    agents = _load_run_agents(db, payload.agent_ids)
    slots: list[dict] = []
    for slot in payload.models:
        model = slot.model
        if not isinstance(model, str) or not model.strip():
            raise HTTPException(422, "model must be non-blank")
        effort = (slot.reasoning_effort or "").strip()
        if effort and effort not in REASONING_EFFORTS:
            raise HTTPException(422, "reasoning_effort must be max|xhigh|high|medium|low|minimal|none")
        prov = (slot.provider or "").strip() if isinstance(slot.provider, str) else ""
        # Per-slot request bodies (messages may differ per model, e.g. the
        # Anthropic fallback appends the schema to the system message).
        _, expected_requests = _build_agent_requests(
            db, agents=agents, input_data=resolved_data,
            model=model, reasoning_effort=effort, provider=prov)
        slot_agents: list[dict] = []
        for agent in agents:
            exp_body = expected_requests.get(agent.id)
            log = _find_existing_log_for_agent(
                db, model=model, reasoning_effort=effort,
                agent_id=agent.id, expected_body=exp_body,
                provider=prov)
            if log is None:
                continue
            created_iso, cost, duration = _source_agent_info(log, agent.id)
            slot_agents.append({
                "agent_id": agent.id,
                "agent_name": agent.name,
                "log_id": log.id,
                "created_at": created_iso,
                "cost_usd": cost,
                "duration_ms": duration,
            })
        slots.append({"model": model, "reasoning_effort": effort,
                      "provider": prov,
                      "agents": slot_agents})
    return {"slots": slots}


def _find_existing_auto_log(
    db: Session, *, model: str, reasoning_effort: str,
    identifier_id: str, expected_body,
    provider: str = "",
) -> RunLog | None:
    """Newest auto RunLog (same model+effort+provider) matching the identifier request.

    - SQL filter on model + reasoning_effort + provider, newest first, limit 300.
    - ``consistency`` must be a dict with ``auto is True``.
    - ``requests[identifier_id]`` must match the would-be-sent identifier
      body (``_request_match_key``; other agents in the log do not matter).
    - ``outputs[identifier_id]`` must exist, be a dict, and have no "_error".
    """
    effort = (reasoning_effort or "").strip()
    prov = (provider or "").strip() if isinstance(provider, str) else ""
    rows = (
        db.query(RunLog)
        .filter(RunLog.model == model, RunLog.reasoning_effort == effort,
                RunLog.provider == prov)
        .order_by(RunLog.created_at.desc())
        .limit(300)
        .all()
    )
    for log in rows:
        try:
            if (getattr(log, "provider", None) or "") != prov:
                continue
        except Exception:
            continue
        try:
            cons = getattr(log, "consistency", None)
        except Exception:
            cons = None
        if not isinstance(cons, dict) or cons.get("auto") is not True:
            continue
        iid = cons.get("identifier_agent_id")
        if not isinstance(iid, str) or not iid:
            iid = identifier_id
        stored_reqs = getattr(log, "requests", None) or {}
        if not isinstance(stored_reqs, dict):
            continue
        stored_body = stored_reqs.get(iid)
        if not isinstance(stored_body, dict):
            # Fall back to the current identifier id (identifier recreation).
            if iid != identifier_id:
                stored_body = stored_reqs.get(identifier_id)
            if not isinstance(stored_body, dict):
                continue
        if _request_match_key(stored_body) != _request_match_key(expected_body):
            continue
        outs = getattr(log, "outputs", None) or {}
        if not isinstance(outs, dict):
            continue
        out = outs.get(iid)
        if not isinstance(out, dict):
            if iid != identifier_id:
                out = outs.get(identifier_id)
            if not isinstance(out, dict):
                continue
        if "_error" in out:
            continue
        return log
    return None


@router.post("/check-existing-auto", response_model=CheckExistingAutoOut)
def check_existing_auto(payload: CheckExistingIn, db: Session = Depends(get_db)):
    """Auto-run variant of check-existing (agent_ids ignored).

    For each model slot (same order): find the newest auto RunLog with the
    same model + reasoning_effort whose identifier request messages equal
    the identifier request /runs/auto would send now, and whose identifier
    output has no "_error". Additionally returns ``agents``: per-agent
    existing-output info for the always-run agents (planned with all
    answers false for this meeting_type), via the same matching as
    check-existing.
    """
    first_effort = (payload.models[0].reasoning_effort or "").strip()
    _, resolved_data, _, task_title, _ = _resolve_run_input(
        meeting_id=payload.meeting_id, input_type=payload.input_type,
        input_data=payload.input_data, reasoning_effort=first_effort)
    identifier = (
        db.query(Agent).filter(Agent.kind == IDENTIFIER_KIND)
        .order_by(Agent.name.asc()).first()
    )
    if identifier is None:
        raise HTTPException(400, "No agent identifier defined")
    ident_id = identifier.id
    try:
        meeting_type = (payload.meeting_type or "").strip() or (task_title or "")
    except Exception:
        meeting_type = ""
    try:
        agents_by_name, attrs_by_agent_name = _agents_by_name_for_planning(db)
    except Exception:
        agents_by_name, attrs_by_agent_name = {}, {}
    try:
        always_plan = plan_auto_agents(
            _all_false_answers(), meeting_type, agents_by_name, attrs_by_agent_name)
    except Exception:
        always_plan = []
    always_agents = []
    always_subsets: dict = {}
    try:
        for entry in always_plan or []:
            ag = entry.get("agent")
            if ag is None:
                continue
            always_agents.append(ag)
            always_subsets[ag.id] = entry.get("attribute_ids")
    except Exception:
        always_agents = []
        always_subsets = {}
    slots: list[dict] = []
    for slot in payload.models:
        model = slot.model
        if not isinstance(model, str) or not model.strip():
            raise HTTPException(422, "model must be non-blank")
        effort = (slot.reasoning_effort or "").strip()
        if effort and effort not in REASONING_EFFORTS:
            raise HTTPException(422, "reasoning_effort must be max|xhigh|high|medium|low|minimal|none")
        prov = (slot.provider or "").strip() if isinstance(slot.provider, str) else ""
        _, expected_requests = _build_agent_requests(
            db, agents=[identifier], input_data=resolved_data,
            model=model, reasoning_effort=effort, provider=prov)
        exp_body = expected_requests.get(ident_id)
        log = _find_existing_auto_log(
            db, model=model, reasoning_effort=effort,
            identifier_id=ident_id, expected_body=exp_body,
            provider=prov)
        slot_agents: list[dict] = []
        try:
            if always_agents:
                _, always_requests = _build_agent_requests(
                    db, agents=always_agents, input_data=resolved_data,
                    model=model, reasoning_effort=effort, provider=prov,
                    attr_subsets=always_subsets)
                for ag in always_agents:
                    try:
                        exp_b = always_requests.get(ag.id)
                        found = _find_existing_log_for_agent(
                            db, model=model, reasoning_effort=effort,
                            agent_id=ag.id, expected_body=exp_b,
                            provider=prov)
                    except Exception:
                        found = None
                    if found is None:
                        continue
                    try:
                        created_iso, cost, duration = _source_agent_info(found, ag.id)
                    except Exception:
                        created_iso, cost, duration = "", None, None
                    slot_agents.append({
                        "agent_id": ag.id,
                        "agent_name": ag.name,
                        "log_id": found.id,
                        "created_at": created_iso,
                        "cost_usd": cost,
                        "duration_ms": duration,
                    })
        except Exception:
            slot_agents = []
        slots.append({"model": model, "reasoning_effort": effort,
                      "provider": prov,
                      "log": (_out_resolved(log, db) if log is not None else None),
                      "agents": slot_agents})
    return {"slots": slots}


@router.post("/feedback-batch")
def batch_feedback(payload: BatchFeedbackIn, db: Session = Depends(get_db)):
    """Batch thumbs/remarks. Reused cells redirect to the ORIGINAL log.

    Returns {ok, applied, skipped, resolved} where resolved parallels
    ``items`` as {run_id, agent_name, attribute_name, resolved_run_id} so the
    frontend optimistic overlay uses the source key. No feedback is copied
    into the new row.
    """
    # Validate everything before mutating: unknown run_id -> 404, nothing applied.
    logs_by_run: dict[str, RunLog] = {}
    for item in payload.items:
        run = db.query(Run).filter(Run.id == item.run_id).first()
        if not run:
            raise HTTPException(404, "run not found")
        log = db.query(RunLog).filter(RunLog.run_id == item.run_id).first()
        if not log:
            raise HTTPException(404, "run not found")
        logs_by_run[item.run_id] = log
    from sqlalchemy.orm.attributes import flag_modified
    applied = 0
    skipped = 0
    resolved: list[dict] = []
    # Cache target logs by id so repeated items share one mutation.
    target_cache: dict[str, RunLog] = {}
    target_fb: dict[str, dict] = {}
    for item in payload.items:
        log = logs_by_run[item.run_id]
        try:
            target, target_name, _aid = _resolve_write_target(db, log, item.agent_name)
        except Exception:
            target, target_name = log, item.agent_name
        try:
            target_run_id = getattr(target, "run_id", "") or item.run_id
        except Exception:
            target_run_id = item.run_id
        resolved.append({"run_id": item.run_id, "agent_name": item.agent_name,
                         "attribute_name": item.attribute_name,
                         "resolved_run_id": target_run_id})
        try:
            tid = getattr(target, "id", "") or ""
        except Exception:
            tid = ""
        if tid not in target_cache:
            target_cache[tid] = target
            try:
                fb0 = dict(getattr(target, "feedback", None) or {}) if isinstance(
                    getattr(target, "feedback", None), dict) else {}
            except Exception:
                fb0 = {}
            target_fb[tid] = {k: dict(v) if isinstance(v, dict) else {} for k, v in fb0.items()}
        fb = target_fb[tid]
        if payload.only_unrated and _cell_is_rated(fb, target_name, item.attribute_name):
            skipped += 1
            continue
        history_rating, history_remarks = _apply_feedback_cell(
            fb, target_name, item.attribute_name, item.rating, item.remarks)
        applied += 1
        # History follows the source (resolved) run.
        db.add(Feedback(run_id=target_run_id, agent_name=target_name,
                        attribute_name=item.attribute_name,
                        rating=history_rating, remarks=history_remarks))
    for tid, target in target_cache.items():
        try:
            target.feedback = target_fb.get(tid, {})
            flag_modified(target, "feedback")
        except Exception:
            pass
    db.commit()
    return {"ok": True, "applied": applied, "skipped": skipped, "resolved": resolved}


@router.post("/auto")
async def auto_run(payload: RunCreate, db: Session = Depends(get_db)):
    """Auto Select Agents: 11 yes/no questions, then deterministic routing.

    One log per call (one model). Body is RunCreate minus agent_ids
    (those are ignored); ``reuse`` ({agent_id: source_log_id}) is honoured
    for any planned agent like create_run. Response shape identical to
    POST /runs. Opt-in streaming via ``stream: true`` returns NDJSON.
    """
    wall_start = perf_counter()
    _auto_stream = bool(getattr(payload, "stream", False))
    provider_requested = (payload.provider or "").strip() if isinstance(payload.provider, str) else ""
    resolved_type, resolved_data, effort, task_title, fireflies_url = _resolve_run_input(
        meeting_id=payload.meeting_id, input_type=payload.input_type,
        input_data=payload.input_data, reasoning_effort=payload.reasoning_effort)
    try:
        chunks = chunk_transcript(resolved_data)
    except Exception:
        chunks = []
    try:
        n_chunks = len(chunks) if isinstance(chunks, list) else 0
    except Exception:
        n_chunks = 0
    identifier = (
        db.query(Agent).filter(Agent.kind == IDENTIFIER_KIND)
        .order_by(Agent.name.asc()).first()
    )
    if identifier is None:
        raise HTTPException(400, "No agent identifier defined")
    ident_id = identifier.id
    try:
        meeting_type = (payload.meeting_type or "").strip() or (task_title or "")
    except Exception:
        meeting_type = ""
    snapshots_ident, requests_ident = _build_agent_requests(
        db, agents=[identifier], input_data=resolved_data,
        model=payload.model, reasoning_effort=effort, provider=provider_requested,
        chunks=chunks)
    ident_body = copy.deepcopy(requests_ident.get(ident_id, {}))
    (parsed_ident, ipt, ict, itt, irt, idur, iserved,
     iactual, idetails, iserved_model) = await _call_single_payload(ident_body)
    evidence_all: dict = {}
    probs_all: dict = {}
    if isinstance(parsed_ident, dict) and "_error" not in parsed_ident:
        try:
            ident_transport = ("decisions"
                               if isinstance(ident_body, dict) and ident_body.get("transport") == "decisions"
                               else "chat")
            _bools, _ev, _pr = split_identifier_result(parsed_ident, ident_transport, n_chunks)
            parsed_ident = normalize_identifier_output(_bools)
            _merge_sidecars(evidence_all, probs_all, ident_id, _ev, _pr)
        except Exception:
            pass
    per_ident = _per_agent_entry(ipt, ict, itt, irt, idur, payload.model, iserved,
                                 iactual, idetails, iserved_model)
    ident_errored = not isinstance(parsed_ident, dict) or "_error" in parsed_ident
    if ident_errored:
        answers = _all_false_answers()
    else:
        try:
            norm = normalize_identifier_output(parsed_ident)
            answers = norm if isinstance(norm, dict) and "_error" not in norm else _all_false_answers()
        except Exception:
            answers = _all_false_answers()
    try:
        agents_by_name, attrs_by_agent_name = _agents_by_name_for_planning(db)
    except Exception:
        agents_by_name, attrs_by_agent_name = {}, {}
    try:
        planned = plan_auto_agents(answers, meeting_type, agents_by_name, attrs_by_agent_name)
    except Exception:
        planned = []
    planned_agents: list[Agent] = []
    attr_subsets: dict = {}
    try:
        for entry in planned or []:
            ag = entry.get("agent")
            if ag is None:
                continue
            planned_agents.append(ag)
            attr_subsets[ag.id] = entry.get("attribute_ids")
    except Exception:
        planned_agents = []
        attr_subsets = {}
    snapshots_sel, requests_sel = _build_agent_requests(
        db, agents=planned_agents, input_data=resolved_data,
        model=payload.model, reasoning_effort=effort, provider=provider_requested,
        attr_subsets=attr_subsets, chunks=chunks)
    snapshots = {**snapshots_ident, **snapshots_sel}
    requests = {**requests_ident, **requests_sel}
    # Stored plan: [{agent_id, agent_name, reasons, scored, attributes: [names]}].
    # Computed before the run so streaming can send start + identifier early.
    plan_stored: list[dict] = []
    try:
        for entry in planned or []:
            ag = entry.get("agent")
            if ag is None:
                continue
            try:
                snap = snapshots.get(ag.id, {})
                names = _snapshot_attr_names(snap)
            except Exception:
                names = []
            plan_stored.append({
                "agent_id": ag.id,
                "agent_name": ag.name,
                "reasons": list(entry.get("reasons", []) or []),
                "scored": bool(entry.get("scored", False)),
                "attributes": list(names),
            })
    except Exception:
        plan_stored = []
    # Per-agent reuse via the shared helper (invalid entries ignored).
    reuse_map = getattr(payload, "reuse", None)
    valid_reuse = _resolve_valid_reuse(
        db, planned_agents, requests, reuse_map, payload.model, effort,
        provider_requested)
    _reused_out, _reused_per, _reused_map = _seed_reused_state(valid_reuse)
    _reuse_ev, _reuse_pr = _copy_reuse_sidecars(valid_reuse)
    for _k, _v in _reuse_ev.items():
        evidence_all.setdefault(_k, _v)
    for _k, _v in _reuse_pr.items():
        probs_all.setdefault(_k, _v)
    outputs: dict = {ident_id: parsed_ident}
    per_agent: dict = {ident_id: per_ident}
    reused_agents: dict = {}
    for _k, _v in _reused_out.items():
        outputs[_k] = _v
    for _k, _v in _reused_per.items():
        per_agent[_k] = _v
    for _k, _v in _reused_map.items():
        reused_agents[_k] = _v
    if _auto_stream:
        return await _auto_run_stream(
            db, payload=payload, identifier=identifier, ident_id=ident_id,
            resolved_type=resolved_type, resolved_data=resolved_data,
            effort=effort, task_title=task_title, meeting_type=meeting_type,
            snapshots_ident=snapshots_ident, requests_ident=requests_ident,
            parsed_ident=parsed_ident, per_ident=per_ident, answers=answers,
            planned=planned, planned_agents=planned_agents,
            attr_subsets=attr_subsets, snapshots_sel=snapshots_sel,
            requests_sel=requests_sel, snapshots=snapshots, requests=requests,
            valid_reuse=valid_reuse, outputs=outputs, per_agent=per_agent,
            reused_agents=reused_agents, plan_stored=plan_stored,
            provider_requested=provider_requested,
            filters=payload.filters if isinstance(payload.filters, dict) else {},
            wall_start=wall_start, n_chunks=n_chunks,
            evidence_all=evidence_all, probs_all=probs_all,
            fireflies_url=fireflies_url)
    fresh_agents = [a for a in planned_agents if a.id not in valid_reuse]
    fresh_requests = {a.id: requests[a.id] for a in fresh_agents if a.id in requests}
    if fresh_agents:
        outputs_sel, per_agent_sel, ev_sel, pr_sel = await _execute_agents(
            db, fresh_agents, fresh_requests, payload.model, n_chunks=n_chunks)
        for k, v in outputs_sel.items():
            outputs[k] = v
        for k, v in per_agent_sel.items():
            per_agent[k] = v
        for k, v in ev_sel.items():
            evidence_all.setdefault(k, v)
        for k, v in pr_sel.items():
            probs_all.setdefault(k, v)
    agents = [identifier] + planned_agents
    agent_ids = [ident_id] + [a.id for a in planned_agents]
    try:
        consistency = compute_consistency(answers, outputs, ident_id, plan_stored)
    except Exception:
        consistency = {"auto": True, "version": 2, "score": None,
                       "identifier_agent_id": ident_id,
                       "answers": dict(answers) if isinstance(answers, dict) else {},
                       "plan": plan_stored, "agents": {}}
    try:
        feedback = apply_auto_feedback({}, consistency)
    except Exception:
        feedback = {}
    # Reused agents share the SOURCE log's feedback: move auto cells there
    # (or skip when already rated), never store a copy on the new row.
    try:
        feedback = _redirect_auto_feedback_for_reuse(
            db, valid_reuse, feedback, snapshots=snapshots)
    except Exception:
        pass
    try:
        prompt_price, completion_price = await get_model_pricing(payload.model)
    except Exception:
        prompt_price, completion_price = None, None
    # Price ONLY fresh (identifier + fresh planned); reused keep source numbers.
    try:
        _fill_pricing(per_agent, prompt_price, completion_price,
                      skip_ids=set(valid_reuse.keys()))
    except Exception:
        pass
    totals = _totals_from_per_agent(per_agent, prompt_price, completion_price)
    wall_ms = (perf_counter() - wall_start) * 1000.0
    try:
        for aid in valid_reuse:
            d = per_agent.get(aid, {}).get("duration_ms")
            if isinstance(d, (int, float)) and d > wall_ms:
                wall_ms = float(d)
    except Exception:
        pass
    usage = _build_usage(per_agent, payload.model, totals, wall_ms, reused_agents,
                       provider_requested=provider_requested)
    filters = payload.filters if isinstance(payload.filters, dict) else {}
    client_name = (payload.client or "").strip()
    meeting_title = (payload.meeting_title or "").strip() or task_title
    # meeting_type already resolved above (payload or task_title).
    run = Run(input_type=resolved_type, input_data=resolved_data, model=payload.model,
              agent_ids=agent_ids, outputs=outputs)
    db.add(run)
    db.commit()
    db.refresh(run)
    log = RunLog(run_id=run.id, input_type=run.input_type, input_data=run.input_data, model=run.model,
                   agent_snapshot=snapshots, attribute_snapshot=snapshots, outputs=outputs, feedback=feedback,
                   usage=usage, filters=filters, requests=requests,
                   client=client_name, meeting_type=meeting_type, meeting_title=meeting_title,
                   reasoning_effort=effort, provider=provider_requested,
                   run_group_id=payload.run_group_id or "",
                   reused_from_log_id="", reused_from_created_at=None,
                   consistency=consistency,
                   fireflies_url=fireflies_url,
                   evidence=evidence_all, probabilities=probs_all)
    db.add(log)
    db.commit()
    db.refresh(log)
    return {"id": run.id, "outputs": outputs, "usage": usage, "requests": requests,
            "log_id": log.id, "run_group_id": log.run_group_id or "", "log": _out_resolved(log, db)}


@router.post("/logs/{log_id}/retry-agent")
async def retry_agent(log_id: str, payload: RetryAgentIn, db: Session = Depends(get_db)):
    """Re-run one agent's stored request and patch the same log in place."""
    from sqlalchemy.orm.attributes import flag_modified

    log = db.query(RunLog).filter(RunLog.id == log_id).first()
    if not log:
        raise HTTPException(404, "log not found")
    agent_id = (payload.agent_id or "").strip() if isinstance(payload.agent_id, str) else ""
    stored_requests = log.requests if isinstance(getattr(log, "requests", None), dict) else {}
    if not agent_id or not isinstance(stored_requests.get(agent_id), dict):
        raise HTTPException(400, "agent_id has no stored request")
    stored_body = copy.deepcopy(stored_requests[agent_id])
    # Identifier detection (also used for normalisation below).
    try:
        snap_kind = ""
        snaps = log.agent_snapshot if isinstance(getattr(log, "agent_snapshot", None), dict) else {}
        snap = snaps.get(agent_id) if isinstance(snaps, dict) else None
        if isinstance(snap, dict):
            snap_kind = str(snap.get("kind", "") or "")
        is_ident = snap_kind == IDENTIFIER_KIND
        if not snap_kind:
            row = db.query(Agent).filter(Agent.id == agent_id).first()
            if row is not None:
                is_ident = is_identifier(row)
    except Exception:
        is_ident = False
    try:
        retry_is_decision = bool(is_decision_model(log.model or ""))
    except Exception:
        retry_is_decision = False
    if (retry_is_decision and not is_ident
            and not (isinstance(stored_body, dict)
                     and stored_body.get("transport") == "decisions")):
        # Decision models serve the identifier agent only: fail without an
        # API call (no spend), same per-agent entry shape as other errors.
        (parsed, pt, ct, tt, rt, dur, served,
         actual_cost, cost_details, served_model) = (
            {"_error": "decision models support the identifier agent only"},
            0, 0, 0, 0, 0.0, "", None, None, "")
    else:
        (parsed, pt, ct, tt, rt, dur, served,
         actual_cost, cost_details, served_model) = await _call_single_payload(stored_body)
    # Identifier split + normalisation when the retried agent is the
    # identifier (same as every other identifier consumer). Sidecars merge
    # into the log columns without touching other agents' keys; the
    # Fireflies URL is kept as-is.
    try:
        if is_ident and isinstance(parsed, dict) and "_error" not in parsed:
            retry_transport = ("decisions"
                               if isinstance(stored_body, dict) and stored_body.get("transport") == "decisions"
                               else "chat")
            try:
                retry_n = len(chunk_transcript(getattr(log, "input_data", None) or ""))
            except Exception:
                retry_n = 0
            _rbools, _rev, _rpr = split_identifier_result(parsed, retry_transport, retry_n)
            parsed = normalize_identifier_output(_rbools)
            try:
                log_ev = getattr(log, "evidence", None)
                log_ev = dict(log_ev) if isinstance(log_ev, dict) else {}
                log_pr = getattr(log, "probabilities", None)
                log_pr = dict(log_pr) if isinstance(log_pr, dict) else {}
                _merge_sidecars(log_ev, log_pr, agent_id, _rev, _rpr)
                log.evidence = log_ev
                log.probabilities = log_pr
                flag_modified(log, "evidence")
                flag_modified(log, "probabilities")
            except Exception:
                pass
    except Exception:
        pass
    outputs = log.outputs if isinstance(getattr(log, "outputs", None), dict) else {}
    outputs = dict(outputs)
    outputs[agent_id] = parsed
    log.outputs = outputs
    flag_modified(log, "outputs")
    usage = log.usage if isinstance(getattr(log, "usage", None), dict) else {}
    usage = copy.deepcopy(usage) if isinstance(usage, dict) else {}
    per_agent = usage.get("per_agent", {})
    if not isinstance(per_agent, dict):
        per_agent = {}
    else:
        per_agent = dict(per_agent)
    try:
        prompt_price, completion_price = await get_model_pricing(log.model or "")
    except Exception:
        prompt_price, completion_price = None, None
    entry = _per_agent_entry(pt, ct, tt, rt, dur, log.model or "", served,
                                 actual_cost, cost_details, served_model)
    _fill_pricing({agent_id: entry}, prompt_price, completion_price)
    # Drop any reused markers on the retried agent.
    for k in ("reused_from_log_id", "reused_from_created_at"):
        try:
            entry.pop(k, None)
        except Exception:
            pass
    per_agent[agent_id] = entry
    reused_agents = usage.get("reused_agents", {})
    if isinstance(reused_agents, dict) and agent_id in reused_agents:
        reused_agents = dict(reused_agents)
        reused_agents.pop(agent_id, None)
    else:
        reused_agents = dict(reused_agents) if isinstance(reused_agents, dict) else {}
    totals = _totals_from_per_agent(per_agent)
    old_dur = usage.get("duration_ms", 0)
    try:
        new_dur = float(dur)
        old_f = float(old_dur) if isinstance(old_dur, (int, float)) else 0.0
        duration_ms = old_f if old_f >= new_dur else new_dur
    except Exception:
        duration_ms = dur
    usage["prompt_tokens"] = totals["prompt_tokens"]
    usage["completion_tokens"] = totals["completion_tokens"]
    usage["total_tokens"] = totals["total_tokens"]
    usage["reasoning_tokens"] = totals["reasoning_tokens"]
    usage["cost_usd"] = totals["cost_usd"]
    usage["input_cost_usd"] = totals["input_cost_usd"]
    usage["output_cost_usd"] = totals["output_cost_usd"]
    usage["duration_ms"] = duration_ms
    usage["per_agent"] = per_agent
    usage["reused_agents"] = reused_agents
    if "model" not in usage:
        usage["model"] = log.model or ""
    if "provider_requested" not in usage:
        try:
            usage["provider_requested"] = getattr(log, "provider", None) or ""
        except Exception:
            usage["provider_requested"] = ""
    log.usage = usage
    flag_modified(log, "usage")
    # Clear feedback for the retried agent (it rated the old output).
    fb = log.feedback if isinstance(getattr(log, "feedback", None), dict) else {}
    fb = {k: dict(v) if isinstance(v, dict) else {} for k, v in fb.items()} if isinstance(fb, dict) else {}
    agent_name = ""
    try:
        snaps2 = log.agent_snapshot if isinstance(getattr(log, "agent_snapshot", None), dict) else {}
        s2 = snaps2.get(agent_id) if isinstance(snaps2, dict) else None
        if isinstance(s2, dict) and isinstance(s2.get("name"), str) and s2.get("name"):
            agent_name = s2.get("name")
    except Exception:
        agent_name = ""
    if not agent_name:
        try:
            row2 = db.query(Agent).filter(Agent.id == agent_id).first()
            if row2 is not None:
                agent_name = getattr(row2, "name", "") or ""
        except Exception:
            pass
    if agent_name and agent_name in fb:
        try:
            del fb[agent_name]
        except Exception:
            pass
    # Recompute consistency v2 + auto feedback when this is a v2 auto log.
    # Old logs (no version) keep consistency as-is.
    try:
        cons_existing = getattr(log, "consistency", None)
    except Exception:
        cons_existing = None
    if isinstance(cons_existing, dict) and bool(cons_existing):
        try:
            is_auto = cons_existing.get("auto") is True
        except Exception:
            is_auto = False
        if is_auto:
            try:
                ver = cons_existing.get("version")
            except Exception:
                ver = None
            if ver == 2:
                try:
                    stored_answers = cons_existing.get("answers", {})
                    stored_plan = cons_existing.get("plan", [])
                    stored_iid = cons_existing.get("identifier_agent_id", "")
                    if not isinstance(stored_answers, dict):
                        stored_answers = {}
                    if not isinstance(stored_plan, list):
                        stored_plan = []
                    if not isinstance(stored_iid, str):
                        stored_iid = ""
                    new_cons = compute_consistency(
                        stored_answers, dict(log.outputs or {}),
                        stored_iid, stored_plan)
                    # Preserve identifier id when recompute cannot find it.
                    try:
                        if not new_cons.get("identifier_agent_id"):
                            old_iid = cons_existing.get("identifier_agent_id", "")
                            if isinstance(old_iid, str) and old_iid:
                                new_cons["identifier_agent_id"] = old_iid
                    except Exception:
                        pass
                    log.consistency = new_cons
                    flag_modified(log, "consistency")
                    fb = apply_auto_feedback(fb, new_cons)
                except Exception:
                    pass
            else:
                # Old shape without version: leave consistency as it is.
                pass
    log.feedback = fb
    flag_modified(log, "feedback")
    # Mirror the fresh output onto the Run row.
    try:
        run_row = db.query(Run).filter(Run.id == log.run_id).first()
        if run_row is not None:
            outs = run_row.outputs if isinstance(getattr(run_row, "outputs", None), dict) else {}
            outs = dict(outs) if isinstance(outs, dict) else {}
            outs[agent_id] = parsed
            run_row.outputs = outs
            flag_modified(run_row, "outputs")
    except Exception:
        pass
    db.commit()
    db.refresh(log)
    return {"log": _out_resolved(log, db)}


@router.get("/{run_id}", response_model=RunDetail)
def get_run(run_id: str, db: Session = Depends(get_db)):
    r = db.query(Run).filter(Run.id == run_id).first()
    if not r:
        raise HTTPException(404, "run not found")
    return RunDetail(id=r.id, input_type=r.input_type, model=r.model,
                     agent_ids=r.agent_ids or [], created_at=r.created_at,
                     input_data=r.input_data, outputs=r.outputs or {})


@router.post("/{run_id}/feedback")
def add_feedback(run_id: str, payload: FeedbackCreate, db: Session = Depends(get_db)):
    """Single-cell thumbs/remarks. Reused cells redirect to the ORIGINAL log.

    Returns {ok, resolved_run_id} so the frontend overlay uses the source key.
    """
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(404, "run not found")
    if payload.rating not in ("up", "down", ""):
        raise HTTPException(422, "rating must be up|down|''")
    log = db.query(RunLog).filter(RunLog.run_id == run_id).first()
    resolved_run_id = run_id
    target_name = payload.agent_name
    if log:
        try:
            target, tname, _aid = _resolve_write_target(db, log, payload.agent_name)
        except Exception:
            target, tname = log, payload.agent_name
        target_name = tname
        try:
            resolved_run_id = getattr(target, "run_id", "") or run_id
        except Exception:
            resolved_run_id = run_id
        fb = dict(getattr(target, "feedback", None) or {}) if isinstance(
            getattr(target, "feedback", None), dict) else {}
        fb = {k: dict(v) if isinstance(v, dict) else {} for k, v in fb.items()}
        history_rating, history_remarks = _apply_feedback_cell(
            fb, target_name, payload.attribute_name, payload.rating, payload.remarks)
        target.feedback = fb
        # Re-assign so SQLAlchemy flags the JSON column dirty on every backend.
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(target, "feedback")
    else:
        existing_remarks = ""
        new_remarks = existing_remarks if payload.remarks is None else (payload.remarks or "")
        history_rating = "clear" if payload.rating == "" else payload.rating
        history_remarks = new_remarks
    db.add(Feedback(run_id=resolved_run_id, agent_name=target_name, attribute_name=payload.attribute_name,
                    rating=history_rating, remarks=history_remarks))
    db.commit()
    return {"ok": True, "resolved_run_id": resolved_run_id}


@router.get("/{run_id}/feedback", response_model=list[FeedbackOut])
def list_feedback(run_id: str, db: Session = Depends(get_db)):
    if not db.query(Run).filter(Run.id == run_id).first():
        raise HTTPException(404, "run not found")
    rows = db.query(Feedback).filter(Feedback.run_id == run_id).order_by(Feedback.created_at.asc()).all()
    return [FeedbackOut(id=r.id, run_id=r.run_id, agent_name=r.agent_name,
                        attribute_name=r.attribute_name, rating=r.rating, remarks=r.remarks) for r in rows]
