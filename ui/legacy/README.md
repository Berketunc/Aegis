# UI: claimant form, adjuster view, project site

Static HTML/CSS/JS with no build step and no CDN. It's served by the CLMI gateway at `/` (`UI_DIR`, default `<repo>/ui`), so it works through the SSH tunnel with no extra ports.

| Page | What it shows |
|---|---|
| `#/overview` | Pitch, sourced problem data (plan.md §4), who benefits, architecture + trust boundary, decision bands, responsible AI, real vs simulated, open-source credits |
| `#/claim` | Upload (sent to `/evidence` immediately) → claim form → band, reasons, what the claimant sees, timing waterfall, record. Four demo scenarios with fictional personas. "Submit the same claim again" demonstrates idempotency. |
| `#/adjuster` | Queue built from the journal (`DECISION` events), photo + heatmap, signals, Granite note (polled until ready), "who sees what" (adjuster vs authorised, via the `X-Role` header), per-claim journal |
| `#/system` | Gateway health (COBOL or Python rule, thresholds version), latency p50/p95 from this session vs the §11.2 targets, fail-safe instructions, recent journal |

## Run

```bash
# Through the gateway (real system): see txn/README.md, then
ssh -i key.pem -L 8080:localhost:8080 linux1@<IP>    # open http://localhost:8080

# Different API origin (development only; the gateway doesn't enable CORS):
http://localhost:8080/?api=http://localhost:8080
```

**Offline demo mode.** If `/health` doesn't answer as CLMI within 2.5 s, or with `?mock=1`, the page runs on `mock.js`, which simulates the gateway in the browser. A banner and an "Offline demo" pill are shown the whole time. Never record the submission video in this mode.

## Demo script mapping (plan.md §12.1)

1. **Genuine claim** → GREEN, waterfall.
2. **AI-generated photo** → RED; open in the adjuster view for the heatmap and Granite note.
3. **Inflated damage** → AMBER or RED; heatmap over the edit.
4. **Re-used photo, new plate** → auto-attaches the last submitted photo → RED `DUPLICATE_PHOTO`.
5. **Who sees what** → adjuster view, switch Adjuster ↔ Authorised.
6. **Fail-safe** → stop the risk service, submit → AMBER `RISK_SERVICE_UNAVAILABLE`.

Use the real demo assets from A for the video. "Use a synthetic test image" draws a labelled placeholder in the browser, only for checking the flow. With the stub risk service, its filename drives the score (`fake_*` → RED, `inflated_*` → AMBER).

## Notes and gaps

- **Heatmap:** the UI calls `GET /claims/{id}/heatmap` and expects `{"grid": [[0..1] × 4] × 4]}`. C's gateway doesn't have this route yet, and B's risk service has to produce the grid. Until both exist, the UI shows "Heatmap not available" and the browser console logs a 404 for AMBER/RED claims.
- **Photos in the adjuster view** are previewed only in the tab that uploaded them (object URLs). The gateway never serves originals back; they stay in the encrypted evidence store.
- **Latency on Windows** includes about 40–50 ms of process start-up for the COBOL rule. Measure on the LinuxONE VM for the video.
- All server data is escaped before it's inserted (claim text is untrusted). The theme choice is the only thing kept in `localStorage`.
