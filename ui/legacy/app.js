/* Aegis UI: claimant form, result panel, adjuster view and system status for the CLMI gateway.
 * Talks to the gateway's API (txn/README.md). Falls back to mock.js, with a visible banner, when the
 * gateway can't be reached. Server data is untrusted: it is always escaped before insertion. */
(function () {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (ms) => (typeof ms === "number" ? (ms < 10 ? ms.toFixed(1) : Math.round(ms).toString()) : "–");

  const params = new URLSearchParams(location.search);
  const API = (params.get("api") || "").replace(/\/$/, "");
  const MAX_BYTES = 15 * 1024 * 1024;
  const MAX_PHOTOS = 3;

  const state = {
    mock: false,
    health: null,
    photos: [],                 // {file, name, url, status: uploading|ok|error, id, sha, ms, error}
    idem: newKey(),
    lastBody: null,
    lastPhotoFile: null,
    decisions: [],              // timings_ms of non-replayed decisions this session
    images: new Map(),          // evidence_id -> object URL (only for photos uploaded in this tab)
    claimImages: new Map(),     // claim_id -> [object URL]
    queue: [],
    filter: "REVIEW",
    selected: null,
    role: "adjuster",
    pollTimer: null,
  };

  // ------------------------------------------------------------------ copy
  const OUTCOME = {
    GREEN: "Fast-tracked to normal processing.",
    AMBER: "Sent for human review.",
    RED: "Referred to an investigator with the reasons. Not rejected.",
  };
  const CLAIMANT_MSG = {
    GREEN: "Thanks, your claim has been received and is being fast-tracked. We'll be in touch shortly.",
    AMBER: "Thanks, your claim has been received. A member of our claims team will review it and contact you.",
    RED: "Thanks, your claim has been received. A member of our claims team will review it and contact you.",
  };
  const REASONS = {
    NONE: "No signal crossed a threshold.",
    AI_SCORE_ABOVE_RED: "The detector's calibrated score is above the red threshold, set so that at most 1% of genuine photos reach it.",
    AI_SCORE_ABOVE_AMBER: "The detector's calibrated score is above the amber threshold (at most 5% of genuine photos reach it).",
    DUPLICATE_PHOTO: "The same photo (perceptual-hash match) was already used on a different claim. This is the plate-swap pattern.",
    EDITING_SOFTWARE_TAG: "The photo's metadata names an editing or generation tool.",
    RISK_SERVICE_UNAVAILABLE: "Fail-safe: the risk service didn't answer within 300 ms, so the claim goes to a person instead of being approved.",
    POLICY_ENGINE_ERROR: "Fail-safe: the decision rule couldn't run, so the claim goes to a person.",
    THRESHOLDS_UNAVAILABLE: "Fail-safe: the thresholds configuration couldn't be read, so the claim goes to a person.",
    POLICY_INPUT_INVALID: "Fail-safe: the decision rule received malformed input.",
    INFO_EXIF_MISSING: "Information only: the photo has no camera metadata. Messaging apps strip it, so this never changes the band.",
    INFO_C2PA_MANIFEST_PRESENT: "Information only: the photo carries a C2PA content-credentials manifest (not yet validated).",
    LOW_RESOLUTION_EVIDENCE: "Low-resolution photo: the AI score alone can hold the claim for review but not refer it.",
  };
  const SCENARIOS = {
    genuine: {
      policy: "PN-4471-0098", name: "Alex Doe", email: "alex.doe@example.com", phone: "07700 900123", damage: "bumper",
      desc: "Reversed into a bollard in a supermarket car park; rear bumper dented. No one else was involved.",
      hint: "Use a real photo of genuine damage. Expected: GREEN, fast-tracked.", test: "genuine_bumper.jpg",
    },
    generated: {
      policy: "PN-5520-1174", name: "Jordan Pike", email: "jordan.pike@example.com", phone: "07700 900456", damage: "lamp",
      desc: "Car was hit by a van while parked on the street overnight. Front left lamp smashed and wing crumpled.",
      hint: "Use a fully AI-generated damage photo (e.g. the SDXL demo asset). Expected: RED, with a heatmap and a Granite note.", test: "fake_sdxl.jpg",
    },
    inflated: {
      policy: "PN-3308-6621", name: "Morgan Reyes", email: "morgan.reyes@example.com", phone: "07700 900789", damage: "dent",
      desc: "Scraped a pillar in a multi-storey car park. Long dent along the driver's door.",
      hint: "Use a real photo with inpainted extra damage. Expected: AMBER or RED; the heatmap should point at the edit.", test: "inflated_door.jpg",
    },
    duplicate: {
      policy: "PN-9184-2290", name: "Casey Lund", email: "casey.lund@example.com", phone: "07700 900321", damage: "bumper",
      desc: "Rear-ended at traffic lights on the way to work. Rear bumper dented. Registration KX19 PLM.",
      hint: "Re-use the photo from an earlier claim with the number plate edited. Expected: RED via the duplicate-photo check.", test: null,
    },
  };

  // ------------------------------------------------------------------ helpers
  function newKey() {
    try { return crypto.randomUUID(); } catch (e) { return "k-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10); }
  }

  class ApiError extends Error { constructor(status, detail) { super(detail); this.status = status; } }

  function detailText(d) {
    if (!d) return "";
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((e) => ((e.loc ? e.loc.slice(1).join(".") + ": " : "") + e.msg)).join("; ");
    return JSON.stringify(d);
  }

  async function api(method, path, opts) {
    opts = opts || {};
    if (state.mock) {
      const r = await window.AegisMock.handle(method, path, opts);
      if (r.status >= 400) throw new ApiError(r.status, detailText(r.body && r.body.detail) || "error");
      return r.body;
    }
    const init = { method, headers: Object.assign({}, opts.headers || {}) };
    if (opts.json !== undefined) { init.headers["content-type"] = "application/json"; init.body = JSON.stringify(opts.json); }
    if (opts.form) init.body = opts.form;
    let res;
    try { res = await fetch(API + path, init); } catch (e) { throw new ApiError(0, "Gateway unreachable"); }
    const text = await res.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch (e) { body = { detail: text.slice(0, 200) }; }
    if (!res.ok) throw new ApiError(res.status, detailText(body && body.detail) || res.statusText);
    return body;
  }

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { t.hidden = true; }, 3500);
  }

  const band = (b, big) => `<span class="band ${esc(b)}${big ? " big" : ""}">${esc(b)}</span>`;
  const reasonList = (reasons) => `<ul class="reasons">${(reasons || []).map((r) =>
    `<li><code>${esc(r)}</code><p>${esc(REASONS[r] || "")}</p></li>`).join("")}</ul>`;

  function pct(sorted, p) {
    if (!sorted.length) return null;
    const i = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
    return sorted[Math.max(0, i)];
  }

  // ------------------------------------------------------------------ connection + theme
  async function connect() {
    if (params.get("mock") !== "1") {
      try {
        const ctrl = new AbortController();
        const t = setTimeout(() => ctrl.abort(), 2500);
        const res = await fetch(API + "/health", { signal: ctrl.signal });
        clearTimeout(t);
        const h = await res.json();
        if (h && h.txn === "CLMI") { state.mock = false; state.health = h; renderConn(); return; }
      } catch (e) { /* fall through to mock */ }
    }
    state.mock = true;
    state.health = (await window.AegisMock.handle("GET", "/health", {})).body;
    renderConn();
  }

  function renderConn() {
    const c = $("#conn");
    if (state.mock) {
      c.className = "conn mock";
      c.textContent = "Offline demo";
      $("#mock-banner").hidden = false;
    } else {
      c.className = "conn live";
      c.textContent = `Live · CLMI · ${state.health.policy_engine === "cobol" ? "COBOL rule" : "rule: " + state.health.policy_engine}`;
      $("#mock-banner").hidden = true;
    }
  }

  function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem("aegis-theme"); } catch (e) { /* storage unavailable */ }
    if (saved === "light" || saved === "dark") document.documentElement.setAttribute("data-theme", saved);
    $("#theme").addEventListener("click", () => {
      const cur = document.documentElement.getAttribute("data-theme") ||
        (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = cur === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try { localStorage.setItem("aegis-theme", next); } catch (e) { /* ignore */ }
    });
  }

  // ------------------------------------------------------------------ routing
  function route() {
    const parts = (location.hash || "#/overview").replace(/^#\/?/, "").split("/");
    const view = ["overview", "claim", "adjuster", "system"].includes(parts[0]) ? parts[0] : "overview";
    $$("section.view").forEach((s) => s.classList.toggle("active", s.id === "view-" + view));
    $$("nav.tabs a").forEach((a) => a.setAttribute("aria-current", a.dataset.view === view ? "page" : "false"));
    if (view !== "adjuster") stopPolling();
    if (view === "adjuster") loadQueue(parts[1] ? decodeURIComponent(parts[1]) : null);
    if (view === "system") renderSystem();
    window.scrollTo(0, 0);
  }

  // ------------------------------------------------------------------ claim form
  function resetForm(keepFields) {
    state.photos.forEach((p) => { if (!state.images.has(p.id)) URL.revokeObjectURL(p.url); });
    state.photos = [];
    state.idem = newKey();
    state.lastBody = null;
    $("#idem").textContent = state.idem;
    $("#resubmit").hidden = true;
    $("#form-error").hidden = true;
    if (!keepFields) {
      $("#claim-form").reset();
      $("#f-date").value = new Date(Date.now() - 2 * 864e5).toISOString().slice(0, 10);
      $("#scenario-hint").hidden = true;
      $$("[data-scenario]").forEach((b) => b.setAttribute("aria-pressed", "false"));
    }
    $("#reuse-photo").hidden = !state.lastPhotoFile;
    renderThumbs();
  }

  function applyScenario(name) {
    const s = SCENARIOS[name];
    resetForm(false);
    $$("[data-scenario]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.scenario === name)));
    $("#f-policy").value = s.policy;
    $("#f-name").value = s.name;
    $("#f-email").value = s.email;
    $("#f-phone").value = s.phone;
    $("#f-damage").value = s.damage;
    $("#f-desc").value = s.desc;
    $("#test-photo").dataset.name = s.test || "test_photo.jpg";
    const hint = $("#scenario-hint");
    hint.textContent = s.hint;
    hint.hidden = false;
    if (name === "duplicate") {
      if (state.lastPhotoFile) addFiles([state.lastPhotoFile]);
      else { hint.textContent = s.hint + " Submit another claim first so there's a photo to re-use."; }
    }
  }

  async function addFiles(files) {
    for (const file of files) {
      if (state.photos.length >= MAX_PHOTOS) { toast(`Up to ${MAX_PHOTOS} photos per claim`); break; }
      const p = { file, name: file.name, url: URL.createObjectURL(file), status: "uploading" };
      state.photos.push(p);
      renderThumbs();
      const typeOk = /^image\/(jpeg|png)$/.test(file.type) || /\.(jpe?g|png)$/i.test(file.name);
      if (!typeOk) { p.status = "error"; p.error = "Only JPEG or PNG photos"; renderThumbs(); continue; }
      if (file.size > MAX_BYTES) { p.status = "error"; p.error = "Over 15 MB"; renderThumbs(); continue; }
      const fd = new FormData();
      fd.append("file", file, file.name);
      const t0 = performance.now();
      try {
        const r = await api("POST", "/evidence", { form: fd });
        Object.assign(p, { id: r.evidence_id, sha: r.sha256, ms: performance.now() - t0, status: "ok" });
        state.images.set(p.id, p.url);
      } catch (e) {
        p.status = "error";
        p.error = e.status === 413 ? "File too large (max 15 MB)" : e.status === 415 ? "Only JPEG or PNG photos"
          : e.status === 503 ? "Evidence store unavailable, try again" : e.message;
      }
      renderThumbs();
    }
  }

  function renderThumbs() {
    $("#thumbs").innerHTML = state.photos.map((p, i) => `
      <div class="thumb">
        <img src="${esc(p.url)}" alt="Uploaded photo ${i + 1}">
        <div>
          <div class="mono" title="${esc(p.name)}">${esc(p.name.length > 22 ? p.name.slice(0, 20) + "…" : p.name)}</div>
          ${p.status === "uploading" ? `<span class="muted">Uploading…</span>` : ""}
          ${p.status === "ok" ? `<span class="ok">✓ ${esc(p.id)}</span><br><span class="muted">sha256 ${esc((p.sha || "").slice(0, 10))}… · ${fmt(p.ms)} ms</span>` : ""}
          ${p.status === "error" ? `<span class="err">✕ ${esc(p.error)}</span>` : ""}
          <br><button type="button" data-remove="${i}">Remove</button>
        </div>
      </div>`).join("");
  }

  function makeTestImage(name) {
    return new Promise((resolve) => {
      const c = document.createElement("canvas");
      c.width = 800; c.height = 600;
      const g = c.getContext("2d");
      const grd = g.createLinearGradient(0, 0, 0, 600);
      grd.addColorStop(0, "#9fb3c8"); grd.addColorStop(1, "#52606d");
      g.fillStyle = grd; g.fillRect(0, 0, 800, 600);
      g.fillStyle = "#c0392b"; g.fillRect(150, 280, 500, 150);
      g.fillStyle = "#1f2933"; [[250, 430], [550, 430]].forEach(([x, y]) => { g.beginPath(); g.arc(x, y, 45, 0, 7); g.fill(); });
      for (let i = 0; i < 400; i++) { g.fillStyle = `rgba(255,255,255,${Math.random() * 0.15})`; g.fillRect(Math.random() * 800, Math.random() * 600, 3, 3); }
      g.fillStyle = "#fff"; g.font = "bold 28px sans-serif"; g.fillText("SYNTHETIC TEST IMAGE", 24, 48);
      g.font = "16px sans-serif"; g.fillText("For testing the flow only, not a claim photo", 24, 74);
      c.toBlob((blob) => resolve(new File([blob], name, { type: "image/jpeg" })), "image/jpeg", 0.9);
    });
  }

  function formBody() {
    const v = (id) => $(id).value.trim();
    return {
      idempotency_key: state.idem,
      policy_number: v("#f-policy"),
      claimant: { name: v("#f-name"), email: v("#f-email") || null, phone: v("#f-phone") || null },
      incident: { date: v("#f-date"), description: v("#f-desc") },
      evidence: state.photos.filter((p) => p.status === "ok").map((p) => ({ evidence_id: p.id })),
      damage_category: v("#f-damage") || null,
    };
  }

  function validate(body) {
    if (state.photos.some((p) => p.status === "uploading")) return "Wait for the photo upload to finish.";
    if (!body.evidence.length) return "Add at least one photo.";
    if (!body.policy_number) return "Policy number is required.";
    if (!body.claimant.name) return "Name is required.";
    if (!body.incident.date) return "Incident date is required.";
    if (!body.incident.description) return "Describe what happened.";
    return null;
  }

  async function submit(again) {
    const errBox = $("#form-error");
    errBox.hidden = true;
    const body = again ? state.lastBody : formBody();
    const problem = again ? null : validate(body);
    if (problem) { errBox.textContent = problem; errBox.hidden = false; return; }
    const btns = [$("#submit"), $("#resubmit")];
    btns.forEach((b) => { b.disabled = true; });
    $("#submit").textContent = "Deciding…";
    $("#result").innerHTML = `<div class="result-empty"><p><strong>Deciding…</strong></p><p class="small">Gateway → risk service → CLMIPOL → journal</p></div>`;
    const t0 = performance.now();
    try {
      const d = await api("POST", "/claims", { json: body });
      const rtt = performance.now() - t0;
      state.lastBody = body;
      if (!d.replayed) {
        state.decisions.push(d.timings_ms);
        state.claimImages.set(d.claim_id, body.evidence.map((e) => state.images.get(e.evidence_id)).filter(Boolean));
        const firstOk = state.photos.find((p) => p.status === "ok");
        if (firstOk) state.lastPhotoFile = firstOk.file;
      }
      renderResult(d, rtt);
      $("#resubmit").hidden = false;
      $("#reuse-photo").hidden = !state.lastPhotoFile;
    } catch (e) {
      errBox.textContent = e.status ? `The gateway rejected the claim (${e.status}): ${e.message}` : e.message;
      errBox.hidden = false;
      $("#result").innerHTML = `<div class="result-empty"><p><strong>No decision</strong></p><p class="small">Nothing was committed. Fix the problem shown on the form and submit again.</p></div>`;
    } finally {
      btns.forEach((b) => { b.disabled = false; });
      $("#submit").textContent = "Submit claim";
    }
  }

  function waterfall(t) {
    if (!t) return "";
    const g = t.gateway || 0, r = t.risk || 0, p = t.policy || 0;
    const total = t.total || g + r + p;
    const pos = (v) => (100 * v / Math.max(total, 0.001)).toFixed(2) + "%";
    const rows = [["Gateway (validate, pseudonymise, commit)", 0, g, "gateway", ""]];
    rows.push(["Risk service call", g, r, "", ""]);
    const d = t.risk_detail || {};
    const lc = typeof d.load_crops === "number" ? d.load_crops : 0;
    [["Privacy rules", "privacy", 0], ["Load precomputed crops", "load_crops", 0], ["Detector inference", "inference", lc],
     ["Metadata flags", "metadata", 0], ["Duplicate-photo lookup", "dup", 0]].forEach(([label, k, off]) => {
      if (typeof d[k] === "number") rows.push([label, g + off, d[k], "", "sub"]);
    });
    rows.push(["Decision rule CLMIPOL", g + r, p, "policy", ""]);
    const within = total <= 200;
    return `<div class="wf" role="img" aria-label="Timing breakdown, total ${fmt(total)} milliseconds">
      ${rows.map(([label, start, dur, cls, row]) => `
        <div class="wf-row ${row}"><span class="wf-label">${esc(label)}</span>
          <span class="wf-track"><span class="wf-bar ${cls}" style="left:${pos(start)};width:${pos(dur)}"></span></span>
          <span class="wf-ms">${fmt(dur)} ms</span></div>`).join("")}
      <div class="wf-total"><span>Decision path total</span><span class="mono">${fmt(total)} ms ·
        <span class="${within ? "ok-text" : "warn-text"}">${within ? "within" : "over"} the 200 ms p95 target</span></span></div>
      <p class="small muted" style="margin-top:6px">Risk-service sub-steps run in parallel. Gateway time is one measured block: validation and pseudonymisation happen before the risk call, the commit after.</p>
    </div>`;
  }

  function renderResult(d, rtt) {
    $("#result").innerHTML = `
      <div class="result-head">
        ${band(d.band, true)}
        <span>${d.replayed ? `<span class="tag replay" title="Same idempotency key: stored decision returned, not re-scored">Replayed</span>` : `<span class="tag">New decision</span>`}</span>
      </div>
      <p class="outcome">${esc(OUTCOME[d.band] || "")}</p>
      ${reasonList(d.reasons)}
      <div class="claimant-msg"><strong>What the claimant sees:</strong> ${esc(CLAIMANT_MSG[d.band] || "")}</div>
      <h3 style="margin-top:16px">Timing</h3>
      ${waterfall(d.timings_ms)}
      <h3 style="margin-top:16px">Record</h3>
      <dl class="kv">
        <dt>Claim ID</dt><dd>${esc(d.claim_id)}</dd>
        <dt>Idempotency key</dt><dd>${esc(d.idempotency_key)}</dd>
        <dt>Model version</dt><dd>${esc(d.model_version || "– (risk service unavailable)")}</dd>
        <dt>Thresholds</dt><dd>${esc(d.thresholds_version)}</dd>
        <dt>Decision rule</dt><dd>${esc(d.policy_engine === "cobol" ? "COBOL (GnuCOBOL) CLMIPOL" : d.policy_engine)}</dd>
        <dt>Browser round trip</dt><dd>${fmt(rtt)} ms</dd>
      </dl>
      <div class="btn-row" style="margin-top:16px">
        <a class="btn secondary" href="#/adjuster/${encodeURIComponent(d.claim_id)}">Open in adjuster view</a>
      </div>`;
    $("#result").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // ------------------------------------------------------------------ adjuster
  async function loadQueue(select) {
    try {
      const rows = await api("GET", "/journal?limit=1000");
      const seen = new Map();
      for (const j of rows) {
        if (j.event === "DECISION" && j.claim_id && !seen.has(j.claim_id)) {
          const p = j.payload_json || {};
          seen.set(j.claim_id, { claim_id: j.claim_id, band: p.band, reasons: p.reasons || [], ts: j.ts });
        }
      }
      state.queue = Array.from(seen.values()).sort((a, b) => (a.ts < b.ts ? 1 : -1));
    } catch (e) {
      state.queue = [];
      $("#queue").innerHTML = `<li class="muted small" style="padding:10px">Couldn't load the queue: ${esc(e.message)}</li>`;
      return;
    }
    renderQueue();
    if (select) selectClaim(select);
  }

  function renderQueue() {
    const f = state.filter;
    const items = state.queue.filter((c) => f === "ALL" || (f === "REVIEW" ? c.band !== "GREEN" : c.band === f));
    $("#queue").innerHTML = items.length ? items.map((c) => `
      <li><button type="button" data-claim="${esc(c.claim_id)}" aria-current="${c.claim_id === state.selected}">
        <span class="q-top"><strong class="mono">${esc(c.claim_id)}</strong>${band(c.band)}</span>
        <span class="q-reasons">${esc(c.reasons.join(", "))}</span>
        <span class="small muted">${esc(new Date(c.ts).toLocaleTimeString())}</span>
      </button></li>`).join("")
      : `<li class="muted small" style="padding:10px">${state.queue.length ? "No claims match this filter." : "No claims yet. Submit one from the claim form."}</li>`;
  }

  function stopPolling() { clearTimeout(state.pollTimer); state.pollTimer = null; }

  async function selectClaim(id) {
    stopPolling();
    state.selected = id;
    renderQueue();
    const det = $("#detail");
    det.innerHTML = `<div class="card result-empty"><p>Loading ${esc(id)}…</p></div>`;
    const queued = state.queue.find((c) => c.claim_id === id);
    const [claim, views, heat, jr] = await Promise.allSettled([
      api("GET", `/claims/${encodeURIComponent(id)}`),
      api("GET", `/claims/${encodeURIComponent(id)}/views`, { headers: { "X-Role": state.role } }),
      // Heatmaps exist only for AMBER/RED (computed asynchronously); don't ask for GREEN.
      queued && queued.band === "GREEN" ? Promise.reject(new ApiError(404, "GREEN")) : api("GET", `/claims/${encodeURIComponent(id)}/heatmap`),
      api("GET", `/journal?claim_id=${encodeURIComponent(id)}`),
    ]);
    if (state.selected !== id) return;
    if (claim.status !== "fulfilled") {
      det.innerHTML = `<div class="card result-empty"><p>Couldn't load ${esc(id)}: ${esc(claim.reason.message)}</p></div>`;
      return;
    }
    const d = claim.value;
    const v = views.status === "fulfilled" ? views.value : null;
    const egress = v && v.egress ? v.egress.payload || {} : {};
    const imgs = state.claimImages.get(id) || [];
    const grid = heat.status === "fulfilled" && heat.value && Array.isArray(heat.value.grid) ? heat.value.grid : null;
    const score = typeof egress.ai_score === "number" ? egress.ai_score : null;
    const color = { GREEN: "var(--green)", AMBER: "var(--amber)", RED: "var(--red)" }[d.band];

    det.innerHTML = `
      <div class="card">
        <div class="result-head">
          <div>${band(d.band, true)} <strong class="mono" style="margin-left:8px">${esc(d.claim_id)}</strong></div>
          <span class="small muted">${esc(OUTCOME[d.band] || "")}</span>
        </div>
        ${reasonList(d.reasons)}
        <dl class="kv">
          <dt>Model</dt><dd>${esc(d.model_version || "–")}</dd>
          <dt>Thresholds</dt><dd>${esc(d.thresholds_version)}</dd>
          <dt>Decision rule</dt><dd>${esc(d.policy_engine)}</dd>
          <dt>Decision path</dt><dd>${fmt(d.timings_ms && d.timings_ms.total)} ms</dd>
        </dl>
      </div>

      <div class="grid g2" style="margin-top:16px">
        <div class="card">
          <div class="result-head"><h3 style="margin:0">Photo</h3>
            ${grid && imgs.length ? `<label class="small"><input type="checkbox" id="heat-toggle" checked> Heatmap</label>` : ""}</div>
          ${imgs.length ? `<div class="photo-wrap"><img src="${esc(imgs[0])}" alt="Claim photo">
              ${grid ? `<div class="heat" id="heat" style="grid-template-columns:repeat(${grid[0].length},1fr);grid-template-rows:repeat(${grid.length},1fr)">
                ${grid.flat().map((x) => `<div style="background:rgba(230,40,40,${Math.max(0, Math.min(1, Number(x) || 0)) * 0.6})"></div>`).join("")}</div>` : ""}
            </div>`
            : `<div class="photo-wrap"><div class="photo-missing">The original is held in the encrypted evidence store.<br>Photos are previewed only in the browser tab that uploaded them.</div></div>`}
          <p class="small muted" style="margin-top:8px">${grid ? "Heatmap: a coarse 4×4 map of where the detector sees generation traces. It's a guide for the reviewer, not proof."
            : (heat.status === "rejected" ? "Heatmap not available for this claim or deployment (computed asynchronously for AMBER/RED only)." : "")}</p>
        </div>
        <div class="card">
          <h3>Signals</h3>
          ${score !== null ? `
            <div class="small">Calibrated AI-generation score <strong class="mono">${score.toFixed(2)}</strong></div>
            <div class="gauge" role="img" aria-label="Score ${score.toFixed(2)} of 1"><span style="width:${(score * 100).toFixed(1)}%;background:${color}"></span></div>
            <div class="small muted">One signal, not proof. Thresholds ${esc(d.thresholds_version)}.</div>` : `<p class="small muted">No detector score: the risk service didn't return signals for this claim.</p>`}
          <dl class="kv" style="margin-top:12px">
            <dt>Duplicate photo</dt><dd>${egress.duplicate_detected ? "yes" : "no"}</dd>
            <dt>Editing software tag</dt><dd>${egress.software_tag_present == null ? "–" : egress.software_tag_present ? "yes" : "no"}</dd>
            <dt>EXIF present</dt><dd>${egress.exif_present == null ? "–" : egress.exif_present ? "yes" : "no"}</dd>
            <dt>C2PA manifest</dt><dd>${egress.c2pa_manifest == null ? "–" : egress.c2pa_manifest ? "present" : "none"}</dd>
          </dl>
          <h3 style="margin-top:16px">Granite note <span class="tag">advisory</span></h3>
          <div id="note">${noteHtml(d.adjuster_note, d.band)}</div>
        </div>
      </div>

      <div class="card" style="margin-top:16px">
        <div class="result-head">
          <h3 style="margin:0">Who sees what</h3>
          <div class="seg" role="group" aria-label="Viewing role">
            <button type="button" data-role="adjuster" aria-pressed="${state.role === "adjuster"}">Adjuster</button>
            <button type="button" data-role="authorised" aria-pressed="${state.role === "authorised"}">Authorised</button>
          </div>
        </div>
        ${v ? viewsHtml(v) : `<p class="muted small">Couldn't load the record views.</p>`}
      </div>

      <div class="card" style="margin-top:16px">
        <h3>Journal <span class="tag">append-only</span></h3>
        <div class="table-wrap"><table class="journal" id="claim-journal">${journalRows(jr.status === "fulfilled" ? jr.value : [])}</table></div>
      </div>`;

    const status = d.adjuster_note && d.adjuster_note.status;
    if (status === "pending") pollNote(id, 0);
  }

  function noteHtml(note, b) {
    const s = note ? note.status : "not_requested";
    if (s === "ready") return `<div class="note">${esc(note.note)}</div><p class="small muted" style="margin-top:6px">${state.mock
      ? "Simulated note (offline demo). On LinuxONE this is written by IBM Granite from the pseudonymised record."
      : "Written by IBM Granite on LinuxONE from the pseudonymised record."} It can't change the band.</p>`;
    if (s === "pending") return `<p class="small muted">Granite is writing the note… (asynchronous, off the decision path)</p>`;
    if (s === "unavailable") return `<p class="small muted">Note unavailable: Granite's output failed validation or Granite was unreachable. The band is unaffected.</p>`;
    return `<p class="small muted">${b === "GREEN" ? "Granite runs only for AMBER and RED claims." : "No note requested (Granite not configured on this deployment)."}</p>`;
  }

  function pollNote(id, n) {
    state.pollTimer = setTimeout(async () => {
      if (state.selected !== id) return;
      try {
        const d = await api("GET", `/claims/${encodeURIComponent(id)}`);
        if (state.selected !== id) return;
        const s = d.adjuster_note && d.adjuster_note.status;
        if (s !== "pending" || n >= 45) {
          $("#note").innerHTML = noteHtml(d.adjuster_note, d.band);
          refreshClaimJournal(id);
          return;
        }
      } catch (e) { /* keep polling */ }
      pollNote(id, n + 1);
    }, 2000);
  }

  async function refreshClaimJournal(id) {
    try {
      const rows = await api("GET", `/journal?claim_id=${encodeURIComponent(id)}`);
      const t = $("#claim-journal");
      if (t && state.selected === id) t.innerHTML = journalRows(rows);
    } catch (e) { /* ignore */ }
  }

  function viewsHtml(v) {
    const pre = (o) => `<pre>${esc(JSON.stringify(o, null, 2))}</pre>`;
    const eg = v.egress || {};
    return `<div class="views">
      <div><h4>Original claim form ${v.original ? `<span class="tag">re-linked</span>` : ""}</h4>
        ${v.original ? pre(v.original) + `<p class="small muted" style="margin-top:6px">Re-linked from the token vault. This access was written to the journal.</p>`
          : `<div class="locked">🔒 Hidden from the adjuster role.<br>Only an authorised role can re-link identity.</div>`}</div>
      <div><h4>Pseudonymised record <span class="tag">stored</span></h4>${pre(v.pseudonymised)}
        <p class="small muted" style="margin-top:6px">What's stored, scored and shown to adjusters. Tokens are HMAC pseudonyms.</p></div>
      <div><h4>Egress payload <span class="tag">${esc(eg.egress_id || "")}</span></h4>${pre(eg.payload || {})}
        <p class="small muted" style="margin-top:6px">Allowlisted fields only. Dropped: ${esc((eg.dropped || []).join(", ") || "none")}.</p></div>
    </div>`;
  }

  function journalRows(rows) {
    if (!rows || !rows.length) return `<tr><td class="muted">No journal entries.</td></tr>`;
    return `<thead><tr><th>#</th><th>Time</th><th>Event</th><th>Claim</th><th>Payload</th></tr></thead><tbody>${rows.map((j) => `
      <tr><td>${esc(j.seq)}</td><td class="mono small">${esc(new Date(j.ts).toLocaleTimeString())}</td><td><strong>${esc(j.event)}</strong></td>
      <td class="mono small">${esc(j.claim_id || "–")}</td><td><code>${esc(summarise(j.payload_json))}</code></td></tr>`).join("")}</tbody>`;
  }

  function summarise(p) {
    if (!p) return "";
    if (p.band) return `${p.band} ${(p.reasons || []).join(",")} · ${p.policy_engine || ""} · ${p.thresholds_version || ""}`;
    const s = JSON.stringify(p);
    return s.length > 90 ? s.slice(0, 88) + "…" : s;
  }

  // ------------------------------------------------------------------ system
  async function renderSystem() {
    try { state.health = await api("GET", "/health"); } catch (e) { /* keep last */ }
    const h = state.health || {};
    const engine = h.policy_engine === "cobol" ? "COBOL (GnuCOBOL)" : h.policy_engine === "python" ? "Python twin" : (h.policy_engine || "–");
    const card = (k, v, cls) => `<div class="card metric"><div class="k">${esc(k)}</div><div class="v ${cls || ""}">${esc(v)}</div></div>`;
    $("#health-cards").innerHTML =
      card("Gateway", state.mock ? "Offline demo" : (h.status === "ok" ? "Live" : "Unknown"), state.mock ? "warn-text" : "ok-text") +
      card("Transaction", h.txn || "CLMI") +
      card("Decision rule", engine, h.policy_engine === "cobol" ? "ok-text" : "") +
      card("Thresholds", h.thresholds_version || "–", h.thresholds_version === "unavailable" ? "bad-text" : "");

    const col = (k) => state.decisions.map((t) => t && t[k]).filter((x) => typeof x === "number").sort((a, b) => a - b);
    const rows = [
      ["Gateway: receive, validate, pseudonymise, journal + commit", "≤ 10 ms + commit", col("gateway")],
      ["Risk service: privacy ∥ detector ∥ metadata ∥ duplicate", "≤ 70 ms", col("risk")],
      ["Decision rule CLMIPOL", "≤ 15 ms (with commit)", col("policy")],
      ["Total decision path", "p95 ≤ 200 ms (stretch 100)", col("total")],
    ];
    $("#latency-table").innerHTML = `<thead><tr><th>Stage</th><th>Target</th><th>n</th><th>p50</th><th>p95</th></tr></thead><tbody>${rows.map(([s, t, v]) =>
      `<tr><td>${esc(s)}</td><td class="mono small">${esc(t)}</td><td class="mono">${v.length}</td><td class="mono">${fmt(pct(v, 50))}</td><td class="mono">${fmt(pct(v, 95))}</td></tr>`).join("")}</tbody>`;

    try {
      const j = await api("GET", "/journal?limit=12");
      $("#sys-journal").innerHTML = journalRows(j);
    } catch (e) {
      $("#sys-journal").innerHTML = `<tr><td class="muted">Couldn't load the journal: ${esc(e.message)}</td></tr>`;
    }
  }

  // ------------------------------------------------------------------ wiring
  function wire() {
    window.addEventListener("hashchange", route);
    $$("[data-scenario]").forEach((b) => b.addEventListener("click", () => applyScenario(b.dataset.scenario)));
    const drop = $("#drop");
    $("#file").addEventListener("change", (e) => { addFiles(Array.from(e.target.files)); e.target.value = ""; });
    ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", (e) => addFiles(Array.from(e.dataTransfer.files)));
    $("#thumbs").addEventListener("click", (e) => {
      const i = e.target.dataset.remove;
      if (i === undefined) return;
      state.photos.splice(Number(i), 1);
      renderThumbs();
    });
    $("#reuse-photo").addEventListener("click", () => state.lastPhotoFile && addFiles([state.lastPhotoFile]));
    $("#test-photo").addEventListener("click", async (e) => addFiles([await makeTestImage(e.currentTarget.dataset.name || "test_photo.jpg")]));
    $("#claim-form").addEventListener("submit", (e) => { e.preventDefault(); submit(false); });
    $("#resubmit").addEventListener("click", () => submit(true));
    $("#new-claim").addEventListener("click", () => {
      resetForm(false);
      $("#result").innerHTML = `<div class="result-empty"><p><strong>No decision yet</strong></p><p class="small">Pick a scenario or fill in the form, add a photo and submit.</p></div>`;
    });

    $$("[data-filter]").forEach((b) => b.addEventListener("click", () => {
      state.filter = b.dataset.filter;
      $$("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      renderQueue();
    }));
    $("#refresh-queue").addEventListener("click", () => loadQueue(state.selected));
    $("#queue").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-claim]");
      if (!btn) return;
      history.replaceState(null, "", "#/adjuster/" + encodeURIComponent(btn.dataset.claim));
      selectClaim(btn.dataset.claim);
    });
    $("#detail").addEventListener("change", (e) => {
      if (e.target.id === "heat-toggle") { const h = $("#heat"); if (h) h.hidden = !e.target.checked; }
    });
    $("#detail").addEventListener("click", (e) => {
      const r = e.target.closest("[data-role]");
      if (r && r.dataset.role !== state.role) { state.role = r.dataset.role; selectClaim(state.selected); }
    });
  }

  async function init() {
    initTheme();
    wire();
    resetForm(false);
    await connect();   // decides live vs offline demo before any view calls the API
    route();
  }

  init();
})();
