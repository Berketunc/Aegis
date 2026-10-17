# Responsible-AI statement

Aegis screens the photos attached to motor-insurance claims for signs of AI generation or AI editing, inside the claims transaction on IBM Z, and routes flagged claims to a person. It never rejects a claim automatically. This statement separates what is implemented and measured from what is still pending.

## Human review and failure behaviour

GREEN continues normal processing. AMBER requires human review. RED refers a claim to an investigator with the reasons attached. RED is not proof of fraud, and nobody is told "fake": adjusters see a band, a calibrated score and reason codes. Keeping a person in every adverse decision is how the design meets UK GDPR Article 22 on solely automated decisions.

Nothing fails open. A risk-service error or timeout (300 ms), a malformed response, unavailable thresholds, a mismatched threshold version or a policy-engine failure all produce AMBER. The decision rule is real COBOL (`txn/CLMIPOL.cbl`) with a Python twin; 25 truth-table cases run against both and must agree. These paths passed natively on LinuxONE (`eval/native_gateway_tests.json`) and in the fail-safe smoke (`linuxone/smoke_failures.py`).

## Accuracy, calibration and who gets flagged

Model `det-r18-v4` (`eval/model_card.md`, `eval/results.md`), test split, thresholds `t-0`:

- Real damage photos wrongly reaching RED: **1.2%**; reaching AMBER: **5.0%**. Thresholds are set by these false-positive caps (1% and 5% on validation), not by how many fakes they catch.
- RED rate among real test photos stays between **0.4% and 2.8%** across JPEG quality, image size, compressibility, source and damaged/undamaged subgroups (`eval/runs/det-r18-v4_mean_audit.md`). The Commons and undamaged subgroups are small (n = 41 and 36) and are reported as such.
- Fully generated images from a generator never seen in training (FLUX.1-schnell): TPR **0.50** at 1% FPR, AUROC 0.944. In-distribution: TPR 0.67 at 1% FPR. Reported rather than hidden.
- Small inpainted edits are the main weakness: AUROC 0.62 with the standard three crops. The heatmap exists to show adjusters where to look, not to prove an edit.
- **Small photos are the main fairness risk.** On test photos halved in size, 16.8% of real ones exceed τ_red. The decision rule therefore caps the AI score at AMBER when the native short side is below 480 px (`LOW_RESOLUTION_EVIDENCE`), so claimants with older phones or compressed messaging-app photos are not sent to an investigator on the score alone. The duplicate-photo rule still applies.
- Scores are temperature-calibrated (T = 0.676). `t-0` was fitted on Modal FP32 scores; `t-1` is fitted on the compiled model's LinuxONE scores after the parity check (`linuxone/RUNBOOK.md`, step 0b) because 16-bit accelerator arithmetic shifts scores slightly.

Every decision records model version, thresholds version, reasons and timings. Missing EXIF and a present C2PA marker are informational only and never change the band; the C2PA check is marker presence, not signature validation.

## Data and shortcut controls

Real photos come from CarDD (research licence, collected before May 2022) and Wikimedia Commons (CC0, public domain, CC BY, CC BY-SA, last uploaded before 1 January 2022, attribution kept in `data/commons_manifest.csv`). Images showing injured people are excluded by a scene filter and a visual spot-check (`data/spotcheck_log.md`). No images are redistributed; only manifests are in Git.

Half of the fakes are regenerated copies of the real photos, so content cannot separate the classes: the damage share differs by 0.5 points between real and fake, and logistic regressions on file size, dimensions and global colour score AUROC 0.50 to 0.55 (`data/dataset_card.md`). On content-matched pairs the model reaches AUROC 0.985, which means it reads generation traces, not scene content.

## Advisory local language model

Granite runs on LinuxONE through llama.cpp, only after an AMBER or RED decision is committed, and cannot alter that decision. Claim text is treated as untrusted input: it is fenced, accepted PII findings must occur literally in the input, notes must be three bullets that include the statement that the detector score is one signal, not proof, and any output that fails validation becomes "note unavailable". No external LLM is configured. Real-model behaviour on the injection and PII cases is verified by `linuxone/smoke_granite.py`; passing a small fixture set does not prove resistance to all prompt injection.

## Privacy and storage

Structured identity and detected identifiers receive stable HMAC pseudonyms; investigators can link records without seeing identities, and re-identification is an explicit, journaled action. Rules can miss unusual identifiers, so with Granite enabled, free text is withheld from adjuster views and egress payloads until the second pass succeeds. Original photo bytes are AES-256-GCM encrypted at rest. The detector reads the original pixels because redaction destroys the traces it analyses; privacy controls govern who sees personal data, not what the model sees. Anything that could leave IBM Z is built from an allowlist of named fields and logged. The demo's role header is not authentication; the gateway is reachable only through SSH to loopback.

## What is real, what is not

CICS is emulated by a FastAPI gateway following CICS patterns; the COBOL rule is real and runs on LinuxONE. The detector runs with PyTorch for s390x and the NNPA extension; zDLC compilation is planned, not achieved. Personas in the console and demo are fictional. Compression, unseen generators, adaptive fraudsters (for example re-photographing a screen) and new cameras can change error rates; the team's phone-photo hold-out measures the last of these when available.

Before any use beyond a prototype: fit `t-1` on LinuxONE scores, validate privacy and LLM behaviour on representative data, add real authentication, authorisation and retention, monitor subgroup error rates in production, and provide an appeal and review process for claimants.
