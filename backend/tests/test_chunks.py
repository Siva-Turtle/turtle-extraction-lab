"""Coverage: numbered speaker-turn transcript chunks (app.core.chunks).

Pure only — no network, no DB, no secrets.
"""

from app.core.chunks import (
    MAX_TRANSCRIPT_CHUNKS,
    chunk_transcript,
    format_chunks_numbered,
    split_paras,
)


def test_max_chunks_constant():
    assert MAX_TRANSCRIPT_CHUNKS == 100


def test_split_paras_fixture_style_turns():
    # Fixture-style Fireflies turns: one paragraph per speaker turn.
    paras = split_paras("Anita: Hello\nBob:  extra spaces kept  ")
    assert paras == ["Anita: Hello", "Bob:  extra spaces kept"]


def test_split_paras_never_splits_a_paragraph():
    # Turn-per-line: every non-empty line is its own para (a line is never
    # split). Adjusted for turn-per-line behaviour — continuation lines no
    # longer merge into the previous turn.
    text = "Anita: first line\nstill Anita talking\n\nBob: reply\nmore from Bob"
    paras = split_paras(text)
    assert paras == ["Anita: first line", "still Anita talking", "Bob: reply", "more from Bob"]


def test_split_paras_timestamp_lines_do_not_split():
    # Turn-per-line: timestamp continuation lines are their own paras —
    # adjusted for turn-per-line behaviour (previously merged).
    paras = split_paras("Anita: see you at\n10:30 tomorrow\nBob: ok")
    assert paras == ["Anita: see you at", "10:30 tomorrow", "Bob: ok"]


def test_split_paras_fallback_blank_lines():
    # No speaker pattern anywhere: split on blank lines.
    paras = split_paras("plain line one\n\nplain line two\n\nplain line three")
    assert paras == ["plain line one", "plain line two", "plain line three"]


def test_split_paras_fallback_single_block():
    assert split_paras("just one line, no turns") == ["just one line, no turns"]


def test_split_paras_blank_and_non_string():
    assert split_paras("") == []
    assert split_paras("   \n  ") == []
    assert split_paras(None) == []  # type: ignore[arg-type]


def test_chunk_transcript_even_packing_under_cap():
    paras = [f"Speaker{i}: line {i}" for i in range(7)]
    chunks = chunk_transcript("\n".join(paras))
    # 7 paras, cap 100 -> one para per chunk.
    assert [c["n"] for c in chunks] == [1, 2, 3, 4, 5, 6, 7]
    assert [c["text"] for c in chunks] == paras


def test_chunk_transcript_many_paras_capped_all_text_preserved():
    paras = [f"Speaker{i}: line {i}" for i in range(250)]
    chunks = chunk_transcript("\n".join(paras))
    assert len(chunks) <= 100
    assert [c["n"] for c in chunks] == list(range(1, len(chunks) + 1))
    # Every para's text survives verbatim inside exactly one chunk.
    for p in paras:
        hits = sum(1 for c in chunks if p in c["text"])
        assert hits == 1
    # ceil(250/100) = 3 paras per chunk -> 84 chunks (last holds 1).
    assert len(chunks) == 84


def test_chunk_transcript_custom_cap():
    paras = [f"A{i}: x" for i in range(10)]
    chunks = chunk_transcript("\n".join(paras), max_chunks=3)
    assert len(chunks) == 3
    assert [c["n"] for c in chunks] == [1, 2, 3]


def test_chunk_transcript_blank():
    assert chunk_transcript("") == []
    assert chunk_transcript("  \n ") == []


def test_chunk_transcript_deterministic():
    text = "Anita: one\nBob: two\nAnita: three\nBob: four"
    assert chunk_transcript(text) == chunk_transcript(text)
    assert chunk_transcript(text, max_chunks=2) == chunk_transcript(text, max_chunks=2)


def test_format_chunks_numbered_shape():
    out = format_chunks_numbered([{"n": 1, "text": "a"}, {"n": 2, "text": "b"}])
    assert out == "[1] a\n\n[2] b"
    assert format_chunks_numbered([]) == ""


def test_format_then_chunk_round_trip_markers():
    chunks = chunk_transcript("Anita: one\nBob: two\nCara: three")
    out = format_chunks_numbered(chunks)
    for i in range(1, len(chunks) + 1):
        assert f"[{i}]" in out


def test_split_paras_timestamped_turn_per_line():
    # Regression: live-shaped transcript — every line is one
    # "[mm:ss] Name: text" turn with zero blank lines.
    lines = [
        f"[{i // 60:02d}:{i % 60:02d}] Advisor: hello line {i}"
        if i % 2 == 0
        else f"[{i // 60:02d}:{i % 60:02d}] Client: reply line {i}"
        for i in range(120)
    ]
    text = "\n".join(lines)
    paras = split_paras(text)
    assert len(paras) == len(lines)
    assert paras == [line.strip() for line in lines]
    # Packing: capped at <= 100 chunks, all text preserved, 1-based numbering.
    chunks = chunk_transcript(text)
    assert len(chunks) <= 100
    assert [c["n"] for c in chunks] == list(range(1, len(chunks) + 1))
    for p in paras:
        hits = sum(1 for c in chunks if p in c["text"])
        assert hits == 1
