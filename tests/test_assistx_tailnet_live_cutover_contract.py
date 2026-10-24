"""No implicit live activation, unsafe socat recovery, or unpinned units."""
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts/assistx_tailnet_live_cutover.sh'
UNITS=ROOT/'scripts/systemd'


def test_cutover_is_inert_by_default():
    cmd=subprocess.run(['/bin/bash',str(SCRIPT)],capture_output=True,
                       text=True,timeout=8,check=True)
    assert 'INERT:' in cmd.stderr
    assert 'PRECHECKS_PASSED' not in cmd.stdout
    subprocess.run(['/bin/bash','-n',str(SCRIPT)],timeout=8,check=True)


def test_live_switch_requires_exact_checkout_and_fail_closed_fallback():
    source=SCRIPT.read_text()
    assert '"--execute-live"' in source
    assert '42132145fb49742142a94cfdaf20f851c1686805' in source
    assert 'cutover_started=0' in source
    assert 'trap fail_closed_recovery ERR' in source
    assert 'systemctl --user disable "$OLD"' in source
    assert 'systemctl --user stop "$OLD"' in source
    assert 'systemctl --user enable --now "$PRIMARY"' in source
    assert 'systemctl --user enable --now "$FALLBACK"' in source
    assert 'systemctl --user disable --now "$PRIMARY"' in source
    assert 'tailnet_socket_owned_by_caddy' in source
    assert 'postcutover_static_gate_met' in source
    assert 'caddy_explicit_tailnet_only_listener_with_env_set_and_unset' in source
    assert '"$WORK/scripts/assistx_tailnet_sanitizer_preflight.py"' in source
    assert '"$WORK/scripts/assistx_tailnet_postcutover_attestation.py"' in source
    assert 'get_basic_in_memory(record)' in source
    assert "assert status({'Tailscale-User-Login':'synthetic-untrusted-identity'})==401" in source
    assert 'MOBILE_SSO_STILL_VALID' in source
    assert not any(x in source for x in ('enable --now "$OLD"','restart "$OLD"',
                                       'start "$OLD"','mask --now "$PRIMARY"'))


def test_primary_and_fallback_are_separate_pinned_non_root_units():
    for name,filename in (('assistx-tailnet-sanitizer.service','Caddyfile'),
                          ('assistx-tailnet-sanitizer-fallback.service',
                           'fallback.Caddyfile')):
        unit=(UNITS/name).read_text()
        assert 'Environment=ASSISTX_TAILNET_IP=100.64.43.123' in unit
        assert 'ExecStartPre=/usr/bin/sha256sum --check' in unit
        assert '/caddy-binary.sha256' in unit
        assert '/'+filename+'.sha256' in unit
        assert 'ExecStart=/usr/bin/caddy run --config ' in unit
        assert '/'+filename+' --adapter caddyfile' in unit
        assert 'NoNewPrivileges=yes' in unit
        assert 'PrivateTmp=yes' in unit
        assert 'Restart=on-failure' in unit
        assert '/usr/bin/socat' not in unit
        assert 'WantedBy=default.target' in unit


def test_original_service_never_appears_as_recovery_target():
    script=SCRIPT.read_text()
    assert script.count('"$OLD"')>=8
    for line in script.splitlines():
        if '"$OLD"' in line and any(x in line for x in (' start ',' restart ',
                                                           ' enable --now ')):
            raise AssertionError('raw TCP old service recovery is prohibited')
