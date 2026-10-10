/* Trace-bound fleet hardware provenance: never substitute today's inventory for an old trace. */
(function (global) {
  "use strict";
  const SNAPSHOT = /^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z$/;
  const DIGEST = /^[0-9a-f]{64}$/;
  function evidenceFromEvent(event) {
    if (!event || typeof event !== "object") return null;
    const options = [];
    if (event.payload_json && typeof event.payload_json === "string" && event.payload_json.length <= 65536) {
      try {
        const parsed = JSON.parse(event.payload_json);
        if (parsed && typeof parsed === "object") options.push(parsed);
      } catch (ignored) { /* Unknown payload schema, not hardware provenance. */ }
    }
    options.push(event);
    for (const payload of options) {
      const candidates = [];
      if (payload.fleet_hardware_evidence && typeof payload.fleet_hardware_evidence === "object") {
        candidates.push({
          snapshot: payload.fleet_hardware_evidence.snapshot,
          digest: payload.fleet_hardware_evidence.evidence_digest_sha256
        });
      }
      candidates.push({
        snapshot: payload.fleet_hardware_snapshot,
        digest: payload.fleet_hardware_evidence_digest_sha256
      });
      for (const candidate of candidates) {
        if (typeof candidate.snapshot === "string" && SNAPSHOT.test(candidate.snapshot) &&
            typeof candidate.digest === "string" && DIGEST.test(candidate.digest)) {
          return {snapshot: candidate.snapshot, digest: candidate.digest};
        }
      }
    }
    return null;
  }
  function extract(trace) {
    const records = [];
    const seen = new Set();
    for (const event of (trace && Array.isArray(trace.events) ? trace.events : []).slice(0, 500)) {
      const ref = evidenceFromEvent(event);
      if (!ref) continue;
      const key = ref.snapshot + "/" + ref.digest;
      if (seen.has(key)) continue;
      seen.add(key);
      records.push(ref);
      if (records.length >= 8) break;
    }
    return records;
  }
  function render(trace, parent) {
    if (!parent || !global.document) return;
    const refs = extract(trace);
    const root = document.createElement("section");
    root.className = "trace-hardware-evidence";
    const title = document.createElement("h3");
    title.textContent = "Fleet hardware provenance";
    root.appendChild(title);
    const note = document.createElement("p");
    if (!refs.length) {
      note.textContent = "No hardware snapshot and digest were recorded in this trace. Current fleet inventory cannot retroactively prove this run's capacity.";
      root.appendChild(note);
      parent.appendChild(root);
      return;
    }
    note.textContent = "References explicitly recorded by trace events (SHA-256 labels, not cryptographic verification of retained files).";
    root.appendChild(note);
    const list = document.createElement("ul");
    for (const ref of refs) {
      const item = document.createElement("li");
      const snap = document.createElement("code");
      snap.textContent = ref.snapshot;
      const digest = document.createElement("code");
      digest.textContent = ref.digest;
      item.appendChild(snap);
      item.appendChild(document.createTextNode(" · "));
      item.appendChild(digest);
      list.appendChild(item);
    }
    root.appendChild(list);
    parent.appendChild(root);
  }

  // Separate operator inspections are POST-HOC observations, never original
  // execution evidence. Historical event provenance is rendered above.
  function renderInspections(data, container) {
    while (container.firstChild) container.removeChild(container.firstChild);
    const title = document.createElement("h3");
    title.textContent = "Post-hoc operator hardware inspections";
    container.appendChild(title);
    if (!data || data.dispatch_allowed !== false || data.admission_allowed !== false ||
        !Array.isArray(data.receipts)) {
      const invalid = document.createElement("p");
      invalid.textContent = "Inspection records unavailable or inconsistent.";
      container.appendChild(invalid);
      return false;
    }
    const entries = data.receipts.slice(0, 20);
    if (!entries.length) {
      const empty = document.createElement("p");
      empty.textContent = "No later hardware inspections attached to this trace.";
      container.appendChild(empty);
      return true;
    }
    const explanation = document.createElement("p");
    explanation.textContent = "These inspections were recorded later by an operator. They do NOT prove hardware state at original execution time.";
    container.appendChild(explanation);
    const list = document.createElement("ul");
    for (const row of entries) {
      if (!row || row.kind !== "POST_HOC_OPERATOR_INSPECTION" ||
          row.historical_decision_proof !== false ||
          row.admission_allowed !== false || row.dispatch_allowed !== false ||
          !SNAPSHOT.test(row.snapshot || "") ||
          !DIGEST.test(row.evidence_digest_sha256 || "")) {
        continue;
      }
      const item = document.createElement("li");
      const stamp = document.createElement("code");
      stamp.textContent = row.snapshot;
      const digest = document.createElement("code");
      digest.textContent = row.evidence_digest_sha256;
      const when = document.createElement("span");
      when.textContent = " · inspected at " + (typeof row.recorded_at_ms === "number" ?
        new Date(row.recorded_at_ms).toLocaleString() : "time unavailable") + " · ";
      item.appendChild(stamp);
      item.appendChild(when);
      item.appendChild(digest);
      list.appendChild(item);
    }
    container.appendChild(list);
    return true;
  }
  async function loadInspections(correlationId, parent, isCurrent) {
    if (!parent || !global.document || typeof correlationId !== "string" ||
        !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(correlationId)) return;
    const section = document.createElement("section");
    section.className = "trace-hardware-evidence";
    section.textContent = "Loading read-only inspection receipts…";
    parent.appendChild(section);
    try {
      const response = await fetch(
        "/api/fleet/trace-inspections/" + encodeURIComponent(correlationId),
        {method: "GET", cache: "no-store", credentials: "same-origin"}
      );
      if (isCurrent && !isCurrent()) return;
      if (!response.ok) throw new Error("unavailable");
      renderInspections(await response.json(), section);
    } catch (ignored) {
      if (isCurrent && !isCurrent()) return;
      section.textContent = "Post-hoc hardware inspections unavailable; existing trace history is unchanged.";
    }
  }

  global.FleetTraceEvidence = {evidenceFromEvent, extract, render, renderInspections, loadInspections};
})(typeof window === "undefined" ? globalThis : window);
