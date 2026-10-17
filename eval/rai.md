# Responsible-AI statement

Aegis screens claim photos and routes flagged claims to human review. It never rejects a claim automatically. This statement separates implemented controls from A's pending model evaluation.

## Human review and failures

GREEN continues normal processing, AMBER requires review, and RED refers a claim to an investigator with reasons. RED is not proof of fraud. Risk errors/timeouts, malformed responses, unavailable thresholds and policy failures produce AMBER. Automated tests cover these cases, replay and concurrent duplicate submissions. Native GnuCOBOL policy/gateway tests passed on LinuxONE; the current pre-model deployment exercises the degraded path.

## Accuracy and calibration remain pending

The planned validation false-positive caps are 1% for RED and 5% for AMBER. These are targets, not established results. A and B must fit thresholds on deployed LinuxONE scores, including the policy's four-decimal rounding, and report test performance by source, camera subgroup and compression level. Stock ImageNet timings do not establish detector accuracy. Dataset balance, shortcut tests, held-out generators, subgroup FPR and calibration remain unverified until A supplies the dataset/model cards and evaluation results.

Decisions record model/config versions and reasons. Missing EXIF and C2PA-marker presence are informational; marker presence is not signature validation. Duplicate matching and metadata are additional signals with their own limitations.

## Advisory local language model

Granite is queued only after an AMBER/RED decision commits and cannot alter that decision. The typed internal API uses a shared secret, bounded queues and sequential inference. Claim text is untrusted; accepted PII findings must occur literally in the input. Notes require three bullets and the disclaimer that a detector score is one signal, not proof. Invalid output becomes unavailable.

The runtime, provisioning tool and real-model smoke checks are prepared. Actual inference, adversarial/privacy acceptance, throughput and memory are verified only when VM smoke results are saved. No external LLM fallback is configured. Passing small fixture sets cannot prove resistance to all prompt injection or PII leakage.

## Privacy and storage

Structured identity and detected identifiers receive stable HMAC pseudonyms. Regex/gazetteer rules can miss unusual identifiers. The private token vault stores identity values and the restricted claim record can temporarily contain missed identifiers. With Granite enabled, descriptions and damage-category free text are withheld from adjuster views and current egress payloads while PII review is pending or unavailable. Successful PII review releases reviewed text independently of note success. GREEN and Granite-disabled claims explicitly report `rules_only`, without claiming a second pass.

Identity reveal is explicit and journaled. `X-Role` and the console role selector are demo controls; access is through SSH to the loopback-bound gateway. Production needs real authentication, authorisation and retention controls. The HMAC key is a protected runtime secret, not hardware-backed in this prototype.

Original photo bytes are AES-256-GCM encrypted with fresh nonces. Crop arrays, SQLite databases and the vault are sensitive ordinary files in restricted directories. The detector reads original pixels because image redaction changes the traces it analyses. No public original-download endpoint is exposed.

Allowlisted payloads are prepared and logged locally. A PREPARED/EGRESS audit row does not mean external transmission occurred. Historical first-pass rows are retained; subsequent PII review updates the current view with an additional journal event. No outbound claim-data service is configured.

## Provenance and limits

Console personas and offline scores/maps/notes are fictional and labelled demo data. A's image licences, consent, labels and exclusion rules require their final dataset card; they are not asserted as completed here. D's optional evidence assets have a separate attribution manifest.

Compression, unseen generators, adaptive manipulation and camera differences can change error rates. Patch-score maps are advisory; edit localisation is unvalidated. CICS is emulated with FastAPI/SQLite; the policy is real COBOL on LinuxONE. The PyTorch/NNPA fallback is used; no zDLC compilation is claimed.

Before use beyond a prototype: complete A's evaluation, validate privacy/LLM behavior on representative data, implement authenticated roles and retention, test a private backup restore, monitor subgroup errors, and provide an appeal/review process.
