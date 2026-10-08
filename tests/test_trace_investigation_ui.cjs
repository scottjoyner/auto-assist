/** Native Node VM contract tests for the authenticated, read-only trace workbench.
 * No network, Neo4j, provider endpoints, production file reads or browser dependency.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const script = fs.readFileSync(path.resolve(__dirname, "../static/js/traces.js"), "utf8");
const template = fs.readFileSync(path.resolve(__dirname, "../templates/traces.html"), "utf8");
const style = fs.readFileSync(path.resolve(__dirname, "../static/css/traces.css"), "utf8");
const IDS = [
  "trace-list", "trace-detail", "trace-detail-caption", "trace-status",
  "trace-total-metric", "trace-loaded-metric", "trace-failed-metric",
  "trace-prev", "trace-next", "trace-range", "trace-total",
  "trace-refresh", "trace-copy", "trace-permalink", "trace-search",
  "trace-search-form", "trace-clear", "trace-outcome"
];

function element(id) {
  const listeners = {};
  const attrs = {};
  const classes = new Set();
  const dom = {
    id, listeners, attrs, classList: {
      toggle(name, selected) { if (selected) classes.add(name); else classes.delete(name); },
      contains(name) { return classes.has(name); },
    },
    value: "", disabled: false, innerHTML: "", textContent: "", href: "", tabIndex: 0,
    addEventListener(name, fn) { listeners[name] = fn; },
    setAttribute(name, value) { attrs[name] = String(value); },
    getAttribute(name) { return attrs[name]; },
    querySelectorAll() { return []; },
    focus() { this.focused = true; },
    fire(name, extra = {}) {
      assert.equal(typeof listeners[name], "function", `missing ${id} ${name} handler`);
      return listeners[name]({ target: this, preventDefault() {}, ...extra });
    },
  };
  if (id === "trace-detail") {
    let html = "";
    dom.details = [];
    Object.defineProperty(dom, "innerHTML", {
      get() { return html; },
      set(content) {
        html = content;
        dom.evidence = String(content).includes('class="trace-evidence-results"')
          ? { textContent: "", innerHTML: "" } : null;
        dom.details = Array.from(String(content).matchAll(/<details data-event-index="([0-9]+)"/g), match => {
          const pre = { textContent: "" };
          return {
            open: false,
            getAttribute(key) { return key === "data-event-index" ? match[1] : null; },
            querySelector(key) { return key === "pre" ? pre : null; },
            addEventListener(key, fn) { if (key === "toggle") this.onToggle = fn; },
            toggle(open) { this.open = open; if (this.onToggle) this.onToggle(); },
          };
        });
      },
    });
    dom.querySelectorAll = selector => selector === "details[data-event-index]" ? dom.details : [];
    dom.focusedSelector = null;
    dom.querySelector = selector => {
      if (selector === ".trace-evidence-results") return dom.evidence;
      return ["button.trace-context-chip.selected", ".trace-context h3"].includes(selector)
        ? { focus() { dom.focusedSelector = selector; } } : null;
    };
  }
  return dom;
}

function trace(id, outcome = "completed") {
  return {
    correlation_id: id, outcome, events: 3, duration_ms: 810,
    last_ts_ms: Date.parse("2026-10-08T10:00:00Z"),
  };
}
function detail(id, withSecret = false) {
  return {
    correlation_id: id, current_state: "observed",
    events: [{
      event_type: "router.started", source: "synthetic-node",
      ts_ms: Date.parse("2026-10-08T10:00:00Z"),
      payload_json: JSON.stringify({
        safe_fixture: id, ...(withSecret ? { private_test_marker: "SYNTHETIC_DO_NOT_RENDER" } : {})
      })
    }]
  };
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}
const ok = value => ({ ok: true, status: 200, json: async () => value });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function harness({ initialUrl = "https://assistx.invalid/traces", response } = {}) {
  const els = Object.fromEntries(IDS.map(id => [id, element(id)]));
  const calls = [];
  const actions = [];
  const document = { getElementById(id) {
    if (!els[id]) throw Error("Unrecognized UI element " + id);
    return els[id];
  }};
  const window = {
    location: { href: initialUrl },
    history: { replaceState(_, __, url) { window.location.href = new URL(url, window.location.href).href; } },
    addEventListener(name, fn) { actions[name] = fn; }
  };
  const fetch = url => {
    calls.push(url);
    if (!url.startsWith("/api/traces")) throw Error("UI attempted non-read-only endpoint");
    return response ? response(url) : Promise.resolve(ok({ total: 0, offset: 0, traces: [] }));
  };
  vm.runInNewContext(script, {
    window, document, fetch, URL, navigator: { clipboard: { writeText: async () => {} } },
    setTimeout, clearTimeout, Number, String, Date, Object, Array, Math, JSON,
    console,
  }, { filename: "traces.js", timeout: 2500 });
  return { els, calls, window, actions };
}

test("HTML has accessible navigation, scoped metrics, and event disclosure contract", () => {
  for (const needle of [
    'aria-current="page"', 'role="status"', 'id="trace-total-metric"',
    'id="trace-failed-metric"', 'Outcome · all indexed history',
    'id="trace-permalink"', 'id="trace-copy"',
    'not a complete tool-call audit ledger', 'aria-label="Previous page of traces"'
  ]) assert.ok(template.includes(needle), needle);
  assert.ok(template.includes("Usage &amp; burn · staged"));
  assert.ok(!template.includes('href="/provider-usage"'), "never advertise an undeployed provider usage URL");
  assert.ok(style.includes("@media (max-width: 600px)"));
  assert.ok(style.includes(":focus-visible"));
  assert.ok(script.includes('details data-event-index'));
  assert.ok(script.includes("node.querySelector(\"pre\").textContent"));
  assert.doesNotMatch(script, /POST|DELETE|PATCH|PUT/);
});

test("index metric totals distinguish global matches from page-only failures", async () => {
  const rows = [trace("id-one", "failed"), trace("id-two"), trace("id-three", "failed")];
  const ui = harness({ response: url => Promise.resolve(ok(url.includes("/api/traces?")
    ? { traces: rows, total: 85000, offset: 0 }
    : detail("id-one"))) });
  await sleep(25);
  assert.equal(ui.els["trace-total-metric"].textContent, "85,000");
  assert.equal(ui.els["trace-loaded-metric"].textContent, "3");
  assert.equal(ui.els["trace-failed-metric"].textContent, "2");
  assert.match(ui.els["trace-list"].innerHTML, /aria-pressed="true"/);
  assert.match(ui.els["trace-list"].innerHTML, /<button/);
  assert.equal(ui.els["trace-prev"].disabled, true);
  assert.equal(ui.els["trace-next"].disabled, false);
  assert.match(ui.els["trace-detail"].innerHTML, /id-one/);
  assert.ok(ui.calls.every(x => x.startsWith("/api/traces")));
});

test("permalink opens a trace outside the first index page; event content stays collapsed", async () => {
  const ui = harness({
    initialUrl: "https://assistx.invalid/traces?trace=outside-page",
    response: url => Promise.resolve(ok(url.includes("?limit=")
      ? { traces: [trace("a-local")], total: 120, offset: 0 }
      : detail("outside-page", true)))
  });
  await sleep(25);
  assert.match(ui.calls.join(" "), /\/api\/traces\/outside-page/);
  assert.match(ui.els["trace-detail"].innerHTML, /outside-page/);
  assert.ok(!ui.els["trace-detail"].innerHTML.includes("SYNTHETIC_DO_NOT_RENDER"));
  assert.match(ui.els["trace-detail"].innerHTML, /<details/);
  const disclosure = ui.els["trace-detail"].details[0];
  assert.ok(disclosure, "event payload uses a deferred disclosure");
  assert.equal(disclosure.querySelector("pre").textContent, "");
  disclosure.toggle(true);
  assert.match(disclosure.querySelector("pre").textContent, /SYNTHETIC_DO_NOT_RENDER/);
  disclosure.toggle(false);
  assert.equal(disclosure.querySelector("pre").textContent, "");
  assert.match(ui.window.location.href, /trace=outside-page/);
  assert.equal(ui.els["trace-permalink"].getAttribute("aria-disabled"), "false");
});

test("outcome filter queries the entire indexed history before pagination", async () => {
  const ui = harness({ response: url => Promise.resolve(ok(url.includes("?limit=")
    ? url.includes("outcome=failed")
      ? { traces: [trace("bad", "failed")], total: 76, offset: 0, outcome: "failed" }
      : { traces: [trace("bad", "failed"), trace("good", "completed")], total: 930, offset: 0 }
    : detail(url.endsWith("good") ? "good" : "bad"))) });
  await sleep(20);
  const baselineCalls = ui.calls.length;
  ui.els["trace-outcome"].fire("change", { target: { value: "failed" } });
  await sleep(20);
  assert.ok(ui.calls.length > baselineCalls);
  assert.match(ui.calls.join(" "), /outcome=failed/);
  assert.match(ui.els["trace-list"].innerHTML, /bad/);
  assert.doesNotMatch(ui.els["trace-list"].innerHTML, /good/);
  assert.equal(ui.els["trace-total-metric"].textContent, "76");
  assert.equal(ui.els["trace-failed-metric"].textContent, "1");
  assert.match(ui.window.location.href, /outcome=failed/);
  assert.match(ui.els["trace-status"].textContent, /matching failed trace groups/);
});

test("authentication failure is explicit with retry and does not invoke writes", async () => {
  const ui = harness({ response: () => Promise.resolve({ ok: false, status: 401 }) });
  await sleep(20);
  assert.match(ui.els["trace-status"].textContent, /Authentication required or expired/);
  assert.ok(ui.els["trace-status"].classList.contains("is-error"));
  assert.match(ui.els["trace-list"].innerHTML, /Try again/);
  assert.ok(ui.calls.every(x => x.startsWith("/api/traces")));
});

test("stale selected detail cannot overwrite a more recent selection", async () => {
  const oldest = deferred();
  const ui = harness({ response: url => {
    if (url.includes("?limit=")) return Promise.resolve(ok({
      traces: [trace("older"), trace("newer")], total: 2, offset: 0
    }));
    if (url.includes("/older")) return oldest.promise;
    return Promise.resolve(ok(detail("newer")));
  } });
  await sleep(20);
  assert.match(ui.calls.join(" "), /\/older/);
  ui.els["trace-list"].fire("click", { target: {
    closest: () => ({ dataset: { cid: "newer" } })
  }});
  await sleep(20);
  assert.match(ui.els["trace-detail"].innerHTML, /newer/);
  oldest.resolve(ok(detail("older")));
  await sleep(20);
  assert.match(ui.els["trace-detail"].innerHTML, /newer/);
  assert.doesNotMatch(ui.els["trace-detail"].innerHTML, /older/);
});

test("stale search response cannot override newer correlation-ID search", async () => {
  const initial = deferred();
  const ui = harness({ response: url => {
    if (url.includes("?limit=") && !url.includes("search=")) return initial.promise;
    if (url.includes("search=new")) return Promise.resolve(ok({
      traces: [trace("new-match")], total: 1, offset: 0
    }));
    return Promise.resolve(ok(detail("new-match")));
  }});
  ui.els["trace-search"].value = "new";
  ui.els["trace-search"].fire("input");
  await sleep(325);
  assert.match(ui.els["trace-list"].innerHTML, /new-match/);
  initial.resolve(ok({ traces: [trace("stale-match")], total: 999, offset: 0 }));
  await sleep(20);
  assert.doesNotMatch(ui.els["trace-list"].innerHTML, /stale-match/);
  assert.equal(ui.els["trace-total-metric"].textContent, "1");
});


test("deep links preserve a global outcome filter for results beyond page one", async () => {
  const ui = harness({
    initialUrl: "https://assistx.invalid/traces?outcome=failed&trace=old-failure",
    response: url => Promise.resolve(ok(url.includes("?limit=")
      ? { traces: [trace("latest-failure", "failed")], total: 74, offset: 0, outcome: "failed" }
      : detail("old-failure")))
  });
  await sleep(28);
  assert.equal(ui.els["trace-outcome"].value, "failed");
  assert.equal(ui.els["trace-total-metric"].textContent, "74");
  assert.match(ui.calls[0], /outcome=failed/);
  assert.match(ui.calls.join(" "), /\/api\/traces\/old-failure/);
  assert.match(ui.els["trace-permalink"].href, /outcome=failed/);
  assert.match(ui.els["trace-permalink"].href, /trace=old-failure/);
});

test("server-side filtered pagination navigates beyond the first 50 results", async () => {
  const first = Array.from({ length: 50 }, (_, i) => trace("failed-" + i, "failed"));
  const next = Array.from({ length: 13 }, (_, i) => trace("older-failed-" + i, "failed"));
  const ui = harness({
    initialUrl: "https://assistx.invalid/traces?outcome=failed",
    response: url => Promise.resolve(ok(url.includes("?limit=")
      ? { traces: url.includes("offset=50") ? next : first,
          total: 63, offset: url.includes("offset=50") ? 50 : 0, outcome: "failed" }
      : detail("failed-0")))
  });
  await sleep(30);
  assert.equal(ui.els["trace-loaded-metric"].textContent, "50");
  assert.equal(ui.els["trace-next"].disabled, false);
  ui.els["trace-next"].fire("click");
  await sleep(25);
  assert.match(ui.calls.join(" "), /offset=50&outcome=failed/);
  assert.equal(ui.els["trace-loaded-metric"].textContent, "13");
  assert.equal(ui.els["trace-total-metric"].textContent, "63");
  assert.equal(ui.els["trace-prev"].disabled, false);
  assert.equal(ui.els["trace-next"].disabled, true);
  assert.match(ui.els["trace-list"].innerHTML, /older-failed-0/);
});

test("unsupported older backend cannot silently impersonate a global filter", async () => {
  const ui = harness({
    initialUrl: "https://assistx.invalid/traces?outcome=failed",
    response: () => Promise.resolve(ok({
      traces: [trace("unfiltered", "completed")], total: 200, offset: 0
    }))
  });
  await sleep(25);
  assert.match(ui.els["trace-status"].textContent, /Global outcome filtering is not yet available/);
  assert.equal(ui.els["trace-total-metric"].textContent, "—");
  assert.doesNotMatch(ui.els["trace-list"].innerHTML, /unfiltered/);
});

test("an old unfiltered response cannot replace a newer global failure selection", async () => {
  const old = deferred();
  const ui = harness({
    response: url => {
      if (url.includes("?limit=") && !url.includes("outcome=")) return old.promise;
      if (url.includes("outcome=failed"))
        return Promise.resolve(ok({ traces: [trace("new-failure", "failed")],
                                    total: 12, offset: 0, outcome: "failed" }));
      return Promise.resolve(ok(detail("new-failure")));
    }
  });
  ui.els["trace-outcome"].fire("change", { target: { value: "failed" } });
  await sleep(20);
  old.resolve(ok({ traces: [trace("old-success", "completed")], total: 999 }));
  await sleep(20);
  assert.match(ui.els["trace-list"].innerHTML, /new-failure/);
  assert.doesNotMatch(ui.els["trace-list"].innerHTML, /old-success/);
  assert.equal(ui.els["trace-total-metric"].textContent, "12");
});


test("context panel groups recorded IDs and filters only the loaded timeline", async () => {
  const data = {
    correlation_id: "investigate-1", current_state: "running",
    events: [
      { event_type: "dispatch.accepted", source: "router", task_id: "task-a",
        ts_ms: 1000, payload_json: '{"fixture_secret":"DO_NOT_INLINE"}' },
      { event_type: "route.selected", source: "router", task_id: "task-b",
        ts_ms: 2000 },
      { event_type: "assignment.claimed", source: "worker", task_id: "task-a",
        assignment_id: "assignment-1", ts_ms: 3000 },
    ],
    context: {
      schema: "trace-context-v1", source_semantics: "producer_label_not_verified_node_or_agent",
      node_or_agent_verified: false, payload_inspected: false,
      fields: {
        source: [
          { value: "router", events: 2, provenance: "trace_event_property" },
          { value: "worker", events: 1, provenance: "trace_event_property" }
        ],
        task_id: [
          { value: "task-a", events: 2, provenance: "trace_event_property" },
          { value: "task-b", events: 1, provenance: "trace_event_property" }
        ],
        dispatch_id: [], route_id: [],
        assignment_id: [{ value: "assignment-1", events: 1, provenance: "trace_event_property" }]
      },
      missing: ["dispatch_id", "route_id"], truncated: []
    }
  };
  const ui = harness({ response: url => Promise.resolve(ok(url.includes("?limit=")
    ? { traces: [trace("investigate-1")], total: 1, outcome: "all" }
    : data)) });
  await sleep(30);
  assert.match(ui.els["trace-detail"].innerHTML, /Recorded context/);
  assert.match(ui.els["trace-detail"].innerHTML, /task-a/);
  assert.match(ui.els["trace-detail"].innerHTML, /task-b/);
  assert.match(ui.els["trace-detail"].innerHTML, /Node\/agent identity: not established/);
  assert.match(ui.els["trace-detail"].innerHTML, /3 of 3 events/);
  assert.doesNotMatch(ui.els["trace-detail"].innerHTML, /DO_NOT_INLINE/);
  const count = ui.calls.length;

  const chip = {
    getAttribute(name) { return name === "data-context-field" ? "task_id" : "task-a"; }
  };
  ui.els["trace-detail"].fire("click", {
    target: { closest(selector) {
      return selector === "button.trace-context-chip" ? chip : null;
    } }
  });
  assert.match(ui.els["trace-detail"].innerHTML, /2 of 3 events/);
  assert.doesNotMatch(ui.els["trace-detail"].innerHTML, /route\.selected/);
  assert.match(ui.els["trace-detail"].innerHTML, /assignment\.claimed/);
  assert.equal(ui.els["trace-detail"].focusedSelector, "button.trace-context-chip.selected",
    "keyboard focus should remain on the selected context value");
  assert.equal(ui.calls.length, count, "context selection must not create graph queries");
  ui.els["trace-detail"].fire("click", {
    target: { closest(selector) {
      return selector === "button.trace-clear-context" ? {} : null;
    } }
  });
  assert.match(ui.els["trace-detail"].innerHTML, /3 of 3 events/);
  assert.match(ui.els["trace-detail"].innerHTML, /route\.selected/);
  assert.equal(ui.els["trace-detail"].focusedSelector, ".trace-context h3",
    "reset focus returns to the context heading");
  assert.equal(ui.calls.length, count);
});

test("unknown context is clearly unavailable, never inferred from payload claims", async () => {
  const ui = harness({ response: url => Promise.resolve(ok(url.includes("?limit=")
    ? { traces: [trace("legacy")], total: 1, outcome: "all" }
    : {
      correlation_id: "legacy", events: [
        { source: "router", event_type: "assignment.claimed", ts_ms: 10,
          payload_json: '{"node_id":"claimed-node","task_id":"untrusted-task"}' }
      ]
    })) });
  await sleep(25);
  assert.match(ui.els["trace-detail"].innerHTML, /Context metadata not available/);
  assert.match(ui.els["trace-detail"].innerHTML, /No node or agent identity is inferred/);
  assert.doesNotMatch(ui.els["trace-detail"].innerHTML, /claimed-node|untrusted-task/);
});

test("malicious context values are escaped and never treated as navigation links", async () => {
  const malicious = '<img src=x onerror="alert(1)">';
  const ui = harness({ response: url => Promise.resolve(ok(url.includes("?limit=")
    ? { traces: [trace("unsafe")], total: 1, outcome: "all" }
    : {
      correlation_id: "unsafe", events: [{ event_type: "route.selected", source: "s",
        ts_ms: 12, route_id: malicious }],
      context: {
        schema: "trace-context-v1", truncated: [], fields: {
          source: [], task_id: [], dispatch_id: [],
          route_id: [{ value: malicious, events: 1, provenance: "trace_event_property" }],
          assignment_id: [],
        }
      }
    })) });
  await sleep(25);
  const html = ui.els["trace-detail"].innerHTML;
  assert.ok(html.includes("&lt;img"));
  assert.ok(!html.includes('<img src=x onerror='));
  assert.ok(!html.includes("href=" + malicious));
  assert.match(html, /trace-context-chip/);
});

test("context filters reject unknown or fabricated field assertions", async () => {
  const ui = harness({ response: url => Promise.resolve(ok(url.includes("?limit=")
    ? { traces: [trace("guard")], total: 1, outcome: "all" }
    : {
      correlation_id: "guard", events: [{ event_type: "route.selected", source: "router",
        task_id: "real-task", ts_ms: 12 }],
      context: {
        schema: "trace-context-v1", truncated: [], fields: {
          source: [], task_id: [
            { value: "real-task", events: 1, provenance: "trace_event_property" }
          ], dispatch_id: [], route_id: [], assignment_id: []
        }
      }
    })) });
  await sleep(24);
  const before = ui.els["trace-detail"].innerHTML;
  const action = (field,value) => ui.els["trace-detail"].fire("click", {
    target: { closest(selector) { return selector === "button.trace-context-chip"
      ? { getAttribute(name) {
        return name === "data-context-field" ? field : value;
      }} : null; }}
  });
  action("node_id", "pretend-node");
  assert.equal(ui.els["trace-detail"].innerHTML, before);
  action("task_id", "invented-task");
  assert.equal(ui.els["trace-detail"].innerHTML, before);
});


function inspectEvidence(ui) {
  ui.els["trace-detail"].fire("click", {
    target: { closest(selector) {
      return selector === "button.trace-inspect-evidence" ? {} : null;
    }}
  });
}
function mockEvidence(cid, tasks) {
  return {
    schema: "trace-task-evidence-v1", correlation_id: cid, tasks,
    trust: "unverified_correspondence_not_execution_attestation",
    node_or_agent_verified: false, source_authenticated: false,
    graph_write_permitted: false, truncated: false,
  };
}
function taskEvidence(id="task-1", node="node-1", state="registry_id_match_unverified") {
  return {
    task_id: id, node_id: node, worker_id:"claimed-worker",task_status:"CLAIMED",
    registry_state: state, registry_matches: 1,
    task_provenance: "trace_event_relationship_and_property",
    projection_provenance: "assignment_projection_unverified",
    node_or_agent_verified: false
  };
}

test("on-demand evidence GET is never triggered on initial trace detail load", async () => {
  const ui = harness({ response: url => Promise.resolve(ok(url.includes("?limit=")
    ? { traces: [trace("run-a")], total: 1, outcome: "all" }
    : detail("run-a"))) });
  await sleep(26);
  assert.equal(ui.calls.length, 2);
  assert.ok(!ui.calls.some(url => url.endsWith("/evidence")));
  assert.ok(!ui.els["trace-detail"].innerHTML.includes("SSH"));
});

test("explicit read-only evidence inspection is bounded, unverified and escaped", async () => {
  const malicious = '<img src=x onerror="alert(1)">';
  const ui = harness({ response: url => Promise.resolve(ok(
    url.includes("?limit=") ? { traces: [trace("run-b")], total: 1, outcome: "all" }
      : url.endsWith("/evidence")
        ? mockEvidence("run-b",[taskEvidence(malicious,malicious)])
        : detail("run-b")
  )) });
  await sleep(22);
  assert.equal(ui.calls.length, 2);
  inspectEvidence(ui);
  await sleep(24);
  assert.ok(ui.calls[2].endsWith("/api/traces/run-b/evidence"));
  assert.equal(ui.calls.length, 3);
  const text = ui.els["trace-detail"].evidence.innerHTML;
  assert.match(text, /Registry ID matches; identity unverified/);
  assert.match(text, /unverified ID correspondence/);
  assert.ok(text.includes("&lt;img"));
  assert.doesNotMatch(text, /<img src=x/);
  assert.doesNotMatch(text, /href=|ssh:\/\/|tailscale_ip|password/i);
});

test("ambiguous registration is not promoted to verified attribution", async () => {
  const ui = harness({ response: url => Promise.resolve(ok(
    url.includes("?limit=") ? { traces: [trace("ambiguous")], total: 1, outcome: "all" }
      : url.endsWith("/evidence") ? mockEvidence("ambiguous",[
        taskEvidence("task-a","node-z","ambiguous_registry_id"),
        taskEvidence("task-b",null,"not_recorded")
      ]) : detail("ambiguous")
  )) });
  await sleep(20);
  inspectEvidence(ui);
  await sleep(22);
  assert.match(ui.els["trace-detail"].evidence.innerHTML, /Ambiguous registry ID/);
  assert.match(ui.els["trace-detail"].evidence.innerHTML, /No node ID recorded/);
  assert.doesNotMatch(ui.els["trace-detail"].evidence.innerHTML, /Verified executor|Execute task|Run command/);
});

test("stale evidence response for earlier selected trace is discarded", async () => {
  const old = deferred();
  const ui = harness({ response: url => {
    if (url.includes("?limit=")) return Promise.resolve(ok({
      traces: [trace("first"),trace("second")],total:2,outcome:"all"
    }));
    if (url.endsWith("/first/evidence")) return old.promise;
    if (url.endsWith("/evidence")) return Promise.resolve(ok(mockEvidence("second",[
      taskEvidence("new-task")
    ])));
    return Promise.resolve(ok(detail(url.includes("second")?"second":"first")));
  }});
  await sleep(24);
  inspectEvidence(ui);
  ui.els["trace-list"].fire("click", { target: {
    closest: () => ({ dataset: { cid:"second" } })
  }});
  await sleep(22);
  inspectEvidence(ui);
  await sleep(20);
  assert.match(ui.els["trace-detail"].evidence.innerHTML, /new-task/);
  old.resolve(ok(mockEvidence("first",[taskEvidence("stale-task")])));
  await sleep(20);
  assert.match(ui.els["trace-detail"].evidence.innerHTML, /new-task/);
  assert.doesNotMatch(ui.els["trace-detail"].evidence.innerHTML, /stale-task/);
});

test("untrusted older evidence schema and auth errors never assert identity", async () => {
  const ui = harness({ response: url => Promise.resolve(
    url.includes("?limit=") ? ok({traces:[trace("older")],total:1,outcome:"all"})
      : url.endsWith("/evidence") ? ok({ tasks:[{node_id:"unverified",node_or_agent_verified:true}] })
      : ok(detail("older"))
  )});
  await sleep(20);
  inspectEvidence(ui);
  await sleep(20);
  assert.match(ui.els["trace-detail"].evidence.textContent, /Evidence schema unavailable/);
  assert.doesNotMatch(ui.els["trace-detail"].evidence.innerHTML, /unverified/);
});

test("refresh retries previously failed selected detail", async () => {
  let detailCount = 0;
  const ui = harness({ response: url => {
    if (url.includes("?limit=")) return Promise.resolve(ok({ traces: [trace("retry-id")], total: 1 }));
    detailCount++;
    return Promise.resolve(detailCount === 1 ? { ok: false, status: 503 } : ok(detail("retry-id")));
  }});
  await sleep(25);
  assert.match(ui.els["trace-detail"].innerHTML, /Timeline unavailable/);
  ui.els["trace-refresh"].fire("click");
  await sleep(25);
  assert.equal(detailCount, 2);
  assert.match(ui.els["trace-detail"].innerHTML, /router.started/);
});

test("index authentication loss clears expanded sensitive detail and disables copy", async () => {
  let indexCount = 0;
  const ui = harness({ response: url => {
    if (url.includes("?limit=")) return Promise.resolve(++indexCount === 1
      ? ok({ traces: [trace("private-id")], total: 1 }) : { ok: false, status: 401 });
    return Promise.resolve(ok(detail("private-id", true)));
  }});
  await sleep(25);
  const disclosure = ui.els["trace-detail"].details[0];
  disclosure.toggle(true);
  assert.match(disclosure.querySelector("pre").textContent, /SYNTHETIC_DO_NOT_RENDER/);
  ui.els["trace-refresh"].fire("click");
  await sleep(25);
  assert.doesNotMatch(ui.els["trace-detail"].innerHTML, /private-id|SYNTHETIC_DO_NOT_RENDER/);
  assert.equal(ui.els["trace-copy"].disabled, true);
  assert.doesNotMatch(ui.window.location.href, /trace=private-id/);
});

test("index auth loss fences pending task registry evidence and preserves unknown metrics", async () => {
  let indexReads = 0;
  const pendingEvidence = deferred();
  const ui = harness({ response: url => {
    if (url.includes("?limit=")) return Promise.resolve(++indexReads === 1
      ? ok({ traces: [trace("private-task")], total: 1, outcome: "all" })
      : { ok: false, status: 401 });
    if (url.endsWith("/evidence")) return pendingEvidence.promise;
    return Promise.resolve(ok(detail("private-task", true)));
  }});
  await sleep(22);
  inspectEvidence(ui);
  assert.equal(ui.calls.length, 3);
  ui.els["trace-refresh"].fire("click");
  await sleep(22);
  pendingEvidence.resolve(ok(mockEvidence("private-task", [taskEvidence("stale-task")])));
  await sleep(22);
  assert.equal(ui.els["trace-detail"].evidence, null);
  assert.doesNotMatch(ui.els["trace-detail"].innerHTML, /stale-task|private-task|SYNTHETIC_DO_NOT_RENDER/);
  assert.equal(ui.els["trace-copy"].disabled, true);
  assert.equal(ui.els["trace-total-metric"].textContent, "—");
  assert.equal(ui.els["trace-loaded-metric"].textContent, "—");
  assert.equal(ui.els["trace-failed-metric"].textContent, "—");
});
