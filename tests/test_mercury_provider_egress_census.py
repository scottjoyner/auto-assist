"""Never dispatch; inspect only synthetic file trees and local git commits."""
import importlib.util
import subprocess
from pathlib import Path
import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts/mercury_provider_egress_census.py"
spec = importlib.util.spec_from_file_location("census", MODULE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def sample(tmp):
    root = tmp / "repo"
    f = root / "src/providers/a.ts"
    f.parent.mkdir(parents=True)
    f.write_text('const a = generateText({ model, prompt });\nreturn globalThis.fetch(url);\n')
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "src"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test",
                    "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "base"], check=True)
    return root, f, m.git(root, "rev-parse", "HEAD")


def test_exact_source_research_hold_not_grant(tmp_path):
    root, _, sha = sample(tmp_path)
    meta = m.manifest(root, sha)
    assert meta["counts"] == {"ai_sdk_call": 1, "provider_object_call": 0,
                              "provider_raw_fetch": 1}
    verdict = m.check(root, meta)
    assert verdict["state"] == "SOURCE_MATCH_RESEARCH_HOLD"
    assert verdict["provider_execution_authorized"] is False
    assert verdict["complete_egress_verified"] is False


@pytest.mark.parametrize("extra", [
    'const b = streamText({ model, prompt });',
    'const b = await provider.generateText(prompt, system);',
    'const b = globalThis.fetch(endpoint);',
])
def test_new_unreviewed_callsite_denied(tmp_path, extra):
    root, file, sha = sample(tmp_path)
    expected = m.manifest(root, sha)
    file.write_text(file.read_text() + extra + "\n")
    assert m.check(root, expected)["state"] == "SOURCE_MISMATCH_DENY"


def test_commit_change_denied_even_without_src_diff(tmp_path):
    root, _, sha = sample(tmp_path)
    expected = m.manifest(root, sha)
    (root / "README.md").write_text("new commit\n")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test",
                    "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "next"], check=True)
    assert m.check(root, expected)["state"] == "SOURCE_MISMATCH_DENY"


def test_forged_manifest_count_denied(tmp_path):
    root, _, sha = sample(tmp_path)
    expected = m.manifest(root, sha)
    expected["counts"]["ai_sdk_call"] = 0
    assert m.check(root, expected)["state"] == "SOURCE_MISMATCH_DENY"


def test_missing_repo_denied(tmp_path):
    assert m.check(tmp_path, {"schema": 1, "upstream_commit": "a"*40})["state"] == "SOURCE_MISMATCH_DENY"


def test_partial_scanner_is_not_full_transport_proof(tmp_path):
    root, _, sha = sample(tmp_path)
    expected = m.manifest(root, sha)
    extra = root / "src/web/session.ts"
    extra.parent.mkdir(parents=True)
    extra.write_text('const x = await fetch(authUrl);\n')
    assert len(m.scan(root)) == 2
    # An unclassified fetch is NOT proof that the model endpoint is fenced.
    verdict = m.check(root, expected)
    assert verdict["state"] == "SOURCE_MISMATCH_DENY"
    assert verdict["complete_egress_verified"] is False

