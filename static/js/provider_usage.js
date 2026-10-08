(() => {
  "use strict";
  const byId = id => document.getElementById(id);
  const fmt = n => Number.isFinite(n) ? Math.round(n).toLocaleString() : "—";
  const count = n => Number.isFinite(n)
    ? n >= 1e9 ? (n / 1e9).toFixed(2) + "B"
    : n >= 1e6 ? (n / 1e6).toFixed(2) + "M"
    : n >= 1e3 ? (n / 1e3).toFixed(1) + "K"
    : fmt(n) : "—";

  function cell(row, content) {
    const td = document.createElement("td");
    td.textContent = content == null ? "—" : String(content);
    row.appendChild(td);
  }
  function clearWithMessage(body, colspan, message) {
    body.replaceChildren();
    const row = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = colspan;
    td.textContent = message;
    row.appendChild(td);
    body.appendChild(row);
  }
  function paint(data) {
    const state = byId("usage-state");
    const good = data && data.state === "fresh" && data.complete === true;
    const partial = data && data.state !== "unavailable" && !data.complete;
    state.textContent = !data || data.state === "unavailable"
      ? "Unavailable: no verified local burn snapshot"
      : (partial ? "PARTIAL COVERAGE · " : data.state === "stale" ? "STALE SNAPSHOT · " : "FRESH SNAPSHOT · ")
        + fmt(data.step_events) + " timestamped steps · " + fmt(data.sources) + " local sources";
    byId("usage-24h").textContent = good ? count(data.observed_tokens_24h) : "—";
    byId("usage-forecast").textContent = good ? count(data.forecast_next_24h) : "—";
    byId("usage-freshness").textContent = data?.age_seconds != null
      ? Math.max(0, Math.round(data.age_seconds / 60)) + " min" : "—";
    const providers = byId("usage-providers");
    providers.replaceChildren();
    if (!data?.providers?.length) {
      clearWithMessage(providers, 8, "No provider observations");
    } else {
      data.providers.forEach(p => {
        const tr = document.createElement("tr");
        [p.provider, p.presence, fmt(p.observed_tokens_1h),
         fmt(p.observed_tokens_24h), fmt(p.observed_tokens_7d),
         fmt(p.cache_read_24h), fmt(p.forecast_next_24h),
         fmt(p.planned_tokens_per_day)].forEach(value => cell(tr, value));
        providers.appendChild(tr);
      });
    }
    const models = byId("usage-models");
    models.replaceChildren();
    if (!data?.models?.length) {
      clearWithMessage(models, 6, "No model observations");
    } else {
      data.models.forEach(m => {
        const tr = document.createElement("tr");
        [m.provider, m.model, m.free_name_hint_only ? "Name only" : "Unverified",
         fmt(m.tokens_24h), fmt(m.tokens_7d),
         m.age_minutes == null ? "—" : m.age_minutes + " min"].forEach(v => cell(tr, v));
        models.appendChild(tr);
      });
    }
  }

  async function refresh() {
    const button = byId("usage-refresh");
    button.disabled = true;
    try {
      const response = await fetch("/api/fleet/provider-burn", {
        credentials: "same-origin", cache: "no-store"
      });
      if (!response.ok) throw Error("HTTP " + response.status);
      paint(await response.json());
    } catch (_error) {
      paint({state: "unavailable", providers: [], models: []});
      byId("usage-state").textContent = "Snapshot unavailable or authorization failed";
    } finally {
      button.disabled = false;
    }
  }
  document.addEventListener("DOMContentLoaded", () => {
    byId("usage-refresh")?.addEventListener("click", refresh);
    refresh();
  });
})();
