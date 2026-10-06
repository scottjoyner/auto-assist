// Trace history viewer — list historical router traces and show their event timeline.
//
// Reads GET /api/traces (new) for the index and the pre-existing
// GET /api/traces/{correlation_id} for the detail. No state is held server-side.
(function () {
  "use strict";

  var LIMIT = 50;
  var state = { offset: 0, search: "", selected: null, total: 0 };

  var listEl = document.getElementById("trace-list");
  var detailEl = document.getElementById("trace-detail");
  var summaryEl = document.getElementById("trace-summary");
  var rangeEl = document.getElementById("trace-range");
  var totalEl = document.getElementById("trace-total");
  var searchEl = document.getElementById("trace-search");
  var prevEl = document.getElementById("trace-prev");
  var nextEl = document.getElementById("trace-next");

  function fmtDuration(ms) {
    if (ms === null || ms === undefined) return "—";
    if (ms < 1000) return ms + "ms";
    if (ms < 60000) return (ms / 1000).toFixed(2) + "s";
    return (ms / 60000).toFixed(1) + "m";
  }

  function fmtWhen(ms) {
    if (!ms) return "—";
    var d = new Date(ms);
    if (isNaN(d.getTime())) return "—";
    return d.toLocaleString();
  }

  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function shortCid(cid) {
    return cid.length > 20 ? cid.slice(0, 10) + "…" + cid.slice(-7) : cid;
  }

  function request(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function renderSummary(data) {
    var traces = data.traces || [];
    var counts = { failed: 0, completed: 0, open: 0 };
    traces.forEach(function (t) {
      if (counts[t.outcome] !== undefined) counts[t.outcome] += 1;
    });
    summaryEl.innerHTML =
      "<span><b>" + esc(data.total) + "</b> traces</span>" +
      "<span class='badge failed'>" + counts.failed + " failed</span>" +
      "<span class='badge completed'>" + counts.completed + " completed</span>" +
      "<span class='badge open'>" + counts.open + " open</span>";
  }

  function renderList(data) {
    state.total = data.total;
    var traces = data.traces || [];
    if (!traces.length) {
      listEl.innerHTML = "<div class='empty'>No traces match.</div>";
    } else {
      listEl.innerHTML = traces
        .map(function (t) {
          return (
            '<div class="trace-row' + (state.selected === t.correlation_id ? " selected" : "") +
            '" data-cid="' + esc(t.correlation_id) + '">' +
            '<div class="cid">' + esc(shortCid(t.correlation_id)) + "</div>" +
            '<div class="meta">' +
            '<span class="badge ' + esc(t.outcome) + '">' + esc(t.outcome) + "</span>" +
            "<span>" + esc(t.events) + " ev</span>" +
            "<span>" + esc(fmtDuration(t.duration_ms)) + "</span>" +
            "<span>" + esc(fmtWhen(t.last_ts_ms)) + "</span>" +
            "</div></div>"
          );
        })
        .join("");
    }

    var from = data.total === 0 ? 0 : data.offset + 1;
    var to = Math.min(data.offset + traces.length, data.total);
    rangeEl.textContent = from === 0 ? "—" : from + "–" + to;
    totalEl.textContent = "of " + data.total;
    prevEl.disabled = data.offset <= 0;
    nextEl.disabled = to >= data.total;
  }

  function load() {
    var url = "/api/traces?limit=" + LIMIT + "&offset=" + state.offset;
    if (state.search) url += "&search=" + encodeURIComponent(state.search);
    request(url)
      .then(function (data) {
        renderSummary(data);
        renderList(data);
        if (!state.selected && data.traces && data.traces.length) {
          select(data.traces[0].correlation_id);
        }
      })
      .catch(function (err) {
        listEl.innerHTML = "<div class='empty'>Could not load traces: " + esc(err.message) + "</div>";
      });
  }

  function renderDetail(trace) {
    var events = trace.events || [];
    var head =
      "<div class='summary-row'>" +
      "<span>state <b>" + esc(trace.current_state || "—") + "</b></span>" +
      "<span><b>" + esc(events.length) + "</b> events</span>" +
      "<span>" + esc(fmtWhen(events.length ? events[0].ts_ms : null)) + "</span>" +
      "</div>" +
      "<div style='font-size:11px;opacity:0.6;margin-bottom:12px'>" + esc(trace.correlation_id) + "</div>";

    var body = events
      .map(function (e) {
        var failed = String(e.event_type || "").endsWith(".failed");
        // The event body arrives as payload_json (a JSON *string*); rendering it
        // verbatim would show a nested escaped blob. Parse it and fold in any
        // other non-envelope fields.
        var META = [
          "event_type", "source", "ts", "ts_ms",
          "correlation_id", "event_id", "payload_json",
          "created_at", "created_at_ts",
        ];
        var payload = {};
        if (e.payload_json) {
          try {
            var parsed = JSON.parse(e.payload_json);
            if (parsed && typeof parsed === "object") payload = parsed;
            else payload = { value: parsed };
          } catch (err) {
            payload = { payload_json: e.payload_json };
          }
        }
        Object.keys(e).forEach(function (k) {
          if (META.indexOf(k) === -1) payload[k] = e[k];
        });
        var detail = "";
        try {
          var text = JSON.stringify(payload, null, 2);
          if (text !== "{}") detail = "<pre>" + esc(text) + "</pre>";
        } catch (err) {
          detail = "";
        }
        return (
          '<div class="event' + (failed ? " failed" : "") + '">' +
          '<div class="etype">' + esc(e.event_type || "event") + "</div>" +
          '<div class="emeta">' + esc(e.source || "?") + " · " + esc(fmtWhen(e.ts_ms)) +
          (e.ts_ms && events[0] && events[0].ts_ms ? " · +" + (e.ts_ms - events[0].ts_ms) + "ms" : "") +
          "</div>" + detail + "</div>"
        );
      })
      .join("");

    detailEl.innerHTML = head + '<div class="timeline">' + body + "</div>";
  }

  function select(cid) {
    state.selected = cid;
    Array.prototype.forEach.call(listEl.querySelectorAll(".trace-row"), function (row) {
      row.classList.toggle("selected", row.getAttribute("data-cid") === cid);
    });
    detailEl.innerHTML = "<div class='empty'>Loading " + esc(shortCid(cid)) + "…</div>";
    request("/api/traces/" + encodeURIComponent(cid))
      .then(renderDetail)
      .catch(function (err) {
        detailEl.innerHTML = "<div class='empty'>Could not load trace: " + esc(err.message) + "</div>";
      });
  }

  listEl.addEventListener("click", function (ev) {
    var row = ev.target.closest(".trace-row");
    if (row) select(row.getAttribute("data-cid"));
  });

  prevEl.addEventListener("click", function () {
    state.offset = Math.max(0, state.offset - LIMIT);
    load();
  });

  nextEl.addEventListener("click", function () {
    if (state.offset + LIMIT < state.total) {
      state.offset += LIMIT;
      load();
    }
  });

  var searchTimer = null;
  searchEl.addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      state.search = searchEl.value.trim();
      state.offset = 0;
      state.selected = null;
      load();
    }, 250);
  });

  load();
})();