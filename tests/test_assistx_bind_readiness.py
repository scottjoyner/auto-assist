import importlib.util
from pathlib import Path
import subprocess
from unittest.mock import patch
import pytest

spec = importlib.util.spec_from_file_location('bind_readiness', Path(__file__).resolve().parents[1] / 'scripts/check_assistx_bind_readiness.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
DEST = '/app/src'
MOUNTS = [{'Destination': DEST, 'Type': 'bind', 'Source': str(m.BASE/'src')}]
GOOD = (1, 100, 'directory')

@pytest.mark.parametrize('live,status', [(GOOD,'match'), ((1,101,'directory'),'stale_live_mount'), ((1,100,'file'),'live_type_mismatch')])
def test_live_identity(live,status):
    with patch.object(m,'host_identity',return_value=GOOD), patch.object(m,'container_identity',return_value=live):
        assert m.check_mount('assistx-api',DEST,'src','directory',MOUNTS)['status']==status

def test_missing_host():
    with patch.object(m,'host_identity',side_effect=FileNotFoundError), patch.object(m,'container_identity') as live:
        assert m.check_mount('assistx-api',DEST,'src','directory',MOUNTS)['status']=='host_path_unavailable'
        live.assert_not_called()

def test_host_symlink_rejected():
    with patch.object(m,'host_identity',return_value=(1,100,'other')), patch.object(m,'container_identity') as live:
        assert m.check_mount('assistx-api',DEST,'src','directory',MOUNTS)['status']=='host_type_mismatch'
        live.assert_not_called()

@pytest.mark.parametrize('mounts',[[],MOUNTS*2,[{**MOUNTS[0],'Source':'/wrong'}],[{**MOUNTS[0],'Type':'volume'}]])
def test_mount_contract(mounts):
    with patch.object(m,'host_identity') as host:
        assert m.check_mount('assistx-api',DEST,'src','directory',mounts)['status']=='mount_contract_mismatch'
        host.assert_not_called()

def test_race_holds():
    with patch.object(m,'host_identity',side_effect=[GOOD,(1,102,'directory')]), patch.object(m,'container_identity',return_value=GOOD):
        assert m.check_mount('assistx-api',DEST,'src','directory',MOUNTS)['status']=='host_identity_changed'

def test_timeout_holds():
    with patch.object(m,'host_identity',return_value=GOOD), patch.object(m,'container_identity',side_effect=subprocess.TimeoutExpired('stat',5)):
        assert m.check_mount('assistx-api',DEST,'src','directory',MOUNTS)['status']=='identity_unavailable'

def test_failed_inventory_has_all_nine_holds():
    with patch.object(m,'run',side_effect=OSError):
        result=m.collect()
    assert not result['ready'] and len(result['mounts'])==9
    assert all(r['status']=='mount_inventory_unavailable' for r in result['mounts'])
