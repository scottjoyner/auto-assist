"""`.env` variants must never be committable.

`.gitignore` matched only `.env`, so override files and pre-deploy snapshots
(`.env.override`, `.env.pre-<change>.<timestamp>`) were untracked but *not*
ignored. A routine `git add -A` would have committed live values: the Neo4j
password, paperclip tokens, the runtime-projection HMAC secret, the signing key
and the per-node tokens.

docs/SECURITY_SECRETS_AND_FLEET_IDENTITY_PLAN.md is explicit that secret values
must never enter Git, so this is enforced mechanically rather than by
convention.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: Real filenames seen in a working clone. If a new pattern appears, add it.
ENV_VARIANTS = (
    ".env",
    ".env.override",
    ".env.pre-adapter-url-fix.20260929T231305Z",
    ".env.pre-fresh-deploy.20260913T193500Z",
    ".env.pre-kipnerter-gateway.20260909T120543Z",
    ".env.local",
)


def ignored(path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=REPO,
        capture_output=True,
    )
    return result.returncode == 0


def tracked(path: str) -> bool:
    return (
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", path],
            cwd=REPO,
            capture_output=True,
        ).returncode
        == 0
    )


@pytest.mark.parametrize("path", ENV_VARIANTS)
def test_env_variants_are_ignored(path: str) -> None:
    assert ignored(path), f"{path} is not ignored; `git add -A` would commit it"


def test_env_example_stays_trackable() -> None:
    """The template is documentation, not a copy of a live environment."""
    assert not ignored(".env.example")
    assert tracked(".env.example")


def test_no_already_tracked_file_becomes_ignored() -> None:
    """Adding an ignore rule must not hide something the repo already ships."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked_files = [name for name in listing.stdout.split("\0") if name]

    hidden = [name for name in tracked_files if ignored(name)]

    assert hidden == []
