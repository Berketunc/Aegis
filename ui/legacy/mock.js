/* Offline demo backend. Used ONLY when the CLMI gateway can't be reached (or with ?mock=1).
 * It imitates the gateway's API shapes (txn/README.md) so the UI can be shown without LinuxONE.
 * The page shows a banner whenever this is active. Nothing here is a real model or a real decision. */
(function () {
  "use strict";

  const T = { version: "t-dev", tau_red: 0.85, tau_amber: 0.60 };
  const MAX_BYTES = 15 * 1024 * 1024;
  const db = {
    seq: 0, claimSeq: 0,
    evidence: new Map(),   // evidence_id -> {sha256, name}
    firstUse: new Map(),   // sha256 -> first claim_id
    claims: new Map(),     // claim_id -> {decision, record, egress, scenario}
    byKey: new Map(),      // idempotency key -> claim_id
    vault: new Map(),      // token -> value
    notes: new Map(),      // claim_id -> {status, note}
    journal: [],
  };

  const now = () => new Date().toISOString();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rand = (a, b) => a + Math.random() * (b - a);
  const round1 = (x) => Math.round(x * 10) / 10;

  function fnv(str) {
    let h = 0x811c9dc5;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
    return h.toString(16).padStart(8, "0");
  }

  async function sha256Hex(buf) {
    try {
      const d = await crypto.subtle.digest("SHA-256", buf);
      return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
    } catch (e) {
      const bytes = new Uint8Array(buf);
      let s = "";
      for (let i = 0; i < bytes.length; i += 97) s += String.fromCharCode(bytes[i]);
      return (fnv(s) + fnv(s + bytes.length) + fnv(String(bytes.length))).padEnd(64, "0");
    }
  }

  function journal(claim_id, event, payload) {
    db.journal.push({ seq: ++db.seq, ts: now(), txn: "CLMI", claim_id, event, payload_json: payload });
  }

  // --- pseudonymisation (simplified mirror of privacy/rules.py) ---
  const token = (kind, value) => `${kind}_${fnv(kind + "|" + value.toLowerCase().replace(/[\s()-]/g, ""))}`;
  const PATTERNS = [
    ["EMAIL", /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g],
    ["POLICY", /\bPN-\d{4}-\d{4}\b/gi],
    ["PHONE", /(?<![\w+])(?:\+44\s?|0)(?:\d\s?){9,10}(?!\w)/g],
    ["POSTCODE", /\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b/gi],
    ["VRM", /\b[A-Z]{2}\d{2}\s?[A-Z]{3}\b/gi],
  ];
  function redact(text, gaz) {
    if (!text) return text;
    const sub = (kind, value) => { const t = token(kind, value); db.vault.set(t, value.trim()); return t; };
    const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    for (const [kind, value] of gaz) {
      if (value && value.trim().length > 1) text = text.replace(new RegExp(reEsc(value.trim()), "gi"), (m) => sub(kind, m));
    }
    const name = (gaz.find(([k]) => k === "PERSON") || [])[1] || "";
    for (const part of name.split(/\s+/)) {
      if (part.length >= 3) text = text.replace(new RegExp(`\\b${reEsc(part)}\\b`, "gi"), (m) => sub("PERSON", m));
    }
    for (const [kind, re] of PATTERNS) text = text.replace(re, (m) => sub(kind, m));
    return text;
  }

  function scoreFor(names) {
    if (/fake|sdxl|flux|generated/.test(names)) return { ai: rand(0.9, 0.97), kind: "generated" };
    if (/inflat|edit/.test(names)) return { ai: rand(0.66, 0.8), kind: "inflated" };
    return { ai: rand(0.04, 0.16), kind: "genuine" };
  }

  function decide(ai, dup, sw, riskOk) {
    const r = [];
    if (!riskOk) return { band: "AMBER", reasons: ["RISK_SERVICE_UNAVAILABLE"] };
    if (dup) r.push(["DUPLICATE_PHOTO", 2]);
    if (ai >= T.tau_red) r.push(["AI_SCORE_ABOVE_RED", 2]);
    else if (ai >= T.tau_amber) r.push(["AI_SCORE_ABOVE_AMBER", 1]);
    if (sw) r.push(["EDITING_SOFTWARE_TAG", 1]);
    if (!r.length) return { band: "GREEN", reasons: ["NONE"] };
    const sev = Math.max(...r.map((x) => x[1]));
    return { band: ["GREEN", "AMBER", "RED"][sev], reasons: r.map((x) => x[0]) };
  }

  const NOTES = {
    generated: "- The detector gives a high calibrated score and the band is RED.\n- The score is one signal, not proof; request the original photo file and any other photos of the vehicle.\n- The description is consistent with the claimed damage type.",
    inflated: "- The detector score is elevated; the band reflects the score and any duplicate or metadata signals.\n- The score is one signal, not proof; compare the photo with earlier photos of the vehicle and check the edited area.\n- The described incident is plausible for the claimed damage.",
    duplicate: "- This photo matches a photo already used on another claim.\n- The match is one signal, not proof; check the earlier claim and the vehicle registration on file.\n- The description doesn't explain why the same photo appears twice.",
  };

  function heatGrid(kind) {
    const g = [];
    for (let y = 0; y < 4; y++) {
      const row = [];
      for (let x = 0; x < 4; x++) {
        let v = rand(0.02, 0.12);
        if (kind === "generated") v = rand(0.55, 0.9);
        if (kind === "inflated" && (x === 1 || x === 2) && y === 2) v = rand(0.75, 0.95);
        row.push(round1(v * 100) / 100);
      }
      g.push(row);
    }
    return g;
  }

  async function handle(method, fullPath, opts) {
    const url = new URL(fullPath, "http://mock");
    const path = url.pathname;
    const q = url.searchParams;
    const headers = Object.fromEntries(Object.entries(opts.headers || {}).map(([k, v]) => [k.toLowerCase(), v]));
    const ok = (body) => ({ status: 200, body });
    const err = (status, detail) => ({ status, body: { detail } });

    if (method === "GET" && path === "/health") {
      return ok({ status: "ok", txn: "CLMI", thresholds_version: T.version, policy_engine: "simulated" });
    }

    if (method === "POST" && path === "/evidence") {
      const file = opts.form && opts.form.get("file");
      if (!file) return err(422, "file is required");
      if (file.size > MAX_BYTES) return err(413, "file too large");
      const buf = await file.arrayBuffer();
      const head = new Uint8Array(buf.slice(0, 8));
      const isJpeg = head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff;
      const isPng = head[0] === 0x89 && head[1] === 0x50 && head[2] === 0x4e && head[3] === 0x47;
      if (!isJpeg && !isPng) return err(415, "only JPEG or PNG photos are accepted");
      const sha = await sha256Hex(buf);
      const id = "EV-" + sha.slice(0, 8).toUpperCase();
      db.evidence.set(id, { sha256: sha, name: (file.name || "").toLowerCase() });
      journal(null, "EVIDENCE", { evidence_id: id, sha256: sha, size_bytes: file.size });
      await sleep(rand(60, 120));
      return ok({ evidence_id: id, sha256: sha });
    }

    if (method === "POST" && path === "/claims") {
      const b = opts.json || {};
      const key = b.idempotency_key || headers["idempotency-key"] || String(Math.random());
      const ev = (b.evidence || []).map((e) => e.evidence_id);
      if (!ev.length) return err(400, "at least one photo is required");
      const missing = ev.filter((id) => !db.evidence.has(id));
      if (missing.length) return err(400, "unknown evidence_id: " + missing.join(", "));
      if (db.byKey.has(key)) {
        const cid = db.byKey.get(key);
        journal(cid, "REPLAY", { idem_key: key });
        await sleep(rand(4, 9));
        return ok({ ...db.claims.get(cid).decision, idempotency_key: key, replayed: true });
      }
      const claim_id = "CLM-" + String(++db.claimSeq).padStart(6, "0");
      const c = b.claimant || {};
      const gaz = [["PERSON", c.name], ["EMAIL", c.email], ["PHONE", c.phone], ["POLICY", b.policy_number]];
      const record = {
        policy_number: redact(b.policy_number, [["POLICY", b.policy_number]]),
        claimant: { name: redact(c.name, [["PERSON", c.name]]), email: c.email ? redact(c.email, [["EMAIL", c.email]]) : null,
                    phone: c.phone ? redact(c.phone, [["PHONE", c.phone]]) : null },
        incident: { date: (b.incident || {}).date, description: redact((b.incident || {}).description || "", gaz) },
        evidence: ev,
        damage_category: b.damage_category || null,
      };
      const items = ev.map((id) => db.evidence.get(id));
      const names = items.map((i) => i.name).join(" ");
      const { ai, kind } = scoreFor(names);
      let dupOf = null;
      for (const it of items) {
        if (!db.firstUse.has(it.sha256)) db.firstUse.set(it.sha256, claim_id);
        else if (db.firstUse.get(it.sha256) !== claim_id) dupOf = db.firstUse.get(it.sha256);
      }
      const sw = /gimp|ps_/.test(names);
      const exif = !/noexif/.test(names);
      const { band, reasons } = decide(ai, !!dupOf, sw, true);
      if (!exif) { if (reasons[0] === "NONE") reasons.length = 0; reasons.push("INFO_EXIF_MISSING"); }
      const d = { privacy: rand(1, 3), load_crops: rand(0.5, 1.2), inference: rand(32, 48), metadata: rand(0.8, 1.6), dup: rand(0.6, 1.4) };
      const riskTotal = d.load_crops + d.inference + rand(1, 3);
      const risk = riskTotal + rand(1.5, 4), policy = rand(2, 4), gateway = rand(4, 8);
      const timings = { gateway: round1(gateway), risk: round1(risk), policy: round1(policy), total: round1(gateway + risk + policy),
                        risk_detail: Object.fromEntries(Object.entries({ ...d, total: riskTotal }).map(([k, v]) => [k, Math.round(v * 100) / 100])) };
      const decision = { claim_id, band, reasons, model_version: "simulated", thresholds_version: T.version,
                         policy_engine: "simulated", timings_ms: timings };
      const egress = { egress_id: "EG-" + claim_id.slice(4), dropped: ["claimant", "policy_number", "evidence"],
        payload: { description_redacted: record.incident.description, damage_category: record.damage_category, band,
                   ai_score: Math.round(ai * 10000) / 10000, exif_present: exif, software_tag_present: sw, c2pa_manifest: false,
                   duplicate_detected: reasons.includes("DUPLICATE_PHOTO") } };
      if (!record.damage_category) delete egress.payload.damage_category;
      db.claims.set(claim_id, { decision, record, egress, kind: dupOf ? "duplicate" : kind });
      db.byKey.set(key, claim_id);
      journal(claim_id, "DECISION", { band, reasons, model_version: "simulated", thresholds_version: T.version,
                                       policy_engine: "simulated", timings_ms: timings });
      journal(claim_id, "EGRESS", { egress_id: egress.egress_id, fields: Object.keys(egress.payload).sort(), dropped: egress.dropped });
      if (band !== "GREEN") {
        db.notes.set(claim_id, { status: "pending", note: null });
        setTimeout(() => {
          db.notes.set(claim_id, { status: "ready", note: NOTES[dupOf ? "duplicate" : kind] || NOTES.inflated });
          journal(claim_id, "PII_SECOND_PASS", { tokens_added: [] });
          journal(claim_id, "NOTE", { status: "ready" });
        }, 2500);
      }
      await sleep(timings.total);
      return ok({ ...decision, idempotency_key: key, replayed: false });
    }

    let m = path.match(/^\/claims\/([^/]+)(\/views|\/heatmap)?$/);
    if (method === "GET" && m) {
      const c = db.claims.get(decodeURIComponent(m[1]));
      if (!c) return err(404, "no such claim");
      if (!m[2]) {
        const note = db.notes.get(c.decision.claim_id) || { status: "not_requested", note: null };
        return ok({ ...c.decision, adjuster_note: note });
      }
      if (m[2] === "/heatmap") {
        if (c.decision.band === "GREEN") return err(404, "heatmap is computed for AMBER/RED claims only");
        return ok({ claim_id: c.decision.claim_id, grid: heatGrid(c.kind) });
      }
      const role = headers["x-role"] || "adjuster";
      const out = { claim_id: c.decision.claim_id, role, pseudonymised: c.record, egress: c.egress, original: null };
      if (role === "authorised") {
        const relink = (v) => typeof v === "string" ? v.replace(/\b[A-Z]+_[0-9a-f]{8}\b/g, (t) => db.vault.get(t) || t)
          : Array.isArray(v) ? v.map(relink) : v && typeof v === "object" ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, relink(x)])) : v;
        out.original = relink(c.record);
        journal(c.decision.claim_id, "VIEW_ORIGINAL", { role });
      }
      return ok(out);
    }

    if (method === "GET" && path === "/journal") {
      const cid = q.get("claim_id");
      const limit = Math.min(Number(q.get("limit") || 200), 1000);
      const rows = cid ? db.journal.filter((j) => j.claim_id === cid) : db.journal.slice().reverse().slice(0, limit);
      return ok(rows);
    }

    return err(404, "Not Found");
  }

  window.AegisMock = { handle };
})();
