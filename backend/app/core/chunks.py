"""Numbered speaker-turn chunks of a (scrubbed) transcript.

Both identifier transports — the chat path (Opus) and the decisions path
(Jev) — see the SAME numbered chunks in a single API call, so per-answer
chunk evidence is comparable across models.

- ``split_paras``: split into speaker-turn paragraphs (a paragraph is never
  split). A new paragraph starts at a Fireflies-style ``Name:`` turn line;
  when no speaker pattern matches anywhere, fall back to blank-line splits.
- ``chunk_transcript``: pack paras evenly into at most ``max_chunks`` chunks
  (``per_chunk = ceil(len(paras) / max_chunks)``). Deterministic.
- ``format_chunks_numbered``: the exact user-visible text both models get:
  ``"[1] <text>\\n\\n[2] <text>..."``.

Pure functions (no I/O, no secrets).
"""

import math
import re

# Tunable cap: a run never sends more than this many numbered chunks.
MAX_TRANSCRIPT_CHUNKS = 50

# Fireflies-style speaker-turn start: a line beginning with a name followed
# by a colon, e.g. "Anita: Hello" or "Bob Smith: ...". The name starts with
# a letter (so timestamps like "10:30 ..." never split) and the colon is
# followed by whitespace or end-of-line (so "key:value" prose never splits).
_SPEAKER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 .'\-]{0,60}:(?:\s|$)")

# Blank-line separator for the no-speaker fallback.
_BLANK_LINE_RE = re.compile(r"\n\s*\n")


def split_paras(text: str) -> list[str]:
    """Split transcript text into speaker-turn paragraphs.

    Lines matching the ``Name:`` turn pattern start a new paragraph; every
    other line (including blank lines) joins the current paragraph, so a
    paragraph is never split. Leading/trailing whitespace is stripped and
    empty paragraphs are dropped. When NO line matches the speaker pattern,
    falls back to splitting on blank lines. Pure and deterministic.
    """
    if not isinstance(text, str):
        return []
    if not text.strip():
        return []
    lines = text.split("\n")
    paras: list[str] = []
    current: list[str] = []
    saw_speaker = False
    for line in lines:
        try:
            is_turn = bool(_SPEAKER_RE.match(line.strip()))
        except Exception:
            is_turn = False
        if is_turn:
            saw_speaker = True
            if current:
                chunk = "\n".join(current).strip()
                if chunk:
                    paras.append(chunk)
                current = []
            current.append(line.strip())
        else:
            current.append(line)
    if current:
        chunk = "\n".join(current).strip()
        if chunk:
            paras.append(chunk)
    if not saw_speaker:
        # No Fireflies-style turns: split on blank lines instead.
        try:
            paras = [p.strip() for p in _BLANK_LINE_RE.split(text.strip())]
        except Exception:
            paras = [text.strip()]
        paras = [p for p in paras if p]
    return paras


def chunk_transcript(text: str, max_chunks: int = MAX_TRANSCRIPT_CHUNKS) -> list[dict]:
    """Pack speaker-turn paragraphs into at most ``max_chunks`` chunks.

    ``per_chunk = ceil(len(paras) / max_chunks)`` paras per chunk ("add more
    paras to the same chunk"), sliced evenly in order. Returns
    ``[{"n": 1-based int, "text": str}]``. Empty/blank text (or no usable
    paras) gives ``[]``. Deterministic: same input -> identical output.
    """
    if not isinstance(text, str):
        return []
    if not text.strip():
        return []
    try:
        cap = int(max_chunks)
    except Exception:
        cap = MAX_TRANSCRIPT_CHUNKS
    if cap < 1:
        cap = 1
    paras = split_paras(text)
    if not paras:
        return []
    per_chunk = int(math.ceil(len(paras) / cap))
    if per_chunk < 1:
        per_chunk = 1
    chunks: list[dict] = []
    for i in range(0, len(paras), per_chunk):
        group = paras[i:i + per_chunk]
        chunks.append({"n": len(chunks) + 1, "text": "\n\n".join(group)})
    return chunks


def format_chunks_numbered(chunks) -> str:
    """Render chunks as ``"[1] <text>\\n\\n[2] <text>..."`` (both models see this)."""
    try:
        items = list(chunks or [])
    except Exception:
        return ""
    parts: list[str] = []
    for c in items:
        try:
            if isinstance(c, dict):
                n = c.get("n")
                t = c.get("text", "")
            else:
                n, t = None, ""
            parts.append(f"[{int(n)}] {t if isinstance(t, str) else ''}")
        except Exception:
            continue
    return "\n\n".join(parts)
