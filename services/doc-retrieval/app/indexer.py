# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline index builder. Runs on Machine B, never on a request path.

Embedding a document collection saturates the RTX 3070 for as long as it takes.
Keeping it in a separate command means an index rebuild can never appear as
latency inside a chat answer.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np

from .backend import BackendError, ModelBackends
from .chunking import TEXT_SUFFIXES, chunk_text, document_title, normalize_text
from .config import Settings, load_settings
from .store import Chunk, write_index


DEFAULT_MAX_FILE_BYTES = 4 * 1024 * 1024


class IndexBuildError(RuntimeError):
    """The index could not be built; the previous index is left in place."""


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def collect_documents(
    source_root: Path,
    *,
    exclude: list[Path],
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> list[Path]:
    """Return indexable files under ``source_root``, newest path order aside.

    Symlinks are never followed: a link pointing outside the collection would
    otherwise pull unrelated private files into the index.
    """
    resolved_root = source_root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise IndexBuildError(f"Source is not a directory: {source_root}")
    excluded = [candidate.resolve() for candidate in exclude]

    found: list[Path] = []
    for directory, subdirectories, filenames in os.walk(resolved_root, followlinks=False):
        current = Path(directory)
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if not name.startswith(".")
            and not any(_is_within((current / name).resolve(), stop) for stop in excluded)
        )
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            path = current / filename
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if any(_is_within(path.resolve(), stop) for stop in excluded):
                continue
            # os.walk does not descend symlinked directories above, but a real
            # path is still checked so nothing outside the root can be indexed.
            if not _is_within(path.resolve(), resolved_root):
                continue
            if path.stat().st_size > max_file_bytes:
                print(f"  skipped (too large): {path.relative_to(resolved_root)}", file=sys.stderr)
                continue
            found.append(path)
    return found


def build_chunks(
    source_root: Path,
    documents: list[Path],
    *,
    target_chars: int,
    overlap_chars: int,
    max_chunks: int,
) -> list[Chunk]:
    resolved_root = source_root.resolve()
    chunks: list[Chunk] = []
    for path in documents:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"  skipped (unreadable): {path} ({exc})", file=sys.stderr)
            continue
        text = normalize_text(raw)
        if not text:
            continue
        relative = path.relative_to(resolved_root).as_posix()
        title = document_title(text, path.stem)
        pieces = chunk_text(
            text,
            target_chars=target_chars,
            overlap_chars=overlap_chars,
            max_chunks=max_chunks,
        )
        for ordinal, piece in enumerate(pieces):
            chunks.append(
                Chunk(document=relative, title=title, ordinal=ordinal, text=piece)
            )
            if len(chunks) > max_chunks:
                raise IndexBuildError(
                    f"Collection exceeds RETRIEVAL_MAX_CHUNKS ({max_chunks}). "
                    "Raise the limit or narrow the source directory."
                )
    return chunks


async def embed_chunks(
    settings: Settings, chunks: list[Chunk], *, batch_size: int
) -> np.ndarray:
    """Embed every chunk on Machine B's GPU, in bounded batches."""
    async with httpx.AsyncClient(trust_env=False) as client:
        backends = ModelBackends(client, settings)
        batches: list[np.ndarray] = []
        total = len(chunks)
        for start in range(0, total, batch_size):
            window = chunks[start : start + batch_size]
            try:
                batches.append(await backends.embed([chunk.text for chunk in window]))
            except BackendError as exc:
                raise IndexBuildError(
                    f"Embedding failed at chunk {start}: {exc}. The previous index is unchanged."
                ) from exc
            done = min(start + batch_size, total)
            print(f"  embedded {done}/{total} chunks", flush=True)
    matrix = np.vstack(batches)
    widths = {int(batch.shape[1]) for batch in batches}
    if len(widths) != 1:
        raise IndexBuildError("The embedding model returned inconsistent dimensions")
    return matrix


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.indexer",
        description="Build the KevinBeLLM document index on Machine B.",
    )
    parser.add_argument("--source", required=True, help="Directory of documents to index")
    parser.add_argument("--index-dir", default=None, help="Override RETRIEVAL_INDEX_DIR")
    parser.add_argument("--target-chars", type=int, default=1_200)
    parser.add_argument("--overlap-chars", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be indexed without calling the GPU or writing files",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # Private document text is about to be written to disk.
    os.umask(0o077)
    arguments = parse_arguments(argv)
    if not 100 <= arguments.target_chars <= 8_000:
        raise IndexBuildError("--target-chars must be between 100 and 8000")
    if not 0 <= arguments.overlap_chars <= arguments.target_chars // 2:
        raise IndexBuildError("--overlap-chars must be between 0 and half of --target-chars")
    if not 1 <= arguments.batch_size <= 256:
        raise IndexBuildError("--batch-size must be between 1 and 256")

    settings = load_settings()
    index_dir = (
        Path(arguments.index_dir).expanduser() if arguments.index_dir else settings.index_dir
    )
    source_root = Path(arguments.source).expanduser()
    if not source_root.exists():
        raise IndexBuildError(f"Source directory does not exist: {source_root}")

    print(f"==> Scanning {source_root}")
    documents = collect_documents(
        source_root,
        exclude=[index_dir, index_dir.parent / f"{index_dir.name}.new"],
        max_file_bytes=arguments.max_file_bytes,
    )
    if not documents:
        raise IndexBuildError(
            f"No indexable files under {source_root}. "
            f"Supported suffixes: {', '.join(sorted(TEXT_SUFFIXES))}"
        )
    print(f"==> Found {len(documents)} documents")

    chunks = build_chunks(
        source_root,
        documents,
        target_chars=arguments.target_chars,
        overlap_chars=arguments.overlap_chars,
        max_chunks=settings.max_chunks,
    )
    if not chunks:
        raise IndexBuildError("Every document was empty after normalization")
    print(f"==> Prepared {len(chunks)} chunks")

    if arguments.dry_run:
        print("==> Dry run: no embedding was requested and no index was written")
        return 0

    print(f"==> Embedding on {settings.embedding_base_url} ({settings.embedding_model})")
    vectors = asyncio.run(embed_chunks(settings, chunks, batch_size=arguments.batch_size))

    write_index(
        index_dir,
        embedding_model=settings.embedding_model,
        built_at=datetime.now(UTC).isoformat(timespec="seconds"),
        document_count=len(documents),
        chunks=chunks,
        vectors=vectors,
    )
    print(f"==> Wrote {len(chunks)} chunks to {index_dir}")
    print("==> Restart kevinbellm-doc-retrieval.service to serve the new index")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except IndexBuildError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error
