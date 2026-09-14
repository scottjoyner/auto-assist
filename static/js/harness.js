/* Harness evolution traceability view — polls /api/harness/evolution. */
(function () {
  "use strict";

  var filterEl = document.getElementById("evo-filter");
  var filterText = "";
  var lastSnapshot = null;

  function esc(value) {
    var div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  function stageClass(status) {
    var s = String(status || "").toUpperCase();
    if (s === "DONE" || s === "COMPLETED" || s === "PASS") return "done";
    if (s === "RUNNING" || s === "CLAIMED" || s === "READY") return "running";
    if (s === "FAILED" || s === "ERROR") return "failed";
    return "";
  }

  function fmtTs(ts) {
    if (!ts) return "—";
    try {
      return new Date(ts).toLocaleTimeString();
    } catch (e) {
      return "—";
    }
  }

  function renderChains(chains) {
    var host = document.getElementById("chains");
    document.getElementById("chains-count").textContent = String(chains.length);
    if (!chains.length) {
      host.innerHTML = '<div class="empty">No evolution chains yet — chains appear when a benchmark run starts a reflect → train → deploy → rescore cycle.</div>';
      return;
    }
    var order = ["reflect", "train", "deploy", "rescore"];
    var html = chains.map(function (chain) {
      var stageHtml = order.map(function (stage, i) {
        var entries = chain.stages.filter(function (s) { return s.stage === stage; });
        var latest = entries.length ? entries[entries.length - 1] : null;
        var cls = latest ? stageClass(latest.status) : "";
        var score = latest && latest.score != null
          ? ' <span class="score">' + Number(latest.score).toFixed(3) + "</span>"
          : "";
        var label = latest ? esc(latest.status) : "pending";
        return (i > 0 ? '<span class="arrow">→</span> ' : "")
          + '<span class="stage ' + cls + '"><span class="dot"></span>' + stage
          + score + " <span>(" + label + ")</span></span>";
      }).join("");
      var covered = chain.stages.filter(function (s) { return s.stage !== "rescore"; }).length;
      return '<div class="chain" data-search="' + esc(
        [chain.chain_id, chain.suite_id, chain.endpoint, chain.model_key].join(" ")
      ) + '">'
        + '<div class="chain-head">'
        + '<span class="chain-id">' + esc(chain.chain_id) + "</span>"
        + '<span class="chain-meta">suite <b>' + esc(chain.suite_id || "—") + "</b></span>"
        + '<span class="chain-meta">endpoint <b>' + esc(chain.endpoint || "—") + "</b></span>"
        + '<span class="chain-meta">model <b>' + esc(chain.model_key || "—") + "</b></span>"
        + '<span class="chain-meta">score <b>' + (chain.score != null ? Number(chain.score).toFixed(3) : "—") + "</b></span>"
        + '<span class="chain-meta">' + fmtTs(chain.updated_at_ts) + "</span>"
        + "</div>"
        + '<div class="stages">' + stageHtml + "</div>"
        + "</div>";
    }).join("");
    host.innerHTML = html;
    applyFilter();
  }

  function renderLive(tasks) {
    var host = document.getElementById("live");
    document.getElementById("live-count").textContent = String(tasks.length);
    if (!tasks.length) {
      host.innerHTML = '<div class="empty">No live harness tasks. Reflect/train/deploy/rescore tasks appear here as they are claimed and answered.</div>';
      return;
    }
    var rows = tasks.slice(0, 40).map(function (t) {
      return "<tr>"
        + '<td class="kind">' + esc(t.kind) + "</td>"
        + '<td class="objective">' + esc(t.objective) + "</td>"
        + '<td><span class="status ' + esc(t.status) + '">' + esc(t.status) + "</span></td>"
        + '<td class="chain-meta">' + esc(t.endpoint || t.target || "—") + "</td>"
        + '<td class="resp" title="' + esc(t.response) + '">' + esc(t.response || "—") + "</td>"
        + '<td class="chain-meta">' + fmtTs(t.updated_at_ts) + "</td>"
        + "</tr>";
    }).join("");
    host.innerHTML = '<table class="evo"><thead><tr>'
      + "<th>kind</th><th>objective</th><th>status</th><th>endpoint</th><th>live response</th><th>updated</th>"
      + "</tr></thead><tbody>" + rows + "</tbody></table>";
  }

  function renderMistakes(mistakes) {
    var host = document.getElementById("mistakes");
    document.getElementById("mistakes-count").textContent = String(mistakes.length);
    if (!mistakes.length) {
      host.innerHTML = '<div class="empty">No recorded mistakes. Fail sets from benchmark runs and failed harness tasks surface here with expected vs actual.</div>';
      return;
    }
    host.innerHTML = mistakes.slice(0, 60).map(function (m) {
      return '<div class="mistake">'
        + '<div class="mistake-head">'
        + '<span class="mistake-task">' + esc(m.task_id || "—") + "</span>"
        + '<span class="chain-meta">' + esc(m.kind) + "</span>"
        + (m.chain_id ? '<span class="mistake-chain">' + esc(m.chain_id) + "</span>" : "")
        + '<span class="chain-meta">' + esc(m.suite_id || "") + "</span>"
        + '<span class="chain-meta">' + esc(m.status) + "</span>"
        + "</div>"
        + '<div class="diff">'
        + '<div><div class="diff-label">expected</div><div class="expected">' + esc(m.expected || "—") + "</div></div>"
        + '<div><div class="diff-label">actual</div><div class="actual">' + esc(m.actual || "—") + "</div></div>"
        + "</div>"
        + "</div>";
    }).join("");
  }

  function applyFilter() {
    var text = filterText.trim().toLowerCase();
    document.querySelectorAll(".chain").forEach(function (el) {
      var haystack = el.getAttribute("data-search") || "";
      el.style.display = !text || haystack.toLowerCase().indexOf(text) !== -1 ? "" : "none";
    });
  }

  function render(snapshot) {
    lastSnapshot = snapshot;
    renderChains(snapshot.chains || []);
    renderLive(snapshot.live_tasks || []);
    renderMistakes(snapshot.mistakes || []);
    document.getElementById("snapshot-meta").textContent =
      snapshot.chain_count + " chain(s) · " + snapshot.task_count + " live task(s)";
  }

  function refresh() {
    fetch("/api/harness/evolution", { headers: { "Accept": "application/json" } })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        document.getElementById("refresh-note").textContent =
          "live · " + new Date().toLocaleTimeString();
        if (data) render(data);
      })
      .catch(function () {
        document.getElementById("refresh-note").textContent = "reconnecting…";
      });
  }

  filterEl.addEventListener("input", function () {
    filterText = filterEl.value;
    applyFilter();
  });

  refresh();
  setInterval(refresh, 5000);
})();
