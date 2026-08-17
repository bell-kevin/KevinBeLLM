# SPDX-License-Identifier: AGPL-3.0-or-later
"""Plain-text and Markdown chunking for the local document index.

Deliberately dependency-free. Binary formats such as PDF and DOCX are not
supported: extracting them needs a parser with a real attack surface, and the
index is the one place where private document text is written to disk.
"""

from __future__ import annotations

import re


# Text-like suffixes only. Anything not listed is skipped rather than guessed at.
TEXT_SUFFIXES = frozenset(
    {
        ".md",
        ".markdown",
        ".txt",
        ".text",
        ".rst",
        ".org",
        ".csv",
        ".tsv",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".log",
    }
)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANK_RUN = re.compile(r"\n{3,}")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")
_ATX_HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$")


def normalize_text(raw: str) -> str:
    """Return document text with line endings, control bytes, and blank runs tamed."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    text = _TRAILING_SPACE.sub("\n", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()


def document_title(text: str, fallback: str) -> str:
    """Prefer a Markdown H1/H2, then the first non-empty line, then the filename."""
    for line in text.split("\n")[:40]:
        stripped = line.strip()
        if not stripped:
            continue
        heading = _ATX_HEADING.match(stripped)
        if heading:
            title = heading.group(1).strip()
            if title:
                return title[:200]
        break
    for line in text.split("\n")[:40]:
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return fallback[:200]


def _hard_split(paragraph: str, target_chars: int) -> list[str]:
    """Split one oversized paragraph on word boundaries, never mid-word."""
    pieces: list[str] = []
    remaining = paragraph
    while len(remaining) > target_chars:
        window = remaining[:target_chars]
        cut = window.rfind(" ")
        # A single unbroken run longer than the target (a URL, a base64 blob)
        # has no boundary to find, so take the whole window.
        if cut <= target_chars // 2:
            cut = target_chars
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        pieces.append(remaining)
    return [piece for piece in pieces if piece]


def _overlap_tail(chunk: str, overlap_chars: int) -> str:
    """Return the chunk's trailing context, snapped forward to a word boundary."""
    if overlap_chars <= 0 or len(chunk) <= overlap_chars:
        return ""
    tail = chunk[-overlap_chars:]
    space = tail.find(" ")
    if space != -1:
        tail = tail[space + 1 :]
    return tail.strip()


def chunk_text(
    text: str,
    *,
    target_chars: int = 1_200,
    overlap_chars: int = 200,
    max_chunks: int = 5_000,
) -> list[str]:
    """Split normalized text into overlapping, paragraph-aligned chunks.

    Overlap keeps a passage that straddles a paragraph boundary retrievable
    from either side. Chunks may exceed ``target_chars`` by up to the overlap.
    """
    if target_chars < 100:
        raise ValueError("target_chars must be at least 100")
    overlap_chars = max(0, min(overlap_chars, target_chars // 4))

    text = text.strip()
    if not text:
        return []

    units: list[str] = []
    for paragraph in _PARAGRAPH_BREAK.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= target_chars:
            units.append(paragraph)
        else:
            units.extend(_hard_split(paragraph, target_chars))

    chunks: list[str] = []
    buffer = ""
    for unit in units:
        if not buffer:
            buffer = unit
            continue
        if len(buffer) + 2 + len(unit) <= target_chars:
            buffer = f"{buffer}\n\n{unit}"
            continue
        chunks.append(buffer)
        if len(chunks) >= max_chunks:
            return chunks
        tail = _overlap_tail(buffer, overlap_chars)
        buffer = f"{tail}\n\n{unit}" if tail else unit
    if buffer:
        chunks.append(buffer)
    return chunks[:max_chunks]
