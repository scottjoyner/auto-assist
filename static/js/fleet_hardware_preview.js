(() => {
  "use strict";
  const id = (name) => document.getElementById(name);
  const form = id("hardware-evidence-form");
  if (!form) return;

  const status = id("hardware-evidence-status");
  const result = id("hardware-evidence-results");
  const button = id("hardware-evidence-inspect");

  // Render only server-defined, redacted fields. Never insert server text as HTML.
  function element(tag, content, cls) {
    const node = document.createElement(tag);
    if (content !== undefined) node.textContent = String(content);
    if (cls) node.className = cls;
    return node;
  }

  function explainNode(row) {
    const tr = document.createElement("tr");
    const columns = [
      row.node_id,
      row.verification,
      row.reported_os_ram_gib == null ? "unknown" : row.reported_os_ram_gib + " GiB",
      (row.reported_gpu_pci_functions_matching_request || []).join(", ") || "none matching",
      (row.evidence || []).join("; ") || "none",
      (row.blockers || []).join("; ") || "none recorded",
      (row.risks || []).join("; ") || "none recorded",
      "DENIED — advisory only",
    ];
    columns.forEach((value) => tr.appendChild(element("td", value)));
    return tr;
  }

  function render(payload) {
    result.replaceChildren();
    const summary = element(
      "p",
      "Snapshot " + (payload.snapshot || "unknown") +
      " · " + (payload.snapshot_freshness || "unknown") +
      " · " + (payload.checked_nodes ?? 0) + " registered nodes checked" +
      (payload.truncated ? " · truncated" : "") +
      " · NO DISPATCH / NO ADMISSION",
      "muted"
    );
    result.appendChild(summary);
    const digest = element("p", "Evidence digest SHA-256: " + (payload.evidence_digest_sha256 || "unavailable"), "muted");
    digest.style.overflowWrap = "anywhere";
    result.appendChild(digest);

    const table = element("table", undefined, "dashboard-table");
    const header = document.createElement("thead");
    const headerRow = document.createElement("tr");
    [
      "Node", "Verified", "OS RAM", "Matching GPU PCI function",
      "Capacity / locality evidence", "Blockers", "Known risks", "Execution"
    ].forEach((label) => headerRow.appendChild(element("th", label)));
    header.appendChild(headerRow);
    table.appendChild(header);
    const tbody = document.createElement("tbody");
    (payload.nodes || []).slice(0, 50).forEach((node) => tbody.appendChild(explainNode(node)));
    table.appendChild(tbody);
    result.appendChild(table);
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const ram = Number(id("hardware-evidence-ram").value);
    const gpu = Number(id("hardware-evidence-gpu").value);
    const host = id("hardware-evidence-data-host").value.trim();
    if (!Number.isFinite(ram) || !Number.isFinite(gpu) || ram < 0 ||
        gpu < 0 || ram > 4096 || gpu > 4096 ||
        (host && !/^[a-z0-9][a-z0-9-]{0,79}$/.test(host))) {
      status.textContent = "Invalid requirements: use nonnegative GiB and a registered host identifier.";
      return;
    }

    const params = new URLSearchParams({
      ram_gib: String(ram),
      gpu_vram_gib: String(gpu),
      limit: "50"
    });
    if (host) params.set("data_host", host);
    button.disabled = true;
    result.replaceChildren();
    status.textContent = "Reading private evidence through the authenticated preview…";
    try {
      const response = await fetch("/api/fleet/hardware-preview?" + params.toString(), {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        headers: {"Accept": "application/json"}
      });
      if (!response.ok) {
        status.textContent = response.status === 503
          ? "Hardware evidence is not configured or is unavailable; no recommendation shown."
          : response.status === 401 || response.status === 403
            ? "Operator authentication is required; no recommendation shown."
            : "Hardware preview unavailable (HTTP " + response.status + "); no recommendation shown.";
        return;
      }
      const data = await response.json();
      // Even a plausible static candidate must be marked non-executable.
      if (data.admission_allowed !== false || data.dispatch_allowed !== false ||
          !Array.isArray(data.nodes)) {
        status.textContent = "Preview contract violation; no recommendation shown.";
        return;
      }
      status.textContent = "Read-only evidence loaded. Candidate appearance never permits dispatch.";
      render(data);
    } catch (_error) {
      status.textContent = "Unable to read hardware evidence; no recommendation shown.";
    } finally {
      button.disabled = false;
    }
  });
})();
