"""No real HTTP or systemd calls. Synthetic watchdog behavior."""
import runpy
from pathlib import Path
from unittest.mock import patch

module = runpy.run_path(str(Path(__file__).resolve().parents[1]
                            / "scripts/lmstudio-api-recheck.py"))
CHECK = module["check_and_recover"]
HEALTH = module["api_available"]


def test_healthy_never_restarts():
    with patch.dict(CHECK.__globals__, {"api_available": lambda: True}):
        with patch.object(module["subprocess"], "run") as run:
            assert CHECK() == "healthy"
            run.assert_not_called()


def test_failed_check_restarts_only_pinned_service_once():
    responses = iter([False, True])
    with patch.dict(CHECK.__globals__, {"api_available": lambda: next(responses)}):
        with patch.object(module["subprocess"], "run") as run, patch.object(module["time"], "sleep"):
            run.return_value.returncode = 0
            assert CHECK() == "recovered"
            run.assert_called_once()
            assert run.call_args.args[0] == [
                "systemctl", "--user", "restart", "lm-studio-api-start.service"
            ]


def test_recovery_can_report_unverified():
    with patch.dict(CHECK.__globals__, {"api_available": lambda: False}):
        with patch.object(module["subprocess"], "run") as run, patch.object(module["time"], "sleep"):
            run.return_value.returncode = 0
            assert CHECK() == "recovery_unverified"


def test_failed_systemd_never_claims_health():
    with patch.dict(CHECK.__globals__, {"api_available": lambda: False}):
        with patch.object(module["subprocess"], "run") as run:
            run.return_value.returncode = 1
            assert CHECK() == "recovery_failed"
