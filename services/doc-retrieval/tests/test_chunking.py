# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import pytest

from app.chunking import chunk_text, document_title, normalize_text


def test_normalize_text_removes_control_bytes_and_collapses_blank_runs() -> None:
    raw = "Title\r\n\r\n\r\n\r\nBody\x00 text\x07   \nMore  \n"

    assert normalize_text(raw) == "Title\n\nBody text\nMore"


def test_normalize_text_keeps_paragraph_structure() -> None:
    assert normalize_text("one\n\ntwo") == "one\n\ntwo"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# Roof warranty\n\nBody", "Roof warranty"),
        ("## Second level ##\n\nBody", "Second level"),
        ("Just a first line\nsecond line", "Just a first line"),
        ("", "fallback-name"),
    ],
)
def test_document_title_prefers_a_heading_then_the_first_line(text, expected) -> None:
    assert document_title(text, "fallback-name") == expected


def test_chunking_keeps_paragraphs_together_under_the_target() -> None:
    text = "\n\n".join(["Paragraph one.", "Paragraph two.", "Paragraph three."])

    assert chunk_text(text, target_chars=200, overlap_chars=0) == [text]


def test_chunking_splits_at_paragraph_boundaries_with_overlap() -> None:
    paragraphs = [f"Paragraph {index} " + "word " * 40 for index in range(6)]
    chunks = chunk_text("\n\n".join(paragraphs), target_chars=500, overlap_chars=100)

    assert len(chunks) > 1
    # Overlap exists so a passage spanning a boundary stays retrievable from
    # either side.
    for earlier, later in zip(chunks, chunks[1:]):
        tail = earlier[-60:].strip()
        assert any(word in later for word in tail.split()[:3])


def test_an_oversized_paragraph_is_split_on_word_boundaries() -> None:
    paragraph = " ".join(f"token{index}" for index in range(500))
    chunks = chunk_text(paragraph, target_chars=300, overlap_chars=0)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 300
    # No word may be cut in half by the splitter.
    rejoined = " ".join(chunks).split()
    assert all(word.startswith("token") for word in rejoined)


def test_an_unbroken_run_longer_than_the_target_still_terminates() -> None:
    chunks = chunk_text("x" * 2_000, target_chars=250, overlap_chars=0)

    assert len(chunks) == 8
    assert "".join(chunks) == "x" * 2_000


def test_chunking_respects_its_hard_ceiling() -> None:
    paragraphs = "\n\n".join("word " * 60 for _ in range(200))
    chunks = chunk_text(paragraphs, target_chars=300, overlap_chars=50, max_chunks=5)

    assert len(chunks) == 5


def test_empty_input_produces_no_chunks() -> None:
    assert chunk_text("   \n\n  ") == []


def test_a_tiny_target_is_rejected() -> None:
    with pytest.raises(ValueError, match="target_chars"):
        chunk_text("text", target_chars=10)
