"""Template shell contracts.

The base template is a shell: brand, nav, one page heading, one content slot.
These tests render the real templates (not string-matching them) so structural
regressions fail loudly:

* the shell must not smuggle page content (a new page that forgets its
  ``content`` block used to silently render a whole control room),
* every page must end up with exactly one ``<h1>`` and one ``<main>``,
* the nav's active marker must follow the page instead of sticking to
  CONTROL ROOM,
* the control-room script must be safe to load on any page.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"

# Elements the shell must never own: they belong to a specific page.
PAGE_SCOPED_IDS = (
    "summary-strip",
    "runtime-body",
    "dependency-grid",
    "performance-body",
    "activity-feed",
    "recovery-grid",
    "fleet-nodes-body",
    "power-grid",
)


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"]),
    )

    def url_for(name: str, **kwargs: object) -> str:
        path = kwargs.get("path")
        if isinstance(path, str):
            return f"/{path.lstrip('/')}"
        endpoint = str(name)
        if endpoint == "static":
            return "/static/asset"
        return f"/{endpoint}"

    env.globals["url_for"] = url_for
    env.globals["request"] = SimpleNamespace(
        url=SimpleNamespace(path="/render-test"),
        headers={},
        query_params={},
        cookies={},
        client=SimpleNamespace(host="testhost"),
        app=None,
    )
    return env


def _children() -> list[str]:
    names = []
    for path in sorted(TEMPLATES.glob("*.html")):
        if path.name == "base.html":
            continue
        if 'extends "base.html"' in path.read_text(encoding="utf-8"):
            names.append(path.name)
    return names


def test_base_template_owns_no_page_content():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    for element_id in PAGE_SCOPED_IDS:
        assert f'id="{element_id}"' not in base, (
            f"base.html still embeds page content ({element_id}); a page that "
            "forgets its content block would render someone else's dashboard"
        )


def test_base_template_has_a_single_main_and_one_heading_slot():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert base.count("<main>") == 1
    # Exactly one heading slot, and it must be conditional: pages that carry
    # their own heading must not receive an empty duplicate.
    assert base.count('class="page-heading"') == 1
    assert "if page_heading.strip()" in base
    # The brand must not be a heading.
    assert "brand-name" in base


def test_every_page_renders_one_heading_and_one_main():
    env = _env()
    for name in _children():
        html = env.get_template(name).render()
        assert html.count("<main>") == 1, f"{name} rendered {html.count('<main>')} <main> elements"
        headings = re.findall(r"<h1\b[^>]*>(.*?)</h1>", html, flags=re.S)
        assert len(headings) == 1, f"{name} rendered {len(headings)} <h1> elements"
        assert headings[0].strip(), f"{name} rendered an empty page heading"


def test_nav_active_marker_follows_the_page():
    env = _env()
    cases = {
        "control_room.html": "CONTROL ROOM",
        "ready.html": "TASKS",
        "review.html": "APPROVALS",
    }
    for name, expected_label in cases.items():
        html = env.get_template(name).render()
        nav = re.search(r'<nav class="command-nav".*?</nav>', html, flags=re.S)
        assert nav, f"{name} rendered no command nav"
        active = re.findall(r'<a class=" active"[^>]*>([^<]+)</a>', nav.group(0))
        assert active == [expected_label], (
            f"{name} highlights {active} instead of {expected_label}"
        )


def test_pages_without_their_own_content_do_not_render_a_dashboard():
    env = _env()
    html = env.get_template("ready.html").render()
    for element_id in PAGE_SCOPED_IDS:
        assert f'id="{element_id}"' not in html, (
            f"a non-control-room page rendered control-room markup ({element_id})"
        )
    # The shell's live widgets are present on every page.
    for shell_id in ("stream-state", "data-age", "manual-refresh", "collected-at"):
        assert f'id="{shell_id}"' in html


def test_control_room_page_keeps_its_sections():
    env = _env()
    html = env.get_template("control_room.html").render()
    for element_id in PAGE_SCOPED_IDS:
        assert f'id="{element_id}"' in html, f"control room lost {element_id}"
    assert "Fleet Control Room" in html


def test_shell_scripts_load_once_on_every_page():
    """Shell scripts sit outside {% block scripts %}.

    Pages that define their own scripts block replace the block default, so
    shell code inside the block ran on only a handful of pages and every other
    topbar stayed on CONNECTING forever.
    """
    env = _env()
    for name in _children() + ["base.html"]:
        html = env.get_template(name).render()
        assert html.count("/static/js/control_room.js") == 1, (
            f"{name} must load the control-room shell script exactly once"
        )
        assert html.count("/static/js/navigation.js") == 1, (
            f"{name} must load navigation.js exactly once"
        )
    dashboard = env.get_template("fleet_dashboard.html").render()
    assert "fleet_dashboard.js" in dashboard


def test_control_room_script_guards_page_scoped_elements():
    """The script binds page-scoped ids, so it must not throw elsewhere.

    The historical bug: an unguarded ``addEventListener`` on a control-room
    element aborted the whole script before its bootstrap fetch, so the shared
    topbar was dead on every page except the control room.
    """
    script = (ROOT / "static" / "js" / "control_room.js").read_text(encoding="utf-8")
    # Section rendering is dispatched through a guarded table.
    assert "PAGE_SECTIONS.forEach" in script
    # Listeners on page-scoped controls must be optional.
    assert "if (onlyActiveToggle)" in script
    assert "if (refreshButton)" in script
    # No top-level listener may be attached to a page-scoped element.
    for element_id in ("only-active", "activity-feed", "runtime-body", "power-grid"):
        for match in re.finditer(
            rf"^\s*\$\('{element_id}'\)\.addEventListener", script, flags=re.M
        ):
            line_start = script.rfind("\n", 0, match.start()) + 1
            line = script[line_start: script.find("\n", match.start())]
            assert "if (" in line, (
                f"control_room.js binds {element_id} without a guard: {line.strip()}"
            )
