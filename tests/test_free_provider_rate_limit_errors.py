"""Focused fail-closed route tests; no inference or provider calls."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from free_subagent_supervisor import provider_state, order_free_pools

FREE = {'id': 'kilo/free:free', 'provider':'kilo', 'base_url':'https://api.kilo.ai/api/openrouter', 'pricing':{'prompt':'0','completion':'0'}}
PAID = {'id':'openrouter/paid','provider':'openrouter','pricing':{'prompt':'2','completion':'4'}}

@pytest.mark.parametrize('observed', [
    {'status_code':429}, {'statusCode':'429'}, {'status_code':'503'},
    {'error_type':'rate_limited'}, {'kind':'too_many_requests'},
    {'error_type':'rate_limit_exceeded'}, {'error_type':'provider_overloaded'},
])
def test_throttle_observation_denies_free_route(observed):
    assert provider_state(FREE, observed) == 'rate_limited'
    assert order_free_pools([dict(FREE, observed=observed)]) == []

@pytest.mark.parametrize('observed', [{'status_code':401}, {'status_code':404}, {'kind':'not_found'}])
def test_unrelated_error_is_not_mislabeled_as_throttle(observed):
    assert provider_state(FREE, observed) == 'usable'


def test_paid_model_remains_excluded_even_when_it_is_throttled():
    assert provider_state(PAID) == 'payment_required'
    assert order_free_pools([dict(PAID,observed={'status_code':429})]) == []


def test_another_confirmed_free_lane_can_remain_usable():
    candidates=[dict(FREE,observed={'status_code':'429'}),dict(FREE,id='kilo/alternate:free')]
    assert [x['id'] for x in order_free_pools(candidates)] == ['kilo/alternate:free']
