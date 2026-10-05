"""`.env` variants must never be committable.

`.gitignore` matched only `.env`, so override files and pre-deploy snapshots
(`.env.override`, `.env.pre-<change>.<timestamp>`) were untracked but *not*
ignored. A routine `git add -A` would have committed live values: the Neo4j
password, paperclip tokens, the runtime-projection HMAC secret, the signing key,
and the per-node tokens.

`docs/SECURITY_SECRETS_AND_FLEET_IDENTITY_PLAN.md` is explicit that secret
values must never enter Git, so this is enforced mechanically rather than by
convention.

These tests read no secret values. They only ask git whether a path is ignored,
which is the property that matters.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: Real filenames observed in a working clone. If a new pattern appears, add it.
ENV_VARIANTS = (
    ".env",
    ".env.override",
    ".env.local",
    ".env.production",
    ".env.pre-adapter-url-fix.20260929T231305Z",
    ".env.pre-fresh-deploy.20260913T193500Z",
    ".env.pre-kipnerter-gateway.20260909T120543Z",
)

#: Documented templates that must remain addable.
ENV_TEMPLATES = (".env.example", ".env.kipnerter-gateway.example")

#: Tracked by name, but not a live environment. Archived after a
#: secret-removal incident; it holds placeholder values, not real ones, and it
#: is evidence that the removal happened. Named explicitly rather than
#: weakening the rule for everything under archive/.
ARCHIVED_ENV_EXAMPLE = "archive/.env.committed-SECRETS-REMOVED"


def _ignored(path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path],
        cwd=str(REPO),
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


@pytest.mark.parametrize("variant", ENV_VARIANTS)
def test_env_variants_are_gitignored(variant: str) -> None:
    assert _ignored(variant), f"{variant} is not ignored; `git add -A` would commit it"


@pytest.mark.parametrize("template", ENV_TEMPLATES)
def test_env_templates_remain_trackable(template: str) -> None:
    assert not _ignored(template), (
        f"{template} is a documented template and must stay addable"
    )


def test_env_templates_are_actually_tracked() -> None:
    """A template that is not tracked is not a template, it is a stale file."""

    tracked = subprocess.run(
        ["git", "ls-files", "--", ".env.example", ".env.*.example"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert tracked, "expected at least one tracked .env template"


def test_no_live_env_variant_is_already_tracked() -> None:
    """The rule is only useful if history is clean.

    A variant that was committed before the rule existed stays tracked, because
    gitignore never untracks. This asserts that has not happened.
    """

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=str(REPO), capture_output=True, text=True, check=True
    ).stdout.splitlines()
    offenders = [
        name
        for name in tracked
        if Path(name).name.startswith(".env")
        and Path(name).name not in ENV_TEMPLATES
        and name != ARCHIVED_ENV_EXAMPLE
    ]
    assert not offenders, f"live .env variants are tracked in git: {offenders}"
