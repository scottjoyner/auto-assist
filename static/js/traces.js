/* AssistX authenticated trace investigation. GET only, no operational authority. */
(function () {
  "use strict";
  var PAGE_SIZE = 50;
  var initialOutcome = new URL(window.location.href).searchParams.get("outcome");
  var permittedOutcomes = ["all", "failed", "completed", "open"];
  var state = {
    offset: 0, search: "", outcome: permittedOutcomes.indexOf(initialOutcome) >= 0 ? initialOutcome : "all",
    total: 0, rows: [],
    selected: null, listRequest: 0, detailRequest: 0,
    deepLink: new URL(window.location.href).searchParams.get("trace") || null
  };

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function when(value) {
    if (value === null || value === undefined) return "Time unknown";
    var date = new Date(Number(value));
    return Number.isFinite(date.getTime()) ? date.toLocaleString() : "Time unknown";
  }
  function duration(value) {
    if (!Number.isFinite(Number(value)) || value == null || Number(value) < 0) return "—";
    var ms = Number(value);
    return ms < 1000 ? Math.round(ms) + "ms" : ms < 60000 ? (ms / 1000).toFixed(2) + "s" : (ms / 60000).toFixed(1) + "m";
  }
  function status(message, error) {
    $("trace-status").textContent = message;
    $("trace-status").classList.toggle("is-error", !!error);
  }
  function setSelectedTools(cid) {
    $("trace-copy").disabled = !cid;
    var link = $("trace-permalink");
    var url = new URL(window.location.href);
    if (cid) url.searchParams.set("trace", cid);
    else url.searchParams.delete("trace");
    if (state.outcome !== "all") url.searchParams.set("outcome", state.outcome);
    else url.searchParams.delete("outcome");
    link.href = url.pathname + url.search;
    link.setAttribute("aria-disabled", cid ? "false" : "true");
    link.tabIndex = cid ? 0 : -1;
    $("trace-detail-caption").textContent = cid ? "Recorded context and event sequence · payloads collapsed by default" : "Select a trace to inspect its recorded steps";
  }
  function persistSelection(cid) {
    var url = new URL(window.location.href);
    if (cid) url.searchParams.set("trace", cid);
    else url.searchParams.delete("trace");
    if (state.outcome !== "all") url.searchParams.set("outcome", state.outcome);
    else url.searchParams.delete("outcome");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
    setSelectedTools(cid);
  }
  function request(url) {
    return fetch(url, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } })
      .then(function (response) {
        if (response.status === 401 || response.status === 403) throw new Error("AUTH");
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      });
  }
  function errorLabel(err) {
    if (err && err.message === "AUTH") return "Authentication required or expired.";
    if (err && err.message === "SERVER_FILTER_UNAVAILABLE") {
      return "Global outcome filtering is not yet available on this server version.";
    }
    return "The trace service is temporarily unavailable.";
  }
  function placeholder(title, message) {
    return '<div class="trace-placeholder"><span aria-hidden="true">⌁</span><h3>' + esc(title) +
      '</h3><p>' + esc(message) + '</p></div>';
  }
  function paintMetrics() {
    var failed = state.rows.filter(function (t) { return t.outcome === "failed"; }).length;
    $("trace-total-metric").textContent = Number(state.total).toLocaleString();
    $("trace-loaded-metric").textContent = String(state.rows.length);
    $("trace-failed-metric").textContent = String(failed);
    $("trace-prev").disabled = state.offset <= 0;
    $("trace-next").disabled = state.offset + state.rows.length >= state.total;
    var first = state.total && state.rows.length ? state.offset + 1 : 0;
    var last = Math.min(state.offset + state.rows.length, state.total);
    $("trace-range").textContent = first ? first + "–" + last : "No results";
    $("trace-total").textContent = "of " + Number(state.total).toLocaleString();
  }
  function paintList() {
    // The server applies global outcome filtering before pagination.
    var shown = state.rows;
    if (!shown.length) {
      $("trace-list").innerHTML = '<p class="trace-empty">' +
        (state.outcome === "all" ? "No traces match this correlation-ID search." :
          "No " + esc(state.outcome) + " traces match across the indexed history. Try All outcomes.") + "</p>";
      return;
    }
    $("trace-list").innerHTML = shown.map(function (t) {
      var cid = String(t.correlation_id || "");
      var outcome = ["failed", "completed", "open"].indexOf(t.outcome) >= 0 ? t.outcome : "open";
      return '<button type="button" class="trace-row' + (cid === state.selected ? ' selected' : '') +
        '" aria-pressed="' + (cid === state.selected ? "true" : "false") +
        '" data-cid="' + esc(cid) + '" title="' + esc(cid) + '">' +
        '<span class="trace-row-top"><span class="trace-row-id">' + esc(cid) + '</span>' +
        '<span class="trace-outcome ' + outcome + '">' + esc(outcome) + '</span></span>' +
        '<span class="trace-row-meta"><span>' + esc(t.events) + ' events</span>' +
        '<span>' + esc(duration(t.duration_ms)) + '</span>' +
        '<span>' + esc(when(t.last_ts_ms)) + '</span></span></button>';
    }).join("");
  }
  function clearSelection(message) {
    state.selected = null;
    currentTrace = null;
    state.detailRequest++;
    persistSelection(null);
    $("trace-detail").innerHTML = placeholder("No trace selected", message || "Choose a result from the index to view its timeline.");
  }
  function loadIndex() {
    var sequence = ++state.listRequest;
    $("trace-refresh").disabled = true;
    status("Loading authenticated router trace index…", false);
    $("trace-list").innerHTML = '<p class="trace-empty">Loading results…</p>';
    var url = "/api/traces?limit=" + PAGE_SIZE + "&offset=" + state.offset;
    if (state.search) url += "&search=" + encodeURIComponent(state.search);
    if (state.outcome !== "all") url += "&outcome=" + encodeURIComponent(state.outcome);
    request(url).then(function (data) {
      if ((data.outcome || "all") !== state.outcome) {
        throw new Error("SERVER_FILTER_UNAVAILABLE");
      }
      if (sequence !== state.listRequest) return;
      state.total = Number(data.total) || 0;
      state.rows = Array.isArray(data.traces) ? data.traces : [];
      paintMetrics();
      paintList();
      status("Loaded " + state.rows.length + " of " + state.total +
        " matching " + (state.outcome === "all" ? "all-outcome" : state.outcome) +
        " trace groups · " + when(Date.now()), false);
      var desired = state.deepLink;
      state.deepLink = null;
      if (desired) select(desired);
      else if (!state.selected && state.rows.length) select(String(state.rows[0].correlation_id));
      else if (!state.selected) $("trace-detail").innerHTML =
        placeholder("No matching traces", "Try a different correlation ID, or refresh the index.");
    }).catch(function (err) {
      if (sequence !== state.listRequest) return;
      state.rows = [];
      state.total = 0;
      paintMetrics();
      // A failed read means the count is unknown, never a verified zero.
      ["trace-total-metric", "trace-loaded-metric", "trace-failed-metric"].forEach(function (id) {
        $(id).textContent = "—";
      });
      $("trace-range").textContent = "—";
      $("trace-total").textContent = "";
      $("trace-list").innerHTML = '<div class="trace-empty">' + esc(errorLabel(err)) +
        '<br><button type="button" class="trace-retry" id="trace-retry">Try again</button></div>';
      status(errorLabel(err) + " No data was changed.", true);
      if (!state.selected) $("trace-detail").innerHTML = placeholder("Index unavailable", "The selected trace can still be opened from a direct link when access returns.");
    }).finally(function () {
      if (sequence === state.listRequest) $("trace-refresh").disabled = false;
    });
  }
  function payloadData(event) {
    var ignored = ["event_type", "source", "ts", "ts_ms", "correlation_id", "event_id", "payload_json", "created_at", "created_at_ts"];
    var data = {};
    if (event.payload_json != null) {
      try {
        var inner = JSON.parse(event.payload_json);
        data = inner && typeof inner === "object" && !Array.isArray(inner) ? inner : { value: inner };
      } catch (_) { data = { note: "Payload was not valid JSON; original content withheld." }; }
    }
    Object.keys(event).forEach(function (key) {
      if (ignored.indexOf(key) < 0) data[key] = event[key];
    });
    return data;
  }
  var currentTrace = null;
  var contextFields = {
    source: "Recorded source",
    task_id: "Task IDs",
    dispatch_id: "Dispatch IDs",
    route_id: "Route IDs",
    assignment_id: "Assignment IDs"
  };
  function contextPanel(trace, filter) {
    var ctx = trace.context;
    if (!ctx || ctx.schema !== "trace-context-v1" || !ctx.fields) {
      return '<section class="trace-context" aria-label="Recorded context">' +
        '<h3>Recorded context</h3><p class="trace-context-note">Context metadata not available for this trace. No node or agent identity is inferred.</p></section>';
    }
    var sections = Object.keys(contextFields).map(function (field) {
      var entries = Array.isArray(ctx.fields[field]) ? ctx.fields[field] : [];
      var chips = entries.map(function (entry) {
        if (!entry || typeof entry.value !== "string" || entry.provenance !== "trace_event_property") return "";
        var selected = filter && filter.field === field && filter.value === entry.value;
        return '<button type="button" class="trace-context-chip' + (selected ? ' selected' : '') +
          '" aria-pressed="' + (selected ? 'true' : 'false') + '" data-context-field="' + esc(field) +
          '" data-context-value="' + esc(entry.value) + '" title="Show events containing this recorded value">' +
          '<span>' + esc(entry.value) + '</span><small>' + esc(entry.events) + ' event(s)</small></button>';
      }).join("");
      return '<div class="trace-context-group"><h4>' + esc(contextFields[field]) + '</h4>' +
        (chips || '<span class="trace-context-unknown">Not recorded</span>') +
        (Array.isArray(ctx.truncated) && ctx.truncated.indexOf(field) >= 0
          ? '<span class="trace-context-unknown">More values omitted from this view</span>' : '') + '</div>';
    }).join("");
    return '<section class="trace-context" aria-label="Recorded context">' +
      '<div class="trace-context-heading"><div><h3 tabindex="-1">Recorded context</h3>' +
      '<p class="trace-context-note">From event properties, not independently verified identities. Source labels are not confirmed agents or machines.</p></div>' +
      (filter ? '<button type="button" class="trace-clear-context">All trace events</button>' : '') + '</div>' +
      '<div class="trace-context-groups">' + sections + '</div>' +
      '<p class="trace-context-foot">Node/agent identity: not established by this trace index. No task or fleet execution actions are available here.</p></section>';
  }
  function renderDetail(trace, filter) {
    currentTrace = trace;
    var events = Array.isArray(trace.events) ? trace.events : [];
    var first = events.length && events[0].ts_ms != null ? Number(events[0].ts_ms) : null;
    var cid = String(trace.correlation_id || state.selected || "");
    var head = '<div class="trace-detail-summary"><span class="trace-eyebrow">Correlation ID</span>' +
      '<span class="trace-id">' + esc(cid) + '</span>' +
      '<div class="trace-detail-stats"><span>State · ' + esc(trace.current_state || "Unknown") + '</span>' +
      '<span>' + events.length + ' events</span><span>Started · ' + esc(when(first)) + '</span></div></div>';
    var visible = events.map(function (e, i) { return { event: e, index: i }; })
      .filter(function (item) {
        return !filter || item.event[filter.field] === filter.value;
      });
    var context = contextPanel(trace, filter);
    if (!events.length) {
      $("trace-detail").innerHTML = head + context +
        placeholder("No timeline events", "This trace has no event records available.");
      return;
    }
    // Only events in this already-loaded trace are filtered. No extra API call.
    // Payloads remain in memory until an explicit event disclosure opens.
    $("trace-detail").innerHTML = head + context +
      '<div class="trace-timeline-header">' +
      '<h3>' + (filter ? "Matching events" : "All recorded events") + '</h3>' +
      '<span>' + visible.length + ' of ' + events.length + ' events</span></div>' +
      (visible.length ? '<div class="trace-timeline">' + visible.map(function (item) {
      var e = item.event;
      var i = item.index;
      var failed = String(e.event_type || "").endsWith(".failed");
      var elapsed = Number.isFinite(Number(e.ts_ms)) && Number.isFinite(first) ? " · +" + duration(Math.max(0, Number(e.ts_ms) - first)) : "";
      return '<article class="trace-event' + (failed ? ' failed' : '') + '">' +
        '<span class="trace-event-type">' + esc(e.event_type || "event") + '</span>' +
        '<div class="trace-event-meta">' + esc(e.source || "Unknown source") + " · " +
        esc(when(e.ts_ms)) + esc(elapsed) + '</div>' +
        '<details data-event-index="' + i + '"><summary>Show event fields (may contain operational data)</summary><pre></pre></details>' +
        '</article>';
    }).join("") + "</div>" :
      '<p class="trace-empty">No loaded events match this recorded value.</p>');
    $("trace-detail").querySelectorAll("details[data-event-index]").forEach(function (node) {
      node.addEventListener("toggle", function () {
        if (!node.open) {
          node.querySelector("pre").textContent = "";
          return;
        }
        var event = events[Number(node.getAttribute("data-event-index"))] || {};
        var text;
        try { text = JSON.stringify(payloadData(event), null, 2); } catch (_) { text = "Fields cannot be formatted."; }
        node.querySelector("pre").textContent = text && text !== "{}" ? text.slice(0, 65536) +
          (text.length > 65536 ? "\n[Display limited to 64 KiB; source unchanged]" : "") : "No additional event fields.";
      });
    });
  }
  $("trace-detail").addEventListener("click", function (event) {
    if (!currentTrace) return;
    var reset = event.target.closest("button.trace-clear-context");
    if (reset) {
      renderDetail(currentTrace, null);
      var heading = $("trace-detail").querySelector(".trace-context h3");
      if (heading && typeof heading.focus === "function") heading.focus();
      return;
    }
    var chip = event.target.closest("button.trace-context-chip");
    if (!chip) return;
    var field = chip.getAttribute("data-context-field");
    var value = chip.getAttribute("data-context-value");
    if (!Object.prototype.hasOwnProperty.call(contextFields, field)) return;
    // Only permit a recorded property from the server's allowlisted context.
    var ctx = currentTrace.context;
    var entries = ctx && ctx.fields && Array.isArray(ctx.fields[field]) ? ctx.fields[field] : [];
    if (!entries.some(function (entry) {
      return entry.value === value && entry.provenance === "trace_event_property";
    })) return;
    renderDetail(currentTrace, { field: field, value: value });
    var active = $("trace-detail").querySelector("button.trace-context-chip.selected");
    if (active && typeof active.focus === "function") active.focus();
  });
  function select(cid) {
    if (!cid) return;
    state.selected = cid;
    currentTrace = null;
    var sequence = ++state.detailRequest;
    persistSelection(cid);
    paintList();
    $("trace-detail").innerHTML = placeholder("Loading timeline", "Retrieving selected trace events…");
    request("/api/traces/" + encodeURIComponent(cid)).then(function (detail) {
      if (sequence !== state.detailRequest) return;
      renderDetail(detail);
    }).catch(function (err) {
      if (sequence !== state.detailRequest) return;
      $("trace-detail").innerHTML = placeholder("Timeline unavailable", errorLabel(err) + " Select another trace or refresh to try again.");
      status("Could not load selected timeline: " + errorLabel(err), true);
    });
  }
  $("trace-list").addEventListener("click", function (event) {
    var row = event.target.closest("button.trace-row");
    if (row) select(row.dataset.cid);
    if (event.target.id === "trace-retry") loadIndex();
  });
  $("trace-prev").addEventListener("click", function () {
    state.offset = Math.max(0, state.offset - PAGE_SIZE);
    clearSelection();
    loadIndex();
  });
  $("trace-next").addEventListener("click", function () {
    if (state.offset + PAGE_SIZE >= state.total) return;
    state.offset += PAGE_SIZE;
    clearSelection();
    loadIndex();
  });
  $("trace-outcome").addEventListener("change", function (e) {
    state.outcome = permittedOutcomes.indexOf(e.target.value) >= 0 ? e.target.value : "all";
    state.offset = 0;
    state.deepLink = null;
    clearSelection();
    loadIndex();
  });
  var searchTimer;
  $("trace-search").addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      state.search = $("trace-search").value.trim();
      state.offset = 0;
      state.deepLink = null;
      clearSelection();
      loadIndex();
    }, 260);
  });
  $("trace-search-form").addEventListener("submit", function (e) {
    e.preventDefault();
    clearTimeout(searchTimer);
    state.search = $("trace-search").value.trim();
    state.offset = 0;
    state.deepLink = null;
    clearSelection();
    loadIndex();
  });
  $("trace-clear").addEventListener("click", function () {
    clearTimeout(searchTimer);
    $("trace-search").value = "";
    state.search = "";
    state.offset = 0;
    clearSelection();
    loadIndex();
    $("trace-search").focus();
  });
  $("trace-refresh").addEventListener("click", loadIndex);
  $("trace-copy").addEventListener("click", function () {
    if (!state.selected) return;
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      status("Clipboard unavailable. Select the correlation ID in the detail panel.", true);
      return;
    }
    navigator.clipboard.writeText(state.selected).then(function () {
      status("Correlation ID copied; no event payload copied.", false);
    }).catch(function () { status("Could not access clipboard. No data was changed.", true); });
  });
  window.addEventListener("popstate", function () {
    var id = new URL(window.location.href).searchParams.get("trace");
    if (id) select(id);
    else clearSelection();
  });
  $("trace-outcome").value = state.outcome;
  setSelectedTools(null);
  loadIndex();
})();
