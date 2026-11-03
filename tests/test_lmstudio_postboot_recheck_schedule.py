"""Static safety constraints for LM Studio boot-health recheck scheduling."""
import configparser
import runpy
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
UNIT=ROOT/"deploy/systemd/user/lm-studio-api-recheck.service"
TIMER=ROOT/"deploy/systemd/user/lm-studio-api-recheck.timer"
SCRIPT=ROOT/"scripts/lmstudio-api-recheck.py"


def _unit(path):
    parser=configparser.ConfigParser(interpolation=None)
    parser.optionxform=str
    parser.read(path)
    return parser


def test_postboot_timer_starts_early_and_rechecks_under_90_seconds():
    timer=_unit(TIMER)
    assert timer["Timer"]["OnStartupSec"]=="90s"
    assert timer["Timer"]["OnUnitInactiveSec"]=="60s"
    assert timer["Timer"]["OnCalendar"]=="*:0/5"
    assert timer["Timer"]["AccuracySec"]=="10s"
    assert timer["Timer"]["Unit"]=="lm-studio-api-recheck.service"
    assert timer["Timer"]["Persistent"]=="true"
    assert timer["Install"]["WantedBy"]=="timers.target"


def test_service_waits_for_initial_bootstrap_no_model_load():
    service=_unit(UNIT)
    assert "lm-studio-api-start.service" in service["Unit"]["After"].split()
    assert service["Service"]["Type"]=="oneshot"
    assert service["Service"]["TimeoutStartSec"]=="45"
    assert service["Service"]["NoNewPrivileges"]=="true"
    assert service["Service"]["UMask"]=="0077"
    assert service["Service"]["ExecStart"]==(
        "/usr/bin/python3 -B /home/scott/.local/bin/lmstudio-api-recheck.py"
    )


def test_watcher_never_loads_model_or_admits_a_runtime():
    source=SCRIPT.read_text()
    assert "lms load" not in source
    assert "model load" not in source.lower()
    assert "neo4j" not in source.lower()
    module=runpy.run_path(str(SCRIPT))
    probe=module["check_and_recover"]
    with patch.dict(probe.__globals__, {"api_available":lambda:True}):
        with patch.object(module["subprocess"],"run") as runner:
            assert probe()=="healthy"
            runner.assert_not_called()
