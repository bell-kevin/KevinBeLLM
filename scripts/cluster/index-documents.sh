#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Machine B only. Rebuilds the document index, then restarts the retrieval API
# so it serves the new index. Embedding saturates the RTX 3070 while it runs and
# touches Machine A not at all.
set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "${script_dir}/../.." && pwd)"
# shellcheck source=common.sh
. "${script_dir}/common.sh"

env_file="${HOME}/.config/kevinbellm-cluster/retrieval.env"
venv_dir="${HOME}/.local/share/kevinbellm-retrieval/venv"
service_dir="${project_dir}/services/doc-retrieval"
source_dir=""
dry_run=0
passthrough=()

usage() {
  cat <<'EOF'
Usage: index-documents.sh [options] [-- extra indexer arguments]

Options:
  --source PATH     Documents to index (default RETRIEVAL_SOURCE_DIR in retrieval.env)
  --env-file PATH   Private Machine B env file
  --venv-dir PATH   Python environment created by install-retrieval.sh
  --dry-run         Report what would be indexed without embedding or writing

Extra arguments after -- are passed straight to the indexer, for example:
  ./scripts/cluster/index-documents.sh -- --target-chars 900 --batch-size 8
EOF
}

while (($#)); do
  case "$1" in
    --source)
      (($# >= 2)) || cluster_die "--source requires a value."
      source_dir="$2"
      shift 2
      ;;
    --env-file)
      (($# >= 2)) || cluster_die "--env-file requires a value."
      env_file="$2"
      shift 2
      ;;
    --venv-dir)
      (($# >= 2)) || cluster_die "--venv-dir requires a value."
      venv_dir="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --)
      shift
      passthrough=("$@")
      break
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      cluster_die "Unknown option: $1"
      ;;
  esac
done

cluster_require_non_root
cluster_require_command systemctl
cluster_require_command curl
[[ -f "${env_file}" ]] || cluster_die "Machine B env file not found: ${env_file}. Run install-retrieval.sh first."
venv_python="${venv_dir}/bin/python"
[[ -x "${venv_python}" ]] || cluster_die "Retrieval environment not found: ${venv_python}. Run install-retrieval.sh first."
[[ -d "${service_dir}/app" ]] || cluster_die "Retrieval service source not found: ${service_dir}"

if [[ -z "${source_dir}" ]]; then
  source_dir="$(cluster_env_value "${env_file}" RETRIEVAL_SOURCE_DIR || true)"
fi
[[ -n "${source_dir}" ]] || cluster_die "Set RETRIEVAL_SOURCE_DIR in ${env_file} or pass --source."
[[ "${source_dir}" != *CHANGE_ME* ]] || cluster_die "Replace the RETRIEVAL_SOURCE_DIR placeholder in ${env_file}."
[[ -d "${source_dir}" ]] || cluster_die "Source directory does not exist: ${source_dir}"

# Export only the settings the indexer actually reads, using the same parser the
# other helpers use. The private env file is never sourced, so a typo in it
# cannot become shell execution. EMBEDDING_BASE_URL is deliberately not exported:
# the indexer must reach the same fixed loopback endpoint the unit serves.
for setting in \
  RETRIEVAL_INDEX_DIR \
  EMBEDDING_MODEL_ALIAS \
  RETRIEVAL_CANDIDATES \
  RETRIEVAL_MAX_RESULTS \
  RETRIEVAL_EXCERPT_CHARS \
  RETRIEVAL_MAX_CHUNKS \
  RETRIEVAL_BACKEND_TIMEOUT_SECONDS; do
  value="$(cluster_env_value "${env_file}" "${setting}" || true)"
  if [[ -n "${value}" ]]; then
    export "${setting}=${value}"
  fi
done

# The API reads the index only at startup, so it keeps serving the old index
# until the rebuild has succeeded and the unit is restarted below.
indexer_arguments=(--source "${source_dir}")
((dry_run)) && indexer_arguments+=(--dry-run)
if ((${#passthrough[@]})); then
  indexer_arguments+=("${passthrough[@]}")
fi

cluster_info "Building the document index from ${source_dir}"
(cd -- "${service_dir}" && "${venv_python}" -m app.indexer "${indexer_arguments[@]}")

if ((dry_run)); then
  cluster_info "Dry run complete; the existing index and service are unchanged."
  exit 0
fi

if systemctl --user is-enabled --quiet kevinbellm-doc-retrieval.service 2>/dev/null; then
  cluster_info "Restarting kevinbellm-doc-retrieval.service to serve the new index"
  systemctl --user restart kevinbellm-doc-retrieval.service
  for _attempt in $(seq 1 30); do
    if curl --silent --fail --max-time 3 http://127.0.0.1:8091/health >/dev/null 2>&1; then
      cluster_info "Retrieval API is serving the new index"
      curl --silent --max-time 3 http://127.0.0.1:8091/health
      printf '\n'
      exit 0
    fi
    sleep 2
  done
  cluster_warn "The retrieval API did not become healthy within 60 s."
  cluster_warn "Inspect: journalctl --user -u kevinbellm-doc-retrieval.service -e"
  exit 1
fi
cluster_warn "kevinbellm-doc-retrieval.service is not enabled; start it to serve the new index."
