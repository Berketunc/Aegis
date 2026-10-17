// Exercise the actual console adapter without starting a browser or duplicating it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../console.js'), 'utf8');
const sandbox = { URLSearchParams, location: {search: ''}, window: {}, localStorage: {getItem: () => null},
  setTimeout, clearTimeout, AbortController, console, fetch: async () => { throw Error('Unexpected network'); } };
vm.createContext(sandbox);
vm.runInContext(source.replace(/  init\(\);\s*\}\)\(\);\s*$/, '  window.test = {Live, state, privacyText, waterfall};\n})();'), sandbox);
const {Live, state, privacyText, waterfall} = sandbox.window.test;
state.mode = 'live';
const decision = {claim_id: 'CLM-1', ts: new Date().toISOString(), payload_json: {
  band: 'AMBER', reasons: ['THRESHOLDS_UNAVAILABLE'], timings_ms: {precommit_total: 11}}};
const pending = {status: 'pending', reason: 'granite_queued', description_released: false};
const view = {pseudonymised: {incident: {description: null}, damage_category: null, evidence: ['EV-1']}, egress: {payload: {}}, privacy_review: pending};
const c = Live.normalise(decision, view);
assert.equal(c.signals.riskOk, false);
assert.equal(c.privacyReview.status, 'pending');
assert.match(privacyText(c), /withheld/);
assert.match(waterfall(c.timings), /Before commit/);
assert.match(privacyText({...c, privacyReview: {status: 'rules_only', reason: 'granite_disabled', description_released: true}}), /Rules pass only/);
let calls = [];
sandbox.fetch = async (url, options) => { calls.push([url, options]); return {ok:true, text:async () => JSON.stringify(view)}; };
(async () => {
  await Live.refreshViews(c);
  assert.equal(c.record.incident.description, null);
  let phase = 0;
  sandbox.fetch = async (url, options) => {
    calls.push([url, options]);
    const value = url.endsWith('/views') ? view : url.endsWith('/heatmap') ? {job_id:'job-1',status:'queued'} :
      url.endsWith('/heatmap/job-1') ? {status:'succeeded', result:{patch_scores:[[.1,.2,.3,.4],[.1,.2,.3,.4],[.1,.2,.3,.4],[.1,.2,.3,.4]]}} :
      {adjuster_note:{status:'unavailable'}, privacy_review:pending};
    return {ok:true,text:async () => JSON.stringify(value)};
  };
  await Live.detail(c);
  assert.equal(c.heatmapStatus, 'succeeded');
  assert.equal(c.heatmap.length, 4);
  assert.ok(calls.some(([u,o]) => u.endsWith('/heatmap') && o.method === 'POST'));
  await Live.detail(c);
  assert.equal(calls.filter(([u,o]) => u.endsWith('/heatmap') && o.method === 'POST').length, 1);
  console.log('Console live API, privacy state, timing scope and async heatmap checks passed.');
})().catch(e => {console.error(e);process.exitCode=1;});
