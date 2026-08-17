# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import numpy as np
import pytest

from app.indexer import IndexBuildError, build_chunks, collect_documents, main
from app.store import load_index


def _supports_symlinks(tmp_path) -> bool:
    target = tmp_path / "_link_probe_target"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "_link_probe"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return False
    link.unlink()
    return True


def _collection(root) -> None:
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "roof.md").write_text("# Roof\n\nWarranty runs to 2031.", encoding="utf-8")
    (root / "notes" / "car.txt").write_text("Oil changed at 60k miles.", encoding="utf-8")
    (root / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0binary")
    (root / ".hidden.md").write_text("secret", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config.ini").write_text("[core]", encoding="utf-8")


def test_only_text_like_visible_files_are_collected(tmp_path) -> None:
    root = tmp_path / "documents"
    _collection(root)

    found = collect_documents(root, exclude=[])

    assert sorted(path.relative_to(root).as_posix() for path in found) == [
        "notes/car.txt",
        "notes/roof.md",
    ]


def test_oversized_files_are_skipped(tmp_path) -> None:
    root = tmp_path / "documents"
    root.mkdir()
    (root / "small.md").write_text("ok", encoding="utf-8")
    (root / "huge.md").write_text("x" * 5_000, encoding="utf-8")

    found = collect_documents(root, exclude=[], max_file_bytes=1_000)

    assert [path.name for path in found] == ["small.md"]


def test_the_index_directory_is_never_indexed_into_itself(tmp_path) -> None:
    root = tmp_path / "documents"
    root.mkdir()
    (root / "real.md").write_text("body", encoding="utf-8")
    index_dir = root / "index"
    index_dir.mkdir()
    (index_dir / "chunks.jsonl").write_text('{"text": "recursive"}', encoding="utf-8")

    found = collect_documents(root, exclude=[index_dir])

    assert [path.name for path in found] == ["real.md"]


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_symlinks_out_of_the_collection_are_not_followed(tmp_path, kind: str) -> None:
    if not _supports_symlinks(tmp_path):
        pytest.skip("this platform does not permit symlink creation")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.md").write_text("must not be indexed", encoding="utf-8")
    root = tmp_path / "documents"
    root.mkdir()
    (root / "real.md").write_text("body", encoding="utf-8")
    if kind == "file":
        (root / "link.md").symlink_to(outside / "private.md")
    else:
        (root / "linked").symlink_to(outside, target_is_directory=True)

    found = collect_documents(root, exclude=[])

    assert [path.name for path in found] == ["real.md"]


def test_chunks_record_relative_paths_and_titles(tmp_path) -> None:
    root = tmp_path / "documents"
    _collection(root)
    documents = collect_documents(root, exclude=[])

    chunks = build_chunks(
        root, documents, target_chars=1_000, overlap_chars=100, max_chunks=100
    )

    by_document = {chunk.document: chunk for chunk in chunks}
    assert set(by_document) == {"notes/car.txt", "notes/roof.md"}
    assert by_document["notes/roof.md"].title == "Roof"
    # A relative POSIX path never leaks the operator's home directory.
    assert all(not chunk.document.startswith("/") for chunk in chunks)


def test_exceeding_the_chunk_ceiling_fails_instead_of_silently_truncating(
    tmp_path,
) -> None:
    root = tmp_path / "documents"
    root.mkdir()
    for index in range(5):
        (root / f"doc-{index}.md").write_text(
            "\n\n".join("word " * 80 for _ in range(10)), encoding="utf-8"
        )
    documents = collect_documents(root, exclude=[])

    with pytest.raises(IndexBuildError, match="RETRIEVAL_MAX_CHUNKS"):
        build_chunks(root, documents, target_chars=200, overlap_chars=0, max_chunks=4)


def test_a_dry_run_never_calls_the_gpu_or_writes_an_index(
    tmp_path, monkeypatch, capsys
) -> None:
    root = tmp_path / "documents"
    _collection(root)
    index_dir = tmp_path / "index"
    monkeypatch.setenv("RETRIEVAL_INDEX_DIR", str(index_dir))

    exit_code = main(["--source", str(root), "--dry-run"])

    assert exit_code == 0
    assert not index_dir.exists()
    assert "Dry run" in capsys.readouterr().out


def test_a_full_build_writes_a_loadable_index(tmp_path, monkeypatch, capsys) -> None:
    root = tmp_path / "documents"
    _collection(root)
    index_dir = tmp_path / "index"
    monkeypatch.setenv("RETRIEVAL_INDEX_DIR", str(index_dir))

    async def fake_embed(_settings, chunks, *, batch_size):
        assert batch_size == 16
        return np.tile(
            np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32), (len(chunks), 1)
        )

    monkeypatch.setattr("app.indexer.embed_chunks", fake_embed)

    assert main(["--source", str(root)]) == 0

    index = load_index(index_dir, max_chunks=1_000)
    assert index.chunk_count == 2
    assert index.document_count == 2
    assert index.embedding_model == "kevinbellm-embed"
    assert "Restart kevinbellm-doc-retrieval.service" in capsys.readouterr().out


def test_an_empty_collection_is_an_explicit_failure(tmp_path, monkeypatch) -> None:
    root = tmp_path / "documents"
    root.mkdir()
    (root / "photo.jpg").write_bytes(b"binary")
    monkeypatch.setenv("RETRIEVAL_INDEX_DIR", str(tmp_path / "index"))

    with pytest.raises(IndexBuildError, match="No indexable files"):
        main(["--source", str(root), "--dry-run"])


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--target-chars", "10"], "--target-chars"),
        (["--overlap-chars", "5000"], "--overlap-chars"),
        (["--batch-size", "0"], "--batch-size"),
    ],
)
def test_unsafe_indexer_arguments_are_rejected(
    tmp_path, monkeypatch, argv, message
) -> None:
    root = tmp_path / "documents"
    _collection(root)
    monkeypatch.setenv("RETRIEVAL_INDEX_DIR", str(tmp_path / "index"))

    with pytest.raises(IndexBuildError, match=message):
        main(["--source", str(root), "--dry-run", *argv])


def test_a_missing_source_directory_is_reported(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RETRIEVAL_INDEX_DIR", str(tmp_path / "index"))

    with pytest.raises(IndexBuildError, match="does not exist"):
        main(["--source", str(tmp_path / "absent"), "--dry-run"])
