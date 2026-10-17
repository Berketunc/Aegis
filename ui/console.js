/* Aegis claims-integrity console.
 * Claims arrive automatically from the insurer's claims system and are screened by transaction CLMI on IBM Z.
 * This console is where claims staff review the results. It reads the CLMI gateway API (txn/README.md);
 * if the gateway can't be reached it shows the built-in demo claims in data.js, with a banner.
 * All server and dataset text is untrusted: it's escaped (esc) or set with textContent. */
(function () {
  "use strict";

  // ================================================================ utilities
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  const params = new URLSearchParams(location.search);
  const API = (params.get("api") || "").replace(/\/$/, "");

  function fmtMs(ms) { return typeof ms === "number" && isFinite(ms) ? (ms < 10 ? ms.toFixed(1) : String(Math.round(ms))) : "–"; }
  function fmtScore(s) { return typeof s === "number" ? s.toFixed(2) : "–"; }
  function pct(sorted, p) {
    if (!sorted.length) return null;
    return sorted[clamp(Math.ceil((p / 100) * sorted.length) - 1, 0, sorted.length - 1)];
  }
  function absTime(d) { return d ? d.toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–"; }
  function clock(d) { return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
  function relTime(d) {
    if (!d) return "–";
    const m = Math.round((Date.now() - d.getTime()) / 60000);
    if (m < 1) return "just now";
    if (m < 60) return m + " min ago";
    const h = Math.floor(m / 60);
    if (h < 24) return h + " h " + (m % 60) + " min ago";
    return Math.floor(h / 24) + " d ago";
  }
  function dur(ms) {
    const m = Math.round(Math.abs(ms) / 60000);
    return m >= 60 ? Math.floor(m / 60) + " h " + (m % 60) + " min" : m + " min";
  }
  function fnv(str) {
    let h = 0x811c9dc5;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
    return h >>> 0;
  }
  function rng(seed) { // mulberry32, deterministic per claim so demo values don't change between reloads
    let a = fnv(seed);
    return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }
  function store(key, val) {
    try {
      if (val === undefined) return JSON.parse(localStorage.getItem(key) || "null");
      localStorage.setItem(key, JSON.stringify(val));
    } catch (e) { /* storage unavailable: state stays in memory */ }
    return null;
  }

  // ================================================================ icons + copy
  const ICON = {
    GREEN: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.4 11.6 2.8 8l1.1-1.1 2.5 2.5 5.7-5.7 1.1 1.1z"/></svg>',
    AMBER: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5 15 14H1zM7.2 6v4h1.6V6zm0 5v1.6h1.6V11z"/></svg>',
    RED: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 1h1.5v14H3zM5.5 2h8l-2 3.5 2 3.5h-8z"/></svg>',
    dup: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 1h9v10H5zm1.5 1.5v7h6v-7zM2 4h1.5v8.5H11V14H2z"/></svg>',
    sw: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m11.3 1.3 3.4 3.4-8.4 8.4H2.9V9.7zM2 14.5h12V16H2z"/></svg>',
    exif: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1.3 2.3 2.4 1.2l12.4 12.4-1.1 1.1-1.6-1.6H1V4.5h2.3zM5.5 3H11l1 1.5h3v8.2l-1.5-1.5V6h-2.3l-1-1.5H6.8z"/></svg>',
    outage: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 1h1.5v3h3V1H11v3h1.5v4A4.5 4.5 0 0 1 8.8 12.4V15H7.2v-2.6A4.5 4.5 0 0 1 3.5 8V4H5z"/></svg>',
    info: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1a7 7 0 1 1 0 14A7 7 0 0 1 8 1zm-.8 6v5h1.6V7zm0-3v1.6h1.6V4z"/></svg>',
    camera: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l1.5 2H21v15H3V5h4.5zm3 4a5 5 0 1 0 0 10 5 5 0 0 0 0-10zm0 2a3 3 0 1 1 0 6 3 3 0 0 1 0-6z"/></svg>',
    lock: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 7V5a4 4 0 1 1 8 0v2h1v8H3V7zm1.5 0h5V5a2.5 2.5 0 0 0-5 0z"/></svg>',
  };
  const OUTCOME = {
    GREEN: "Fast-tracked to normal processing.",
    AMBER: "Held for human review.",
    RED: "Referred to an investigator. Aegis never rejects a claim.",
  };
  const REASON = {
    NONE: ["No signal crossed a threshold.", "ok"],
    AI_SCORE_ABOVE_RED: ["Detector score above the red threshold, which is set so that at most 1% of genuine photos reach it.", "crit"],
    AI_SCORE_ABOVE_AMBER: ["Detector score above the amber threshold, which is set so that at most 5% of genuine photos reach it.", "warn"],
    DUPLICATE_PHOTO: ["The same photo (perceptual-hash match) was already used on a different claim.", "crit"],
    EDITING_SOFTWARE_TAG: ["The photo's metadata names an editing or generation tool.", "warn"],
    RISK_SERVICE_UNAVAILABLE: ["Fail-safe: the risk service didn't answer within 300 ms, so the claim was held for review instead of approved.", "warn"],
    POLICY_ENGINE_ERROR: ["Fail-safe: the decision rule couldn't run.", "warn"],
    THRESHOLDS_UNAVAILABLE: ["Fail-safe: the thresholds configuration couldn't be read.", "warn"],
    POLICY_INPUT_INVALID: ["Fail-safe: the decision rule received malformed input.", "warn"],
    INFO_EXIF_MISSING: ["Information only: no camera metadata (messaging apps strip it). Never changes the band.", "info"],
    INFO_C2PA_MANIFEST_PRESENT: ["Information only: a C2PA content-credentials manifest is present (not yet validated).", "info"],
    LOW_RESOLUTION_EVIDENCE: ["Low-resolution photo (short side under 480 px): the detector is less reliable on small photos, so the AI score alone holds the claim for review instead of referring it.", "warn"],
  };
  const REASON_SHORT = {
    AI_SCORE_ABOVE_RED: "AI score above red", AI_SCORE_ABOVE_AMBER: "AI score above amber", DUPLICATE_PHOTO: "Re-used photo",
    EDITING_SOFTWARE_TAG: "Editing-software tag", RISK_SERVICE_UNAVAILABLE: "Fail-safe: risk service down",
    POLICY_ENGINE_ERROR: "Fail-safe: rule error", THRESHOLDS_UNAVAILABLE: "Fail-safe: thresholds", POLICY_INPUT_INVALID: "Fail-safe: bad input",
    INFO_EXIF_MISSING: "No camera metadata (info)", INFO_C2PA_MANIFEST_PRESENT: "C2PA manifest (info)", LOW_RESOLUTION_EVIDENCE: "Low-resolution photo", NONE: "No flags",
  };
  const DAMAGE = { dent: "Dent", scratch: "Scratch / scuff", lamp: "Smashed lamp", windscreen: "Cracked windscreen", bumper: "Bumper damage", other: "Other" };
  const STATUS = {
    fast_tracked: { label: "Fast-tracked", open: false },
    awaiting_review: { label: "Awaiting review", open: true },
    awaiting_siu: { label: "Awaiting investigator", open: true, attn: true },
    in_review: { label: "In review", open: true },
    evidence_requested: { label: "Evidence requested", open: true },
    referred_siu: { label: "Referred to SIU", open: true, attn: true },
    cleared: { label: "Cleared · released", open: false },
    closed_nfa: { label: "Closed · no further action", open: false },
    closed_withdrawn: { label: "Closed · claim withdrawn", open: false },
  };
  const ACTION = {
    assign: { label: "Assign to me", note: false, verb: "Assigned" },
    request_evidence: { label: "Request evidence", note: true, verb: "Requested evidence", desc: "Ask the claimant for original files or more photos. The case stays open." },
    clear: { label: "Clear and release", note: true, verb: "Cleared and released", desc: "Release the claim to normal processing. Record why the flags don't stand." },
    refer_siu: { label: "Refer to SIU", note: true, verb: "Referred to SIU", desc: "Send the claim to the Special Investigations Unit with your reasoning." },
    close_nfa: { label: "Close · no further action", note: true, verb: "Closed · no further action", desc: "Close the investigation; the claim proceeds through normal handling." },
    close_withdrawn: { label: "Close · claim withdrawn", note: true, verb: "Closed · claim withdrawn", desc: "The claimant withdrew the claim." },
  };
  const SLA_MIN = { RED: 240, AMBER: 1440 };
  const ACTOR_NAME = () => ({ handler: state.users.handler.name, siu: state.users.siu.name, "siu-queue": "SIU queue", system: "Aegis" });

  // ================================================================ decision rule (twin of CLMIPOL.cbl)
  function decide(s, T) {
    const f = (x) => clamp(Math.round(x * 10000), 0, 10000);
    if (!s.riskOk) return { band: "AMBER", reasons: ["RISK_SERVICE_UNAVAILABLE"] };
    const r = [];
    if (s.duplicate) r.push(["DUPLICATE_PHOTO", 2]);
    if (f(s.aiScore) >= f(T.tau_red) && s.lowRes) { r.push(["AI_SCORE_ABOVE_RED", 1]); r.push(["LOW_RESOLUTION_EVIDENCE", 1]); }
    else if (f(s.aiScore) >= f(T.tau_red)) r.push(["AI_SCORE_ABOVE_RED", 2]);
    else if (f(s.aiScore) >= f(T.tau_amber)) r.push(["AI_SCORE_ABOVE_AMBER", 1]);
    if (s.softwareTag) r.push(["EDITING_SOFTWARE_TAG", 1]);
    let band = "GREEN", reasons = ["NONE"];
    if (r.length) { band = ["GREEN", "AMBER", "RED"][Math.max(...r.map((x) => x[1]))]; reasons = r.map((x) => x[0]); }
    if (s.exif === false) reasons = (reasons[0] === "NONE" ? [] : reasons).concat("INFO_EXIF_MISSING");
    return { band, reasons };
  }

  // ================================================================ state
  const state = {
    mode: null,               // "live" | "demo"
    health: null,
    thresholds: null,         // {version, tau_red, tau_amber} when known
    users: (window.AEGIS_DEMO && window.AEGIS_DEMO.users) || { handler: { name: "Morgan Osei", initials: "MO", role: "Claims handler" }, siu: { name: "Kai Brandt", initials: "KB", role: "SIU investigator" } },
    role: store("aegis-role") || "handler",
    claims: new Map(),
    journal: [],              // gateway journal (asc by seq)
    revealed: new Set(),
    fresh: new Set(),
    queue: { band: "ALL", status: "open", reason: "", assignee: "", q: "", sort: "priority", dir: "asc" },
    dash: { range: "all" },
    audit: { event: "", q: "" },
    tableView: {},
    charts: [],
    route: { view: "dashboard", arg: null },
  };

  // ================================================================ pseudonymisation (demo data only; live data arrives already pseudonymised)
  const PATTERNS = [
    ["EMAIL", /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g],
    ["POLICY", /\bPN-\d{4}-\d{4}\b/gi],
    ["PHONE", /(?<![\w+])(?:\+44\s?|0)(?:\d\s?){9,10}(?!\w)/g],
    ["POSTCODE", /\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b/g],
    ["VRM", /\b[A-Z]{2}\d{2}\s?[A-Z]{3}\b/g],
  ];
  function tokenFor(kind, value, vault) {
    const t = kind + "_" + fnv(kind + "|" + value.toLowerCase().replace(/[\s()-]/g, "")).toString(16).padStart(8, "0");
    vault[t] = value.trim();
    return t;
  }
  function redact(text, gaz, vault) {
    if (!text) return text;
    const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    gaz.forEach(([kind, value]) => {
      if (value && value.trim().length > 1) text = text.replace(new RegExp(reEsc(value.trim()), "gi"), (m) => tokenFor(kind, m, vault));
    });
    const name = (gaz.find((g) => g[0] === "PERSON") || [])[1] || "";
    name.split(/\s+/).forEach((part) => {
      if (part.length >= 3) text = text.replace(new RegExp("\\b" + reEsc(part) + "\\b", "gi"), (m) => tokenFor("PERSON", m, vault));
    });
    PATTERNS.forEach(([kind, re]) => { text = text.replace(re, (m) => tokenFor(kind, m, vault)); });
    return text;
  }

  // ================================================================ data sources
  class ApiError extends Error { constructor(status, msg) { super(msg); this.status = status; } }
  async function api(path, headers, options) {
    let res;
    try { res = await fetch(API + path, { ...(options || {}), headers: headers || {} }); } catch (e) { throw new ApiError(0, "Gateway unreachable"); }
    const text = await res.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch (e) { body = { detail: text.slice(0, 200) }; }
    if (!res.ok) throw new ApiError(res.status, (body && typeof body.detail === "string" && body.detail) || res.statusText);
    return body;
  }
  async function pool(items, n, fn) {
    const out = new Array(items.length);
    let i = 0;
    await Promise.all(Array.from({ length: Math.min(n, items.length) }, async () => {
      while (i < items.length) { const k = i++; try { out[k] = await fn(items[k]); } catch (e) { out[k] = null; } }
    }));
    return out;
  }

  function absorbHealth(h) {
    state.health = h;
    // Live thresholds come from the gateway's config (A's eval/thresholds_<version>.json), never from demo data.
    if (h && typeof h.tau_red === "number" && typeof h.tau_amber === "number") {
      state.thresholds = { version: h.thresholds_version, tau_red: h.tau_red, tau_amber: h.tau_amber, low_res_short_side: h.low_res_short_side };
    }
  }

  const Live = {
    lastSeq: 0,
    async init() {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 2500);
      try {
        const res = await fetch(API + "/health", { signal: ctrl.signal });
        const h = await res.json();
        if (!h || h.txn !== "CLMI") throw new Error("not CLMI");
        absorbHealth(h);
        return true;
      } finally { clearTimeout(t); }
    },
    async load() {
      const rows = await api("/journal?limit=1000");
      await this.ingest(rows.slice().reverse());
    },
    async poll() {
      absorbHealth(await api("/health"));
      const rows = await api("/journal?limit=200");
      const fresh = rows.filter((r) => r.seq > this.lastSeq).reverse();
      if (!fresh.length) return { added: [], notes: [] };
      return this.ingest(fresh);
    },
    async ingest(rowsAsc) {
      const added = [], notes = [];
      const decisions = [];
      rowsAsc.forEach((r) => {
        this.lastSeq = Math.max(this.lastSeq, r.seq);
        state.journal.push(r);
        if (r.event === "DECISION" && r.claim_id && !state.claims.has(r.claim_id)) decisions.push(r);
        if (["NOTE", "PII_SECOND_PASS", "EGRESS_PII_UPDATED", "EGRESS_PRIVACY_RELEASED", "PRIVACY_REVIEW"].includes(r.event) && r.claim_id) notes.push(r.claim_id);
      });
      const views = await pool(decisions, 6, (r) => api("/claims/" + encodeURIComponent(r.claim_id) + "/views", { "X-Role": "adjuster" }));
      decisions.forEach((r, i) => { state.claims.set(r.claim_id, this.normalise(r, views[i])); added.push(r.claim_id); });
      await pool(Array.from(new Set(notes)), 4, async (id) => {
        const c = state.claims.get(id);
        if (c) { await this.refreshViews(c); c.note = undefined; }
      });
      return { added, notes };
    },
    normalise(r, v) {
      const p = r.payload_json || {};
      const record = v && v.pseudonymised ? v.pseudonymised : null;
      const eg = (v && v.egress) || { payload: {}, dropped: [] };
      const ep = eg.payload || {};
      const reasons = p.reasons || [];
      return {
        id: r.claim_id, receivedAt: new Date(r.ts), channel: null,
        band: p.band, reasons, aiScore: typeof ep.ai_score === "number" ? ep.ai_score : null,
        modelVersion: p.model_version, thresholdsVersion: p.thresholds_version, engine: p.policy_engine, timings: p.timings_ms || {},
        record, egress: eg, privacyReview: v && v.privacy_review,
        signals: {
          duplicate: !!ep.duplicate_detected || reasons.includes("DUPLICATE_PHOTO"), duplicateOf: null,
          softwareTag: ep.software_tag_present == null ? null : !!ep.software_tag_present,
          exif: ep.exif_present == null ? null : ep.exif_present, c2pa: ep.c2pa_manifest == null ? null : ep.c2pa_manifest,
          riskOk: typeof ep.ai_score === "number" && !reasons.some((r) => ["RISK_SERVICE_UNAVAILABLE", "THRESHOLDS_UNAVAILABLE", "THRESHOLDS_VERSION_MISMATCH"].includes(r)),
        },
        photos: ((record && record.evidence) || []).map((id) => ({ evidence_id: id })),
        note: undefined, heatmap: undefined, original: null, seededCase: null,
      };
    },
    async detail(c) {
      const [d] = await Promise.allSettled([
        api("/claims/" + encodeURIComponent(c.id)),
        this.refreshViews(c),
      ]);
      if (d.status === "fulfilled") { c.note = d.value.adjuster_note || null; c.privacyReview = d.value.privacy_review || c.privacyReview; }
      if (c.band !== "GREEN" && c.heatmap === undefined) {
        c.heatmap = null;
        try {
          const job = await api("/claims/" + encodeURIComponent(c.id) + "/heatmap", {}, { method: "POST" });
          c.heatmapJob = job.job_id;
          c.heatmapStatus = job.status;
          await this.pollHeatmap(c);
        } catch (e) { c.heatmapStatus = "unavailable"; }
      }
    },
    async refreshViews(c) {
      const v = await api("/claims/" + encodeURIComponent(c.id) + "/views", { "X-Role": "adjuster" });
      c.record = v.pseudonymised;
      c.egress = v.egress;
      c.privacyReview = v.privacy_review;
    },
    async pollHeatmap(c) {
      if (!c.heatmapJob || ["succeeded", "failed", "unavailable"].includes(c.heatmapStatus)) return;
      try {
        const job = await api("/claims/" + encodeURIComponent(c.id) + "/heatmap/" + encodeURIComponent(c.heatmapJob));
        c.heatmapStatus = job.status;
        if (job.status === "succeeded") c.heatmap = job.result && job.result.patch_scores || null;
      } catch (e) { c.heatmapStatus = "unavailable"; }
    },
    async reveal(c) {
      const v = await api("/claims/" + encodeURIComponent(c.id) + "/views", { "X-Role": "authorised" });
      c.original = v.original;
      await this.poll();
    },
    claimJournal(c) { return state.journal.filter((j) => j.claim_id === c.id); },
  };

  const Demo = {
    seq: 0,
    load() {
      const D = window.AEGIS_DEMO;
      state.thresholds = D.thresholds;
      const T = D.thresholds;
      const now = Date.now();
      this.byKey = {};
      const list = D.claims.slice().sort((a, b) => b.offset_min - a.offset_min);
      list.forEach((d) => {
        const idx = D.claims.indexOf(d);
        const id = "CLM-" + String(101 + idx).padStart(6, "0");
        this.materialise(d, id, D, T, now);
      });
      state.journal.sort((a, b) => a.ts.localeCompare(b.ts) || a.seq - b.seq);
    },
    materialise(d, id, D, T, now) {
      {
        const byKey = this.byKey;
        const R = rng(d.key);
        const vault = {};
        const gaz = [["PERSON", d.claimant.name], ["EMAIL", d.claimant.email], ["PHONE", d.claimant.phone], ["POLICY", d.policy_number]];
        const date = d.incident.date || new Date(now - d.incident.date_offset_days * 864e5).toISOString().slice(0, 10);
        const original = { policy_number: d.policy_number, claimant: Object.assign({}, d.claimant), incident: { date, description: d.incident.description }, damage_category: d.damage_category };
        const record = {
          policy_number: tokenFor("POLICY", d.policy_number, vault),
          claimant: { name: tokenFor("PERSON", d.claimant.name, vault), email: tokenFor("EMAIL", d.claimant.email, vault), phone: tokenFor("PHONE", d.claimant.phone, vault) },
          incident: { date, description: redact(d.incident.description, gaz, vault) },
          evidence: [], damage_category: d.damage_category,
        };
        const dupOf = d.duplicate_of ? byKey[d.duplicate_of] : null;
        const photos = [];
        for (let i = 0; i < d.photos; i++) {
          if (i === 0 && dupOf) { photos.push(Object.assign({}, dupOf.photos[0])); continue; }
          const h = fnv(d.key + ":" + i).toString(16).padStart(8, "0").toUpperCase();
          photos.push({ evidence_id: "EV-" + h, sha256: (h + fnv(h).toString(16).padStart(8, "0") + fnv(h + "x").toString(16)).toLowerCase().padEnd(64, "0"),
            name: d.exif_present === false ? "WhatsApp Image 2026-10-1" + (6 - (d.incident.date_offset_days % 3)) + " at 0" + (8 + i) + ".14.2" + i + ".jpeg" : "IMG_" + (2000 + (fnv(d.key + i) % 7000)) + ".jpg" });
        }
        // Use the real demo photo's evidence ID (same ID the gateway gives it in live mode).
        const ev = (window.AEGIS_EVIDENCE || {})[d.key];
        if (ev && !dupOf) photos[0].evidence_id = ev.evidence_id;
        record.evidence = photos.map((p) => p.evidence_id);
        const signals = { aiScore: d.ai_score, duplicate: !!dupOf, duplicateOf: dupOf ? dupOf.id : null, softwareTag: d.software_tag || null,
          exif: d.exif_present, c2pa: d.outage ? null : false, riskOk: !d.outage };
        const { band, reasons } = decide(signals, T);
        const gw = 4 + R() * 5, pol = 2 + R() * 3;
        const det = d.outage ? null : { privacy: 1 + R() * 2, load_crops: 0.5 + R() * 0.8, inference: 30 + R() * 18, metadata: 0.8 + R() * 0.9, dup: 0.6 + R() * 0.9 };
        const risk = d.outage ? 300 + R() * 2.5 : det.load_crops + det.inference + 2 + R() * 3;
        const timings = { gateway: +gw.toFixed(1), risk: +risk.toFixed(1), policy: +pol.toFixed(1), total: +(gw + risk + pol).toFixed(1) };
        if (det) timings.risk_detail = Object.fromEntries(Object.entries(det).map(([k, v]) => [k, +v.toFixed(2)]));
        const egressPayload = { description_redacted: record.incident.description, damage_category: d.damage_category, band,
          ai_score: d.outage ? undefined : d.ai_score, exif_present: d.outage ? undefined : d.exif_present,
          software_tag_present: d.outage ? undefined : !!d.software_tag, c2pa_manifest: d.outage ? undefined : false, duplicate_detected: reasons.includes("DUPLICATE_PHOTO") };
        Object.keys(egressPayload).forEach((k) => egressPayload[k] === undefined && delete egressPayload[k]);
        const c = {
          id, key: d.key, receivedAt: new Date(now - d.offset_min * 60000), channel: d.channel,
          band, reasons, aiScore: d.outage ? null : d.ai_score,
          modelVersion: d.outage ? null : D.model_version, thresholdsVersion: T.version, engine: "cobol", timings,
          record, egress: { egress_id: "EG-" + id.slice(4), payload: egressPayload, dropped: [] },
          signals: { duplicate: signals.duplicate, duplicateOf: signals.duplicateOf, softwareTag: signals.softwareTag, exif: d.outage ? null : d.exif_present, c2pa: signals.c2pa, riskOk: signals.riskOk },
          photos, vault, original, seededCase: d.case || null,
          note: band === "GREEN" || d.outage ? { status: "not_requested", note: null } : { status: "ready", note: this.noteFor(d, band, reasons, dupOf) },
          heatmap: band === "GREEN" || d.outage ? null : this.heatFor(d, reasons),
        };
        byKey[d.key] = c;
        state.claims.set(id, c);
        const t = c.receivedAt.getTime();
        photos.forEach((p) => this.push(t - 2400, null, "EVIDENCE", { evidence_id: p.evidence_id, sha256: p.sha256 }));
        this.push(t, id, "DECISION", { band, reasons, model_version: c.modelVersion, thresholds_version: T.version, policy_engine: "cobol", timings_ms: timings });
        this.push(t, id, "EGRESS", { egress_id: c.egress.egress_id, fields: Object.keys(egressPayload).sort(), dropped: [] });
        if (c.note.status === "ready") {
          this.push(t + 7000 + R() * 4000, id, "PII_SECOND_PASS", { tokens_added: [] });
          this.push(t + 9000 + R() * 4000, id, "NOTE", { status: "ready" });
        }
        return c;
      }
    },
    userSeq: 0,
    submitted: {},   // "name:size" of uploaded files -> demo key of the first claim that used it
    simulate(input, files) {
      // Browser-only stand-in for the gateway when no LinuxONE connection exists: no detector runs.
      // The score comes from the file name (fake/ai -> high, edit/inflat -> amber, otherwise low),
      // the same photo submitted twice is a duplicate, and the band comes from the CLMIPOL twin above.
      const D = window.AEGIS_DEMO, T = state.thresholds || D.thresholds, now = Date.now();
      const key = "U" + (++this.userSeq);
      const sigs = files.map((f) => f.name + ":" + f.size);
      const dupKey = sigs.map((x) => this.submitted[x]).find(Boolean) || null;
      const name = files.map((f) => f.name.toLowerCase()).join(" ");
      const R = rng("upload:" + sigs.join("|"));
      const ai = /fake|generated|synthetic|\bai[-_ .]/.test(name) ? 0.86 + R() * 0.13
        : /edit|inflat|retouch|photoshop|gimp/.test(name) ? T.tau_amber + R() * (T.tau_red - T.tau_amber) * 0.9
        : R() * 0.25;
      const d = {
        key, offset_min: 0, channel: "Manual submission (simulated)", policy_number: input.policy_number,
        claimant: { name: input.name, email: input.email || "", phone: input.phone || "" },
        incident: { date: input.date, date_offset_days: 0, description: input.description },
        damage_category: input.damage_category, photos: files.length, ai_score: +ai.toFixed(3),
        exif_present: R() > 0.25, software_tag: /gimp|photoshop/.test(name) ? "GIMP 2.10" : null,
        duplicate_of: dupKey, outage: false,
      };
      const id = "CLM-" + String(101 + D.claims.length + this.userSeq).padStart(6, "0");
      const c = this.materialise(d, id, D, T, now);
      c.photos.forEach((p, i) => {
        if (!files[i]) return;
        p.name = files[i].name;
        if (!(dupKey && i === 0)) EVID_BY_ID[p.evidence_id] = { file: "", url: URL.createObjectURL(files[i]), title: files[i].name, kind: "upload", license: "uploaded in this browser", license_url: "", source: "Manual submission (simulated)" };
      });
      sigs.forEach((x) => { if (!this.submitted[x]) this.submitted[x] = key; });
      state.journal.sort((a, b) => a.ts.localeCompare(b.ts) || a.seq - b.seq);
      state.fresh.add(id);
      setTimeout(() => state.fresh.delete(id), 3000);
      updateNav();
      return { claim_id: id, band: c.band, reasons: c.reasons, model_version: c.modelVersion, thresholds_version: c.thresholdsVersion,
               policy_engine: "CLMIPOL twin (simulated in browser)", timings_ms: c.timings, replayed: false, simulated: true };
    },
    push(ms, claim_id, event, payload) {
      state.journal.push({ seq: ++this.seq, ts: new Date(ms).toISOString(), txn: "CLMI", claim_id, event, payload_json: payload });
    },
    noteFor(d, band, reasons, dupOf) {
      const s = fmtScore(d.ai_score);
      const b = [];
      if (reasons.includes("DUPLICATE_PHOTO")) b.push("- The photo matches one already submitted on " + dupOf.id + " under a different policy and registration.");
      else if (reasons.includes("AI_SCORE_ABOVE_RED")) b.push("- The detector's calibrated score is " + s + ", above the red threshold" + (d.software_tag ? ", and the file was last saved by an editing tool (" + d.software_tag + ")." : "; camera metadata is present."));
      else if (reasons.includes("AI_SCORE_ABOVE_AMBER")) b.push("- The detector's calibrated score is " + s + ", in the amber range" + (d.exif_present === false ? "; the photo has no camera metadata, which is common for forwarded images." : "."));
      else if (reasons.includes("EDITING_SOFTWARE_TAG")) b.push("- The photo's metadata names an editing tool (" + d.software_tag + "), but the detector score is low (" + s + ").");
      b.push("- The score is one signal, not proof. Next step: request the original image file" + (dupOf ? " and compare with the earlier claim." : " and any other photos of the vehicle."));
      b.push(dupOf ? "- The incident description differs from the earlier claim that used the same photo." : "- The description is consistent with the claimed damage type (" + (DAMAGE[d.damage_category] || d.damage_category).toLowerCase() + ").");
      return b.join("\n");
    },
    heatFor(d, reasons) {
      const R = rng(d.key + "heat");
      const g = [];
      const hot = Math.floor(R() * 3) + 1, hotRow = Math.floor(R() * 2) + 1;
      for (let y = 0; y < 4; y++) {
        const row = [];
        for (let x = 0; x < 4; x++) {
          let v = 0.05 + R() * 0.13;
          if (reasons.includes("AI_SCORE_ABOVE_RED")) v = 0.55 + R() * 0.37;
          else if (reasons.includes("AI_SCORE_ABOVE_AMBER")) {
            if (y === hotRow && (x === hot || x === hot - 1)) v = 0.78 + R() * 0.17;
            else if (Math.abs(y - hotRow) <= 1 && Math.abs(x - hot + 0.5) <= 1.5) v = 0.3 + R() * 0.2;
          } else if (reasons.includes("EDITING_SOFTWARE_TAG")) v = 0.1 + R() * 0.2;
          row.push(+v.toFixed(2));
        }
        g.push(row);
      }
      return g;
    },
    async detail() { /* everything is preloaded */ },
    async reveal(c) {
      c.original = c.original || null;
      this.push(Date.now(), c.id, "VIEW_ORIGINAL", { role: "authorised" });
    },
    claimJournal(c) { return state.journal.filter((j) => j.claim_id === c.id); },
  };
  const Source = () => (state.mode === "live" ? Live : Demo);

  // ================================================================ demo evidence photos (ui/evidence, fetched by demo/fetch_evidence.py)
  const EVID = window.AEGIS_EVIDENCE || {};
  const EVID_BY_ID = {};
  Object.keys(EVID).forEach((k) => { EVID_BY_ID[EVID[k].evidence_id] = EVID[k]; });
  function evidenceFor(c) {
    for (const p of c.photos || []) { const e = EVID_BY_ID[p.evidence_id]; if (e) return e; }
    return null;
  }
  function photoUrl(e) { return e.url || "evidence/" + encodeURIComponent(e.file); }
  function creditHtml(e) {
    const title = esc((e.title || "").replace(/^File:/, ""));
    return '<p class="credit">' + (e.kind === "ai" ? "AI-generated image" : "Photo") + ": " + (e.page_url ? '<a href="' + esc(e.page_url) + '" target="_blank" rel="noopener">' + title + "</a>" : title) +
      (e.author ? " · " + esc(e.author) : "") + " · " + (e.license_url ? '<a href="' + esc(e.license_url) + '" target="_blank" rel="noopener">' + esc(e.license) + "</a>" : esc(e.license || "")) +
      " · " + esc(e.source || "Wikimedia Commons") + "</p>";
  }

  // ================================================================ case workflow (kept in this browser; see note in UI)
  const Cases = {
    data: {},
    key() { return "aegis-cases-" + state.mode; },
    load() { this.data = store(this.key()) || {}; },
    save() { store(this.key(), this.data); },
    defaults(c) {
      if (c.seededCase) {
        const now = Date.now();
        return {
          status: c.seededCase.status, assignee: c.seededCase.assignee,
          history: c.seededCase.history.map((h) => ({ at: new Date(now - h.offset_min * 60000).toISOString(), actor: h.actor, action: h.action, note: h.note, seeded: true })),
        };
      }
      return { status: c.band === "GREEN" ? "fast_tracked" : c.band === "RED" ? "awaiting_siu" : "awaiting_review", assignee: null, history: [] };
    },
    // Demo claims are identified by their dataset key (their times are relative to page load);
    // live claims by ID + receipt time, so a reset gateway database doesn't inherit old case state.
    stamp(c) { return c.key || c.receivedAt.toISOString(); },
    get(c) {
      const s = this.data[c.id];
      if (s && s.stamp === this.stamp(c)) return s;
      return this.defaults(c);
    },
    act(c, action, note) {
      const cur = this.get(c);
      const next = { status: cur.status, assignee: cur.assignee, history: cur.history.slice(), stamp: this.stamp(c) };
      const to = { request_evidence: "evidence_requested", clear: "cleared", refer_siu: "referred_siu", close_nfa: "closed_nfa", close_withdrawn: "closed_withdrawn" }[action];
      if (action === "assign") { next.assignee = state.role; if (cur.status === "awaiting_review" || cur.status === "awaiting_siu" || cur.status === "referred_siu") next.status = "in_review"; }
      else next.status = to;
      if (action === "refer_siu") next.assignee = "siu-queue";
      if (action !== "assign" && action !== "refer_siu" && !next.assignee) next.assignee = state.role;
      next.history.push({ at: new Date().toISOString(), actor: state.role, action, note: note || "" });
      this.data[c.id] = next;
      this.save();
    },
    available(c) {
      const k = this.get(c);
      const st = STATUS[k.status];
      if (!st || !st.open) return [];
      const siu = state.role === "siu";
      const redLike = c.band === "RED" || k.status === "referred_siu";
      const out = [];
      if (k.assignee !== state.role) out.push({ id: "assign" });
      out.push({ id: "request_evidence" });
      if (c.band === "AMBER" && k.status !== "referred_siu") out.push({ id: "refer_siu" });
      out.push({ id: "clear", disabled: redLike && !siu, why: "RED and referred claims are cleared by an SIU investigator. Switch role to try it." });
      if (redLike) {
        out.push({ id: "close_nfa", disabled: !siu, why: "Only an SIU investigator can close an investigation." });
        out.push({ id: "close_withdrawn", disabled: !siu, why: "Only an SIU investigator can close an investigation." });
      }
      return out;
    },
  };
  function caseOf(c) { return Cases.get(c); }
  function isOpen(c) { const s = STATUS[caseOf(c).status]; return !!(s && s.open); }
  function sla(c) {
    if (!isOpen(c) || !SLA_MIN[c.band]) return null;
    const due = c.receivedAt.getTime() + SLA_MIN[c.band] * 60000;
    // The clock stops while we wait for the claimant to send evidence.
    return { due: new Date(due), left: due - Date.now(), paused: caseOf(c).status === "evidence_requested" };
  }
  function priority(c) { return isOpen(c) ? (c.band === "RED" ? 0 : c.band === "AMBER" ? 1 : 2) : 3; }

  // ================================================================ small renderers
  const band = (b, lg) => '<span class="band ' + esc(b) + (lg ? " lg" : "") + '">' + (ICON[b] || "") + esc(b) + "</span>";
  function statusChip(c) {
    const k = caseOf(c), s = STATUS[k.status] || { label: k.status };
    return '<span class="status ' + (s.open ? (s.attn ? "attn" : "open") : "closed") + '">' + esc(s.label) + "</span>";
  }
  function assigneeName(a) { return a ? ACTOR_NAME()[a] || a : "–"; }
  function slaCell(c) {
    const s = sla(c);
    if (!s) return '<span class="faint">–</span>';
    if (s.paused) return '<span class="sla" title="SLA paused while waiting for the claimant">Paused</span>';
    if (s.left < 0) return '<span class="sla over" title="Due ' + esc(absTime(s.due)) + '">Overdue ' + esc(dur(s.left)) + "</span>";
    return '<span class="sla' + (s.left < 3600000 ? " soon" : "") + '" title="Due ' + esc(absTime(s.due)) + '">' + esc(dur(s.left)) + " left</span>";
  }
  function bandColor(b) { return b === "RED" ? "var(--crit)" : b === "AMBER" ? "var(--warn)" : "var(--good)"; }
  function scoreCell(c) {
    if (c.aiScore == null) return '<span class="faint" title="No score: the risk service didn\'t return signals">–</span>';
    return '<span class="scorecell"><span class="scorebar" aria-hidden="true"><span style="width:' + (c.aiScore * 100).toFixed(0) + "%;background:" + bandColor(c.band) + '"></span></span><span class="num">' + esc(fmtScore(c.aiScore)) + "</span></span>";
  }
  function signalIcons(c) {
    const s = c.signals, out = [];
    if (s.duplicate) out.push(['on', "dup", "Re-used photo: matches another claim"]);
    if (s.softwareTag) out.push(['warn', "sw", "Metadata names an editing tool"]);
    if (s.exif === false) out.push(['', "exif", "No camera metadata (information only)"]);
    if (!s.riskOk) out.push(['warn', "outage", "Fail-safe: risk service unavailable at receipt"]);
    if (!out.length) return '<span class="faint">–</span>';
    return '<span class="signals">' + out.map(([cls, ic, t]) => '<span class="sig ' + cls + '" title="' + esc(t) + '" aria-label="' + esc(t) + '">' + ICON[ic] + "</span>").join("") + "</span>";
  }
  function tokHtml(text, c) {
    // Highlight pseudonym tokens; when identity has been revealed, show the original values instead.
    const reveal = state.revealed.has(c.id);
    const vault = reveal ? (c.vault || vaultFromOriginal(c)) : null;
    return esc(text).replace(/\b[A-Z]+_[0-9a-f]{8}\b/g, (t) => (vault && vault[t] ? '<span class="revealed" title="' + t + '">' + esc(vault[t]) + "</span>" : '<span class="tok">' + t + "</span>"));
  }
  function vaultFromOriginal(c) {
    // Live: build token → value by walking the pseudonymised and original records in parallel.
    const v = {};
    const walk = (p, o) => {
      if (typeof p === "string" && typeof o === "string") {
        const toks = p.match(/\b[A-Z]+_[0-9a-f]{8}\b/g) || [];
        if (toks.length === 1 && p === toks[0]) v[toks[0]] = o;
        else if (toks.length) {
          const parts = p.split(/\b[A-Z]+_[0-9a-f]{8}\b/);
          let rest = o;
          toks.forEach((t, i) => {
            const before = parts[i], after = parts[i + 1];
            if (rest.startsWith(before)) rest = rest.slice(before.length);
            const end = after ? rest.indexOf(after) : rest.length;
            if (end >= 0) { v[t] = rest.slice(0, end); rest = rest.slice(end); }
          });
        }
      } else if (p && o && typeof p === "object") Object.keys(p).forEach((k) => walk(p[k], o[k]));
    };
    if (c.record && c.original) walk(c.record, c.original);
    return v;
  }

  function toast(html) {
    const t = $("#toast");
    t.innerHTML = html;
    t.hidden = false;
    clearTimeout(toast.t);
    toast.t = setTimeout(() => { t.hidden = true; }, 4500);
  }

  // ================================================================ tooltip + charts
  const tip = $("#tooltip");
  function showTip(evt, title, rows) {
    tip.replaceChildren();
    const h = document.createElement("div");
    h.className = "t-title"; h.textContent = title; tip.appendChild(h);
    rows.forEach((r) => {
      const row = document.createElement("div"); row.className = "t-row";
      const k = document.createElement("span"); k.className = "t-key";
      if (r.color) { const i = document.createElement("i"); i.style.background = r.color; k.appendChild(i); }
      k.appendChild(document.createTextNode(r.label));
      const b = document.createElement("b"); b.textContent = r.value;
      row.append(k, b); tip.appendChild(row);
    });
    tip.hidden = false;
    let x, y;
    if (evt.clientX != null && evt.type !== "focus") { x = evt.clientX + 14; y = evt.clientY + 14; }
    else { const r = evt.target.getBoundingClientRect(); x = r.right + 8; y = r.top; }
    const w = tip.offsetWidth, hh = tip.offsetHeight;
    if (x + w > innerWidth - 8) x = Math.max(8, x - w - 28);
    if (y + hh > innerHeight - 8) y = Math.max(8, innerHeight - hh - 8);
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  function hideTip() { tip.hidden = true; }
  function bindTips(root, lookup) {
    $$("[data-tip]", root).forEach((el) => {
      const on = (e) => { const t = lookup(el.dataset.tip); if (t) { showTip(e, t.title, t.rows); el.closest("g") && el.closest("g").classList.add("hover"); } };
      const off = () => { hideTip(); el.closest("g") && el.closest("g").classList.remove("hover"); };
      el.addEventListener("pointermove", on); el.addEventListener("focus", on);
      el.addEventListener("pointerleave", off); el.addEventListener("blur", off);
    });
  }
  function niceMax(v) {
    if (v <= 5) return Math.max(1, Math.ceil(v));
    const p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }
  function roundTopRect(x, y, w, h, r) {
    if (h <= 0) return "";
    r = Math.min(r, h, w / 2);
    return "M" + x + "," + (y + h) + "V" + (y + r) + "Q" + x + "," + y + " " + (x + r) + "," + y + "H" + (x + w - r) + "Q" + (x + w) + "," + y + " " + (x + w) + "," + (y + r) + "V" + (y + h) + "Z";
  }
  function roundRightRect(x, y, w, h, r) {
    if (w <= 0) return "";
    r = Math.min(r, w, h / 2);
    return "M" + x + "," + y + "H" + (x + w - r) + "Q" + (x + w) + "," + y + " " + (x + w) + "," + (y + r) + "V" + (y + h - r) + "Q" + (x + w) + "," + (y + h) + " " + (x + w - r) + "," + (y + h) + "H" + x + "Z";
  }
  function chartCard(id, title, sub, legendHtml) {
    const tv = !!state.tableView[id];
    return '<div class="card"><div class="card-h"><div><h2>' + esc(title) + "</h2>" + (sub ? "<p>" + esc(sub) + "</p>" : "") + '</div><button class="link-btn view-toggle" data-tv="' + id + '" aria-pressed="' + tv + '">' + (tv ? "Chart view" : "Table view") + "</button></div>" +
      '<div class="card-b">' + (legendHtml || "") + '<div class="chart" id="' + id + '"></div></div></div>';
  }
  function registerChart(id, render, table) {
    state.charts.push({ id, render, table });
  }
  function drawCharts() {
    state.charts.forEach((ch) => {
      const el = document.getElementById(ch.id);
      if (!el) return;
      if (state.tableView[ch.id]) { el.innerHTML = ch.table(); return; }
      ch.render(el);
    });
  }

  function stackedColumns(el, buckets) {
    const W = Math.max(280, el.clientWidth), H = 220, m = { l: 34, r: 8, t: 10, b: 26 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const max = niceMax(Math.max(1, ...buckets.map((b) => b.total)));
    const ticks = [0, max / 2, max].filter((v, i, a) => a.indexOf(v) === i);
    const slot = iw / buckets.length, bw = Math.min(24, slot * 0.62);
    const y = (v) => m.t + ih - (v / max) * ih;
    let s = '<svg viewBox="0 0 ' + W + " " + H + '" height="' + H + '" role="img" aria-label="Claims screened over time, stacked by band">';
    ticks.forEach((t) => { s += '<line class="gridline" x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + y(t) + '" y2="' + y(t) + '"/><text x="' + (m.l - 6) + '" y="' + (y(t) + 4) + '" text-anchor="end">' + (Number.isInteger(t) ? t : t.toFixed(1)) + "</text>"; });
    const every = Math.max(1, Math.ceil(46 / slot));
    buckets.forEach((b, i) => {
      const x = m.l + i * slot + (slot - bw) / 2;
      let acc = 0;
      const segs = ["GREEN", "AMBER", "RED"].filter((k) => b.counts[k] > 0);
      s += '<g>';
      segs.forEach((k, j) => {
        const y0 = y(acc), y1 = y(acc + b.counts[k]);
        const top = j === segs.length - 1;
        const h = y0 - y1 - (j > 0 ? 2 : 0);
        s += top ? '<path class="mark" d="' + roundTopRect(x, y1, bw, h, 4) + '" fill="' + bandColor(k) + '"/>' : '<rect class="mark" x="' + x + '" y="' + y1 + '" width="' + bw + '" height="' + Math.max(0, h) + '" fill="' + bandColor(k) + '"/>';
        acc += b.counts[k];
      });
      s += '<rect class="hit" x="' + (m.l + i * slot) + '" y="' + m.t + '" width="' + slot + '" height="' + ih + '" data-tip="' + i + '" tabindex="0" aria-label="' + esc(b.label + ": " + b.total + " claims") + '"/></g>';
      if (i % every === 0) s += '<text x="' + (m.l + i * slot + slot / 2) + '" y="' + (H - 8) + '" text-anchor="middle">' + esc(b.short) + "</text>";
    });
    s += '<line class="axis" x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + y(0) + '" y2="' + y(0) + '"/></svg>';
    el.innerHTML = s;
    bindTips(el, (i) => { const b = buckets[+i]; return b && { title: b.label, rows: [["RED", "RED"], ["AMBER", "AMBER"], ["GREEN", "GREEN"]].map(([k, l]) => ({ label: l, value: String(b.counts[k]), color: bandColor(k) })).concat([{ label: "Total", value: String(b.total) }]) }; });
  }
  function hBars(el, items, unit) {
    const W = Math.max(260, el.clientWidth), row = 30, labelW = Math.min(170, W * 0.42), m = { r: 34 };
    const H = Math.max(row, items.length * row);
    if (!items.length) { el.innerHTML = '<p class="empty small">No flags in this period.</p>'; return; }
    const max = Math.max(1, ...items.map((i) => i.value));
    const bw = W - labelW - m.r;
    let s = '<svg viewBox="0 0 ' + W + " " + H + '" height="' + H + '" role="img" aria-label="Flag reasons by count">';
    const maxChars = Math.floor((labelW - 8) / 6.2);
    items.forEach((it, i) => {
      const y = i * row, w = Math.max(2, (it.value / max) * bw);
      const lbl = it.label.length > maxChars ? it.label.slice(0, maxChars - 1) + "…" : it.label;
      s += '<g><text x="0" y="' + (y + row / 2 + 4) + '">' + esc(lbl) + "</text>" +
        '<path class="mark" d="' + roundRightRect(labelW, y + 7, w, 16, 4) + '" fill="var(--series-1)"/>' +
        '<text class="val" x="' + (labelW + w + 6) + '" y="' + (y + row / 2 + 4) + '">' + it.value + "</text>" +
        '<rect class="hit" x="0" y="' + y + '" width="' + W + '" height="' + row + '" data-tip="' + i + '" tabindex="0" aria-label="' + esc(it.label + ": " + it.value) + '"/></g>';
    });
    el.innerHTML = s + "</svg>";
    bindTips(el, (i) => { const it = items[+i]; return it && { title: it.label, rows: [{ label: unit, value: String(it.value) }, { label: "Code", value: it.code }] }; });
  }
  function histogram(el, values, target) {
    const W = Math.max(260, el.clientWidth), H = 200, m = { l: 30, r: 10, t: 22, b: 26 };
    if (!values.length) { el.innerHTML = '<p class="empty small">No decisions yet.</p>'; return; }
    const step = 20, top = Math.max(target + step, Math.ceil((Math.max(...values) + 1) / step) * step);
    const bins = Array.from({ length: top / step }, (_, i) => ({ lo: i * step, hi: (i + 1) * step, n: 0 }));
    values.forEach((v) => { bins[clamp(Math.floor(v / step), 0, bins.length - 1)].n++; });
    const iw = W - m.l - m.r, ih = H - m.t - m.b, max = niceMax(Math.max(...bins.map((b) => b.n)));
    const x = (v) => m.l + (v / top) * iw, y = (v) => m.t + ih - (v / max) * ih;
    const slot = iw / bins.length, bw = Math.max(2, Math.min(24, slot - 2));
    let s = '<svg viewBox="0 0 ' + W + " " + H + '" height="' + H + '" role="img" aria-label="Distribution of decision time per claim">';
    [0, max].forEach((t) => { s += '<line class="gridline" x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + y(t) + '" y2="' + y(t) + '"/><text x="' + (m.l - 6) + '" y="' + (y(t) + 4) + '" text-anchor="end">' + t + "</text>"; });
    bins.forEach((b, i) => {
      const bx = m.l + i * slot + (slot - bw) / 2;
      s += "<g>" + (b.n ? '<path class="mark" d="' + roundTopRect(bx, y(b.n), bw, y(0) - y(b.n), 4) + '" fill="var(--series-1)"/>' : "") +
        '<rect class="hit" x="' + (m.l + i * slot) + '" y="' + m.t + '" width="' + slot + '" height="' + ih + '" data-tip="' + i + '" tabindex="0" aria-label="' + b.lo + " to " + b.hi + " ms: " + b.n + ' claims"/></g>';
    });
    const every = [20, 40, 100, 200, 500, 1000].find((v) => (v * iw) / top >= 44) || 1000;
    for (let v = 0; v <= top - every / 2; v += every) s += '<text x="' + x(v) + '" y="' + (H - 8) + '" text-anchor="middle">' + v + "</text>";
    s += '<line class="ref-line" x1="' + x(target) + '" x2="' + x(target) + '" y1="' + (m.t - 6) + '" y2="' + y(0) + '"/><text class="ref" x="' + (x(target) - 4) + '" y="' + (m.t - 10) + '" text-anchor="end">Target p95 ' + target + " ms</text>";
    s += '<line class="axis" x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + y(0) + '" y2="' + y(0) + '"/><text x="' + (W - m.r) + '" y="' + (H - 8) + '" text-anchor="end">ms</text></svg>';
    el.innerHTML = s;
    bindTips(el, (i) => { const b = bins[+i]; return b && { title: b.lo + "–" + b.hi + " ms", rows: [{ label: "Claims", value: String(b.n) }] }; });
  }
  function tableHtml(head, rows) {
    return '<div class="table-wrap"><table class="plain"><thead><tr>' + head.map((h, i) => "<th" + (i ? ' class="num"' : "") + ">" + esc(h) + "</th>").join("") + "</tr></thead><tbody>" +
      rows.map((r) => "<tr>" + r.map((v, i) => "<td" + (i ? ' class="num"' : "") + ">" + esc(v) + "</td>").join("") + "</tr>").join("") + "</tbody></table></div>";
  }

  // ================================================================ derived data
  function claimsInRange() {
    const all = Array.from(state.claims.values());
    const lim = { "24h": 864e5, "7d": 7 * 864e5 }[state.dash.range];
    return lim ? all.filter((c) => Date.now() - c.receivedAt.getTime() <= lim) : all;
  }
  function buckets(list) {
    if (!list.length) return [];
    const minT = Math.min(...list.map((c) => c.receivedAt.getTime())), now = Date.now();
    const span = now - minT;
    // Smallest bucket that keeps the timeline to ≤ 24 slots; always show at least 12 slots ending now,
    // so a burst of claims reads as recent activity on a timeline rather than one lonely bar.
    const size = [60e3, 5 * 60e3, 10 * 60e3, 15 * 60e3, 30 * 60e3, 3600e3, 3 * 3600e3, 864e5].find((s) => span / s <= 24) || 864e5;
    const nb = Math.max(12, Math.ceil(span / size) + 1);
    const start = Math.floor(now / size) * size - (nb - 1) * size;
    const out = [];
    for (let t = start; t <= now; t += size) {
      const d = new Date(t);
      out.push({ t, counts: { GREEN: 0, AMBER: 0, RED: 0 }, total: 0,
        short: size < 864e5 ? clock(d) : d.toLocaleDateString([], { day: "numeric", month: "short" }),
        label: size < 864e5 ? clock(d) + "–" + clock(new Date(t + size)) : d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }) });
    }
    list.forEach((c) => { const b = out[Math.floor((c.receivedAt.getTime() - start) / size)]; if (b) { b.counts[c.band]++; b.total++; } });
    return out;
  }
  function openCount() { return Array.from(state.claims.values()).filter(isOpen).length; }

  // ================================================================ views
  function setCrumbs(parts) {
    $("#crumbs").innerHTML = parts.map((p, i) => (i ? '<span class="sep">/</span>' : "") + (p.href ? '<a href="' + p.href + '">' + esc(p.label) + "</a>" : "<span>" + esc(p.label) + "</span>")).join("");
    document.title = parts[parts.length - 1].label + " · Aegis Claims Integrity";
  }

  function viewDashboard(root) {
    setCrumbs([{ label: "Dashboard" }]);
    const list = claimsInRange();
    const n = list.length;
    const by = (b) => list.filter((c) => c.band === b);
    const openAmber = by("AMBER").filter(isOpen).length;
    const openRed = list.filter((c) => isOpen(c) && (c.band === "RED" || caseOf(c).status === "referred_siu")).length;
    const totals = list.map((c) => state.mode === "live" ? c.timings.precommit_total : c.timings.total).filter((x) => typeof x === "number").sort((a, b) => a - b);
    const p95 = pct(totals, 95), p50 = pct(totals, 50);
    const greenN = by("GREEN").length;
    const reasonCounts = {};
    list.forEach((c) => c.reasons.forEach((r) => { if (r !== "NONE") reasonCounts[r] = (reasonCounts[r] || 0) + 1; }));
    const reasons = Object.entries(reasonCounts).sort((a, b) => b[1] - a[1]).map(([code, value]) => ({ code, value, label: REASON_SHORT[code] || code }));
    const attention = list.filter((c) => isOpen(c) && c.band !== "GREEN").sort((a, b) => priority(a) - priority(b) || a.receivedAt - b.receivedAt).slice(0, 7);
    const earliest = n ? new Date(Math.min(...list.map((c) => c.receivedAt.getTime()))) : null;
    const bks = buckets(list);
    const tile = (label, value, sub, key, href) =>
      '<div class="card kpi"><div class="label">' + (key ? '<span class="key" style="background:' + key + '"></span>' : "") + esc(label) + "</div>" +
      (href ? '<a class="value" href="' + href + '">' + value + "</a>" : '<div class="value">' + value + "</div>") + '<div class="sub">' + sub + "</div></div>";

    root.innerHTML =
      '<div class="page-head"><div><h1>Claims screening overview</h1><p>Claims enter the screening transaction on LinuxONE. Flagged claims and service failures go to a person for review.</p></div>' +
      '<div class="seg" role="group" aria-label="Time range">' + [["24h", "Last 24 h"], ["7d", "Last 7 days"], ["all", "All"]].map(([k, l]) => '<button data-range="' + k + '" aria-pressed="' + (state.dash.range === k) + '">' + l + "</button>").join("") + "</div></div>" +
      '<div class="grid cols-5">' +
        tile("Claims screened", String(n), earliest ? "since " + esc(absTime(earliest)) : "no claims yet", null, "#/queue?band=ALL&status=all") +
        tile("Fast-tracked", String(greenN), n ? Math.round((100 * greenN) / n) + "% of screened · GREEN" : "–", "var(--good)", "#/queue?band=GREEN&status=all") +
        tile("Awaiting review", String(openAmber), "AMBER · open cases", "var(--warn)", "#/queue?band=AMBER&status=open") +
        tile("With investigators", String(openRed), "RED or referred · open", "var(--crit)", "#/queue?band=RED&status=open") +
        tile(state.mode === "live" ? "Before commit p95" : "Demo decision p95", p95 == null ? "–" : fmtMs(p95) + '<small style="font-size:.9rem;font-weight:500"> ms</small>', "p50 " + fmtMs(p50) + (state.mode === "live" ? " ms · excludes commit and HTTP" : " ms · simulated"), null, null) +
      "</div>" +
      '<div class="grid cols-main" style="margin-top:16px">' +
        chartCard("ch-volume", "Screening volume by band", "Claims received from the claims system, by decision band",
          '<div class="legend">' + ["RED", "AMBER", "GREEN"].map((b) => '<span><i style="background:' + bandColor(b) + '"></i>' + b + "</span>").join("") + "</div>") +
        '<div class="card"><div class="card-h"><div><h2>Needs attention</h2><p>Open flagged cases, most urgent first</p></div><a class="small" href="#/queue">Open queue →</a></div><div style="margin-top:10px">' +
          (attention.length ? '<ul class="attn-list">' + attention.map((c) => '<li><a href="#/claims/' + encodeURIComponent(c.id) + '">' + band(c.band) + '<span class="claim-id">' + esc(c.id) + "</span>" + slaCell(c) + '<span class="r">' + esc(c.reasons.map((r) => REASON_SHORT[r] || r).join(" · ")) + "</span></a></li>").join("") + "</ul>"
            : '<p class="empty small">Nothing waiting. All flagged claims have been handled.</p>') +
        "</div></div>" +
      "</div>" +
      '<div class="grid cols-3" style="margin-top:16px">' +
        chartCard("ch-reasons", "Why claims were flagged", "Reasons attached to decisions (a claim can have several)") +
        chartCard("ch-latency", "Decision time per claim", "Gateway receives the claim → band returned") +
        '<div class="card"><div class="card-h"><div><h2>Screening pipeline</h2><p>Status of the services on the decision path</p></div><a class="small" href="#/system">Details →</a></div><div class="card-b" style="padding:6px 0 4px">' + healthList(true) + "</div></div>" +
      "</div>";

    registerChart("ch-volume", (el) => stackedColumns(el, bks), () => tableHtml(["Period", "RED", "AMBER", "GREEN", "Total"], bks.map((b) => [b.label, b.counts.RED, b.counts.AMBER, b.counts.GREEN, b.total])));
    registerChart("ch-reasons", (el) => hBars(el, reasons, "Claims"), () => tableHtml(["Reason", "Claims"], reasons.map((r) => [r.label, r.value])));
    registerChart("ch-latency", (el) => histogram(el, totals, 200), () => tableHtml(["Statistic", "ms"], [["p50", fmtMs(p50)], ["p95", fmtMs(p95)], ["p99", fmtMs(pct(totals, 99))], ["max", fmtMs(totals[totals.length - 1])], ["Claims", totals.length]]));
    $$("[data-range]", root).forEach((b) => b.addEventListener("click", () => { state.dash.range = b.dataset.range; render(); }));
  }

  function healthList(compact) {
    const all = Array.from(state.claims.values()).sort((a, b) => b.receivedAt - a.receivedAt);
    const last = all[0];
    const live = state.mode === "live";
    const h = state.health || {};
    const engine = live ? h.policy_engine : "cobol";
    const lastRiskOk = last ? last.signals.riskOk : null;
    const outages = all.filter((c) => !c.signals.riskOk).length;
    const notes = all.filter((c) => c.note && c.note.status === "ready").length;
    const items = [
      ["Claims transaction CLMI", live ? (h.status === "ok" ? ["ok", "Running"] : ["bad", "Unreachable"]) : ["off", "Demo data"]],
      ["Decision rule CLMIPOL", engine === "cobol" ? ["ok", "COBOL"] : engine === "python" ? ["warn", "Python twin"] : ["off", engine || "–"]],
      ["Risk service", lastRiskOk == null ? ["off", "No traffic yet"] : lastRiskOk ? ["ok", "Answering"] : ["bad", "Last call failed safe"]],
      ["AI-image detector", last && last.modelVersion ? ["ok", last.modelVersion] : ["off", "–"]],
      ["Thresholds", [h.thresholds_version === "unavailable" ? "bad" : "ok", live ? h.thresholds_version || "–" : (state.thresholds || {}).version || "–"]],
      ["Granite (adjuster notes)", notes ? ["ok", notes + " notes"] : ["off", live ? "Not reporting" : "–"]],
    ];
    if (!compact) items.push(["Fail-safe events", [outages ? "warn" : "ok", outages + " in view"]]);
    return '<ul class="health">' + items.map(([k, [cls, v]]) => "<li><span>" + esc(k) + '</span><span class="dot ' + cls + '">' + esc(v) + "</span></li>").join("") + "</ul>";
  }

  function viewQueue(root, query) {
    setCrumbs([{ label: "Work queue" }]);
    const q = state.queue;
    if (query) {
      if (query.get("band")) q.band = query.get("band");
      if (query.get("status")) q.status = query.get("status");
      if (query.get("q") != null) q.q = query.get("q");
      history.replaceState(null, "", "#/queue");
    }
    const all = Array.from(state.claims.values());
    const reasonsPresent = Array.from(new Set(all.flatMap((c) => c.reasons))).filter((r) => r !== "NONE").sort();
    const matchStatus = (c) => q.status === "all" || (q.status === "open" ? isOpen(c) : !isOpen(c));
    const hiddenByStatus = all.filter((c) => !matchStatus(c)).length;
    const matchAssignee = (c) => !q.assignee || (q.assignee === "me" ? caseOf(c).assignee === state.role : !caseOf(c).assignee);
    const text = q.q.trim().toLowerCase();
    const matchText = (c) => !text || [c.id, c.record && c.record.incident && c.record.incident.description, c.record && c.record.policy_number, c.damageLabel, DAMAGE[c.record && c.record.damage_category]]
      .some((v) => v && String(v).toLowerCase().includes(text));
    const base = all.filter((c) => matchStatus(c) && matchAssignee(c) && matchText(c) && (!q.reason || c.reasons.includes(q.reason)));
    const counts = { ALL: base.length, RED: 0, AMBER: 0, GREEN: 0 };
    base.forEach((c) => counts[c.band]++);
    const rows = base.filter((c) => q.band === "ALL" || c.band === q.band);
    const dir = q.dir === "asc" ? 1 : -1;
    const keyFn = {
      priority: (c) => priority(c) * 1e13 + c.receivedAt.getTime(),
      received: (c) => c.receivedAt.getTime(),
      score: (c) => (c.aiScore == null ? -1 : c.aiScore),
      id: (c) => c.id,
      sla: (c) => { const s = sla(c); return s ? s.left : 1e15; },
    }[q.sort] || ((c) => 0);
    rows.sort((a, b) => { const x = keyFn(a), y = keyFn(b); return (x < y ? -1 : x > y ? 1 : 0) * dir; });
    const th = (k, l, cls) => '<th' + (cls ? ' class="' + cls + '"' : "") + ' scope="col"><button data-sort="' + k + '" aria-sort="' + (q.sort === k ? (q.dir === "asc" ? "ascending" : "descending") : "none") + '"' + (q.sort === k ? ' data-dir="' + q.dir + '"' : "") + ">" + l + "</button></th>";

    root.innerHTML =
      '<div class="page-head"><div><h1>Work queue</h1><p>Claims screened by Aegis. Open cases need a decision from a person; GREEN claims were fast-tracked automatically.</p></div></div>' +
      '<div class="toolbar">' +
        '<div class="seg" role="group" aria-label="Band">' + [["ALL", "All bands"], ["RED", "RED"], ["AMBER", "AMBER"], ["GREEN", "GREEN"]].map(([k, l]) => '<button data-band="' + k + '" aria-pressed="' + (q.band === k) + '">' + l + ' <span class="count">' + counts[k] + "</span></button>").join("") + "</div>" +
        '<label class="visually-hidden" for="f-status">Case status</label><select class="ctl" id="f-status"><option value="open">Open cases</option><option value="closed">Closed / fast-tracked</option><option value="all">All statuses</option></select>' +
        '<label class="visually-hidden" for="f-reason">Reason</label><select class="ctl" id="f-reason"><option value="">Any reason</option>' + reasonsPresent.map((r) => '<option value="' + esc(r) + '">' + esc(REASON_SHORT[r] || r) + "</option>").join("") + "</select>" +
        '<label class="visually-hidden" for="f-assignee">Assignee</label><select class="ctl" id="f-assignee"><option value="">Anyone</option><option value="me">Assigned to me</option><option value="none">Unassigned</option></select>' +
        '<span class="spacer"></span>' +
        '<label class="visually-hidden" for="f-q">Filter text</label><input class="ctl" id="f-q" type="search" placeholder="Filter: ID, damage, text…" value="' + esc(q.q) + '">' +
      "</div>" +
      '<div class="card"><div class="table-wrap"><table><thead><tr>' +
        th("priority", "Band") + th("id", "Claim") + th("received", "Received") + '<th scope="col">Summary</th>' + th("score", "AI score") + '<th scope="col">Signals</th><th scope="col">Status</th><th scope="col">Assignee</th>' + th("sla", "SLA") +
      "</tr></thead><tbody>" +
      (rows.length ? rows.map((c) => {
        const k = caseOf(c);
        const desc = c.record && c.record.incident ? c.record.incident.description : "";
        return '<tr tabindex="0" data-id="' + esc(c.id) + '"' + (state.fresh.has(c.id) ? ' class="fresh"' : "") + ">" +
          "<td>" + band(c.band) + "</td>" +
          '<td><div class="claim-cell">' + (function () { const e = evidenceFor(c); return e ? '<img class="qthumb" src="' + esc(photoUrl(e)) + '" alt="" loading="lazy">' : '<span class="qthumb ph" aria-hidden="true">' + ICON.camera + "</span>"; })() +
            '<div><span class="claim-id">' + esc(c.id) + '</span><div class="small faint">' + esc(DAMAGE[c.record && c.record.damage_category] || "–") + "</div></div></div></td>" +
          '<td title="' + esc(absTime(c.receivedAt)) + '"><span class="num">' + esc(clock(c.receivedAt)) + '</span><div class="small faint">' + esc(c.channel || "Claims system feed") + "</div></td>" +
          '<td><div class="desc-cell" title="' + esc(desc) + '">' + tokHtml(desc, c) + "</div></td>" +
          "<td>" + scoreCell(c) + "</td><td>" + signalIcons(c) + "</td><td>" + statusChip(c) + '</td><td class="small">' + esc(assigneeName(k.assignee)) + '</td><td class="small">' + slaCell(c) + "</td></tr>";
      }).join("") : '<tr><td colspan="9"><div class="empty">' + (state.claims.size ? "No claims match these filters." : "No claims have arrived yet. They appear here as soon as the claims system sends them.") + "</div></td></tr>") +
      '</tbody></table></div><div class="card-f muted">' + rows.length + " of " + all.length + " claims" +
        (hiddenByStatus ? ' · <strong>' + hiddenByStatus + "</strong> " + (q.status === "open" ? "fast-tracked (GREEN) or closed" : "open") + ' claims are hidden by the status filter · <a href="#" data-showall>Show all</a>' : "") +
        " · SLA: RED 4 h, AMBER 24 h from receipt · Case actions are kept in this browser for the demo.</div></div>";
    const showAll = $("[data-showall]", root);
    if (showAll) showAll.addEventListener("click", (e) => { e.preventDefault(); q.status = "all"; render(); });

    $("#f-status", root).value = q.status;
    $("#f-reason", root).value = q.reason;
    $("#f-assignee", root).value = q.assignee;
    $$("[data-band]", root).forEach((b) => b.addEventListener("click", () => { q.band = b.dataset.band; render(); }));
    $("#f-status", root).addEventListener("change", (e) => { q.status = e.target.value; render(); });
    $("#f-reason", root).addEventListener("change", (e) => { q.reason = e.target.value; render(); });
    $("#f-assignee", root).addEventListener("change", (e) => { q.assignee = e.target.value; render(); });
    $("#f-q", root).addEventListener("input", (e) => { q.q = e.target.value; clearTimeout(viewQueue.t); viewQueue.t = setTimeout(() => { render(); const f = $("#f-q"); if (f) { f.focus(); f.setSelectionRange(f.value.length, f.value.length); } }, 250); });
    $$("[data-sort]", root).forEach((b) => b.addEventListener("click", () => { if (q.sort === b.dataset.sort) q.dir = q.dir === "asc" ? "desc" : "asc"; else { q.sort = b.dataset.sort; q.dir = b.dataset.sort === "score" || b.dataset.sort === "received" ? "desc" : "asc"; } render(); }));
    $$("tbody tr[data-id]", root).forEach((tr) => {
      tr.addEventListener("click", () => { location.hash = "#/claims/" + encodeURIComponent(tr.dataset.id); });
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") location.hash = "#/claims/" + encodeURIComponent(tr.dataset.id); });
    });
  }

  async function viewClaim(root, id) {
    const c = state.claims.get(id);
    setCrumbs([{ label: "Work queue", href: "#/queue" }, { label: id }]);
    if (!c) { root.innerHTML = '<div class="card"><div class="empty">Claim <b>' + esc(id) + '</b> isn\'t in this console. <a href="#/queue">Back to the queue</a></div></div>'; return; }
    if (c.note === undefined && state.mode === "live") {
      root.innerHTML = '<div class="card"><div class="empty">Loading ' + esc(id) + "…</div></div>";
      try { await Source().detail(c); } catch (e) { /* render with what we have */ }
      if (state.route.arg !== id) return;
    }
    const k = caseOf(c);
    const st = STATUS[k.status] || { label: k.status };
    const T = state.thresholds;
    const s = c.signals;
    const due = sla(c);
    const canReveal = state.role === "siu";
    const revealed = state.revealed.has(c.id);
    const rec = c.record || {};
    const cl = rec.claimant || {};
    const inc = rec.incident || {};
    const journal = Source().claimJournal(c);
    const actions = Cases.available(c);
    const reasonsHtml = '<ul class="reasons">' + c.reasons.map((r) => {
      const [text, sev] = REASON[r] || ["", "info"];
      return '<li class="' + sev + '">' + (sev === "crit" ? ICON.RED : sev === "warn" ? ICON.AMBER : sev === "ok" ? ICON.GREEN : ICON.info) + "<code>" + esc(r) + "</code><p>" + esc(text) + "</p></li>";
    }).join("") + "</ul>";
    const meter = c.aiScore == null ? '<p class="muted small">No detector score: the risk service didn\'t return signals for this claim.</p>' :
      '<div class="meter" role="img" aria-label="Calibrated AI-generation score ' + fmtScore(c.aiScore) + ' out of 1"><div class="track"><div class="fill" style="width:' + (c.aiScore * 100).toFixed(1) + "%;background:" + bandColor(c.band) + '"></div></div>' +
      (T ? '<span class="tick" style="left:calc(' + (T.tau_amber * 100) + '% - 1px)"></span><span class="tick" style="left:calc(' + (T.tau_red * 100) + '% - 1px)"></span>' : "") + "</div>" +
      (T ? '<div class="meter-labels"><span style="left:' + T.tau_amber * 100 + '%">amber ' + T.tau_amber.toFixed(2) + '</span><span style="left:' + T.tau_red * 100 + '%">red ' + T.tau_red.toFixed(2) + "</span></div>" : "");
    const heat = c.heatmap;
    const photo = evidenceFor(c);
    const seqColor = (v) => ["var(--seq-1)", "var(--seq-2)", "var(--seq-3)", "var(--seq-4)", "var(--seq-5)"][clamp(Math.floor(v * 5), 0, 4)];
    const tl = k.history.map((h) => '<li><div class="when">' + esc(absTime(new Date(h.at))) + " · " + esc(assigneeName(h.actor)) + '</div><div class="what">' + esc((ACTION[h.action] || {}).verb || h.action) + "</div>" + (h.note ? '<div class="note-text">' + esc(h.note) + "</div>" : "") + "</li>").join("");
    const t = c.timings || {};

    root.innerHTML =
      '<div class="claim-head"><div><h1>' + esc(c.id) + " " + band(c.band, true) + "</h1>" +
        '<div class="claim-meta"><span>Received ' + esc(absTime(c.receivedAt)) + " (" + esc(relTime(c.receivedAt)) + ")</span><span>" + esc(c.channel || "Claims system feed") + "</span><span>" + statusChip(c) + "</span>" + (due ? "<span>SLA " + slaCell(c) + "</span>" : "") + "</div></div>" +
        '<div><div class="btn-row" id="actions">' + (actions.length ? actions.map((a) => '<button class="btn ' + (a.id === "clear" ? "" : "ghost") + '" data-act="' + a.id + '"' + (a.disabled ? ' disabled title="' + esc(a.why) + '"' : "") + ">" + esc(ACTION[a.id].label) + "</button>").join("") : '<span class="pill">' + esc(st.label) + "</span>") + "</div>" +
        (actions.some((a) => a.disabled) ? '<p class="small faint" style="margin:6px 0 0;text-align:right">Clearing or closing a RED case needs the SIU investigator role.</p>' : "") + "</div></div>" +
      '<div class="grid cols-main">' +
        '<div class="stack">' +
          '<div class="card"><div class="card-h"><h2>Screening decision</h2><span class="small faint">' + (state.mode === "live" ? "Before commit " : "Demo decision ") + esc(fmtMs(t.total == null ? t.precommit_total : t.total)) + ' ms</span></div><div class="card-b">' +
            '<p class="decision-line">' + esc(OUTCOME[c.band]) + "</p>" + reasonsHtml +
            '<h3 style="margin:16px 0 2px">Calibrated AI-generation score <span class="num" style="float:right">' + esc(fmtScore(c.aiScore)) + "</span></h3>" + meter +
            '<p class="small faint" style="margin-top:6px">One signal, not proof. Thresholds are set on genuine photos: at most 1% reach RED and 5% reach AMBER on validation.</p>' +
          "</div></div>" +
          '<div class="card"><div class="card-h"><h2>Evidence</h2><span class="small faint">' + c.photos.length + " photo" + (c.photos.length === 1 ? "" : "s") + '</span></div><div class="card-b"><div class="evidence">' +
            (photo
              ? '<div><div class="photo-frame"><a href="' + esc(photoUrl(photo)) + '" target="_blank" rel="noopener" aria-label="Open photo full size"><img src="' + esc(photoUrl(photo)) + '" alt="Claim photo for ' + esc(c.id) + '"></a>' +
                  (heat ? '<div class="photo-heat" id="photo-heat" aria-hidden="true">' + heat.flat().map((v) => '<span style="opacity:' + (clamp(Number(v) || 0, 0, 1) * 0.7).toFixed(2) + '"></span>').join("") + "</div>" : "") + "</div>" +
                  (heat ? '<label class="small" style="display:inline-flex;gap:6px;margin-top:8px"><input type="checkbox" id="ov-toggle" checked> Overlay the attention map</label>' : "") +
                  creditHtml(photo) +
                  '<p class="small faint" style="margin:4px 0 0">Demo evidence. In production the original stays in the encrypted evidence store inside IBM Z and is opened through an authorised, journaled request.</p>'
              : '<div><div class="photo-ph">' + '<div>' + ICON.camera + "<strong>Original held in the encrypted evidence store</strong><br>Photos stay inside IBM Z. Viewing originals needs an authorised, journaled request.</div></div>") +
              '<dl class="kv" style="margin-top:12px">' + c.photos.map((p, i) => "<dt>Photo " + (i + 1) + '</dt><dd class="mono">' + esc(p.evidence_id) + (p.name ? '<div class="faint">' + esc(p.name) + "</div>" : "") + "</dd>").join("") + "</dl></div>" +
            "<div><h3>Patch score map</h3>" +
              (heat ? '<p class="small faint" style="margin:2px 0 8px">Detector scores on 16 local image patches (4×4 grid). Advisory only; edit localisation has not been validated.</p><div class="heat-grid" role="img" aria-label="Attention map">' + heat.flat().map((v, i) => '<div tabindex="0" data-tip="' + i + '" style="background:' + seqColor(v) + '" aria-label="Cell ' + (i + 1) + ": " + Number(v).toFixed(2) + '"></div>').join("") + '</div><div class="heat-scale"><span>low</span><span class="ramp"></span><span>high</span></div>'
                : '<p class="small muted" style="margin-top:6px">' + (c.band === "GREEN" ? "Computed only for AMBER and RED claims." : !s.riskOk ? "Not available: the risk service didn't answer." : "Unavailable or still computing on this deployment.") + "</p>") +
              '<dl class="kv" style="margin-top:14px">' +
                "<dt>Re-used photo</dt><dd>" + (s.duplicate ? '<span class="band RED">' + ICON.dup + "MATCH</span> " + (s.duplicateOf ? 'also on <a href="#/claims/' + encodeURIComponent(s.duplicateOf) + '">' + esc(s.duplicateOf) + "</a>" : "matches another claim") : s.riskOk ? "No match" : "–") + "</dd>" +
                "<dt>Editing software</dt><dd>" + (s.softwareTag ? esc(typeof s.softwareTag === "string" ? s.softwareTag : "Tag present") : s.softwareTag === false || (s.riskOk && s.softwareTag == null) ? "None found" : "–") + "</dd>" +
                "<dt>Camera metadata</dt><dd>" + (s.exif === true ? "Present" : s.exif === false ? "Missing (information only)" : "–") + "</dd>" +
                "<dt>C2PA credentials</dt><dd>" + (s.c2pa === true ? "Manifest present (not validated)" : s.c2pa === false ? "None" : "–") + "</dd>" +
              "</dl></div>" +
          "</div></div></div>" +
          '<div class="card"><div class="card-h"><div><h2>Claim details</h2><p>' + (revealed ? "Identity re-linked for this session. The access is in the audit log." : privacyText(c)) + "</p></div>" +
            (revealed ? '<button class="btn ghost" id="hide-id">Hide identity</button>' : '<button class="btn ghost" id="reveal"' + (canReveal ? "" : ' disabled title="Only an SIU investigator can re-link identity. Switch role to try it."') + ">" + ICON.lock + " Reveal identity</button>") + "</div>" +
            '<div class="card-b"><dl class="kv">' +
              "<dt>Policy</dt><dd>" + tokHtml(rec.policy_number || "–", c) + "</dd>" +
              "<dt>Claimant</dt><dd>" + tokHtml(cl.name || "–", c) + "</dd>" +
              "<dt>Email</dt><dd>" + tokHtml(cl.email || "–", c) + "</dd>" +
              "<dt>Phone</dt><dd>" + tokHtml(cl.phone || "–", c) + "</dd>" +
              "<dt>Incident date</dt><dd>" + esc(inc.date || "–") + "</dd>" +
              "<dt>Damage</dt><dd>" + esc(DAMAGE[rec.damage_category] || rec.damage_category || "–") + "</dd>" +
            '</dl><h3 style="margin:14px 0 6px">Claimant\'s description</h3><div class="desc-block">' + tokHtml(inc.description || (c.privacyReview && !c.privacyReview.description_released ? "Withheld pending successful privacy review" : "–"), c) + "</div></div></div>" +
          '<div class="card"><div class="card-h"><div><h2>Allowed egress payload (local audit)</h2><p>Prepared allowlisted fields. This record does not mean data was transmitted outside LinuxONE.</p></div><span class="pill">' + esc((c.egress && c.egress.egress_id) || "–") + '</span></div><div class="card-b"><pre class="json">' + esc(JSON.stringify((c.egress && c.egress.payload) || {}, null, 2)) + "</pre></div></div>" +
        "</div>" +
        '<div class="stack">' +
          '<div class="card"><div class="card-h"><h2>Case</h2></div><div class="card-b"><dl class="kv"><dt>Status</dt><dd>' + statusChip(c) + "</dd><dt>Assignee</dt><dd>" + esc(assigneeName(k.assignee)) + "</dd><dt>SLA</dt><dd>" + (due ? slaCell(c) + '<div class="small faint">due ' + esc(absTime(due.due)) + "</div>" : '<span class="faint">–</span>') + "</dd></dl>" +
            '<h3 style="margin:14px 0 8px">History</h3><ul class="timeline"><li class="sys"><div class="when">' + esc(absTime(c.receivedAt)) + ' · Aegis</div><div class="what">Screened: ' + esc(c.band) + "</div><div class=\"note-text\">" + esc(OUTCOME[c.band]) + "</div></li>" + tl + "</ul>" +
            '<p class="small faint" style="margin:0">Case actions are kept in this browser for the demo. In production they are written back to the claims system.</p></div></div>' +
          '<div class="card"><div class="card-h"><h2>Investigator note</h2><span class="pill accent">Advisory · Granite</span></div><div class="card-b">' + noteHtml(c) + "</div></div>" +
          '<div class="card"><div class="card-h"><h2>Processing</h2><span class="small faint">' + esc(c.engine === "cobol" ? "COBOL rule" : c.engine || "") + "</span></div><div class=\"card-b\">" + waterfall(t) +
            '<dl class="kv" style="margin-top:12px"><dt>Model</dt><dd class="mono">' + esc(c.modelVersion || "–") + '</dd><dt>Thresholds</dt><dd class="mono">' + esc(c.thresholdsVersion || "–") + "</dd></dl></div></div>" +
          '<div class="card"><div class="card-h"><h2>Audit trail</h2><a class="small" href="#/audit?q=' + encodeURIComponent(c.id) + '">Full log →</a></div><div class="card-b"><ul class="timeline">' +
            journal.map((j) => '<li class="sys"><div class="when">#' + esc(j.seq) + " · " + esc(absTime(new Date(j.ts))) + '</div><div class="what">' + esc(j.event) + '</div><div class="note-text mono small">' + esc(summarise(j)) + "</div></li>").join("") + "</ul></div></div>" +
        "</div>" +
      "</div>";

    if (heat) bindTips(root, (i) => ({ title: "Cell " + (+i + 1) + " (row " + (Math.floor(i / 4) + 1) + ", col " + ((i % 4) + 1) + ")", rows: [{ label: "Attention", value: Number(heat.flat()[+i]).toFixed(2) }] }));
    $$("[data-act]", root).forEach((b) => b.addEventListener("click", () => openAction(c, b.dataset.act)));
    const rv = $("#reveal", root);
    if (rv) rv.addEventListener("click", async () => {
      rv.disabled = true;
      try { await Source().reveal(c); state.revealed.add(c.id); toast("Identity re-linked for " + esc(c.id) + ". The access was written to the audit log."); }
      catch (e) { toast("Couldn't re-link identity: " + esc(e.message)); }
      render();
    });
    const ov = $("#ov-toggle", root);
    if (ov) ov.addEventListener("change", () => { const h = $("#photo-heat", root); if (h) h.hidden = !ov.checked; });
    const hide = $("#hide-id", root);
    if (hide) hide.addEventListener("click", () => { state.revealed.delete(c.id); render(); });
  }

  function privacyText(c) {
    const p = c.privacyReview;
    if (!p) return state.mode === "demo" ? "Fictional demo identities with simulated pseudonyms." : "Privacy review status unavailable. Refresh the claim before relying on its free text.";
    if (!p.description_released) return "Free text is withheld: privacy review " + p.status + ". Structured identifiers use HMAC pseudonyms.";
    if (p.status === "reviewed") return "Rules and the local Granite second pass completed. This reduces missed personal data; it does not guarantee its absence.";
    return "Rules pass only (" + p.reason + "). Free text has not had a Granite second pass.";
  }

  function noteHtml(c) {
    const n = c.note;
    if (!n || n.status === "not_requested") return '<p class="small muted">' + (c.band === "GREEN" ? "Notes are written only for AMBER and RED claims." : !c.signals.riskOk ? "No note: there were no signals to summarise." : "No note: Granite isn't configured on this deployment.") + "</p>";
    if (n.status === "pending") return '<p class="small muted">Granite is writing the note. It runs after the decision, off the decision path.</p>';
    if (n.status === "unavailable") return '<p class="small muted">Note unavailable: the model output failed validation. The band is unaffected.</p>';
    return '<div class="note">' + esc(n.note) + '</div><p class="small faint" style="margin:8px 0 0">' + (state.mode === "demo" ? "Simulated note (demo data). On LinuxONE it's written by IBM Granite from the pseudonymised record." : "Written by IBM Granite on LinuxONE from the pseudonymised record.") + " It can't change the band.</p>";
  }

  function waterfall(t) {
    if (!t || (t.total == null && t.precommit_total == null)) return '<p class="small muted">No timing data.</p>';
    const g = t.gateway || 0, r = t.risk || 0, p = t.policy || 0, total = t.total == null ? t.precommit_total : t.total;
    const pos = (v) => ((100 * v) / total).toFixed(2) + "%";
    const d = t.risk_detail || {};
    const rows = [["Gateway", 0, g, ""], ["Risk service", g, r, ""]];
    [["Privacy rules", "privacy", 0], ["Load crops", "load_crops", 0], ["Detector", "inference", d.load_crops || 0], ["Metadata", "metadata", 0], ["Duplicate check", "dup", 0]].forEach(([l, key, off]) => {
      if (typeof d[key] === "number") rows.push([l, g + off, d[key], "sub"]);
    });
    rows.push(["CLMIPOL rule", g + r, p, ""]);
    return rows.map(([l, st, du, cls]) => '<div class="wf-row ' + cls + '"><span>' + esc(l) + '</span><span class="wf-track"><span class="wf-bar" style="left:' + pos(st) + ";width:" + pos(du) + '"></span></span><span class="ms">' + fmtMs(du) + "</span></div>").join("") +
      '<div class="wf-row" style="font-weight:600;color:var(--text);margin-top:4px"><span>' + (t.total == null ? "Before commit" : "Total") + '</span><span></span><span class="ms">' + fmtMs(total) + "</span></div>";
  }

  function summarise(j) {
    const p = j.payload_json || {};
    if (j.event === "DECISION") return p.band + " · " + (p.reasons || []).join(", ") + " · " + (p.policy_engine || "") + " · " + (p.thresholds_version || "");
    if (j.event === "EGRESS") return (p.egress_id || "") + " · " + (p.fields || []).length + " fields";
    if (j.event === "EVIDENCE") return p.evidence_id || "";
    if (j.event === "VIEW_ORIGINAL") return "identity re-linked · role " + (p.role || "");
    if (j.event === "REPLAY") return "same idempotency key, stored decision returned";
    if (j.event === "NOTE") return "status " + (p.status || "");
    if (j.event === "PII_SECOND_PASS") return (p.tokens_added || []).length + " extra tokens";
    const s = JSON.stringify(p);
    return s.length > 80 ? s.slice(0, 78) + "…" : s;
  }

  function viewAudit(root, query) {
    setCrumbs([{ label: "Audit log" }]);
    const a = state.audit;
    if (query && query.get("q") != null) { a.q = query.get("q"); history.replaceState(null, "", "#/audit"); }
    const local = [];
    state.claims.forEach((c) => {
      caseOf(c).history.forEach((h) => local.push({ seq: "–", ts: h.at, claim_id: c.id, event: "REVIEW_" + h.action.toUpperCase(), payload_json: { actor: assigneeName(h.actor), note: h.note }, local: true, seeded: !!h.seeded }));
    });
    const rows = state.journal.concat(local).filter((j) => (!a.event || j.event === a.event) && (!a.q || String(j.claim_id || "").toLowerCase().includes(a.q.toLowerCase()) || String(j.event).toLowerCase().includes(a.q.toLowerCase())))
      .sort((x, y) => (y.ts > x.ts ? 1 : y.ts < x.ts ? -1 : 0));
    const events = Array.from(new Set(state.journal.map((j) => j.event).concat(local.map((l) => l.event)))).sort();
    root.innerHTML =
      '<div class="page-head"><div><h1>Audit log</h1><p>The CLMI journal is append-only: the database rejects updates and deletes. Every decision, data export and identity re-link is recorded.</p></div></div>' +
      '<div class="toolbar"><label class="visually-hidden" for="a-ev">Event type</label><select class="ctl" id="a-ev"><option value="">All events</option>' + events.map((e) => '<option value="' + esc(e) + '">' + esc(e) + "</option>").join("") + "</select>" +
      '<span class="spacer"></span><label class="visually-hidden" for="a-q">Filter</label><input class="ctl" id="a-q" type="search" placeholder="Claim ID or event…" value="' + esc(a.q) + '"></div>' +
      '<div class="card"><div class="table-wrap"><table class="plain"><thead><tr><th scope="col">#</th><th scope="col">Time</th><th scope="col">Transaction</th><th scope="col">Event</th><th scope="col">Claim</th><th scope="col">Details</th><th scope="col">Source</th></tr></thead><tbody>' +
      (rows.length ? rows.slice(0, 500).map((j) => '<tr><td class="num faint">' + esc(j.seq) + '</td><td class="num small">' + esc(absTime(new Date(j.ts))) + '</td><td class="mono small">CLMI</td><td><strong class="small">' + esc(j.event) + "</strong></td><td>" + (j.claim_id ? '<a class="claim-id" href="#/claims/' + encodeURIComponent(j.claim_id) + '">' + esc(j.claim_id) + "</a>" : '<span class="faint">–</span>') + '</td><td class="mono small">' + esc(j.local ? (j.payload_json.actor + (j.payload_json.note ? " · " + j.payload_json.note : "")) : summarise(j)) + '</td><td class="small">' + (j.local ? (j.seeded ? '<span class="pill warn">Case history · demo data</span>' : '<span class="pill warn">Reviewer · this browser</span>') : '<span class="pill">Gateway journal</span>') + "</td></tr>").join("")
        : '<tr><td colspan="7"><div class="empty">No events match.</div></td></tr>') +
      '</tbody></table></div><div class="card-f muted">' + Math.min(rows.length, 500) + " of " + rows.length + " events shown</div></div>";
    $("#a-ev", root).value = a.event;
    $("#a-ev", root).addEventListener("change", (e) => { a.event = e.target.value; render(); });
    $("#a-q", root).addEventListener("input", (e) => { a.q = e.target.value; clearTimeout(viewAudit.t); viewAudit.t = setTimeout(() => { render(); const f = $("#a-q"); if (f) { f.focus(); f.setSelectionRange(f.value.length, f.value.length); } }, 250); });
  }


  // ================================================================ manual claim submission (live gateway only)
  function viewSubmit(root) {
    setCrumbs([{ label: "Submit claim" }]);
    const live = state.mode === "live";
    const key = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + "-" + Math.random().toString(16).slice(2)); // idempotency key, fixed for this form
    const today = new Date().toISOString().slice(0, 10);
    root.innerHTML =
      '<div class="page-head"><div><h1>Submit a claim manually</h1><p>Stands in for the insurer’s claims system: the photo goes to the evidence store, then the claim runs through transaction CLMI on IBM Z and comes back with a band in real time.</p></div></div>' +
      (live ? "" : '<div class="callout" style="margin-bottom:16px">' + ICON.info + "<div><strong>Demo mode.</strong> Not connected to the CLMI gateway, so the decision is simulated in this browser with the same rule as CLMIPOL: no detector runs and nothing is stored. File names containing <code>fake</code> score high and <code>edit</code> scores amber; submitting the same photo twice is a duplicate. Connect through the SSH tunnel to LinuxONE for real screening.</div></div>") +
      '<div class="grid cols-main">' +
        '<div class="card"><div class="card-h"><div><h2>Claim</h2><p>Fictional personas only. Personal data is pseudonymised by the gateway before anything is stored or scored.</p></div></div>' +
          '<div class="card-b"><form id="submit-form" novalidate>' +
            '<div class="form-grid">' +
              '<label class="field"><span>Policy number</span><input class="ctl" name="policy_number" required maxlength="40" placeholder="PN-4471-0098"></label>' +
              '<label class="field"><span>Incident date</span><input class="ctl" type="date" name="date" required value="' + today + '"></label>' +
              '<label class="field"><span>Claimant name</span><input class="ctl" name="name" required maxlength="200" placeholder="Alex Doe"></label>' +
              '<label class="field"><span>Email</span><input class="ctl" type="email" name="email" maxlength="200" placeholder="alex@example.com"></label>' +
              '<label class="field"><span>Phone</span><input class="ctl" type="tel" name="phone" maxlength="40" placeholder="07700 900123"></label>' +
              '<label class="field"><span>Damage category</span><select class="ctl" name="damage_category">' + Object.keys(DAMAGE).map((k) => '<option value="' + k + '">' + esc(DAMAGE[k]) + "</option>").join("") + "</select></label>" +
            "</div>" +
            '<label class="field"><span>Incident description</span><textarea name="description" rows="4" required maxlength="4000" placeholder="Reversed into a bollard in a supermarket car park; rear bumper pushed in."></textarea></label>' +
            '<label class="field"><span>Damage photos (JPEG or PNG, up to 8, 15 MB each)</span><input class="ctl" type="file" name="photos" accept="image/jpeg,image/png" multiple required></label>' +
            '<div class="thumbs" id="submit-thumbs"></div>' +
            '<div class="btn-row" style="margin-top:14px;align-items:center"><button class="btn" type="submit" id="submit-btn">' + (live ? "Screen this claim" : "Screen this claim (simulated)") + '</button><span class="small muted" id="submit-status"></span></div>' +
            '<p class="small faint" style="margin:10px 0 0">' + (live ? "Idempotency key <code>" + esc(key) + "</code>: submitting this form twice returns the same claim and decision." : "Simulated submissions live only in this browser tab.") + "</p>" +
          "</form></div></div>" +
        '<div class="stack">' +
          '<div class="card" id="submit-result" hidden></div>' +
          '<div class="card"><div class="card-h"><h2>What happens</h2></div><div class="card-b"><ol class="steps">' +
            "<li><strong>Upload.</strong> Each photo is decoded, cropped and hashed once, and the original is encrypted at rest.</li>" +
            "<li><strong>Transaction CLMI.</strong> The gateway pseudonymises the form, calls the risk service with a 300 ms deadline and runs the COBOL rule.</li>" +
            "<li><strong>Band.</strong> GREEN is fast-tracked, AMBER goes to review, RED to an investigator. Nothing is rejected automatically.</li>" +
            "<li><strong>After the decision.</strong> AMBER and RED claims get a Granite note and a heatmap, off the decision path.</li>" +
          "</ol></div></div>" +
        "</div>" +
      "</div>";

    const form = $("#submit-form"), status = $("#submit-status"), btn = $("#submit-btn"), thumbs = $("#submit-thumbs");
    form.elements.photos.addEventListener("change", () => {
      thumbs.replaceChildren();
      Array.from(form.elements.photos.files).slice(0, 8).forEach((f) => {
        const img = document.createElement("img");
        img.alt = f.name; img.title = f.name + " · " + Math.round(f.size / 1024) + " KB";
        img.src = URL.createObjectURL(f);
        thumbs.appendChild(img);
      });
    });
    form.addEventListener("submit", async (evt) => {
      evt.preventDefault();
      const files = Array.from(form.elements.photos.files);
      if (!form.reportValidity()) return;
      if (!files.length) { status.textContent = "Add at least one photo."; return; }
      if (files.length > 8) { status.textContent = "At most 8 photos per claim."; return; }
      btn.disabled = true;
      const result = $("#submit-result");
      result.hidden = true;
      const fields = () => ({
        policy_number: form.elements.policy_number.value.trim(), name: form.elements.name.value.trim(),
        email: form.elements.email.value.trim(), phone: form.elements.phone.value.trim(), date: form.elements.date.value,
        description: form.elements.description.value.trim(), damage_category: form.elements.damage_category.value,
      });
      try {
        if (!live) {
          status.textContent = "Simulating…";
          await new Promise((r) => setTimeout(r, 400));
          const d = Demo.simulate(fields(), files);
          status.textContent = "";
          showResult(d, 0);
          return;
        }
        const evidence = [];
        for (let i = 0; i < files.length; i++) {
          status.textContent = "Uploading photo " + (i + 1) + " of " + files.length + "…";
          const fd = new FormData();
          fd.append("file", files[i], files[i].name);
          const up = await api("/evidence", {}, { method: "POST", body: fd });
          evidence.push({ evidence_id: up.evidence_id });
        }
        status.textContent = "Screening inside transaction CLMI…";
        const body = {
          idempotency_key: key,
          policy_number: form.elements.policy_number.value.trim(),
          claimant: { name: form.elements.name.value.trim(), email: form.elements.email.value.trim() || null, phone: form.elements.phone.value.trim() || null },
          incident: { date: form.elements.date.value, description: form.elements.description.value.trim() },
          damage_category: form.elements.damage_category.value,
          evidence,
        };
        const t0 = performance.now();
        const d = await api("/claims", { "Content-Type": "application/json" }, { method: "POST", body: JSON.stringify(body) });
        const httpMs = performance.now() - t0;
        status.textContent = "";
        showResult(d, httpMs);
        try { await Live.poll(); updateNav(); } catch (e) { /* the 5 s poll will catch up */ }
      } catch (e) {
        const msg = e instanceof ApiError ? (e.status ? "Gateway answered " + e.status + ": " + e.message : e.message) : String(e);
        status.textContent = "";
        result.innerHTML = '<div class="card-h"><h2>Not submitted</h2></div><div class="card-b"><p class="decision-line">' + esc(msg) + '</p><p class="small muted">Nothing was committed. Oversized files are refused before upload (413), non-JPEG/PNG files with 415, and an unavailable evidence store with 503. Fix the input and try again; the idempotency key stays the same.</p></div>';
        result.hidden = false;
      } finally {
        btn.disabled = false;
      }
      function showResult(d, httpMs) {
        const reasons = (d.reasons || []).map((r) => '<li class="' + esc((REASON[r] || ["", "info"])[1]) + '">' + ICON[r === "NONE" ? "GREEN" : (REASON[r] || ["", "info"])[1] === "crit" ? "RED" : (REASON[r] || ["", "info"])[1] === "info" ? "info" : "AMBER"] + "<code>" + esc(REASON_SHORT[r] || r) + "</code><p>" + esc((REASON[r] || [r])[0]) + "</p></li>").join("");
        result.innerHTML =
          '<div class="card-h"><div><h2>Decision ' + esc(d.claim_id) + "</h2><p>" + (d.simulated ? "Simulated in this browser (demo mode): no detector ran." : d.replayed ? "Same idempotency key: the stored decision was returned, nothing was re-scored." : "Decided inside transaction CLMI on LinuxONE.") + "</p></div>" + band(d.band, true) + "</div>" +
          '<div class="card-b"><p class="decision-line">' + esc(OUTCOME[d.band] || "") + '</p><ul class="reasons">' + reasons + "</ul>" +
          '<h3 style="margin:14px 0 6px">Timings</h3>' + waterfall(d.timings_ms) +
          (d.band === "GREEN"
            ? '<div class="callout" style="margin-top:12px">' + ICON.info + '<div><strong>Fast-tracked.</strong> GREEN claims need no human work, so they are not listed under the work queue\u2019s default \u201cOpen cases\u201d filter. Open the case below, search its ID, or <a href="#/queue?band=ALL&status=all">show all claims in the queue</a>. A score below τ amber' + (state.thresholds ? " (" + state.thresholds.tau_amber.toFixed(2) + ")" : "") + " is GREEN by design.</div></div>"
            : '<div class="callout" style="margin-top:12px">' + ICON.info + '<div><strong>In the work queue.</strong> ' + (d.band === "RED" ? "Referred to an investigator; an SIU investigator can clear or close it." : "Awaiting human review; a claims handler can clear it or refer it to SIU.") + ' <a href="#/queue">Open the queue</a>.</div></div>') +
          '<p class="small faint" style="margin:8px 0 0">' + (d.simulated ? "Simulated timings" : "Browser round trip " + esc(fmtMs(httpMs)) + " ms through the SSH tunnel") + " · model " + esc(d.model_version || "–") + " · thresholds " + esc(d.thresholds_version || "–") + " · rule engine " + esc(d.policy_engine || "–") + "</p>" +
          '<div class="btn-row" style="margin-top:14px"><a class="btn" href="#/claims/' + encodeURIComponent(d.claim_id) + '">Open the case</a><button class="btn ghost" type="button" id="submit-again">Submit another</button></div></div>';
        result.hidden = false;
        $("#submit-again").addEventListener("click", () => { location.hash = "#/submit"; render(); });
        result.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  }

  function viewPolicy(root) {
    setCrumbs([{ label: "Decision policy" }]);
    const T = state.thresholds;
    const tv = state.mode === "live" ? (state.health || {}).thresholds_version : T && T.version;
    root.innerHTML =
      '<div class="page-head"><div><h1>Decision policy</h1><p>How Aegis turns detector signals into a band. The rule is owned by the claims transaction, not by the AI.</p></div></div>' +
      '<div class="grid cols-2">' +
        '<div class="card"><div class="card-h"><h2>Bands</h2></div><div class="card-b"><div class="table-wrap"><table class="plain"><thead><tr><th>Band</th><th>When</th><th>Outcome</th></tr></thead><tbody>' +
          "<tr><td>" + band("RED") + "</td><td>Score ≥ τ<sub>red</sub>, <strong>or</strong> the same photo was already used on a different claim</td><td>Investigator referral. Never auto-rejected.</td></tr>" +
          "<tr><td>" + band("AMBER") + "</td><td>τ<sub>amber</sub> ≤ score &lt; τ<sub>red</sub>, <strong>or</strong> metadata names an editing tool, <strong>or</strong> the risk service is unavailable, <strong>or</strong> score ≥ τ<sub>red</sub> on a low-resolution photo (short side under " + esc(String((T && T.low_res_short_side) || 480)) + " px: the detector is less reliable there, so the score alone cannot refer)</td><td>Human review</td></tr>" +
          "<tr><td>" + band("GREEN") + "</td><td>None of the above</td><td>Fast-tracked</td></tr>" +
        "</tbody></table></div>" +
        '<div class="callout" style="margin-top:12px">' + ICON.info + "<div>Every matching reason is reported and the band is the most severe one. Missing camera metadata and C2PA presence are information only and never change the band.</div></div></div></div>" +
        '<div class="card"><div class="card-h"><h2>Thresholds</h2><span class="pill">' + esc(tv || "–") + '</span></div><div class="card-b"><dl class="kv">' +
          "<dt>τ red</dt><dd>" + (T ? '<span class="num">' + T.tau_red.toFixed(2) + "</span>" : "From the thresholds config") + ' <span class="faint">· target validation false-positive cap ≤ 1%</span></dd>' +
          "<dt>τ amber</dt><dd>" + (T ? '<span class="num">' + T.tau_amber.toFixed(2) + "</span>" : "From the thresholds config") + ' <span class="faint">· target validation false-positive cap ≤ 5%</span></dd>' +
          "<dt>Fitted on</dt><dd>" + (state.mode === "live" ? "Validation real photos: τ red = the score 1% of genuine photos exceed, τ amber = the score 5% exceed (version " + esc(tv || "–") + "). A score below τ amber is GREEN by design, even if it is not near zero." : "A must fit and validate thresholds on the deployed LinuxONE detector scores") + "</dd>" +
          "<dt>Reported by</dt><dd>False-positive rate per real-photo subgroup (phone photos, compressed images, each source)</dd>" +
        "</dl></div></div>" +
      "</div>" +
      '<div class="grid cols-main" style="margin-top:16px">' +
        '<div class="card"><div class="card-h"><div><h2>CLMIPOL.cbl</h2><p>The decision rule is real COBOL, compiled with GnuCOBOL on LinuxONE. In production it runs inside CICS transaction CLMI on z/OS.</p></div></div><div class="card-b"><pre class="code">' + esc(
          "           IF IN-RISK-OK = 0\n               MOVE \"RISK_SERVICE_UNAVAILABLE\" TO WS-NEW-REASON\n               MOVE 1 TO WS-NEW-SEV\n               PERFORM ADD-REASON\n           ELSE\n               IF IN-DUP = 1\n                   MOVE \"DUPLICATE_PHOTO\" TO WS-NEW-REASON\n                   MOVE 2 TO WS-NEW-SEV\n                   PERFORM ADD-REASON\n               END-IF\n               IF IN-SCORE >= IN-TAU-RED AND IN-LOWRES = 1\n                   MOVE \"AI_SCORE_ABOVE_RED\" TO WS-NEW-REASON\n                   MOVE 1 TO WS-NEW-SEV\n                   PERFORM ADD-REASON\n                   MOVE \"LOW_RESOLUTION_EVIDENCE\" TO WS-NEW-REASON\n                   MOVE 1 TO WS-NEW-SEV\n                   PERFORM ADD-REASON\n               ELSE\n               IF IN-SCORE >= IN-TAU-RED\n                   MOVE \"AI_SCORE_ABOVE_RED\" TO WS-NEW-REASON\n                   MOVE 2 TO WS-NEW-SEV\n                   PERFORM ADD-REASON\n               ELSE\n                   IF IN-SCORE >= IN-TAU-AMBER\n                       MOVE \"AI_SCORE_ABOVE_AMBER\" TO WS-NEW-REASON\n                       MOVE 1 TO WS-NEW-SEV\n                       PERFORM ADD-REASON\n                   END-IF\n               END-IF\n               IF IN-SWTAG = 1\n                   MOVE \"EDITING_SOFTWARE_TAG\" TO WS-NEW-REASON\n                   MOVE 1 TO WS-NEW-SEV\n                   PERFORM ADD-REASON\n               END-IF\n           END-IF") + "</pre></div></div>" +
        '<div class="stack"><div class="card"><div class="card-h"><h2>Guarantees</h2></div><div class="card-b"><ul class="reasons">' +
          [["Nothing is rejected automatically.", "RED means a person investigates, in line with UK GDPR Article 22."],
           ["Errors never approve a claim.", "Risk-service timeout (300 ms), rule failure or unreadable thresholds all give AMBER."],
           ["The LLM can't change the band.", "Granite runs after the decision is committed, and treats claim text as untrusted input."],
           ["Personal data is pseudonymised.", "Review status is explicit. Identity reveal is journaled; the role selector is a demo control."],
           ["Nothing leaves without an allowlist.", "Egress payloads are built from named fields only, and logged."]].map(([h, p]) => '<li class="ok">' + ICON.GREEN + "<code>" + esc(h) + "</code><p>" + esc(p) + "</p></li>").join("") +
        "</ul></div></div>" +
        '<div class="card"><div class="card-h"><h2>What\'s real in this build</h2></div><div class="card-b" style="padding-top:8px"><dl class="kv">' +
          "<dt>Decision rule</dt><dd>Real COBOL on LinuxONE</dd><dt>Detector</dt><dd>Requires A’s artifact; readiness shown on System</dd><dt>Privacy rules</dt><dd>Real, on LinuxONE</dd><dt>LLM</dt><dd>Optional local Granite; model smoke result required</dd><dt>CICS</dt><dd>Emulated by the gateway</dd><dt>Claims</dt><dd>Fictional personas</dd>" +
        "</dl></div></div></div>" +
      "</div>";
  }

  function viewSystem(root) {
    setCrumbs([{ label: "System" }]);
    const all = Array.from(state.claims.values());
    const col = (k) => all.map((c) => c.timings && c.timings[k]).filter((x) => typeof x === "number").sort((a, b) => a - b);
    const rows = [["Gateway: validate and pseudonymise", "gateway", "≤ 10 ms + commit"], ["Risk service: privacy ∥ detector ∥ metadata ∥ duplicate", "risk", "≤ 70 ms"], ["Decision rule CLMIPOL", "policy", "≤ 15 ms with commit"], [state.mode === "live" ? "Before commit (excludes HTTP)" : "Demo decision", state.mode === "live" ? "precommit_total" : "total", state.mode === "live" ? "Benchmark full HTTP separately" : "Simulated"]];
    root.innerHTML =
      '<div class="page-head"><div><h1>System</h1><p>Services on the decision path and measured latency for the claims in this console.</p></div></div>' +
      '<div class="grid cols-2">' +
        '<div class="card"><div class="card-h"><h2>Services</h2><span class="small faint">' + (state.mode === "live" ? "Live from the CLMI gateway" : "Demo data") + '</span></div><div class="card-b" style="padding:6px 0">' + healthList(false) + "</div></div>" +
        '<div class="card"><div class="card-h"><h2>Latency</h2><span class="small faint">' + all.length + ' decisions</span></div><div class="card-b"><div class="table-wrap"><table class="plain"><thead><tr><th>Stage</th><th>Target</th><th class="num">p50</th><th class="num">p95</th><th class="num">p99</th></tr></thead><tbody>' +
          rows.map(([l, k, tg]) => { const v = col(k); return "<tr><td>" + esc(l) + '</td><td class="small mono">' + esc(tg) + '</td><td class="num">' + fmtMs(pct(v, 50)) + '</td><td class="num">' + fmtMs(pct(v, 95)) + '</td><td class="num">' + fmtMs(pct(v, 99)) + "</td></tr>"; }).join("") +
        '</tbody></table></div><p class="small faint" style="margin:10px 0 0">Fail-safe decisions include the risk timeout. Live journal totals exclude commit and HTTP; use eval/latency.md for full request measurements.</p></div></div>' +
      "</div>" +
      '<div class="card" style="margin-top:16px"><div class="card-h"><div><h2>Trust boundary</h2><p>The insurer\'s claims system sends each claim into transaction CLMI. Everything below runs inside IBM Z.</p></div></div><div class="card-b">' + boundarySvg() + "</div></div>";
  }

  function boundarySvg() {
    const box = (x, y, w, h, t, s, key) => '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" rx="8" fill="var(--layer)" stroke="' + (key ? "var(--accent)" : "var(--border-strong)") + '" stroke-width="' + (key ? 2 : 1.2) + '"/><text x="' + (x + w / 2) + '" y="' + (y + h / 2 - (s ? 3 : -4)) + '" text-anchor="middle" fill="var(--text)" font-size="13" font-weight="600">' + t + "</text>" + (s ? '<text x="' + (x + w / 2) + '" y="' + (y + h / 2 + 14) + '" text-anchor="middle" fill="var(--text-3)" font-size="11">' + s + "</text>" : "");
    const arrow = (d) => '<path d="' + d + '" fill="none" stroke="var(--text-3)" stroke-width="1.4" marker-end="url(#ah)"/>';
    return '<div class="diagram"><svg viewBox="0 0 980 300" role="img" aria-label="Claims flow from the insurer\'s claims system into transaction CLMI on IBM Z, which calls the risk service and returns a band to the claims system and this console.">' +
      '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 10 5 0 10z" fill="var(--text-3)"/></marker></defs>' +
      box(10, 70, 170, 64, "Insurer claims system", "sends claims + photos") + box(10, 190, 170, 64, "Claims staff", "this console") +
      '<rect x="220" y="14" width="750" height="276" rx="12" fill="none" stroke="var(--accent)" stroke-dasharray="6 5"/><text x="236" y="36" fill="var(--accent)" font-size="12" font-weight="600">IBM Z · LinuxONE · trust boundary</text>' +
      box(250, 70, 200, 84, "Transaction CLMI", "gateway + COBOL rule + journal", true) + box(250, 196, 200, 64, "Evidence store", "encrypted originals") +
      box(500, 70, 180, 84, "Risk service", "checks in parallel", true) +
      box(720, 46, 230, 52, "Privacy rules", "HMAC pseudonyms") + box(720, 108, 230, 52, "AI-image detector", "zDLC · NNPA if present") + box(720, 170, 230, 52, "Metadata + duplicate photo", "") + box(720, 232, 230, 48, "Granite LLM", "advisory note, after decision") +
      arrow("M180 102 H248") + arrow("M180 222 H248") + arrow("M450 102 H498") + arrow("M680 100 C700 100 700 72 718 72") + arrow("M680 112 C700 112 700 134 718 134") + arrow("M680 124 C700 124 700 196 718 196") + arrow("M590 154 C600 230 680 256 718 256") + arrow("M350 154 V194") +
      "</svg></div>";
  }

  // ================================================================ case action dialog
  let pendingAction = null;
  function openAction(c, act) {
    const a = ACTION[act];
    if (!a.note) { Cases.act(c, act, ""); toast(esc(a.verb) + " · " + esc(c.id)); updateNav(); render(); return; }
    pendingAction = { c, act };
    $("#ad-title").textContent = a.label + " · " + c.id;
    $("#ad-desc").textContent = a.desc || "";
    $("#ad-note").value = "";
    $("#ad-hint").textContent = "Signed as " + state.users[state.role].name + " (" + state.users[state.role].role + ").";
    $("#ad-confirm").textContent = a.label;
    $("#action-dialog").showModal();
    $("#ad-note").focus();
  }
  function wireDialog() {
    $("#ad-cancel").addEventListener("click", () => $("#action-dialog").close());
    $("#action-form").addEventListener("submit", (e) => {
      const note = $("#ad-note").value.trim();
      if (note.length < 5) { e.preventDefault(); $("#ad-hint").textContent = "Add a short reason (at least 5 characters)."; return; }
      if (!pendingAction) return;
      const { c, act } = pendingAction;
      Cases.act(c, act, note);
      pendingAction = null;
      toast(esc(ACTION[act].verb) + " · " + esc(c.id));
      updateNav();
      setTimeout(render, 0);
    });
  }

  // ================================================================ routing + chrome
  function parseRoute() {
    const raw = (location.hash || "#/dashboard").replace(/^#\/?/, "");
    const [path, qs] = raw.split("?");
    const parts = path.split("/");
    return { view: parts[0] || "dashboard", arg: parts[1] ? decodeURIComponent(parts[1]) : null, query: qs ? new URLSearchParams(qs) : null };
  }
  function render() {
    const r = parseRoute();
    state.route = r;
    state.charts = [];
    hideTip();
    const root = $("#view");
    const nav = r.view === "claims" ? "queue" : r.view;
    $$("[data-nav]").forEach((a) => a.setAttribute("aria-current", a.dataset.nav === nav ? "page" : "false"));
    const views = { dashboard: viewDashboard, queue: viewQueue, claims: (el) => viewClaim(el, r.arg), submit: viewSubmit, audit: viewAudit, policy: viewPolicy, system: viewSystem };
    const fn = views[r.view] || viewDashboard;
    const done = fn(root, r.query);
    Promise.resolve(done).then(() => {
      drawCharts();
      $$("[data-tv]", root).forEach((b) => b.addEventListener("click", () => { state.tableView[b.dataset.tv] = !state.tableView[b.dataset.tv]; render(); }));
    });
  }
  function onNavigate() {
    const prev = state.route.view + "/" + state.route.arg;
    render();
    const r = parseRoute();
    if (prev !== r.view + "/" + r.arg) { window.scrollTo(0, 0); $("#view").focus({ preventScroll: true }); }
  }
  function updateNav() {
    const n = openCount();
    const el = $("#nav-open");
    el.textContent = n; el.hidden = !n;
    el.setAttribute("aria-label", n + " open cases");
  }
  function setRole(r) {
    state.role = r === "siu" ? "siu" : "handler";
    store("aegis-role", state.role);
    $("#role").value = state.role;
    $("#avatar").textContent = state.users[state.role].initials;
  }
  function renderEnv() {
    const live = state.mode === "live";
    const env = $("#env"), side = $("#side-env");
    env.className = "env " + (live ? "live" : "demo");
    env.textContent = live ? "Live · CLMI gateway" : "Demo data";
    side.className = "side-env " + (live ? "live" : "demo");
    side.textContent = live ? "Connected to CLMI" : "Demo data (offline)";
    $("#demo-banner").hidden = live;
  }
  function initTheme() {
    const saved = store("aegis-theme");
    if (saved === "light" || saved === "dark") document.documentElement.setAttribute("data-theme", saved);
    $("#theme").addEventListener("click", () => {
      const cur = document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = cur === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      store("aegis-theme", next);
    });
  }
  function wire() {
    window.addEventListener("hashchange", onNavigate);
    $("#role").addEventListener("change", (e) => {
      setRole(e.target.value);
      toast("Signed in as " + esc(state.users[state.role].name) + " · " + esc(state.users[state.role].role) + "<br><span style=\"opacity:.8\">" +
        (state.role === "siu" ? "Can clear or close RED and referred cases, and reveal a claimant\u2019s identity (journaled)." : "Can assign, request evidence, clear AMBER cases and refer to SIU. Cannot reveal identity or close RED cases.") + "</span>");
      render();
    });
    $("#search-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const v = $("#search").value.trim();
      if (!v) return;
      const up = v.toUpperCase();
      const exact = state.claims.has(up) ? up : /^\d+$/.test(v) && state.claims.has("CLM-" + v.padStart(6, "0")) ? "CLM-" + v.padStart(6, "0") : null;
      if (exact) location.hash = "#/claims/" + encodeURIComponent(exact);
      else { state.queue.q = v; state.queue.status = "all"; state.queue.band = "ALL"; location.hash = "#/queue"; render(); }
      $("#search").value = "";
    });
    let rt;
    window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(drawCharts, 150); });
    wireDialog();
  }

  async function poll() {
    try {
      const { added, notes } = await Live.poll();
      if (added.length) {
        added.forEach((id) => state.fresh.add(id));
        setTimeout(() => added.forEach((id) => state.fresh.delete(id)), 3000);
        const flagged = added.map((id) => state.claims.get(id)).filter((c) => c && c.band !== "GREEN").length;
        toast(added.length + " new claim" + (added.length > 1 ? "s" : "") + " screened" + (flagged ? " · " + flagged + " flagged" : "") + ' · <a href="#/queue">Open queue</a>');
      }
      const r = state.route;
      if (added.length && (r.view === "dashboard" || r.view === "queue" || r.view === "audit" || r.view === "system")) render();
      if (notes.length && r.view === "claims" && notes.includes(r.arg)) render();
      const selected = state.claims.get(r.arg);
      if (r.view === "claims" && selected && selected.heatmapJob && !["succeeded", "failed", "unavailable"].includes(selected.heatmapStatus)) {
        await Live.pollHeatmap(selected); render();
      }
      renderEnv();
      updateNav();
    } catch (e) {
      $("#env").textContent = "Gateway unreachable · showing last data";
      $("#side-env").textContent = "Connection lost";
    }
    setTimeout(poll, 5000);
  }

  async function init() {
    initTheme();
    setRole(state.role);
    wire();
    $("#view").innerHTML = '<div class="empty">Connecting to the CLMI gateway…</div>';
    let live = false;
    if (params.get("demo") !== "1") { try { live = await Live.init(); } catch (e) { live = false; } }
    state.mode = live ? "live" : "demo";
    renderEnv();
    Cases.load();
    try {
      if (live) await Live.load(); else Demo.load();
    } catch (e) {
      $("#view").innerHTML = '<div class="card"><div class="empty">Couldn\'t load claims from the gateway: ' + esc(e.message) + "</div></div>";
      return;
    }
    updateNav();
    render();
    if (live) setTimeout(poll, 5000);
  }

  init();
})();
