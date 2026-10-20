/* AssistX authenticated trace investigation. GET only, no operational authority. */
(function () {
  "use strict";
  var PAGE_SIZE = 50;
  var state = {
    offset: 0, search: "", outcome: "all", total: 0, rows: [],
    selected: null, detailLoaded: false, listRequest: 0, detailRequest: 0,
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
    link.href = url.pathname + url.search;
    link.setAttribute("aria-disabled", cid ? "false" : "true");
    link.tabIndex = cid ? 0 : -1;
    $("trace-detail-caption").textContent = cid ? "Router event sequence · payloads collapsed by default" : "Select a trace to inspect its recorded steps";
  }
  function persistSelection(cid) {
    var url = new URL(window.location.href);
    if (cid) url.searchParams.set("trace", cid);
    else url.searchParams.delete("trace");
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
    return err && err.message === "AUTH" ? "Authentication required or expired." : "The trace service is temporarily unavailable.";
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
    var active = document.activeElement;
    var focusedCid = active && active.matches && active.matches("button.trace-row") ? active.dataset.cid : null;
    var shown = state.rows.filter(function (t) {
      return state.outcome === "all" || t.outcome === state.outcome;
    });
    if (!shown.length) {
      $("trace-list").innerHTML = '<p class="trace-empty">' +
        (state.rows.length ? "No " + esc(state.outcome) + " traces on this page. Try All on page or navigate to another page." :
          "No traces match this correlation-ID search.") + "</p>";
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
    // Replacing innerHTML removes the focused button; transfer focus to its new peer.
    if (focusedCid !== null) {
      var replacement = Array.from($("trace-list").querySelectorAll("button.trace-row")).find(function (row) {
        return row.dataset.cid === focusedCid;
      });
      if (replacement) replacement.focus();
    }
  }
  function clearSelection(message) {
    state.selected = null;
    state.detailLoaded = false;
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
    request(url).then(function (data) {
      if (sequence !== state.listRequest) return;
      state.total = Number(data.total) || 0;
      state.rows = Array.isArray(data.traces) ? data.traces : [];
      paintMetrics();
      paintList();
      status("Loaded " + state.rows.length + " trace groups · " + when(Date.now()) + " · ID search only", false);
      var desired = state.deepLink;
      state.deepLink = null;
      if (desired) select(desired);
      else if (!state.selected && state.rows.length) select(String(state.rows[0].correlation_id));
      else if (state.selected && !state.detailLoaded) select(state.selected);
      else if (!state.selected) $("trace-detail").innerHTML =
        placeholder("No matching traces", "Try a different correlation ID, or refresh the index.");
    }).catch(function (err) {
      if (sequence !== state.listRequest) return;
      state.rows = [];
      state.total = 0;
      state.detailRequest++;
      state.detailLoaded = false;
      if (err && err.message === "AUTH") {
        state.selected = null;
        persistSelection(null);
      } else {
        setSelectedTools(null);
      }
      $("trace-detail").innerHTML = placeholder("Index unavailable", errorLabel(err) + " Retry to restore authenticated trace evidence.");
      paintMetrics();
      $("trace-list").innerHTML = '<div class="trace-empty">' + esc(errorLabel(err)) +
        '<br><button type="button" class="trace-retry" id="trace-retry">Try again</button></div>';
      status(errorLabel(err) + " No data was changed.", true);
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
  function renderDetail(trace) {
    var events = Array.isArray(trace.events) ? trace.events : [];
    var first = events.length ? Number(events[0].ts_ms) : null;
    var cid = String(trace.correlation_id || state.selected || "");
    var head = '<div class="trace-detail-summary"><span class="trace-eyebrow">Correlation ID</span>' +
      '<span class="trace-id">' + esc(cid) + '</span>' +
      '<div class="trace-detail-stats"><span>State · ' + esc(trace.current_state || "Unknown") + '</span>' +
      '<span>' + events.length + ' events</span><span>Started · ' + esc(when(first)) + '</span></div></div>';
    if (!events.length) {
      $("trace-detail").innerHTML = head + placeholder("No timeline events", "This trace has no event records available.");
      return;
    }
    // Payload data stays in memory, never inserted in DOM until disclosure opens.
    $("trace-detail").innerHTML = head + '<div class="trace-timeline">' + events.map(function (e, i) {
      var failed = String(e.event_type || "").endsWith(".failed");
      var elapsed = Number.isFinite(Number(e.ts_ms)) && Number.isFinite(first) ? " · +" + duration(Math.max(0, Number(e.ts_ms) - first)) : "";
      return '<article class="trace-event' + (failed ? ' failed' : '') + '">' +
        '<span class="trace-event-type">' + esc(e.event_type || "event") + '</span>' +
        '<div class="trace-event-meta">' + esc(e.source || "Unknown source") + " · " +
        esc(when(e.ts_ms)) + esc(elapsed) + '</div>' +
        '<details data-event-index="' + i + '"><summary>Show event fields (may contain operational data)</summary><pre></pre></details>' +
        '</article>';
    }).join("") + "</div>";
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
  function select(cid) {
    if (!cid) return;
    state.selected = cid;
    state.detailLoaded = false;
    var sequence = ++state.detailRequest;
    persistSelection(cid);
    paintList();
    $("trace-detail").innerHTML = placeholder("Loading timeline", "Retrieving selected trace events…");
    request("/api/traces/" + encodeURIComponent(cid)).then(function (detail) {
      if (sequence !== state.detailRequest) return;
      renderDetail(detail);
      state.detailLoaded = true;
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
    state.outcome = e.target.value;
    paintList();
    status("Outcome filter applies to the " + state.rows.length + " loaded results on this page only.", false);
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
  setSelectedTools(null);
  loadIndex();
})();
