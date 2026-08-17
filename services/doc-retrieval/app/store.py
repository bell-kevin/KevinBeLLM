# SPDX-License-Identifier: AGPL-3.0-or-later
"""On-disk index format, validation, and dense vector search.

The index holds private document text, so every file it writes is mode 0600
inside a mode 0700 directory. Search reads only what was loaded at startup: a
request never touches the filesystem, so there is no request-time path handling.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import INDEX_FORMAT_VERSION


MANIFEST_NAME = "manifest.json"
CHUNKS_NAME = "chunks.jsonl"
VECTORS_NAME = "vectors.f32"

MAX_DIMENSION = 8_192


class IndexUnavailable(RuntimeError):
    """The index is missing, unreadable, or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class Chunk:
    document: str
    title: str
    ordinal: int
    text: str


@dataclass(frozen=True, slots=True)
class LoadedIndex:
    dimension: int
    built_at: str
    embedding_model: str
    document_count: int
    chunks: tuple[Chunk, ...]
    vectors: np.ndarray

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)


def normalize_rows(matrix: np.ndarray) -> np.ndarray:
    """L2-normalize each row so a dot product is exactly cosine similarity.

    A zero row would otherwise divide by zero and poison every later score.
    """
    vectors = np.ascontiguousarray(matrix, dtype=np.float32)
    if vectors.ndim != 2:
        raise ValueError("expected a two-dimensional embedding matrix")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vectors / norms).astype(np.float32, copy=False)


def write_index(
    index_dir: Path,
    *,
    embedding_model: str,
    built_at: str,
    document_count: int,
    chunks: list[Chunk],
    vectors: np.ndarray,
) -> None:
    """Write a complete index, then swap it into place.

    The service loads only at startup, so a brief swap window cannot be
    observed by a running search. A partially written index is never promoted.
    """
    normalized = normalize_rows(vectors)
    if normalized.shape[0] != len(chunks):
        raise ValueError("vector count does not match chunk count")
    if not 1 <= normalized.shape[1] <= MAX_DIMENSION:
        raise ValueError("embedding dimension is out of range")

    index_dir = index_dir.expanduser()
    staging = index_dir.parent / f"{index_dir.name}.new"
    previous = index_dir.parent / f"{index_dir.name}.old"
    index_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(mode=0o700)

    manifest = {
        "format_version": INDEX_FORMAT_VERSION,
        "built_at": built_at,
        "embedding_model": embedding_model,
        "dimension": int(normalized.shape[1]),
        "chunk_count": len(chunks),
        "document_count": document_count,
    }
    manifest_path = staging / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    chunks_path = staging / CHUNKS_NAME
    with chunks_path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(
                json.dumps(
                    {
                        "document": chunk.document,
                        "title": chunk.title,
                        "ordinal": chunk.ordinal,
                        "text": chunk.text,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    vectors_path = staging / VECTORS_NAME
    # Pin little-endian on write and read so an index file stays portable
    # instead of silently depending on the builder's native byte order.
    normalized.astype("<f4", copy=False).tofile(vectors_path)
    for path in (manifest_path, chunks_path, vectors_path):
        os.chmod(path, 0o600)

    shutil.rmtree(previous, ignore_errors=True)
    if index_dir.exists():
        index_dir.rename(previous)
    staging.rename(index_dir)
    shutil.rmtree(previous, ignore_errors=True)


def load_index(index_dir: Path, *, max_chunks: int) -> LoadedIndex:
    """Load and fully validate an index, or raise IndexUnavailable."""
    index_dir = index_dir.expanduser()
    manifest_path = index_dir / MANIFEST_NAME
    chunks_path = index_dir / CHUNKS_NAME
    vectors_path = index_dir / VECTORS_NAME
    for path in (manifest_path, chunks_path, vectors_path):
        if not path.is_file():
            raise IndexUnavailable(f"index file is missing: {path.name}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IndexUnavailable("index manifest is unreadable") from exc
    if not isinstance(manifest, dict):
        raise IndexUnavailable("index manifest is not an object")
    if manifest.get("format_version") != INDEX_FORMAT_VERSION:
        raise IndexUnavailable(
            f"index format version must be {INDEX_FORMAT_VERSION}; rebuild the index"
        )

    dimension = manifest.get("dimension")
    chunk_count = manifest.get("chunk_count")
    if not isinstance(dimension, int) or not 1 <= dimension <= MAX_DIMENSION:
        raise IndexUnavailable("index manifest has an invalid dimension")
    if not isinstance(chunk_count, int) or chunk_count < 0:
        raise IndexUnavailable("index manifest has an invalid chunk count")
    if chunk_count > max_chunks:
        raise IndexUnavailable(
            f"index holds {chunk_count} chunks, above the configured limit of {max_chunks}"
        )

    chunks: list[Chunk] = []
    try:
        with chunks_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise IndexUnavailable("index contains a malformed chunk record")
                chunks.append(
                    Chunk(
                        document=str(record.get("document", ""))[:1_024],
                        title=str(record.get("title", ""))[:200],
                        ordinal=int(record.get("ordinal", 0)),
                        text=str(record.get("text", "")),
                    )
                )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise IndexUnavailable("index chunk file is unreadable") from exc
    if len(chunks) != chunk_count:
        raise IndexUnavailable("index chunk count does not match its manifest")

    expected_bytes = chunk_count * dimension * 4
    actual_bytes = vectors_path.stat().st_size
    if actual_bytes != expected_bytes:
        raise IndexUnavailable(
            f"index vector file is {actual_bytes} bytes; expected {expected_bytes}"
        )
    try:
        flat = np.fromfile(vectors_path, dtype="<f4")
    except OSError as exc:
        raise IndexUnavailable("index vector file is unreadable") from exc
    vectors = flat.reshape(chunk_count, dimension).astype(np.float32, copy=False)
    if not np.all(np.isfinite(vectors)):
        raise IndexUnavailable("index vectors contain non-finite values")

    built_at = manifest.get("built_at")
    embedding_model = manifest.get("embedding_model")
    document_count = manifest.get("document_count")
    return LoadedIndex(
        dimension=dimension,
        built_at=built_at if isinstance(built_at, str) else "",
        embedding_model=embedding_model if isinstance(embedding_model, str) else "",
        document_count=document_count if isinstance(document_count, int) else 0,
        chunks=tuple(chunks),
        vectors=vectors,
    )


def search(index: LoadedIndex, query_vector: np.ndarray, limit: int) -> list[tuple[int, float]]:
    """Return (chunk position, cosine score) pairs, best first."""
    if limit <= 0 or index.chunk_count == 0:
        return []
    query = np.ascontiguousarray(query_vector, dtype=np.float32).reshape(-1)
    if query.shape[0] != index.dimension:
        raise ValueError("query dimension does not match the index")
    norm = float(np.linalg.norm(query))
    if norm == 0.0 or not np.isfinite(norm):
        return []
    scores = index.vectors @ (query / norm)

    limit = min(limit, index.chunk_count)
    if limit == index.chunk_count:
        order = np.argsort(-scores)
    else:
        # argpartition finds the top-k boundary in linear time; only that slice
        # is then sorted, which matters once the index holds tens of thousands
        # of chunks.
        candidates = np.argpartition(-scores, limit - 1)[:limit]
        order = candidates[np.argsort(-scores[candidates])]
    return [(int(position), float(scores[position])) for position in order]
