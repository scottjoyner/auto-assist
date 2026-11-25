import importlib.util
from pathlib import Path
from unittest.mock import patch
import subprocess,sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
@pytest.mark.parametrize('name',['native_race_522_remote','native_race_522_http'])
def test_standalone_is_plan_only(name):
    p=subprocess.run([sys.executable,'-B',str(ROOT/'scripts'/f'{name}.py')],capture_output=True,text=True,timeout=5)
    assert p.returncode==0 and 'plan-only' in p.stdout
@pytest.mark.parametrize('name',['native_race_522_remote','native_race_522_http'])
def test_wrong_host_or_query_denied_without_process(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    with patch.object(m.socket,'gethostname',return_value='x1-370'),patch.object(m.subprocess,'run',side_effect=AssertionError('unexpected process')) as runner:
        r=m.run('RETURN 1');assert r['state']=='hold' and not r['productionGraphUsed'] and r['trials']==[];runner.assert_not_called()
