"""Static Jinja inheritance contract; no API startup, database or network.

The control-room shell must remain available on its own, while all child
templates must actually render their content, head styles and scripts.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]


def render(page: str) -> str:
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True)
    return env.get_template(page).render(
        url_for=lambda name, path: f"/static/{path}"
    )


@pytest.mark.parametrize(
    ("page", "required_markers", "unique_script"),
    [
        ("base.html", ("id=\"summary-strip\"", "id=\"runtime-body\""), "/static/js/control_room.js"),
        ("control_room.html", ("id=\"summary-strip\"", "id=\"runtime-body\"", "id=\"fleet-nodes-body\""), "/static/js/control_room.js"),
        ("fleet_dashboard.html", ("id=\"section-nodes\"", "id=\"section-inference-state\"", "id=\"section-routing\""), None),
        ("operations.html", ("id=\"new-task\"", "id=\"view-attention\"", "id=\"view-fleet\""), None),
        ("workbench.html", ("id=\"ctx-fleet\"", "/static/js/workbench.js"), "/static/js/workbench.js"),
        ("devices.html", ("id=\"device-list\"",), None),
    ],
)
def test_child_render_contains_page_specific_content_and_single_document(
    page: str, required_markers: tuple[str, ...], unique_script: str | None
) -> None:
    html = render(page)
    assert html.lower().count("<!doctype html>") == 1, page
    assert len(re.findall(r"<html\b", html, flags=re.I)) == 1, page
    assert len(re.findall(r"</html>", html, flags=re.I)) == 1, page
    assert len(re.findall(r"<body\b", html, flags=re.I)) == 1, page
    for marker in required_markers:
        assert marker in html, f"{page} missing: {marker}"
    assert html.count("ASSISTX FLEET CONTROL ROOM") == 1, page
    if unique_script:
        assert html.count(f'"/static/js/{unique_script.rsplit("/", 1)[-1]}"') == 1, page
    if page != "base.html" and page != "control_room.html":
        assert '<script src="/static/js/control_room.js">' not in html, page


def test_child_head_and_script_blocks_apply_only_to_child() -> None:
    html = render("operations.html")
    assert '<link rel="stylesheet" href="/static/css/operations.css">' in html
    assert "<title>AssistX – Operations</title>" in html
    assert "control_room.js" not in html
    assert 'id="kpi-running"' in html

    html = render("workbench.html")
    assert "<title>AssistX // Agent Workbench</title>" in html
    assert html.count('src="/static/js/workbench.js"') == 1
    assert "control_room.js" not in html


def test_default_control_room_navigation_and_resources_preserved() -> None:
    for page in ("base.html", "control_room.html"):
        html = render(page)
        for nav in ('href="/control-room"', 'href="/workbench"', 'href="/metrics"'):
            assert nav in html, f"{page}: missing navigation {nav}"
        for asset in ("/static/css/base.css", "/static/css/components.css", "/static/css/control_room.css"):
            assert asset in html
        assert 'id="manual-refresh"' in html
        assert 'id="summary-dependencies"' in html


def test_template_inheritance_is_explicit_and_without_preceding_html() -> None:
    content = (ROOT / "templates/control_room.html").read_text()
    assert content.lstrip().startswith('{% extends "base.html" %}')
    for name in ("base.html", "control_room.html", "fleet_dashboard.html", "operations.html"):
        Environment(loader=FileSystemLoader(ROOT / "templates")).get_template(name)
