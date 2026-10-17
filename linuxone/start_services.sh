#!/usr/bin/env bash
# Run on LinuxONE; only the optional gateway publishes a loopback port.
set -euo pipefail
script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname -- "$script_dir")"
runtime_dir="$project_dir/.runtime"
with_granite=false
with_gateway=false
for argument in "$@"; do
  case "$argument" in
    --with-granite) with_granite=true ;;
    --with-gateway) with_gateway=true ;;
    *) echo "Usage: bash linuxone/start_services.sh [--with-gateway] [--with-granite]" >&2; exit 2 ;;
  esac
done
if [[ "$(uname -m)" != s390x ]]; then
  echo "Run this on the LinuxONE s390x VM." >&2
  exit 2
fi
docker_command=(docker)
if ! docker info >/dev/null 2>&1; then docker_command=(sudo docker); fi
umask 077
prepare_arguments=("$runtime_dir")
if "$with_granite"; then prepare_arguments+=(--with-granite); fi
python3 "$script_dir/prepare_runtime.py" "${prepare_arguments[@]}"
# C's reviewed prompts are supplied; preserve local overrides if already present.
for prompt in pii_second_pass adjuster_note; do
  if [[ ! -e "$runtime_dir/prompts/$prompt.txt" ]]; then
    cp "$project_dir/llm/prompts/$prompt.txt" "$runtime_dir/prompts/$prompt.txt"
  fi
done

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

# Build requested images before replacing a working service.
"${docker_command[@]}" build -f "$project_dir/risk_service/Dockerfile" -t aegis-risk:person-b "$project_dir"
if "$with_gateway"; then
  "${docker_command[@]}" build -f "$project_dir/txn/Dockerfile" -t aegis-gateway:person-b "$project_dir"
fi
if "$with_granite"; then
  "${docker_command[@]}" build -f "$project_dir/llm/Dockerfile" -t aegis-granite:person-b "$project_dir"
fi
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
  replace_owned_container aegis-granite
  "${docker_command[@]}" run -d --name aegis-granite --restart unless-stopped \
    --label aegis.owner=person-b --network "$network" \
    --user "$(id -u):$(id -g)" \
    --env-file "$runtime_dir/granite.env" \
    --mount "type=bind,source=$runtime_dir/models,target=/models,readonly" \
    --mount "type=bind,source=$runtime_dir/prompts,target=/prompts,readonly" \
    aegis-granite:person-b >/dev/null
fi

if "$with_gateway"; then
  replace_owned_container aegis-gateway
  gateway_mounts=()
  if [[ -d "$project_dir/ui" ]]; then
    gateway_mounts+=(--mount "type=bind,source=$project_dir/ui,target=/app/ui,readonly")
  fi
  "${docker_command[@]}" run -d --name aegis-gateway --restart unless-stopped \
    --label aegis.owner=person-b --network "$network" \
    --user "$(id -u):$(id -g)" \
    --env-file "$runtime_dir/gateway.env" \
    --publish 127.0.0.1:8080:8080 \
    --mount "type=bind,source=$runtime_dir/gateway,target=/data" \
    --mount "type=bind,source=$runtime_dir/models,target=/models,readonly" \
    "${gateway_mounts[@]}" \
    aegis-gateway:person-b >/dev/null
  echo "Gateway: http://127.0.0.1:8080 (SSH tunnel only); risk failures remain AMBER."
fi

echo "Risk service: http://aegis-risk:8000 on Docker network aegis-internal"
echo "Risk and Granite have no published ports."
echo "Run: bash linuxone/service_status.sh"
echo "Back up .runtime privately, including services.env, data and gateway; it is excluded from Git."
