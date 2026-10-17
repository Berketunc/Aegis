# UI: Aegis claims-integrity console

A console for insurance staff (claims handlers and SIU investigators) to review the results of AI-image screening. Claims arrive **automatically** from the insurer's claims system into transaction `CLMI` on IBM Z; there is no claim-submission page. The console shows what was screened, what was flagged and why, and lets staff work the flagged cases.

Static HTML/CSS/JS with no build step and no external requests. The CLMI gateway serves this folder at `/` (`UI_DIR`), so it works through the SSH tunnel.

| Page | What it's for |
|---|---|
| `#/dashboard` | KPIs (screened, fast-tracked, awaiting review, with investigators, decision-time p95), volume by band over time, flag reasons, decision-time distribution, "needs attention" list, pipeline status |
| `#/queue` | Work queue: filter by band, status, reason and assignee; sort by priority, AI score, received time or SLA; open cases first |
| `#/claims/<id>` | Case view: decision and reasons, score against thresholds, evidence and attention map, pseudonymised claim (SIU can reveal identity, which is journaled), egress payload, case actions and history, Granite note, timing breakdown, audit trail |
| `#/audit` | The append-only CLMI journal, plus reviewer actions |
| `#/policy` | Bands, thresholds, the COBOL rule `CLMIPOL`, guarantees, and what's real vs emulated |
| `#/system` | Service status, latency p50/p95/p99 per stage against targets, trust-boundary diagram |

**Roles** (top-right): *Claims handler* can assign, request evidence, clear AMBER claims and refer them to SIU. *SIU investigator* can also clear or close RED/referred cases and reveal identity. There's no reject button: outcomes are human decisions.

## Data sources

- **Live:** if `/health` answers as CLMI, the console reads the gateway: `/journal` (claim list and audit), `/claims/{id}/views` (pseudonymised record, egress payload), `/claims/{id}` (Granite note), `/claims/{id}/heatmap` (if present). It polls every 5 s and announces new claims.
- **Demo:** otherwise (or with `?demo=1`) it shows the 28 fictional claims in `data.js`, with simulated results and a "Demo data" banner throughout.

## Pre-filling the live system

`demo/feed_claims.py` stands in for the insurer's claims system and sends the same 28 claims through the real gateway, so the bands are decided by the real COBOL rule:

```bash
python demo/feed_claims.py --gateway http://localhost:8080 --stub http://localhost:9000            # dev stub
python demo/feed_claims.py --gateway http://localhost:8080 --stub http://localhost:9000 --interval 5   # trickle, for the video
python demo/feed_claims.py --gateway http://localhost:8080 --photos ./demo_photos                  # B's real risk service
```

It checks each live band against the dataset and reports any mismatch. Re-running it is safe, because each claim has idempotency key `demo-<key>`.

## Evidence photos

`ui/evidence/manifest.json` lists one photo per demo claim:
- **24 real photos** from Wikimedia Commons. Each was uploaded before 2022 under an open licence (CC0, public domain, CC BY or CC BY-SA), viewed by hand, and checked for no injured people and visible damage matching the claim. The console shows author and licence under each photo.
- **2 AI images supplied by the team.** D07 is an AI-generated front-end crash. D18 is the right half of a real/AI-edited Transit pair. Openly licensed AI car-damage images don't exist on Commons or Openverse.

`python demo/fetch_evidence.py` downloads the photos and writes `evidence.js`, which maps each photo's `EV-…` evidence ID to its file. Photos aren't committed to git (plan.md); the manifest and script are. Team-supplied images go in as `ui/evidence/D07_source.jpg` and `D18_source.jpg`; the script crops D18 to the edited half. Then reset the gateway database and re-run the feeder, so the live evidence IDs match.

AMBER claims with a real photo (D04, D16, D23, D27) show the review case: a genuine photo that reached the amber threshold and needs a person to clear it. The confidently fake claims are D07 and D18 (RED, AI score), plus D21 and D26 (RED, re-used photo).

## Known gaps

- **Case actions are stored in the browser** (localStorage). The gateway has no case-management endpoint yet; in production these would be written back to the claims system and journaled.
- **Heatmap:** the gateway route `/claims/{id}/heatmap` exists, but it answers 503 until a trained model is loaded, so live mode shows "not available" for AMBER/RED claims until then.
- **Photos aren't displayed:** originals stay in the encrypted evidence store, and there's no authorised evidence-viewer endpoint yet.
- **Live claims show "Claims system feed" as their channel**, because the gateway doesn't record a channel.
- The **old site** (claim form + adjuster view) is kept in `ui/legacy/` and served at `/legacy/`.
