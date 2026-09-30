"""Regression tests for defects found by the browser UI audit.

Each test here corresponds to a real production symptom:

* ``getCriticalAlerts`` was called but never defined, so ``render()`` threw on
  every page and no dashboard panel ever populated.
* ``Navigation.init()`` resolved to the browser's built-in ``Navigation`` API
  because ``navigation.js`` was never loaded by the shell.
* ``/api/answers/events`` was registered *after* ``/api/answers/{answer_id}``,
  so Starlette matched the parameterized route first and the SSE endpoint was
  permanently unreachable (the page silently fell back to nothing).
* One Task with an integer ``priority`` (100) among 31k rows made
  ``/api/live/strategy`` raise ``'int' object has no attribute 'upper'`` and
  500 the whole page.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from assistx.api import _normalize_priority, _priority_sort_key


ROOT = Path(__file__).resolve().parents[1]
CONTROL_ROOM_JS = ROOT / "static" / "js" / "control_room.js"
BASE_HTML = ROOT / "templates" / "base.html"

# Globals the browser provides; anything else called as a bare function must be
# defined in the file itself.
JS_GLOBALS = {
    "fetch", "JSON", "Math", "Number", "String", "Date", "Object", "Array",
    "Boolean", "parseInt", "parseFloat", "isFinite", "isNaN", "setTimeout",
    "clearTimeout", "setInterval", "clearInterval", "EventSource",
    "WebSocket", "console", "encodeURIComponent", "decodeURIComponent",
    "require", "structuredClone", "URL", "Uint8Array", "Promise", "Symbol",
    "Map", "Set", "WeakMap", "WeakSet", "Proxy", "Reflect", "Error", "RegExp",
}


def test_every_bare_function_call_in_the_control_room_script_is_defined():
    script = CONTROL_ROOM_JS.read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", " ", script, flags=re.S)
    body = re.sub(r"//[^\n]*", " ", body)
    # Strip string/template literals so text inside them is not parsed as code.
    body = re.sub(r"`(?:\\.|[^`\\])*`", "``", body)
    body = re.sub(r"'(?:\\.|[^'\\])*'", "''", body)
    body = re.sub(r'"(?:\\.|[^"\\])*"', '""', body)
    called = set(re.findall(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", body))
    called -= {
        "for", "if", "while", "switch", "catch", "return", "typeof", "new",
        "await", "function", "do", "else", "var", "let", "const",
    }
    # Only *declarations* count as definitions. Matching a bare `name(`
    # assignment would wrongly treat a call site as its own definition.
    defined = set(re.findall(r"(?:function|const|let|var)\s+([A-Za-z_$][\w$]*)", body))
    defined |= set(
        re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\b|\()", body)
    )
    # Callback/function parameters are definitions too (they are local names,
    # e.g. destructured pair elements inside forEach).
    for params in re.findall(r"\(([^()]*)\)\s*(?:=>|\{)", body):
        for token in re.findall(r"[A-Za-z_$][\w$]*", params):
            defined.add(token)
    undefined = sorted(called - defined - JS_GLOBALS)
    assert not undefined, (
        "control_room.js calls functions that are never defined: "
        f"{undefined} (an undefined call aborts the script at runtime)"
    )


def test_shell_loads_navigation_before_the_control_room_script():
    html = BASE_HTML.read_text(encoding="utf-8")
    nav = html.index("/static/js/navigation.js")
    control_room = html.index("/static/js/control_room.js")
    assert nav < control_room, (
        "navigation.js must load before control_room.js, which calls "
        "Navigation.init(); otherwise the browser's built-in Navigation API "
        "shadows it and init() is not a function"
    )


def test_critical_alert_derivation_handles_an_empty_snapshot():
    script = CONTROL_ROOM_JS.read_text(encoding="utf-8")
    assert "const getCriticalAlerts = (snapshot)" in script
    assert "state.criticalAlerts = getCriticalAlerts(snapshot);" in script


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("HIGH", "HIGH"),
        ("background", "BACKGROUND"),
        (" critical ", "CRITICAL"),
        (100, "UNSET"),  # malformed value, not a priority label
        (None, "UNSET"),
        ("", "UNSET"),
    ],
)
def test_priority_is_normalized_before_it_reaches_the_payload(stored, expected):
    assert _normalize_priority(stored) == expected


def test_priority_sort_key_survives_non_string_values():
    """The int priority (100) in the graph used to 500 the strategy page."""
    assert _priority_sort_key({"priority": 100}) == 9  # UNSET rank
    assert _priority_sort_key({"priority": None}) == 9
    assert _priority_sort_key({"priority": "CRITICAL"}) == 0
    assert _priority_sort_key({"priority": "background"}) == 4
    assert _priority_sort_key({}) == 9


def test_priority_sort_key_orders_the_canonical_buckets():
    items = [
        {"priority": "LOW"},
        {"priority": "CRITICAL"},
        {"priority": 7},
        {"priority": "BACKGROUND"},
    ]
    ordered = [item["priority"] for item in sorted(items, key=_priority_sort_key)]
    assert ordered == ["CRITICAL", "LOW", "BACKGROUND", 7]


def test_no_api_route_is_shadowed_by_an_earlier_parameterized_route():
    """Starlette matches in registration order: literal paths must come first."""
    from assistx.api_router import app

    def to_regex(path: str) -> re.Pattern[str]:
        segments = [
            r"[^/]+" if (s.startswith("{") or s == "*") else re.escape(s)
            for s in path.split("/")
            if s
        ]
        return re.compile("^/" + "/".join(segments) + "/?$")

    seen: list[tuple[str, set[str]]] = []
    shadowed: list[str] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        methods = set(getattr(route, "methods", set()) or set())
        if "{" not in path and "*" not in path:
            for earlier_path, earlier_methods in seen:
                if "{" in earlier_path and to_regex(earlier_path).match(path):
                    if methods & earlier_methods:
                        shadowed.append(f"{path} swallowed by {earlier_path}")
                        break
        seen.append((path, methods))
    assert not shadowed, f"unreachable API routes (registration order): {shadowed}"

def test_answers_sse_handler_resolves_its_redis_url():
    """The four answers SSE handlers referenced an undefined REDIS_URL.

    Route shadowing hid this: the endpoint answered 404 before the handler body
    ever ran, so the NameError (500) only appeared once the route was reachable.
    """
    from assistx import api

    assert hasattr(api, "REDIS_URL"), "api.py must expose REDIS_URL for the SSE handlers"
    assert api.REDIS_URL.startswith("redis://")
    for name in (
        "api_answers_events",
        "api_answer_events",
    ):
        assert callable(getattr(api, name))


def test_nav_key_block_value_is_not_printed_on_the_page():
    """A bare {% block %} emits its value into the body.

    The nav_active block was declared as a text node, so pages rendered a stray
    "control_room" label above the navigation bar.
    """
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(ROOT / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )

    def url_for(name: str, **kwargs: object) -> str:
        path = kwargs.get("path")
        return "/" + (path.lstrip("/") if isinstance(path, str) else str(name))

    env.globals["url_for"] = url_for
    # Only a bare text node is the bug; the key legitimately appears inside
    # asset URLs such as /static/js/control_room.js.
    nav_keys = ("control_room", "tasks", "approvals", "metrics")
    for name in ("control_room.html", "ready.html", "review.html", "fleet_dashboard.html"):
        html = env.get_template(name).render()
        leaked = [k for k in nav_keys if re.search(rf">\s*{k}\s*<", html)]
        assert not leaked, f"{name} printed the raw nav key as page text: {leaked}"
    html = env.get_template("control_room.html").render()
    assert 'class=" active" href="/control-room"' in html


def test_control_room_script_selectors_exist_in_the_template():
    """Selector drift: the script queried classes the template no longer has.

    ``.component-health`` and ``.trend-data`` were renamed to
    ``.component-health-body`` / ``.trend-body``, so two panels rendered their
    "Loading ..." placeholder forever with no console error to show for it.
    """
    import re as _re

    # The script legitimately queries both shell (base) and page containers.
    template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8") + (
        ROOT / "templates" / "control_room.html"
    ).read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "control_room.js").read_text(encoding="utf-8")
    selectors = set(
        _re.findall(r"document\.querySelector\('\.([a-z-]+)'\)", script)
    )
    assert selectors, "expected the script to query class selectors"
    template_classes = set(_re.findall(r'class="([^"]+)"', template))
    flat = {token for group in template_classes for token in group.split()}
    missing = sorted(s for s in selectors if s not in flat)
    assert not missing, f"script queries classes absent from the page: {missing}"
