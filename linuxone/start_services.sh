#!/usr/bin/env bash
# Run in the repository on LinuxONE. Starts internal services without public ports.
set -euo pipefail
script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname -- "$script_dir")"
runtime_dir="$project_dir/.runtime"
with_granite=false
if [[ "${1:-}" == "--with-granite" ]]; then
  with_granite=true
elif [[ $# -gt 0 ]]; then
  echo "Usage: bash linuxone/start_services.sh [--with-granite]" >&2
  exit 2
fi
if [[ "$(uname -m)" != s390x ]]; then
  echo "Run this on the LinuxONE s390x VM." >&2
  exit 2
fi
docker_command=(docker)
if ! docker info >/dev/null 2>&1; then docker_command=(sudo docker); fi
umask 077
mkdir -p "$runtime_dir/data" "$runtime_dir/models" "$runtime_dir/integrations" "$runtime_dir/prompts"

# Keep the evidence key stable across rebuilds; never print it or replace it.
python3 - "$runtime_dir" <<'PY'
import base64
from pathlib import Path
import secrets
import sys

root = Path(sys.argv[1])
env = root / "services.env"
if not env.exists():
    if any((root / "data").rglob("*")):
        raise SystemExit("Existing evidence data has no saved services.env. Restore its original encryption key before starting.")
    shared = secrets.token_urlsafe(32)
    values = {
        "AEGIS_RISK_SECRET": shared,
        "RISK_SHARED_SECRET": shared,
        "AEGIS_EVIDENCE_KEY_B64": base64.b64encode(secrets.token_bytes(32)).decode(),
        "AEGIS_DATA_DIR": "/data",
        "AEGIS_MODEL_MANIFEST": "/models/manifest.json",
        "AEGIS_THRESHOLDS_PATH": "/models/thresholds.json",
        "AEGIS_MODEL_DEVICE": "cpu",
        "AEGIS_TORCH_THREADS": "1",
        "AEGIS_PRIVACY_ADAPTER": "privacy_adapter:process",
        "PYTHONPATH": "/app:/opt/aegis:/models:/integrations",
        "GRANITE_MODEL_PATH": "/models/granite.gguf",
        "GRANITE_PII_PROMPT_PATH": "/prompts/pii.txt",
        "GRANITE_NOTE_PROMPT_PATH": "/prompts/note.txt",
        "GRANITE_THREADS": "2",
        "GRANITE_CONTEXT": "1024",
    }
    with env.open("x", encoding="utf-8") as target:
        target.write("".join(f"{key}={value}\n" for key, value in values.items()))
env.chmod(0o600)
# Granite receives only its own settings, never the evidence encryption key.
granite_values = []
for line in env.read_text(encoding="utf-8").splitlines():
    key, separator, value = line.partition("=")
    if separator and (key.startswith("GRANITE_") or key == "RISK_SHARED_SECRET"):
        granite_values.append(f"{key}={value}\n")
granite_env = root / "granite.env"
granite_env.write_text("".join(granite_values), encoding="utf-8")
granite_env.chmod(0o600)
PY

network=aegis-internal
if "${docker_command[@]}" network inspect "$network" >/dev/null 2>&1; then
  if [[ "$("${docker_command[@]}" network inspect --format '{{.Internal}}' "$network")" != true ]]; then
    echo "Existing aegis-internal network is not isolated. Use a dedicated internal network for these services." >&2
    exit 1
  fi
else
  "${docker_command[@]}" network create --internal "$network" >/dev/null
fi

replace_owned_container() {
  local name="$1" owner
  if "${docker_command[@]}" container inspect "$name" >/dev/null 2>&1; then
    owner="$("${docker_command[@]}" container inspect --format '{{index .Config.Labels "aegis.owner"}}' "$name")"
    if [[ "$owner" != person-b ]]; then
      echo "$name already exists and is not managed by this script." >&2
      exit 1
    fi
    "${docker_command[@]}" container stop "$name" >/dev/null
    "${docker_command[@]}" container rm "$name" >/dev/null
  fi
}

"${docker_command[@]}" build -f "$project_dir/risk_service/Dockerfile" -t aegis-risk:person-b "$project_dir"
replace_owned_container aegis-risk
"${docker_command[@]}" run -d --name aegis-risk --restart unless-stopped \
  --label aegis.owner=person-b --network "$network" \
  --user "$(id -u):$(id -g)" \
  --env-file "$runtime_dir/services.env" \
  --mount "type=bind,source=$runtime_dir/data,target=/data" \
  --mount "type=bind,source=$runtime_dir/models,target=/models,readonly" \
  --mount "type=bind,source=$runtime_dir/integrations,target=/integrations,readonly" \
  aegis-risk:person-b >/dev/null

if "$with_granite"; then
  "${docker_command[@]}" build -f "$project_dir/llm/Dockerfile" -t aegis-granite:person-b "$project_dir"
  replace_owned_container aegis-granite
  "${docker_command[@]}" run -d --name aegis-granite --restart unless-stopped \
    --label aegis.owner=person-b --network "$network" \
    --user "$(id -u):$(id -g)" \
    --env-file "$runtime_dir/granite.env" \
    --mount "type=bind,source=$runtime_dir/models,target=/models,readonly" \
    --mount "type=bind,source=$runtime_dir/prompts,target=/prompts,readonly" \
    aegis-granite:person-b >/dev/null
fi

echo "Risk service: http://aegis-risk:8000 on Docker network aegis-internal"
echo "No service ports were published. Attach C's gateway to aegis-internal."
echo "Run: bash linuxone/service_status.sh"
echo "Back up .runtime/services.env and .runtime/data privately; they are excluded from Git."
