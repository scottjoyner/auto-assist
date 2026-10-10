/* Strictly read-only on-demand fleet evidence preview: no task, dispatch or claim calls. */
(function (global) {
  "use strict";
  function make(tag, value, parent) {
    const element = document.createElement(tag);
    if (value !== undefined && value !== null) element.textContent = String(value);
    if (parent) parent.appendChild(element);
    return element;
  }
  function clear(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }
  function bounded(value, max) { return String(value == null ? "" : value).slice(0, max); }
  function render(container, status, data) {
    clear(container);
    if (!data || data.dispatch_allowed !== false || data.admission_allowed !== false ||
        !Array.isArray(data.nodes) || !/^[0-9a-f]{64}$/.test(data.evidence_digest_sha256 || "")) {
      status.textContent = "Evidence invalid or not explicitly advisory. No nodes displayed.";
      return false;
    }
    for (const row of data.nodes.slice(0, 100)) {
      if (!row || row.dispatch !== "DENIED_NO_AUTHORITY") {
        status.textContent = "Unexpected dispatch authority in evidence; all nodes withheld.";
        return false;
      }
    }
    status.textContent = "Evidence loaded: " + bounded(data.snapshot_freshness, 14) +
      " / " + Number(data.checked_nodes || 0) + " registered nodes. All dispatch denied.";
    const ref = make("p", null, container);
    make("strong", "Snapshot: ", ref);
    make("code", bounded(data.snapshot, 28), ref);
    make("span", " · Preview SHA-256: ", ref);
    make("code", data.evidence_digest_sha256, ref);
    make("p", "Source-file digest, not a signature, lease, or historical trace reference.", container);
    const table = make("table", null, container);
    table.className = "dashboard-table";
    const header = make("tr", null, make("thead", null, table));
    ["Node", "Verified", "OS RAM GiB", "PCI GPU functions meeting request", "Evidence", "Blockers and risks", "Admission"].forEach(v => make("th", v, header));
    const body = make("tbody", null, table);
    for (const row of data.nodes.slice(0, 100)) {
      const tr = make("tr", null, body);
      make("td", bounded(row.node_id, 80), tr);
      make("td", bounded(row.verification, 20), tr);
      make("td", typeof row.reported_os_ram_gib === "number" ? row.reported_os_ram_gib.toFixed(2) : "unknown", tr);
      make("td", (row.reported_gpu_pci_functions_matching_request || []).slice(0, 8).map(v => bounded(v, 20)).join(", ") || "none", tr);
      make("td", (row.evidence || []).slice(0, 12).map(v => bounded(v, 64)).join(" · ") || "none", tr);
      make("td", (row.blockers || []).concat(row.risks || []).slice(0, 20).map(v => bounded(v, 64)).join(" · ") || "runtime unverified", tr);
      make("td", "DENIED", tr);
    }
    if (data.truncated) make("p", "Result truncated; refine your query.", container);
    return true;
  }
  function init() {
    const form = document.getElementById("fleet-hardware-evidence-form");
    if (!form) return;
    const container = document.getElementById("fleet-hardware-evidence-details");
    const status = document.getElementById("fleet-hardware-evidence-status");
    const button = document.getElementById("evidence-refresh");
    let sequence = 0;
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const run = ++sequence;
      button.disabled = true;
      clear(container);
      status.textContent = "Loading private hardware evidence…";
      try {
        const ram = Number(document.getElementById("evidence-ram").value);
        const gpu = Number(document.getElementById("evidence-gpu").value);
        if (![ram, gpu].every(v => Number.isFinite(v) && v >= 0 && v <= 4096)) throw new Error("Capacity out of range 0–4096 GiB");
        const q = new URLSearchParams({ram_gib: String(ram), gpu_vram_gib: String(gpu), limit: "50"});
        const host = document.getElementById("evidence-data-host").value.trim();
        if (host) {
          if (!/^[a-z0-9][a-z0-9-]{0,79}$/.test(host)) throw new Error("Invalid data host");
          q.set("data_host", host);
        }
        const response = await fetch("/api/fleet/hardware-preview?" + q.toString(), {
          method: "GET", credentials: "same-origin", cache: "no-store", headers: {"Accept": "application/json"}
        });
        if (run !== sequence) return;
        if (response.status === 503) {
          status.textContent = "Hardware evidence not configured or unavailable. Other fleet views remain usable.";
          return;
        }
        if (!response.ok) throw new Error("HTTP " + response.status);
        const data = await response.json();
        if (run === sequence) render(container, status, data);
      } catch (error) {
        if (run === sequence) status.textContent = "Evidence unavailable: " + bounded(error && error.message, 80);
      } finally {
        if (run === sequence) button.disabled = false;
      }
    });
  }
  global.FleetHardwarePreview = {render, init};
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
  }
})(typeof window === "undefined" ? globalThis : window);
