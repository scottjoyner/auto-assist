import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../static/js/fleet_hardware_preview.js", import.meta.url), "utf8");

function setup(handler) {
  const all = new Map();
  class FakeNode {
    constructor(tag = "div") {
      this.tag = tag;
      this.children = [];
      this.textContent = "";
      this.listeners = {};
      this.value = "";
      this.disabled = false;
      this.style = {};
      this.className = "";
    }
    appendChild(child) {
      this.children.push(child);
      return child;
    }
    replaceChildren(...children) {
      this.children = children;
    }
    addEventListener(name, callback) {
      this.listeners[name] = callback;
    }
  }
  const elementIds = ["hardware-evidence-form", "hardware-evidence-status",
    "hardware-evidence-results", "hardware-evidence-inspect",
    "hardware-evidence-ram", "hardware-evidence-gpu", "hardware-evidence-data-host"];
  elementIds.forEach((name) => all.set(name, new FakeNode()));
  all.get("hardware-evidence-ram").value = "16";
  all.get("hardware-evidence-gpu").value = "8";
  const calls = [];
  const sandbox = {
    document: {
      getElementById: (id) => all.get(id) ?? null,
      createElement: (tag) => new FakeNode(tag),
    },
    URLSearchParams,
    fetch: async (path, opts) => {
      calls.push({path, opts});
      return handler(path, opts);
    },
  };
  vm.runInNewContext(source, sandbox);
  return {
    all, calls,
    submit: () => all.get("hardware-evidence-form").listeners.submit({preventDefault() {}}),
  };
}
const payload = {
  admission_allowed: false, dispatch_allowed: false,
  snapshot: "2026-10-10T16-00-00Z", snapshot_freshness: "FRESH",
  evidence_digest_sha256: "a".repeat(64), checked_nodes: 2,
  truncated: false,
  nodes: [
    {node_id: "<img src=x onerror=alert(1)>", verification: "VERIFIED",
     reported_os_ram_gib: 48.0, reported_gpu_pci_functions_matching_request: ["0000:01:00.0"],
     evidence: ["GPU_PCI_FUNCTION_VRAM_OBSERVED"], blockers: [],
     risks: ["SHARED_USB_ROOT_BUS"]},
  ],
};

test("no automatic request, GET-only on explicit submit, no HTML sink", async () => {
  const ui = setup(async () => ({ok: true, json: async () => payload}));
  assert.equal(ui.calls.length, 0);
  assert.match(ui.all.get("hardware-evidence-status").textContent, /^$/);
  await ui.submit();
  assert.equal(ui.calls.length, 1);
  const request = ui.calls[0];
  assert.equal(request.opts.method, "GET");
  assert.equal(request.opts.credentials, "same-origin");
  assert.equal(request.opts.cache, "no-store");
  assert.equal(request.opts.redirect, "error");
  assert.match(request.path, /^\/api\/fleet\/hardware-preview\?/);
  assert.match(ui.all.get("hardware-evidence-status").textContent, /never permits dispatch/i);
  const nodes = ui.all.get("hardware-evidence-results").children;
  const table = nodes.find((n) => n.tag === "table");
  assert.ok(table);
  const row = table.children[1].children[0];
  assert.equal(row.children[0].textContent, "<img src=x onerror=alert(1)>");
  assert.equal(row.children[7].textContent, "DENIED — advisory only");
  assert.equal(ui.all.get("hardware-evidence-inspect").disabled, false);
  assert.doesNotMatch(source, /innerHTML\s*=|eval\(|new Function/);
});

test("unavailable private data fails without displaying candidates", async () => {
  const ui = setup(async () => ({ok: false, status: 503}));
  await ui.submit();
  assert.equal(ui.all.get("hardware-evidence-results").children.length, 0);
  assert.match(ui.all.get("hardware-evidence-status").textContent, /not configured or is unavailable/i);
});

test("endpoint contract cannot claim admission or dispatch", async () => {
  const ui = setup(async () => ({ok: true, json: async () => ({...payload, dispatch_allowed: true})}));
  await ui.submit();
  assert.equal(ui.all.get("hardware-evidence-results").children.length, 0);
  assert.match(ui.all.get("hardware-evidence-status").textContent, /contract violation/i);
});

test("invalid RAM or malformed host prevents requests", async () => {
  const ui = setup(async () => ({ok: true, json: async () => payload}));
  ui.all.get("hardware-evidence-gpu").value = "99999";
  await ui.submit();
  assert.equal(ui.calls.length, 0);
  ui.all.get("hardware-evidence-gpu").value = "8";
  ui.all.get("hardware-evidence-data-host").value = "../private";
  await ui.submit();
  assert.equal(ui.calls.length, 0);
});
