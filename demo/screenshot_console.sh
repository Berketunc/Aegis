#!/usr/bin/env bash
# Headless-Chrome screenshots of the live console through the SSH tunnel (run on the Mac).
#   bash demo/screenshot_console.sh [base-url] [claim-id] [out-dir]
# Defaults: http://localhost:8080, the most recent RED claim, evidence/console/
set -euo pipefail
base="${1:-http://localhost:8080}"
out="${3:-evidence/console}"
chrome="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[[ -x "$chrome" ]] || { echo "Google Chrome not found" >&2; exit 2; }
curl -sf -m 5 "$base/health" >/dev/null || { echo "Gateway not reachable at $base (is the SSH tunnel up?)" >&2; exit 1; }
claim="${2:-$(curl -s "$base/journal?limit=1000" | python3 -c '
import json,sys
rows=[r for r in json.load(sys.stdin) if r["event"]=="DECISION"]
red=[r for r in rows if r["payload_json"]["band"]=="RED"] or rows
print(red[-1]["claim_id"] if red else "")')}"
mkdir -p "$out"
shot() {  # name, hash route, [extra chrome flags...]
  local name="$1" route="$2"; shift 2
  "$chrome" --headless=new --disable-gpu --hide-scrollbars --window-size=1600,1000 \
    --virtual-time-budget=12000 --screenshot="$out/$name.png" "$@" "$base/$route" >/dev/null 2>&1 \
    && echo "  $out/$name.png"
}
echo "Screenshots of $base (case: ${claim:-none})"
shot 01-dashboard "#/dashboard"
shot 02-queue-open "#/queue"
shot 03-queue-all "#/queue?band=ALL&status=all"
[[ -n "$claim" ]] && shot 04-case-red "#/claims/$claim"
shot 05-submit "#/submit"
shot 06-audit "#/audit"
shot 07-policy "#/policy"
shot 08-system "#/system"
echo "Done. Dark-mode variants: open the console once, toggle the theme, and re-run (the choice is kept in the browser profile Chrome uses here)."
