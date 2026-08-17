# Document retrieval service (Machine B only)

Read-only dense retrieval over the operator's own documents, served from
Machine B's RTX 3070. Machine A never installs, imports, or runs any of this.

```text
Machine A                                Machine B (RTX 3070)
─────────                                ────────────────────
assistant-web
  search_documents tool
    │ one POST, ~8 s ceiling
    ▼
  127.0.0.1:8091  ──ssh -L──────────────► 127.0.0.1:8091  doc-retrieval API
                                            ├─ embed query  → 127.0.0.1:8081  bge-m3
                                            ├─ dense search over the local index
                                            └─ rerank top 40 → 127.0.0.1:8082  bge-reranker-v2-m3
```

The whole pipeline runs inside one request on Machine B. Machine A performs no
embedding, no vector arithmetic, and no reranking; it sends a query string and
receives ranked passages.

## Why the split looks like this

Machine A's RTX 3060 holds the everyday 9B model with about 3.2 GiB of VRAM to
spare, and its measured 53–69 tokens/second depends on keeping that card
uncontended. Both retrieval models are small — roughly 1.4 GiB together — but
putting them on Machine A would take VRAM and GPU time directly from generation.
Machine B's 8 GiB card is otherwise idle, so retrieval is free capacity there.

## Endpoints

`GET /health` reports whether an index is loaded, its chunk and document counts,
its build timestamp, and the embedding model alias it was built with. It answers
`degraded` rather than failing when no index exists yet, so the service can be
installed before the first index build.

`POST /search` takes `{"query": str, "limit": int}` and returns ranked passages
with the document name, title, bounded excerpt, and both the cosine and rerank
scores. It never returns an absolute path.

The service loads its index once at startup and never writes to it. A request
therefore touches no filesystem at all, so there is no request-time path
handling to get wrong. Rebuilding the index is an explicit operator action that
ends in a service restart.

## Index format

`RETRIEVAL_INDEX_DIR` holds three mode-0600 files inside a mode-0700 directory:

| File | Contents |
|---|---|
| `manifest.json` | format version, build time, embedding model alias, dimension, counts |
| `chunks.jsonl` | one record per chunk: relative document path, title, ordinal, text |
| `vectors.f32` | raw little-endian float32, `chunk_count × dimension`, L2-normalized |

Loading validates every one of those against the others and refuses a mismatch
rather than serving wrong scores. Because the vectors are normalized at write
time, a dot product is exactly cosine similarity.

Private document text lands here in plain form. Keep the directory off any
synchronised or shared filesystem.

## Building the index

Use `scripts/cluster/index-documents.sh` on Machine B; it loads the same private
env file the services use, so the index is always built with the deployed model
alias, and it restarts the API when the build succeeds.

Only text-like files are indexed: `.md`, `.txt`, `.rst`, `.org`, `.csv`, `.tsv`,
`.json`, `.yaml`, `.toml`, `.ini`, `.cfg`, `.log`, and their close variants.
PDF and DOCX are deliberately unsupported — extracting them needs a parser with
a real attack surface, and this index is exactly where private text is written.
Hidden files and directories are skipped, and symlinks are never followed, so a
link inside the collection cannot pull in unrelated files.

Chunking is paragraph-aligned at about 1,200 characters with 200 characters of
overlap, so a passage that straddles a paragraph boundary stays retrievable from
either side. An embedding failure aborts the build and leaves the previous index
in place.

## Running the tests

Machine B's dependency lock targets Python 3.12, matching Ubuntu 24.04:

```bash
/usr/bin/python3 -m venv .venv-doc-retrieval
.venv-doc-retrieval/bin/python -m pip install --require-hashes \
  -r services/doc-retrieval/requirements-dev.lock
(cd services/doc-retrieval && ../../.venv-doc-retrieval/bin/python -m pytest -q)
```

Three tests skip on non-POSIX platforms: two need symlink creation and one
checks POSIX permission bits. Both behaviours are real on Machine B.
