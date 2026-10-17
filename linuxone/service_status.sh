#!/usr/bin/env bash
set -euo pipefail
docker_command=(docker)
if ! docker info >/dev/null 2>&1; then docker_command=(sudo docker); fi
found=false
failed=0
for name in aegis-risk aegis-granite; do
  if "${docker_command[@]}" container inspect "$name" >/dev/null 2>&1; then
    found=true
    echo "$name"
    port=8000
    if [[ "$name" == aegis-granite ]]; then port=8001; fi
    if ! "${docker_command[@]}" exec -i "$name" python3 - "$port" <<'PY'
import json
import sys
import urllib.error
import urllib.request

try:
    with urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/health", timeout=5) as response:
        body = response.read().decode()
except urllib.error.HTTPError as error:
    print(error.read().decode())
    raise SystemExit(1)
except urllib.error.URLError as error:
    print(f"Health check unavailable: {error.reason}", file=sys.stderr)
    raise SystemExit(1)
print(body)
try:
    ready = json.loads(body).get("status") == "ready"
except (ValueError, AttributeError):
    ready = False
raise SystemExit(0 if ready else 1)
PY
    then
      failed=1
    fi
  fi
done
if ! "$found"; then
  echo "No Aegis service containers found. Run bash linuxone/start_services.sh first." >&2
  exit 1
fi
exit "$failed"
