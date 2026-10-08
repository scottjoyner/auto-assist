/* Pure, offline AssistX provider burn projection. No fetch, authority or side effects. */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.AssistXProviderBurnModel = api;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";
  var UNITS = ["tokens", "requests", "USD"];
  function stamp(value) {
    if (typeof value === "number") return Number.isFinite(value) && value >= 0 ? value : null;
    if (typeof value !== "string" || !/(Z|[+-][0-9]{2}:[0-9]{2})$/i.test(value)) return null;
    var n = Date.parse(value);
    return Number.isFinite(n) ? n : null;
  }
  function measure(value) {
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
  }
  function text(value) { return typeof value === "string" && value.trim() ? value : null; }
  function result(status, reason, snapshot) {
    return {
      status: status, reason: reason,
      provider: text(snapshot && snapshot.provider),
      model: text(snapshot && snapshot.model),
      unit: UNITS.includes(snapshot && snapshot.unit) ? snapshot.unit : null,
      observed_at: null, used: null, limit: null, remaining: null,
      burn_per_hour: null, projected_exhaustion_at: null,
      forecast_reason: "not_eligible", sample_count: 0
    };
  }
  function sameIdentity(a, b) {
    return ["provider", "model", "source", "account_alias", "unit", "window_start", "window_end"]
      .every(function (key) { return a[key] === b[key]; });
  }
  function evaluate(snapshot, samples, options) {
    options = options || {};
    var now = options.nowMs === undefined ? Date.now() : stamp(options.nowMs);
    var maxAge = options.maxAgeMs === undefined ? 15 * 60 * 1000 : measure(options.maxAgeMs);
    var minSpan = options.minSpanMs === undefined ? 60 * 1000 : measure(options.minSpanMs);
    var output = result("unknown", "invalid_snapshot", snapshot);
    if (!snapshot || typeof snapshot !== "object" || !Array.isArray(samples || []) ||
        now === null || maxAge === null || minSpan === null) return output;
    if (snapshot.http_status !== undefined && snapshot.http_status !== 200)
      return result("unavailable", "source_http_" + String(snapshot.http_status), snapshot);
    if (!text(snapshot.provider) || !text(snapshot.model) || !text(snapshot.source) ||
        !text(snapshot.account_alias) || !UNITS.includes(snapshot.unit)) return output;
    var observed = stamp(snapshot.observed_at);
    var start = stamp(snapshot.window_start), end = stamp(snapshot.window_end);
    var used = measure(snapshot.used), limit = measure(snapshot.limit);
    if (observed === null || used === null || observed > now ||
        (snapshot.unit !== "USD" && !Number.isSafeInteger(used))) return output;
    output.observed_at = new Date(observed).toISOString();
    output.used = used;
    if (now - observed > maxAge) {
      output.status = "stale"; output.reason = "observation_too_old";
      return output;
    }
    if (start === null || end === null || !(start < end) || observed < start ||
        observed > end || now < start || now >= end) {
      output.status = "unknown"; output.reason = "window_unavailable";
      return output;
    }
    if (snapshot.authoritative !== true || limit === null ||
        (snapshot.unit !== "USD" && !Number.isSafeInteger(limit))) {
      output.status = "observed_only"; output.reason = "no_authoritative_quota";
      return output;
    }
    var reset = stamp(snapshot.reset_at);
    if (reset === null || reset <= now || reset > end || limit <= 0) {
      output.status = "unknown"; output.reason = "reset_or_limit_unavailable";
      return output;
    }
    output.status = "authoritative"; output.reason = null;
    output.limit = limit; output.remaining = Math.max(0, limit - used);
    if (used >= limit) { output.forecast_reason = "quota_exhausted"; return output; }
    if (!samples || samples.length < 3) {
      output.forecast_reason = "insufficient_samples"; return output;
    }
    var pairs = [];
    for (var i = 0; i < samples.length; i++) {
      var s = samples[i], at = s && stamp(s.observed_at), val = s && measure(s.used);
      if (!s || !sameIdentity(snapshot, s) || at === null || val === null ||
          at < start || at > observed || (snapshot.unit !== "USD" && !Number.isSafeInteger(val)) ||
          val > used) {
        output.forecast_reason = "invalid_sample_identity_or_window"; return output;
      }
      pairs.push({ at: at, value: val });
    }
    pairs.sort(function (a, b) { return a.at - b.at; });
    var unique = [];
    for (var j = 0; j < pairs.length; j++) {
      var current = pairs[j], prior = unique[unique.length - 1];
      if (prior && prior.at === current.at) {
        if (prior.value !== current.value) {
          output.forecast_reason = "conflicting_duplicate_samples"; return output;
        }
        continue;
      }
      if (prior && current.value < prior.value) {
        output.forecast_reason = "counter_rollback"; return output;
      }
      unique.push(current);
    }
    output.sample_count = unique.length;
    if (unique.length < 3 || unique[unique.length - 1].at - unique[0].at < minSpan) {
      output.forecast_reason = "insufficient_samples"; return output;
    }
    if (unique[unique.length - 1].at !== observed) {
      output.forecast_reason = "snapshot_sample_timestamp_mismatch"; return output;
    }
    if (unique[unique.length - 1].value !== used) {
      output.forecast_reason = "snapshot_sample_mismatch"; return output;
    }
    var delta = unique[unique.length - 1].value - unique[0].value;
    if (delta <= 0) { output.forecast_reason = "zero_measured_burn"; return output; }
    var rateMs = delta / (unique[unique.length - 1].at - unique[0].at);
    output.burn_per_hour = rateMs * 3600000;
    var exhaustionMs = now + output.remaining / rateMs;
    if (!Number.isFinite(exhaustionMs) || exhaustionMs >= reset) {
      output.forecast_reason = "not_before_reset"; return output;
    }
    output.projected_exhaustion_at = new Date(exhaustionMs).toISOString();
    output.forecast_reason = null;
    return output;
  }
  return Object.freeze({ evaluate: evaluate });
});
