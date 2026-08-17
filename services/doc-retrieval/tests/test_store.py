# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import json
import os
import stat

import numpy as np
import pytest

from app.store import (
    CHUNKS_NAME,
    MANIFEST_NAME,
    VECTORS_NAME,
    Chunk,
    IndexUnavailable,
    load_index,
    normalize_rows,
    search,
    write_index,
)


def _chunks(count: int) -> list[Chunk]:
    return [
        Chunk(document=f"notes/doc-{index}.md", title=f"Doc {index}", ordinal=0, text=f"body {index}")
        for index in range(count)
    ]


def _write(tmp_path, count: int = 4, dimension: int = 8):
    index_dir = tmp_path / "index"
    generator = np.random.default_rng(seed=count)
    vectors = generator.standard_normal((count, dimension)).astype(np.float32)
    write_index(
        index_dir,
        embedding_model="kevinbellm-embed",
        built_at="2026-08-16T00:00:00+00:00",
        document_count=count,
        chunks=_chunks(count),
        vectors=vectors,
    )
    return index_dir, vectors


def test_normalize_rows_makes_dot_products_cosine() -> None:
    matrix = np.array([[3.0, 4.0], [1.0, 0.0]], dtype=np.float32)

    normalized = normalize_rows(matrix)

    assert np.allclose(np.linalg.norm(normalized, axis=1), 1.0)
    assert np.allclose(normalized[0], [0.6, 0.8])


def test_normalize_rows_survives_a_zero_vector() -> None:
    normalized = normalize_rows(np.zeros((1, 4), dtype=np.float32))

    assert np.all(np.isfinite(normalized))


def test_round_trip_preserves_chunks_and_normalized_vectors(tmp_path) -> None:
    index_dir, vectors = _write(tmp_path, count=5, dimension=16)

    index = load_index(index_dir, max_chunks=100)

    assert index.chunk_count == 5
    assert index.dimension == 16
    assert index.document_count == 5
    assert index.embedding_model == "kevinbellm-embed"
    assert index.chunks[2].document == "notes/doc-2.md"
    assert np.allclose(np.linalg.norm(index.vectors, axis=1), 1.0, atol=1e-6)
    assert np.allclose(index.vectors, normalize_rows(vectors), atol=1e-6)


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX permission bits are not enforceable on this platform; Machine B is Ubuntu",
)
def test_index_files_are_private(tmp_path) -> None:
    """The index is the one place private document text lands on disk."""
    index_dir, _vectors = _write(tmp_path)

    for name in (MANIFEST_NAME, CHUNKS_NAME, VECTORS_NAME):
        mode = stat.S_IMODE((index_dir / name).stat().st_mode)
        assert mode & 0o077 == 0, f"{name} is group/world accessible"


def test_rewriting_replaces_the_previous_index_and_leaves_no_debris(tmp_path) -> None:
    index_dir, _vectors = _write(tmp_path, count=4)
    _write(tmp_path, count=7)

    index = load_index(index_dir, max_chunks=100)

    assert index.chunk_count == 7
    assert not (tmp_path / "index.new").exists()
    assert not (tmp_path / "index.old").exists()


def test_search_ranks_by_cosine_similarity(tmp_path) -> None:
    index_dir = tmp_path / "index"
    vectors = np.array(
        [[1.0, 0.0], [0.0, 1.0], [0.9, 0.1]],
        dtype=np.float32,
    )
    write_index(
        index_dir,
        embedding_model="m",
        built_at="now",
        document_count=3,
        chunks=_chunks(3),
        vectors=vectors,
    )
    index = load_index(index_dir, max_chunks=10)

    hits = search(index, np.array([1.0, 0.0], dtype=np.float32), 3)

    assert [position for position, _score in hits] == [0, 2, 1]
    assert hits[0][1] == pytest.approx(1.0, abs=1e-6)


def test_search_handles_limits_at_and_beyond_the_index_size(tmp_path) -> None:
    index_dir, _vectors = _write(tmp_path, count=4, dimension=8)
    index = load_index(index_dir, max_chunks=10)
    query = np.ones(8, dtype=np.float32)

    assert len(search(index, query, 4)) == 4
    assert len(search(index, query, 99)) == 4
    assert search(index, query, 0) == []


def test_search_rejects_a_mismatched_query_dimension(tmp_path) -> None:
    index_dir, _vectors = _write(tmp_path, dimension=8)
    index = load_index(index_dir, max_chunks=10)

    with pytest.raises(ValueError, match="dimension"):
        search(index, np.ones(4, dtype=np.float32), 2)


def test_a_zero_query_vector_returns_nothing_instead_of_nan(tmp_path) -> None:
    index_dir, _vectors = _write(tmp_path, dimension=8)
    index = load_index(index_dir, max_chunks=10)

    assert search(index, np.zeros(8, dtype=np.float32), 3) == []


def test_missing_index_files_are_reported_clearly(tmp_path) -> None:
    with pytest.raises(IndexUnavailable, match="missing"):
        load_index(tmp_path / "absent", max_chunks=10)


def test_a_truncated_vector_file_is_refused(tmp_path) -> None:
    index_dir, _vectors = _write(tmp_path, count=4, dimension=8)
    vectors_path = index_dir / VECTORS_NAME
    data = vectors_path.read_bytes()
    vectors_path.write_bytes(data[: len(data) - 16])

    with pytest.raises(IndexUnavailable, match="vector file"):
        load_index(index_dir, max_chunks=10)


def test_a_chunk_count_mismatch_is_refused(tmp_path) -> None:
    index_dir, _vectors = _write(tmp_path, count=4)
    chunks_path = index_dir / CHUNKS_NAME
    lines = chunks_path.read_text(encoding="utf-8").splitlines()
    chunks_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    with pytest.raises(IndexUnavailable, match="chunk count"):
        load_index(index_dir, max_chunks=10)


def test_an_index_above_the_configured_ceiling_is_refused(tmp_path) -> None:
    index_dir, _vectors = _write(tmp_path, count=6)

    with pytest.raises(IndexUnavailable, match="above the configured limit"):
        load_index(index_dir, max_chunks=5)


def test_an_older_format_version_demands_a_rebuild(tmp_path) -> None:
    index_dir, _vectors = _write(tmp_path)
    manifest_path = index_dir / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["format_version"] = 0
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(IndexUnavailable, match="rebuild the index"):
        load_index(index_dir, max_chunks=10)


def test_writing_rejects_a_vector_and_chunk_count_mismatch(tmp_path) -> None:
    with pytest.raises(ValueError, match="does not match"):
        write_index(
            tmp_path / "index",
            embedding_model="m",
            built_at="now",
            document_count=1,
            chunks=_chunks(3),
            vectors=np.ones((2, 4), dtype=np.float32),
        )
