/* node --test tests/ui/fleet_hardware_evidence.test.cjs — no npm dependencies. */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "../..");
class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.textContent = "";
    this.listeners = {};
    this.value = "";
    this.disabled = false;
  }
  get firstChild() { return this.children[0] || null; }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) { const i = this.children.indexOf(child); if (i >= 0) this.children.splice(i, 1); }
  addEventListener(event, fn) { this.listeners[event] = fn; }
}
function fixture(source, ids = {}) {
  const elements = Object.fromEntries(Object.entries(ids).map(([id, v]) => {
    const el = new Element("div");
    el.value = v;
    return [id, el];
  }));
  const document = {
    readyState: "complete",
    createElement: tag => new Element(tag),
    createTextNode: value => ({tagName: "#text", textContent: value}),
    getElementById: id => elements[id] || null,
    addEventListener() {},
  };
  const window = {document};
  const context = {window, document, URLSearchParams, console, fetch: () => { throw Error("fetch not mocked"); }};
  vm.runInNewContext(fs.readFileSync(path.join(root, source), "utf8"), context);
  return {elements, window, context};
}
function allNodes(element) {
  return [element, ...(element.children || []).flatMap(allNodes)];
}
const good = {
  snapshot: "2026-10-10T16-00-00Z",
  snapshot_freshness: "FRESH",
  checked_nodes: 1, truncated: false,
  evidence_digest_sha256: "a".repeat(64),
  dispatch_allowed: false, admission_allowed: false,
  nodes: [{
    node_id: "<script>alert(1)</script>",
    verification: "VERIFIED", reported_os_ram_gib: 48,
    reported_gpu_pci_functions_matching_request: ["0000:01:00.0"],
    blockers: ["<img src=x onerror=alert(1)>"],
    evidence: ["OS_VISIBLE_RAM_TOTAL"], risks: ["SHARED_USB_ROOT_BUS"],
    dispatch: "DENIED_NO_AUTHORITY",
  }],
};

test("render uses textContent only for private data; no markup insertion", () => {
  const {window} = fixture("static/js/fleet_hardware_preview.js");
  const table = new Element("section"), status = new Element("div");
  assert.equal(window.FleetHardwarePreview.render(table, status, good), true);
  assert.equal(allNodes(table).filter(n => n.tagName === "script").length, 0);
  assert.ok(allNodes(table).some(n => n.textContent.includes("<script>alert(1)</script>")));
  assert.ok(allNodes(table).some(n => n.textContent.includes("<img src=x onerror=alert(1)>")));
  assert.ok(status.textContent.includes("All dispatch denied"));
});
test("rejects authoritative or malformed response before displaying any row", () => {
  const {window} = fixture("static/js/fleet_hardware_preview.js");
  const panel = new Element("section"), status = new Element("div");
  assert.equal(window.FleetHardwarePreview.render(panel, status, {...good, admission_allowed: true}), false);
  assert.equal(panel.children.length, 0);
  assert.equal(window.FleetHardwarePreview.render(panel, status, {...good, nodes: [{...good.nodes[0], dispatch: "ALLOWED"}]}), false);
  assert.equal(panel.children.length, 0);
});
test("only issues an authenticated GET when operator submits; 503 is safe", async () => {
  const ids = {
    "fleet-hardware-evidence-form": "",
    "fleet-hardware-evidence-details": "",
    "fleet-hardware-evidence-status": "",
    "evidence-refresh": "",
    "evidence-ram": "20", "evidence-gpu": "16", "evidence-data-host": "",
  };
  const {elements, context} = fixture("static/js/fleet_hardware_preview.js", ids);
  const calls = [];
  context.fetch = async (url, options) => {
    calls.push({url, options});
    return {status: 503, ok: false};
  };
  assert.equal(calls.length, 0);
  const event = {preventDefault() {}};
  await elements["fleet-hardware-evidence-form"].listeners.submit(event);
  assert.equal(calls.length, 1);
  assert.ok(calls[0].url.startsWith("/api/fleet/hardware-preview?"));
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[0].options.cache, "no-store");
  assert.equal(calls[0].options.credentials, "same-origin");
  assert.ok(elements["fleet-hardware-evidence-status"].textContent.includes("not configured or unavailable"));
  assert.equal(elements["evidence-refresh"].disabled, false);
});
test("trace provenance cannot be inferred from current hardware or unrelated hashes", () => {
  const {window} = fixture("static/js/fleet_trace_evidence.js");
  const extract = window.FleetTraceEvidence.extract;
  assert.equal(extract({events: [{payload_json: JSON.stringify({snapshot: good.snapshot, digest: good.evidence_digest_sha256})}]}).length, 0);
  assert.equal(extract({events: [{payload_json: JSON.stringify({evidence_digest_sha256: good.evidence_digest_sha256})}]}).length, 0);
  const matching = {fleet_hardware_evidence: {snapshot: good.snapshot, evidence_digest_sha256: good.evidence_digest_sha256}};
  const events = [{payload_json: JSON.stringify(matching)}, {payload_json: JSON.stringify(matching)}];
  assert.equal(extract({events}).length, 1);
  assert.equal(extract({events})[0].digest, good.evidence_digest_sha256);
});
test("trace UI transparently states missing historical evidence", () => {
  const {window} = fixture("static/js/fleet_trace_evidence.js");
  const panel = new Element("div");
  window.FleetTraceEvidence.render({events: []}, panel);
  assert.ok(allNodes(panel).some(node => node.textContent.includes("cannot retroactively prove")));
  const payload = {fleet_hardware_evidence: {snapshot: good.snapshot, evidence_digest_sha256: good.evidence_digest_sha256}};
  const stored = new Element("div");
  window.FleetTraceEvidence.render({events: [{payload_json: JSON.stringify(payload)}]}, stored);
  assert.ok(allNodes(stored).some(n => n.textContent === good.evidence_digest_sha256));
});
