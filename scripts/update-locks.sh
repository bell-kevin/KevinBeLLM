#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to regenerate dependency locks." >&2
  exit 1
fi

uv pip compile "${project_dir}/services/assistant-web/requirements.txt" \
  --python-version 3.13 \
  --python-platform x86_64-manylinux_2_36 \
  --generate-hashes \
  --output-file "${project_dir}/services/assistant-web/requirements.lock" \
  --custom-compile-command './scripts/update-locks.sh'

uv pip compile "${project_dir}/services/assistant-web/requirements-dev.txt" \
  --python-version 3.13 \
  --python-platform x86_64-manylinux_2_36 \
  --generate-hashes \
  --output-file "${project_dir}/services/assistant-web/requirements-dev.lock" \
  --custom-compile-command './scripts/update-locks.sh'

uv pip compile "${project_dir}/services/live-tools/requirements.txt" \
  --python-version 3.12 \
  --python-platform x86_64-manylinux_2_36 \
  --generate-hashes \
  --output-file "${project_dir}/services/live-tools/requirements.lock" \
  --custom-compile-command './scripts/update-locks.sh'

# Machine B only. Ubuntu 24.04 ships Python 3.12, and this service runs in a
# host virtual environment rather than a container.
uv pip compile "${project_dir}/services/doc-retrieval/requirements.txt" \
  --python-version 3.12 \
  --python-platform x86_64-manylinux_2_36 \
  --generate-hashes \
  --output-file "${project_dir}/services/doc-retrieval/requirements.lock" \
  --custom-compile-command './scripts/update-locks.sh'

uv pip compile "${project_dir}/services/doc-retrieval/requirements-dev.txt" \
  --python-version 3.12 \
  --python-platform x86_64-manylinux_2_36 \
  --generate-hashes \
  --output-file "${project_dir}/services/doc-retrieval/requirements-dev.lock" \
  --custom-compile-command './scripts/update-locks.sh'

echo "Dependency locks regenerated with hashes."
