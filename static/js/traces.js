/* AssistX authenticated trace investigation: metadata GET and explicit preview POST only. No operational authority. */
(function () {
  "use strict";
  var PAGE_SIZE = 50;
  var initialOutcome = new URL(window.location.href).searchParams.get("outcome");
  var permittedOutcomes = ["all", "failed", "completed", "open"];
  var state = {
    offset: 0, search: "", outcome: permittedOutcomes.indexOf(initialOutcome) >= 0 ? initialOutcome : "all",
    total: 0, rows: [], selected: null, detailLoaded: false, listRequest: 0, detailRequest: 0,
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
    updateLiveButton();
    var link = $("trace-permalink");
    var url = new URL(window.location.href);
    if (cid) url.searchParams.set("trace", cid);
    else url.searchParams.delete("trace");
    if (state.outcome !== "all") url.searchParams.set("outcome", state.outcome);
    else url.searchParams.delete("outcome");
    link.href = url.pathname + url.search;
    link.setAttribute("aria-disabled", cid ? "false" : "true");
    link.tabIndex = cid ? 0 : -1;
    $("trace-detail-caption").textContent = cid ? "Paged event metadata · event payloads never downloaded until opened" : "Select a trace to inspect its recorded steps";
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
  function request(url, options) {
    var preview = options && options.preview;
    return fetch(url, {
      method: preview ? "POST" : "GET",
      credentials: "same-origin", cache: "no-store",
      headers: preview ? { Accept: "application/json", "Content-Type": "application/json" } : { Accept: "application/json" },
      body: preview ? JSON.stringify({ event_id: options.eventId }) : undefined
    }).then(function (response) {
      if (response.status === 401) throw new Error("AUTH");
      if (response.status === 403) throw new Error("FORBIDDEN");
      if (response.status === 503) throw new Error("PAGING_DISABLED");
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json();
    });
  }
  function errorLabel(err) {
    if (err && err.message === "AUTH") return "Authentication required or expired.";
    if (err && err.message === "FORBIDDEN") return "Access denied by operator authorization policy.";
    if (err && err.message === "PAGING_DISABLED") {
      return "Bounded trace paging is disabled on this server; legacy unbounded detail is intentionally unavailable.";
    }
    if (err && err.message === "INVALID_PAGE") {
      return "Invalid paged trace response; legacy full-detail fallback is prohibited.";
    }
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
    var active = document.activeElement;
    var focusedCid = active && active.matches && active.matches("button.trace-row") ? active.dataset.cid : null;
    // Global filtering is performed on the server before pagination.
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
    // Replacing innerHTML removes the focused button; transfer focus to its new peer.
    if (focusedCid !== null) {
      var replacement = Array.from($("trace-list").querySelectorAll("button.trace-row")).find(function (row) {
        return row.dataset.cid === focusedCid;
      });
      if (replacement) replacement.focus();
    }
  }
  function clearSelection(message) {
    stopLiveFollow(true);
    evidenceRequest++;
    currentEvidence = null;
    state.selected = null;
    currentTrace = null;
    timelineLimit = TIMELINE_BATCH;
    timelineTypeQuery = "";
    activeContextFilter = null;
    timelineCursor = null;
    timelineHasMore = false;
    timelineLoading = false;
    timelineError = "";
    previewRequest++;
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
        stopLiveFollow(true);
        state.selected = null;
        persistSelection(null);
      } else {
        setSelectedTools(null);
      }
      $("trace-detail").innerHTML = placeholder("Index unavailable", errorLabel(err) + " Retry to restore authenticated trace evidence.");
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
    }).finally(function () {
      if (sequence === state.listRequest) $("trace-refresh").disabled = false;
    });
  }
  // Context chips are derived exclusively from the metadata pages actually fetched.
  // Never infer a trace-wide total or use a full-detail/context hydration request.
  function contextFromLoadedPages(events) {
    var fields = {};
    var truncated = [];
    Object.keys(contextFields).forEach(function (field) {
      var counts = new Map();
      events.forEach(function (event) {
        var value = event[field];
        if (typeof value === "string" && value && value.length <= 160 &&
            !/[\x00-\x1f\x7f]/.test(value)) {
          counts.set(value, (counts.get(value) || 0) + 1);
        }
      });
      if (counts.size > 30) truncated.push(field);
      fields[field] = Array.from(counts).slice(0, 30).map(function (pair) {
        return { value: pair[0], events: pair[1], provenance: "trace_event_property" };
      });
    });
    return { schema: "trace-context-v1", fields: fields, truncated: truncated,
      loaded_pages_only: true };
  }
  function validPage(page, cid, preceding, seenCursors) {
    if (!page || page.schema !== "trace-event-page-v1" ||
        page.correlation_id !== cid || page.metadata_only !== true ||
        page.historical_retention_proven !== false || page.source_snapshot_immutable !== false ||
        !Array.isArray(page.events) || page.events.length > TIMELINE_BATCH ||
        page.returned !== page.events.length || typeof page.has_more !== "boolean" ||
        (page.has_more && (typeof page.next_cursor !== "string" ||
          !page.next_cursor || page.next_cursor === timelineCursor ||
          seenCursors.has(page.next_cursor))) ||
        (!page.has_more && page.next_cursor != null)) return false;
    var allowed = ["event_id", "ts_ms", "event_type", "source",
      "task_id", "dispatch_id", "route_id", "assignment_id"];
    var seen = new Set(preceding.map(function (e) { return e.event_id; }));
    var last = preceding.length ? preceding[preceding.length - 1] : null;
    for (var i = 0; i < page.events.length; i++) {
      var event = page.events[i];
      if (!event || typeof event.event_id !== "string" || !event.event_id ||
          !Number.isSafeInteger(event.ts_ms) || event.ts_ms < 0 ||
          seen.has(event.event_id) ||
          Object.keys(event).some(function (key) { return allowed.indexOf(key) < 0; })) return false;
      if (last && (event.ts_ms > last.ts_ms ||
          (event.ts_ms === last.ts_ms && event.event_id >= last.event_id))) return false;
      seen.add(event.event_id);
      last = event;
    }
    return !page.has_more || page.events.length > 0;
  }
  function loadTimelinePage(cid, sequence, cursor) {
    if (timelineLoading) return;
    timelineLoading = true;
    timelineError = "";
    var pageUrl = "/api/traces/" + encodeURIComponent(cid) + "/timeline?limit=" + TIMELINE_BATCH;
    if (cursor) pageUrl += "&cursor=" + encodeURIComponent(cursor);
    if (cursor && currentTrace) renderDetail(currentTrace, activeContextFilter);
    return request(pageUrl).then(function (page) {
      if (sequence !== state.detailRequest || cid !== state.selected) return;
      var preceding = currentTrace && currentTrace._newestFirst ? currentTrace._newestFirst : [];
      if (!validPage(page, cid, preceding, timelineSeenCursors)) throw new Error("INVALID_PAGE");
      if (cursor) timelineSeenCursors.add(cursor);
      var loaded = preceding.concat(page.events).slice(-TIMELINE_RETAIN_MAX);
      timelineCursor = page.next_cursor;
      timelineHasMore = page.has_more;
      timelineLimit = loaded.length;
      var indexRow = state.rows.find(function (row) { return row.correlation_id === cid; });
      var trace = { correlation_id: cid,
        current_state: indexRow ? (indexRow.outcome || "Unknown") : "Unknown",
        _newestFirst: loaded, events: loaded.slice().reverse(),
        context: contextFromLoadedPages(loaded) };
      currentTrace = trace;
      timelineLoading = false;
      state.detailLoaded = true;
      updateLiveButton();
      renderDetail(trace, activeContextFilter);
    }).catch(function (err) {
      if (sequence !== state.detailRequest || cid !== state.selected) return;
      timelineLoading = false;
      if (err && err.message === "AUTH") {
        clearSelection();
        status(errorLabel(err) + " Event metadata cleared.", true);
        return;
      }
      timelineError = errorLabel(err);
      if (currentTrace) renderDetail(currentTrace, activeContextFilter);
      else $("trace-detail").innerHTML = placeholder("Timeline unavailable",
        timelineError + " Select another trace or refresh to try again.");
      status("Could not load bounded timeline: " + timelineError, true);
    });
  }
  var currentTrace = null;
  var TIMELINE_BATCH = 80;
  var TIMELINE_RETAIN_MAX = 800; // Sliding metadata window; refresh returns to newest.
  var LIVE_INTERVAL_MS = 2000;
  var LIVE_MAX_PAGES = 4;
  var liveEnabled = false;
  var liveTimer = null;
  var livePolling = false;
  var liveGeneration = 0;
  var liveGapPossible = false;
  var liveLastPollMs = null;
  var liveLastNew = 0;
  var liveLastPages = 0;
  var livePollError = "";
  var liveWindowTrimmed = false;
  var timelineLimit = TIMELINE_BATCH;
  var timelineTypeQuery = "";
  var activeContextFilter = null;
  var timelineCursor = null;
  var timelineHasMore = false;
  var timelineLoading = false;
  var timelineError = "";
  var timelineSeenCursors = new Set();
  var previewRequest = 0;
  var evidenceRequest = 0;
  var currentEvidence = null;
  function updateLiveButton() {
    var button = $("trace-live");
    if (!button) return;
    var available = !!(state.selected && state.detailLoaded && currentTrace &&
      (!liveWindowTrimmed || liveEnabled));
    button.disabled = !available;
    button.setAttribute("aria-pressed", liveEnabled ? "true" : "false");
    button.textContent = liveEnabled ? "Pause live" : "Follow live";
    button.title = liveWindowTrimmed && !liveEnabled
      ? "Reload this trace before resuming live follow; the 800-event client window was capped."
      : "Poll bounded metadata pages for newly indexed events.";
  }
  function stopLiveFollow(resetHealth) {
    if (liveTimer) clearTimeout(liveTimer);
    liveTimer = null;
    liveEnabled = false;
    livePolling = false;
    liveGeneration++;
    if (resetHealth) {
      liveGapPossible = false;
      liveLastPollMs = null;
      liveLastNew = 0;
      liveLastPages = 0;
      livePollError = "";
      liveWindowTrimmed = false;
    }
    updateLiveButton();
  }
  function scheduleLiveFollow(delay) {
    if (!liveEnabled) return;
    if (liveTimer) clearTimeout(liveTimer);
    liveTimer = setTimeout(function () {
      liveTimer = null;
      pollLiveFollow();
    }, delay == null ? LIVE_INTERVAL_MS : delay);
  }
  function validLivePage(page, cid, seenCursors) {
    if (!page || page.schema !== "trace-event-page-v1" ||
        page.correlation_id !== cid || page.metadata_only !== true ||
        page.historical_retention_proven !== false ||
        page.source_snapshot_immutable !== false ||
        !Array.isArray(page.events) || page.events.length > TIMELINE_BATCH ||
        page.returned !== page.events.length || typeof page.has_more !== "boolean" ||
        (page.has_more && (typeof page.next_cursor !== "string" ||
          !page.next_cursor || seenCursors.has(page.next_cursor))) ||
        (!page.has_more && page.next_cursor != null)) return false;
    var allowed = ["event_id", "ts_ms", "event_type", "source",
      "task_id", "dispatch_id", "route_id", "assignment_id"];
    var ids = new Set();
    var last = null;
    for (var i = 0; i < page.events.length; i++) {
      var event = page.events[i];
      if (!event || typeof event.event_id !== "string" || !event.event_id ||
          !Number.isSafeInteger(event.ts_ms) || event.ts_ms < 0 ||
          ids.has(event.event_id) ||
          Object.keys(event).some(function (key) { return allowed.indexOf(key) < 0; })) return false;
      if (last && (event.ts_ms > last.ts_ms ||
          (event.ts_ms === last.ts_ms && event.event_id >= last.event_id))) return false;
      ids.add(event.event_id);
      last = event;
    }
    return !page.has_more || page.events.length > 0;
  }
  function liveHealthMarkup() {
    if (!liveEnabled && !liveLastPollMs && !liveGapPossible && !livePollError) return "";
    var newest = currentTrace && currentTrace._newestFirst && currentTrace._newestFirst[0];
    var age = newest && Number.isSafeInteger(newest.ts_ms)
      ? Math.max(0, Date.now() - newest.ts_ms) : null;
    var label = liveEnabled ? "Live follow on" : "Live follow paused";
    var className = "trace-live-health";
    if (livePollError) {
      label += " · " + livePollError;
      className += " is-error";
    } else if (liveGapPossible) {
      label += " · possible observation gap";
      className += " is-gap";
    } else if (liveWindowTrimmed) {
      label += " · 800-event client window capped";
      className += " is-gap";
    }
    if (liveLastPollMs) label += " · last poll " + when(liveLastPollMs);
    if (liveLastPages) label += " · " + liveLastPages + " page" + (liveLastPages === 1 ? "" : "s");
    if (liveLastNew) label += " · +" + liveLastNew + " event" + (liveLastNew === 1 ? "" : "s");
    if (age !== null) label += " · newest indexed age " + duration(age);
    return '<span class="' + className + '">' + esc(label) + '</span>';
  }
  function pollLiveFollow() {
    if (!liveEnabled) return;
    if (livePolling || !state.selected || !state.detailLoaded || !currentTrace) {
      scheduleLiveFollow(500);
      return;
    }
    livePolling = true;
    var generation = liveGeneration;
    var sequence = state.detailRequest;
    var cid = state.selected;
    var known = new Set((currentTrace._newestFirst || []).map(function (e) { return e.event_id; }));
    var newEvents = [];
    var newIds = new Set();
    var seenCursors = new Set();
    var cursor = null;
    var pages = 0;
    var reachedKnown = false;

    function onePage() {
      var url = "/api/traces/" + encodeURIComponent(cid) + "/timeline?limit=" + TIMELINE_BATCH;
      if (cursor) url += "&cursor=" + encodeURIComponent(cursor);
      return request(url).then(function (page) {
        if (!liveEnabled || generation !== liveGeneration ||
            sequence !== state.detailRequest || cid !== state.selected) return "STALE";
        if (!validLivePage(page, cid, seenCursors)) throw new Error("INVALID_PAGE");
        pages++;
        if (cursor) seenCursors.add(cursor);
        for (var i = 0; i < page.events.length; i++) {
          var event = page.events[i];
          if (known.has(event.event_id)) {
            reachedKnown = true;
            break;
          }
          if (!newIds.has(event.event_id)) {
            newIds.add(event.event_id);
            newEvents.push(event);
          }
        }
        if (reachedKnown || !page.has_more || !page.next_cursor || pages >= LIVE_MAX_PAGES) return null;
        cursor = page.next_cursor;
        return onePage();
      });
    }

    onePage().then(function (result) {
      if (result === "STALE" || !liveEnabled || generation !== liveGeneration ||
          sequence !== state.detailRequest || cid !== state.selected) return;
      liveLastPollMs = Date.now();
      liveLastPages = pages;
      liveLastNew = newEvents.length;
      livePollError = "";
      liveGapPossible = known.size > 0 && !reachedKnown;

      if (newEvents.length) {
        var merged = [];
        var mergedIds = new Set();
        newEvents.concat(currentTrace._newestFirst || []).forEach(function (event) {
          if (!mergedIds.has(event.event_id)) {
            mergedIds.add(event.event_id);
            merged.push(event);
          }
        });
        if (merged.length > TIMELINE_RETAIN_MAX) {
          merged = merged.slice(0, TIMELINE_RETAIN_MAX);
          liveWindowTrimmed = true;
          timelineHasMore = true;
        }
        currentTrace._newestFirst = merged;
        currentTrace.events = merged.slice().reverse();
        currentTrace.context = contextFromLoadedPages(merged);
      }
      updateLiveButton();
      renderDetail(currentTrace, activeContextFilter);
      status(liveGapPossible
        ? "Live follow reached its bounded read budget without reconnecting to already-loaded history; possible gap."
        : "Live follow checked " + pages + " bounded metadata page" + (pages === 1 ? "" : "s") +
          (newEvents.length ? " and observed " + newEvents.length + " new event(s)." : "; no new events observed."),
        liveGapPossible);
    }).catch(function (err) {
      if (!liveEnabled || generation !== liveGeneration ||
          sequence !== state.detailRequest || cid !== state.selected) return;
      if (err && err.message === "AUTH") {
        stopLiveFollow(true);
        clearSelection();
        status(errorLabel(err) + " Live metadata cleared.", true);
        return;
      }
      livePollError = errorLabel(err);
      stopLiveFollow(false);
      if (currentTrace) renderDetail(currentTrace, activeContextFilter);
      status("Live follow paused: " + livePollError, true);
    }).finally(function () {
      livePolling = false;
      if (liveEnabled) scheduleLiveFollow(LIVE_INTERVAL_MS);
    });
  }
  var contextFields = {
    source: "Recorded source",
    task_id: "Task IDs",
    dispatch_id: "Dispatch IDs",
    route_id: "Route IDs",
    assignment_id: "Assignment IDs"
  };
  function evidenceInspector() {
    return '<div class="trace-evidence"><button type="button" class="trace-inspect-evidence">' +
      'Inspect task / registry evidence (read-only)</button>' +
      '<div class="trace-evidence-results" role="status" aria-live="polite">' +
      'Not inspected. A registry match does not attest who executed this trace.</div></div>';
  }
  function contextPanel(trace, filter) {
    var ctx = trace.context;
    if (!ctx || ctx.schema !== "trace-context-v1" || !ctx.fields) {
      return '<section class="trace-context" aria-label="Recorded context">' +
        '<h3>Recorded context</h3><p class="trace-context-note">Context metadata not available for this trace. No node or agent identity is inferred.</p>' +
        evidenceInspector() + '</section>';
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
      '<p class="trace-context-note">Loaded metadata pages only (not all history). Source labels are not verified agents or machines.</p></div>' +
      (filter ? '<button type="button" class="trace-clear-context">All trace events</button>' : '') + '</div>' +
      '<div class="trace-context-groups">' + sections + '</div>' +
      '<p class="trace-context-foot">Node/agent identity: not established by this trace index. No task or fleet execution actions are available here.</p>' +
      evidenceInspector() + '</section>';
  }
  function renderDetail(trace, filter) {
    currentTrace = trace;
    activeContextFilter = filter || null;
    var events = Array.isArray(trace.events) ? trace.events : [];
    var first = events.length && events[0].ts_ms != null ? Number(events[0].ts_ms) : null;
    var cid = String(trace.correlation_id || state.selected || "");
    var head = '<div class="trace-detail-summary"><span class="trace-eyebrow">Correlation ID</span>' +
      '<span class="trace-id">' + esc(cid) + '</span>' +
      '<div class="trace-detail-stats"><span>Index outcome · ' + esc(trace.current_state || "Unknown") + '</span>' +
      '<span>' + events.length + ' loaded events' + (timelineHasMore ? ' · earlier pages available' : '') +
      '</span><span>Earliest loaded · ' + esc(when(first)) + '</span>' +
      liveHealthMarkup() + '</div></div>';
    var visible = events.map(function (e, i) { return { event: e, index: i }; })
      .filter(function (item) {
        return (!filter || item.event[filter.field] === filter.value) &&
          (!timelineTypeQuery || String(item.event.event_type || "").toLowerCase()
            .indexOf(timelineTypeQuery) >= 0);
      });
    // Bound the timeline DOM, never the source evidence nor claimed history.
    var windowed = visible.slice(-timelineLimit);
    var hasEarlier = !liveWindowTrimmed && (timelineHasMore || windowed.length < visible.length);
    var context = contextPanel(trace, filter);
    if (!events.length) {
      $("trace-detail").innerHTML = head + context +
        placeholder("No timeline events", "This trace has no event records available.");
      return;
    }
    // Only fetched metadata is filtered. Payloads are never included in metadata responses.
    $("trace-detail").innerHTML = head + context +
      '<div class="trace-timeline-header">' +
      '<h3>' + (filter || timelineTypeQuery ? "Matching events" : "Recorded event timeline") + '</h3>' +
      '<span>' + visible.length + ' of ' + events.length + ' loaded events' +
      (timelineHasMore ? ' · older events not loaded' : '') + '</span></div>' +
      '<div class="trace-local-filter"><label for="trace-type-query">Find event types · loaded trace only</label>' +
      '<div class="trace-local-filter-row"><input id="trace-type-query" type="search" ' +
      'maxlength="80" autocomplete="off" spellcheck="false" ' +
      'value="' + esc(timelineTypeQuery) + '">' +
      (timelineTypeQuery ? '<button type="button" class="trace-clear-type">Clear type search</button>' : '') +
      '</div><p>Matches event-type names only, never payloads. Not an all-history query.</p></div>' +
      '<p class="trace-window-status" role="status" aria-live="polite">Showing latest ' +
      windowed.length + ' of ' + visible.length + ' matching loaded events' +
      (timelineHasMore ? '; additional older pages may exist.' : '.') +
      (liveWindowTrimmed ? ' Older loaded metadata was dropped after the 800-event client cap; reselect this trace to browse history.' : '') +
      ' Up to 800 retained client-side; reselecting returns to newest.</p>' +
      (timelineLoading ? '<p role="status">Loading one bounded earlier page…</p>' : '') +
      (timelineError ? '<p class="trace-empty" role="alert">' + esc(timelineError) + '</p>' : '') +
      (hasEarlier && !timelineLoading ? '<button type="button" class="trace-show-earlier">' +
        (timelineHasMore ? 'Load up to 80 earlier events' : 'Show already loaded earlier events') +
        '</button>' : '') +
      (visible.length ? '<div class="trace-timeline">' + windowed.map(function (item) {
      var e = item.event;
      var i = item.index;
      var failed = String(e.event_type || "").endsWith(".failed");
      var elapsed = Number.isFinite(Number(e.ts_ms)) && Number.isFinite(first) ? " · +" + duration(Math.max(0, Number(e.ts_ms) - first)) : "";
      return '<article class="trace-event' + (failed ? ' failed' : '') + '">' +
        '<span class="trace-event-type">' + esc(e.event_type || "event") + '</span>' +
        '<div class="trace-event-meta">' + esc(e.source || "Unknown source") + " · " +
        esc(when(e.ts_ms)) + esc(elapsed) + '</div>' +
        '<details data-event-index="' + i + '"><summary>Request payload preview (up to 4,096 characters; may contain sensitive data)</summary><pre></pre></details>' +
        '</article>';
    }).join("") + "</div>" :
      '<p class="trace-empty">No loaded events match the selected context and event-type search.</p>');
    if (currentEvidence) paintEvidence(currentEvidence);
    $("trace-detail").querySelectorAll("details[data-event-index]").forEach(function (node) {
      var disclosure = 0;
      node.addEventListener("toggle", function () {
        var generation = ++disclosure;
        var target = node.querySelector("pre");
        if (!node.open) { target.textContent = ""; return; }
        var item = events[Number(node.getAttribute("data-event-index"))];
        if (!item || !item.event_id) { target.textContent = "No event identifier available."; return; }
        // A human opening this disclosure is the ONLY trigger for payload access.
        target.textContent = "Requesting an explicit bounded preview…";
        var cid = state.selected;
        var sequence = state.detailRequest;
        request("/api/traces/" + encodeURIComponent(cid) + "/payload-preview",
          { preview: true, eventId: item.event_id }).then(function (preview) {
            if (generation !== disclosure || !node.open ||
                sequence !== state.detailRequest || cid !== state.selected) return;
            if (!preview || preview.schema !== "trace-payload-preview-v1" ||
                preview.correlation_id !== cid || preview.event_id !== item.event_id ||
                typeof preview.payload_preview !== "string" ||
                preview.payload_preview.length > 4096 ||
                typeof preview.truncated !== "boolean" ||
                preview.historical_retention_proven !== false) {
              target.textContent = "Invalid payload preview response; data withheld.";
              return;
            }
            target.textContent = (preview.payload_preview || "No payload recorded.") +
              (preview.truncated ? "\n[Preview truncated to 4,096 characters]" : "");
          }).catch(function (err) {
            if (generation !== disclosure || !node.open ||
                sequence !== state.detailRequest || cid !== state.selected) return;
            if (err && err.message === "AUTH") {
              clearSelection();
              status(errorLabel(err) + " Previews cleared.", true);
              return;
            }
            target.textContent = errorLabel(err) + " Preview not retained.";
          });
      });
    });
  }
  function paintEvidence(evidence) {
    var host = $("trace-detail").querySelector(".trace-evidence-results");
    if (!host) return;
    if (!evidence || evidence.schema !== "trace-task-evidence-v1" ||
        evidence.node_or_agent_verified !== false ||
        evidence.source_authenticated !== false ||
        evidence.graph_write_permitted !== false ||
        evidence.correlation_id !== state.selected ||
        evidence.trust !== "unverified_correspondence_not_execution_attestation" ||
        !Array.isArray(evidence.tasks)) {
      host.textContent = "Evidence schema unavailable. No node or agent attribution established.";
      return;
    }
    if (!evidence.tasks.length) {
      host.textContent = "No corroborated task relationship recorded for this trace.";
      return;
    }
    var descriptions = {
      not_recorded: "No node ID recorded",
      not_registered: "Node ID not found in registry",
      registry_id_match_unverified: "Registry ID matches; identity unverified",
      ambiguous_registry_id: "Ambiguous registry ID: multiple records"
    };
    host.innerHTML = evidence.tasks.slice(0, 12).map(function (t) {
      if (!t || typeof t.task_id !== "string" ||
          t.task_provenance !== "trace_event_relationship_and_property" ||
          t.projection_provenance !== "assignment_projection_unverified" ||
          t.node_or_agent_verified !== false ||
          !Object.prototype.hasOwnProperty.call(descriptions, t.registry_state)) return "";
      var fields = [
        ["Task", t.task_id],
        ["Status (projection)", t.task_status],
        ["Worker (unverified)", t.worker_id],
        ["Node (unverified)", t.node_id]
      ];
      return '<article class="trace-evidence-card"><div class="trace-evidence-label">' +
        esc(descriptions[t.registry_state]) + '</div>' +
        fields.map(function (f) {
          return '<div><span>' + esc(f[0]) + '</span><strong>' +
            esc(typeof f[1] === "string" && f[1] ? f[1].slice(0,128) : "Not recorded") +
            '</strong></div>';
        }).join("") + '</article>';
    }).join("") +
      (evidence.truncated ? '<p class="trace-context-note">Additional task links omitted. This is a bounded view.</p>' : '') +
      '<p class="trace-context-foot">Registry matches are unverified ID correspondence, not execution, node, or agent attestation. No actions or remote links.</p>';
  }
  $("trace-detail").addEventListener("click", function (event) {
    if (!currentTrace) return;
    var inspect = event.target.closest("button.trace-inspect-evidence");
    if (inspect) {
      var cid = state.selected;
      var generation = ++evidenceRequest;
      var results = $("trace-detail").querySelector(".trace-evidence-results");
      if (results) results.textContent = "Checking recorded task and registry IDs…";
      request("/api/traces/" + encodeURIComponent(cid) + "/evidence").then(function (response) {
        if (generation !== evidenceRequest || cid !== state.selected) return;
        currentEvidence = response;
        paintEvidence(response);
      }).catch(function (err) {
        if (generation !== evidenceRequest || cid !== state.selected) return;
        currentEvidence = null;
        var panel = $("trace-detail").querySelector(".trace-evidence-results");
        if (panel) panel.textContent = errorLabel(err) + " No identity was verified.";
      });
      return;
    }
    var earlier = event.target.closest("button.trace-show-earlier");
    if (earlier) {
      if (timelineHasMore) {
        loadTimelinePage(state.selected, state.detailRequest, timelineCursor);
      } else {
        timelineLimit += TIMELINE_BATCH;
        renderDetail(currentTrace, activeContextFilter);
      }
      var next = $("trace-detail").querySelector("button.trace-show-earlier") ||
        $("trace-detail").querySelector("#trace-type-query");
      if (next && typeof next.focus === "function") next.focus();
      return;
    }
    var clearType = event.target.closest("button.trace-clear-type");
    if (clearType) {
      timelineTypeQuery = "";
      timelineLimit = TIMELINE_BATCH;
      renderDetail(currentTrace, activeContextFilter);
      var query = $("trace-detail").querySelector("#trace-type-query");
      if (query && typeof query.focus === "function") query.focus();
      return;
    }
    var reset = event.target.closest("button.trace-clear-context");
    if (reset) {
      timelineLimit = TIMELINE_BATCH;
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
    timelineLimit = TIMELINE_BATCH;
    renderDetail(currentTrace, { field: field, value: value });
    var active = $("trace-detail").querySelector("button.trace-context-chip.selected");
    if (active && typeof active.focus === "function") active.focus();
  });
  $("trace-detail").addEventListener("input", function (event) {
    if (!currentTrace || !event.target || event.target.id !== "trace-type-query") return;
    // No raw payload indexing, no server query, no request authority.
    timelineTypeQuery = String(event.target.value || "").slice(0, 80).toLowerCase();
    timelineLimit = TIMELINE_BATCH;
    var caret = typeof event.target.selectionStart === "number"
      ? event.target.selectionStart : timelineTypeQuery.length;
    renderDetail(currentTrace, activeContextFilter);
    var query = $("trace-detail").querySelector("#trace-type-query");
    if (query && typeof query.focus === "function") {
      query.focus();
      if (typeof query.setSelectionRange === "function") query.setSelectionRange(caret, caret);
    }
  });
  function select(cid) {
    if (!cid) return;
    stopLiveFollow(true);
    evidenceRequest++;
    currentEvidence = null;
    state.selected = cid;
    currentTrace = null;
    timelineLimit = TIMELINE_BATCH;
    timelineTypeQuery = "";
    activeContextFilter = null;
    timelineCursor = null;
    timelineHasMore = false;
    timelineLoading = false;
    timelineError = "";
    timelineSeenCursors = new Set();
    previewRequest++;
    state.detailLoaded = false;
    var sequence = ++state.detailRequest;
    persistSelection(cid);
    paintList();
    $("trace-detail").innerHTML = placeholder("Loading timeline", "Retrieving selected trace events…");
    // Fail closed when experimental paging is off; never call legacy full detail.
    loadTimelinePage(cid, sequence, null);
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
  $("trace-live").addEventListener("click", function () {
    if (!state.selected || !state.detailLoaded || !currentTrace) return;
    if (liveEnabled) {
      stopLiveFollow(false);
      renderDetail(currentTrace, activeContextFilter);
      status("Live follow paused by operator; no background metadata polling is active.", false);
      return;
    }
    if (liveWindowTrimmed) {
      status("Reselect this trace before resuming live follow; the bounded client window was already capped.", true);
      return;
    }
    liveEnabled = true;
    liveGeneration++;
    liveGapPossible = false;
    livePollError = "";
    liveLastNew = 0;
    liveLastPages = 0;
    updateLiveButton();
    renderDetail(currentTrace, activeContextFilter);
    status("Live follow enabled for the selected trace; metadata-only polling is bounded to four pages per poll.", false);
    scheduleLiveFollow(0);
  });
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