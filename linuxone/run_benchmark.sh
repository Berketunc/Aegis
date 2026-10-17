#!/usr/bin/env bash
# Run on the LinuxONE VM. Uses the public event image, without ICR credentials.
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname -- "$script_dir")"
image="${AEGIS_LINUXONE_IMAGE:-registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x:latest}"
cache_dir="${AEGIS_MODEL_CACHE:-$project_dir/.cache/linuxone}"
output_file="${AEGIS_BENCHMARK_OUTPUT:-$project_dir/eval/stock_resnet18.json}"

if [[ "$(uname -m)" != "s390x" ]]; then
  echo "Run this script on the LinuxONE s390x VM." >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required; the public event image does not require IBM Cloud registry credentials." >&2
  exit 2
fi

mkdir -p -- "$cache_dir" "$(dirname -- "$output_file")"
cache_dir="$(CDPATH= cd -- "$cache_dir" && pwd)"
output_dir="$(CDPATH= cd -- "$(dirname -- "$output_file")" && pwd)"
output_name="$(basename -- "$output_file")"

docker_command=(docker)
if ! docker info >/dev/null 2>&1; then
  docker_command=(sudo docker)
fi

"${docker_command[@]}" run --rm \
  --user "$(id -u):$(id -g)" \
  --env PYTHONUNBUFFERED=1 \
  --env TORCH_NNPA_DEBUG=0 \
  --env ZDNN_ENABLE_PRECHECK=false \
  --mount "type=bind,source=$script_dir,target=/opt/aegis/linuxone,readonly" \
  --mount "type=bind,source=$cache_dir,target=/cache" \
  --mount "type=bind,source=$output_dir,target=/results" \
  --workdir /opt/aegis/linuxone \
  --entrypoint python3 \
  "$image" /opt/aegis/linuxone/benchmark_resnet18.py \
  --cache-dir /cache --output "/results/$output_name" "$@"

echo "Saved benchmark: $output_dir/$output_name" >&2
