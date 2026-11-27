"""Format-only (invisible) provider output must not earn healthy-success (r14).

``classify_text`` is the module's single answer to "is this HTTP-200 answer
consumable?".  Callers grant healthy-success credit only for the ``ok`` reason,
so a response made entirely of invisible/format characters (zero-width space,
word joiner, BOM, soft hyphen, ...) used to pass as a real answer: it is a
non-empty string, ``str.strip()`` does not remove Unicode format characters,
and the placeholder run detector only looks at ``?`` and U+FFFD.

These tests are pure in-memory strings: no I/O, model, network, credential or
service is touched.  The module is loaded directly by path so importing the
package facade (and its HTTP clients) is not required.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src" / "assistx" / "llm" / "output_integrity.py"
)
_spec = importlib.util.spec_from_file_location("_output_integrity_r14", _MODULE_PATH)
oi = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(oi)

INVISIBLE_ONLY = "invisible_only"

# Only characters whose Unicode general category is Cf (format) are used here:
# they have no visible glyph and no width, so an answer made solely of them is
# semantically empty even though ``str.strip()`` leaves it intact.
INVISIBLE_ONLY_CASES = [
    ("zero-width space", "\u200b" * 8),
    ("zero-width non-joiner", "\u200c" * 6),
    ("word joiner", "\u2060" * 6),
    ("byte-order mark", "\ufeff" * 4),
    ("soft hyphen", "\u00ad" * 8),
    ("mixed format run", "\u200b\u2060\ufeff\u200c"),
    ("format run with outer spaces", "  \u200b\u200c\u200b  "),
]


@pytest.mark.parametrize("label, value", INVISIBLE_ONLY_CASES)
def test_invisible_only_answer_is_rejected(label: str, value: str) -> None:
    """The regression: invisible-only text used to be classified ``ok``."""
    assert oi.classify_text(value) == INVISIBLE_ONLY, label


def test_invisible_only_is_not_usable() -> None:
    assert oi.is_usable(INVISIBLE_ONLY) is False


def test_invisible_only_is_a_declared_defect_reason() -> None:
    assert INVISIBLE_ONLY in oi.DEFECT_REASONS


# --- controls: existing semantics must be byte-for-byte unchanged ------------

@pytest.mark.parametrize(
    "value, expected",
    [
        ("", oi.EMPTY_CONTENT),
        ("   \t\n ", oi.WHITESPACE_ONLY),
        (None, oi.NON_TEXT_CONTENT),
        ({"content": "x"}, oi.NON_TEXT_CONTENT),
        ("hello world", oi.OK),
        ("\u4eca\u65e5\u306f\u4e16\u754c", oi.OK),
        ("a\u200bb", oi.OK),  # inline format char in a real answer stays usable
        ("\ufffd", oi.UNDECODABLE_TEXT),
        ("????", oi.PLACEHOLDER_RUN),
        ("caf\u00c3\u00a9", oi.MOJIBAKE_TEXT),  # UTF-8 e-acute read as latin-1
    ],
)
def test_existing_classification_is_unchanged(value: object, expected: str) -> None:
    assert oi.classify_text(value) == expected


def test_declared_charset_behaviour_is_unchanged() -> None:
    assert oi.declared_charset("text/event-stream; charset=utf-8") == "utf-8"
    assert oi.declared_charset("text/event-stream") is None
