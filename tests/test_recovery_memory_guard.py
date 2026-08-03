from __future__ import annotations

from types import SimpleNamespace

import pytest

from assistx.recovery_memory_guard import (
    RecoveryMemoryGuard,
    pressure_level,
    validate_sheddable_units,
)


def write_meminfo(path, *, total_mb=14336, available_mb=900, swap_free_mb=512):
    path.write_text(
        "\n".join(
            [
                f"MemTotal:       {total_mb * 1024} kB",
                f"MemAvailable:  {available_mb * 1024} kB",
                f"SwapFree:      {swap_free_mb * 1024} kB",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_pressure_levels_are_deterministic():
    assert pressure_level(2500) == ("NORMAL", [])
    assert pressure_level(1800)[0] == "ELEVATED"
    assert pressure_level(1200)[0] == "CRITICAL"
    assert pressure_level(800)[0] == "EMERGENCY"


def test_protected_units_cannot_be_shed():
    with pytest.raises(ValueError, match="protected"):
        validate_sheddable_units(["falkordb.service"])
    with pytest.raises(ValueError, match="invalid"):
        validate_sheddable_units(["unsafe;shutdown.service"])


def test_emergency_stops_only_allowlisted_model_units_and_sets_flags(tmp_path):
    meminfo = tmp_path / "meminfo"
    write_meminfo(meminfo, available_mb=800)
    calls = []

    def runner(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    guard = RecoveryMemoryGuard(
        state_dir=tmp_path / "state",
        sheddable_units=["lmstudio-headless.service", "small-llm.service"],
        runner=runner,
        meminfo_path=meminfo,
        clock=lambda: 1000,
    )
    result = guard.evaluate()

    assert result["level"] == "EMERGENCY"
    assert calls == [
        ["systemctl", "--user", "stop", "lmstudio-headless.service"],
        ["systemctl", "--user", "stop", "small-llm.service"],
    ]
    assert (tmp_path / "state" / "block-neo4j-promotion").is_file()
    assert (tmp_path / "state" / "reject-new-work").is_file()

    # Repeated evaluation does not repeatedly stop already-shed services.
    guard.evaluate()
    assert len(calls) == 2


def test_normal_memory_clears_promotion_and_admission_blocks(tmp_path):
    meminfo = tmp_path / "meminfo"
    state = tmp_path / "state"
    state.mkdir()
    (state / "block-neo4j-promotion").write_text("1\n")
    (state / "reject-new-work").write_text("1\n")
    write_meminfo(meminfo, available_mb=3000)

    result = RecoveryMemoryGuard(
        state_dir=state,
        sheddable_units=[],
        meminfo_path=meminfo,
    ).evaluate()

    assert result["level"] == "NORMAL"
    assert not (state / "block-neo4j-promotion").exists()
    assert not (state / "reject-new-work").exists()
