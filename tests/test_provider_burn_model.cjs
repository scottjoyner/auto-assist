/** Synthetic provider-budget fixtures: no network, API keys, provider calls or dispatch. */
const test = require("node:test");
const assert = require("node:assert/strict");
const { evaluate } = require("../static/js/provider_burn_model.js");

const iso = time => "2026-10-08T" + time + ":00Z";
const nowMs = Date.parse(iso("16:00"));
const options = { nowMs };
const base = () => ({
  provider: "synthetic-free-model", model: "fake-small-model",
  source: "fixture-meter", account_alias: "anonymous-synthetic",
  unit: "tokens", authoritative: true, http_status: 200,
  window_start: iso("15:00"), window_end: iso("17:00"),
  reset_at: iso("17:00"), observed_at: iso("15:59"),
  limit: 100, used: 80
});
function samples(s = base()) {
  return [ ["15:40", 20], ["15:50", 50], ["15:59", s.used] ].map(([at, used]) => ({
    provider: s.provider, model: s.model, source: s.source,
    account_alias: s.account_alias, unit: s.unit,
    window_start: s.window_start, window_end: s.window_end,
    observed_at: iso(at), used
  }));
}
test("authoritative monotonic fixture predicts exhaustion before reset", () => {
  const r = evaluate(base(), samples(), options);
  assert.equal(r.status, "authoritative");
  assert.equal(r.used, 80);
  assert.equal(r.limit, 100);
  assert.equal(r.remaining, 20);
  assert.equal(r.sample_count, 3);
  assert.ok(r.burn_per_hour > 0);
  assert.ok(Date.parse(r.projected_exhaustion_at) > nowMs);
  assert.ok(Date.parse(r.projected_exhaustion_at) < Date.parse(iso("17:00")));
  assert.equal(r.forecast_reason, null);
});
test("zero usage is observed zero, not missing telemetry", () => {
  const s = base(); s.used = 0;
  const r = evaluate(s, [], options);
  assert.equal(r.status, "authoritative");
  assert.equal(r.used, 0);
  assert.equal(r.remaining, 100);
  assert.equal(r.projected_exhaustion_at, null);
});
test("missing authoritative quota stays observed-only without remaining or forecast", () => {
  const s = base(); s.authoritative = false; s.limit = null;
  const r = evaluate(s, samples(s), options);
  assert.equal(r.status, "observed_only");
  assert.equal(r.used, 80);
  assert.equal(r.remaining, null);
  assert.equal(r.burn_per_hour, null);
});
test("provider HTTP errors fail closed and do not turn into zero usage", () => {
  for (const status of [401, 403, 404, 429, 503]) {
    const s = base(); s.http_status = status;
    const r = evaluate(s, samples(s), options);
    assert.equal(r.status, "unavailable");
    assert.equal(r.used, null);
    assert.equal(r.remaining, null);
    assert.equal(r.forecast_reason, "not_eligible");
    assert.equal(r.reason, "source_http_" + status);
  }
});
test("stale observation has no trusted remaining or burn", () => {
  const s = base();
  const r = evaluate(s, samples(s), { nowMs: Date.parse(iso("16:30")) });
  assert.equal(r.status, "stale");
  assert.equal(r.remaining, null);
  assert.equal(r.burn_per_hour, null);
});
test("expired reset boundary cannot produce remaining quota", () => {
  const s = base(); s.reset_at = iso("15:59");
  const r = evaluate(s, samples(s), options);
  assert.equal(r.status, "unknown");
  assert.equal(r.reason, "reset_or_limit_unavailable");
});
test("window mismatch, now outside window and future observation fail closed", () => {
  const s = base(); s.window_end = iso("15:58");
  assert.equal(evaluate(s, samples(s), options).reason, "window_unavailable");
  const t = base(); t.observed_at = iso("16:01");
  assert.equal(evaluate(t, samples(t), options).status, "unknown");
  const u = base(); u.window_start = iso("16:10");
  assert.equal(evaluate(u, samples(u), options).reason, "window_unavailable");
});
test("cross-provider/account/model/source/unit samples cannot be combined", () => {
  for (const key of ["provider", "model", "source", "account_alias", "unit", "window_start"]) {
    const arr = samples();
    arr[1][key] = arr[1][key] === "requests" ? "tokens" : "requests";
    const r = evaluate(base(), arr, options);
    assert.equal(r.forecast_reason, "invalid_sample_identity_or_window", key);
    assert.equal(r.projected_exhaustion_at, null);
  }
});
test("counter rollback rejects forecast without discarding current authoritative remaining", () => {
  const arr = samples(); arr[1].used = 10;
  const r = evaluate(base(), arr, options);
  assert.equal(r.forecast_reason, "counter_rollback");
  assert.equal(r.remaining, 20);
});
test("conflicting same-timestamp duplicates reject forecast", () => {
  const arr = samples(); arr.push({ ...arr[1], used: 49 });
  const r = evaluate(base(), arr, options);
  assert.equal(r.forecast_reason, "conflicting_duplicate_samples");
});
test("identical duplicate timestamps deduplicate safely", () => {
  const arr = samples(); arr.push({ ...arr[1] });
  const r = evaluate(base(), arr, options);
  assert.equal(r.sample_count, 3);
  assert.equal(r.forecast_reason, null);
});
test("insufficient samples or span cannot infer burn", () => {
  const r = evaluate(base(), samples().slice(1), options);
  assert.equal(r.forecast_reason, "insufficient_samples");
  const arr = samples().map((s,i) => ({ ...s, observed_at: iso(["15:58","15:58","15:59"][i]) }));
  assert.equal(evaluate(base(), arr, options).projected_exhaustion_at, null);
});
test("no exhaust-before-reset stays forecast-unknown even with measured positive burn", () => {
  const s = base(); s.limit = 2000;
  const r = evaluate(s, samples(s), options);
  assert.equal(r.status, "authoritative");
  assert.equal(r.forecast_reason, "not_before_reset");
  assert.ok(r.burn_per_hour > 0, "measured burn is still valid even when forecast is not");
  assert.equal(r.projected_exhaustion_at, null);
});
test("non-monotone, mismatched snapshot and invalid unit remain bounded", () => {
  const s = base(); s.used = 79;
  const r = evaluate(s, samples(base()), options);
  assert.equal(r.forecast_reason, "invalid_sample_identity_or_window");
  const bad = base(); bad.unit = "USD/token";
  assert.equal(evaluate(bad, [], options).status, "unknown");
  const malformed = base(); malformed.used = "80";
  assert.equal(evaluate(malformed, [], options).status, "unknown");
});
test("no network, secrets or authority verbs in projection module", () => {
  const src = require("node:fs").readFileSync(require.resolve("../static/js/provider_burn_model.js"), "utf8");
  assert.doesNotMatch(src, /\bfetch\s*\(|XMLHttpRequest|axios\.|\bdispatch\s*\(|\bPOST\b|\.writeFile\s*\(/);
});
