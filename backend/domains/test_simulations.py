import fakeredis
import pytest
import redis
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .models import Domain

User = get_user_model()
PASSWORD = 'S3cure-pass-phrase!'


@pytest.fixture
def alice(db):
    return User.objects.create_user(email='alice@example.com', password=PASSWORD)


@pytest.fixture
def client(alice):
    c = APIClient()
    c.force_authenticate(alice)
    return c


@pytest.fixture
def bob_client(db):
    c = APIClient()
    c.force_authenticate(User.objects.create_user(email='bob@example.com', password=PASSWORD))
    return c


@pytest.fixture
def domain(alice):
    return Domain.objects.create(owner=alice, name='www.example.com')


@pytest.fixture
def fake_redis(monkeypatch):
    server = fakeredis.FakeRedis()
    monkeypatch.setattr('domains.views.get_redis', lambda: server)
    return server


def cache_sim_url(d):
    return f'/api/domains/{d.id}/cache-policy/simulate/'


def check_url(d):
    return f'/api/domains/{d.id}/rate-limit-policy/check/'


def set_rate_limit(client, domain, **fields):
    payload = {'enabled': True, **fields}
    assert client.patch(f'/api/domains/{domain.id}/rate-limit-policy/', payload, format='json').status_code == 200


# --- cache simulation -----------------------------------------------------

def test_cache_simulation_uses_the_stored_policy(client, domain):
    client.patch(f'/api/domains/{domain.id}/cache-policy/',
                 {'default_ttl_seconds': 60, 'bypass_paths': ['/admin']}, format='json')

    response = client.post(cache_sim_url(domain), {
        'capacity': 10,
        'requests': [
            {'path': '/a', 'at': 0}, {'path': '/a?x=1', 'at': 10},
            {'path': '/admin', 'at': 11}, {'path': '/a', 'at': 70},
        ],
    }, format='json')

    assert response.status_code == 200
    body = response.json()
    assert [r['outcome'] for r in body['results']] == ['MISS', 'HIT', 'BYPASS', 'MISS']
    assert body['summary']['origin_requests'] == 3
    assert body['summary']['hit_ratio'] == 0.25


@pytest.mark.parametrize('payload', [
    {'requests': []},
    {'requests': [{'path': 'no-slash', 'at': 0}]},
    {'requests': [{'path': '/a', 'at': -1}]},
    {'requests': [{'path': '/a', 'at': 5}, {'path': '/b', 'at': 1}]},
    {'capacity': 0, 'requests': [{'path': '/a', 'at': 0}]},
    {'capacity': 1001, 'requests': [{'path': '/a', 'at': 0}]},
    {'requests': [{'path': '/a', 'at': i} for i in range(501)]},
])
def test_cache_simulation_validation(client, domain, payload):
    assert client.post(cache_sim_url(domain), payload, format='json').status_code == 400


def test_cache_simulation_is_owner_only(bob_client, domain):
    response = bob_client.post(cache_sim_url(domain), {'requests': [{'path': '/a', 'at': 0}]}, format='json')

    assert response.status_code == 404


# --- rate limit check -----------------------------------------------------

def test_check_enforces_a_token_bucket(client, domain, fake_redis):
    set_rate_limit(client, domain, algorithm='token_bucket', limit=5, window_seconds=3600)

    body = client.post(check_url(domain), {'client_ip': '203.0.113.9', 'requests': 8}, format='json').json()

    assert body['summary'] == {'sent': 8, 'allowed': 5, 'limited': 3, 'blocked': 3}
    assert [r['status'] for r in body['results']] == [200] * 5 + [429] * 3
    assert body['results'][-1]['retry_after'] > 0


def test_state_persists_across_calls_and_is_per_client(client, domain, fake_redis):
    set_rate_limit(client, domain, algorithm='sliding_window', limit=3, window_seconds=3600)
    def post(ip, n):
        return client.post(check_url(domain), {'client_ip': ip, 'requests': n}, format='json').json()

    assert post('198.51.100.1', 2)['summary']['allowed'] == 2
    assert post('198.51.100.1', 2)['summary']['allowed'] == 1  # only one slot left
    assert post('198.51.100.2', 2)['summary']['allowed'] == 2  # another client is unaffected


def test_global_scope_shares_one_counter(client, domain, fake_redis):
    set_rate_limit(client, domain, algorithm='sliding_window', limit=2, window_seconds=3600, scope='global')
    def post():
        return client.post(check_url(domain), {'requests': 2}, format='json').json()

    assert post()['summary']['allowed'] == 2
    assert post()['summary']['allowed'] == 0


def test_log_only_mode_reports_but_never_returns_429(client, domain, fake_redis):
    set_rate_limit(client, domain, algorithm='sliding_window', limit=1, window_seconds=3600, action='log_only')

    body = client.post(check_url(domain), {'client_ip': '203.0.113.9', 'requests': 3}, format='json').json()

    assert body['summary'] == {'sent': 3, 'allowed': 1, 'limited': 2, 'blocked': 0}
    assert {r['status'] for r in body['results']} == {200}


def test_disabled_policy_lets_everything_through_without_touching_redis(client, domain, monkeypatch):
    def boom():
        raise AssertionError('Redis must not be used when the policy is disabled')
    monkeypatch.setattr('domains.views.get_redis', boom)

    body = client.post(check_url(domain), {'client_ip': '203.0.113.9', 'requests': 5}, format='json').json()

    assert body['enabled'] is False and body['summary']['allowed'] == 5


def test_reset_clears_the_sandbox_state(client, domain, fake_redis):
    set_rate_limit(client, domain, algorithm='sliding_window', limit=1, window_seconds=3600)
    body = {'client_ip': '203.0.113.9', 'requests': 1}
    client.post(check_url(domain), body, format='json')
    assert client.post(check_url(domain), body, format='json').json()['summary']['allowed'] == 0

    assert client.delete(check_url(domain) + '?client_ip=203.0.113.9').status_code == 204

    assert client.post(check_url(domain), body, format='json').json()['summary']['allowed'] == 1


def test_switching_algorithm_does_not_crash_on_existing_keys(client, domain, fake_redis):
    set_rate_limit(client, domain, algorithm='token_bucket', limit=5, window_seconds=60)
    client.post(check_url(domain), {'client_ip': '203.0.113.9'}, format='json')

    set_rate_limit(client, domain, algorithm='sliding_window', burst=None)

    assert client.post(check_url(domain), {'client_ip': '203.0.113.9'}, format='json').status_code == 200


def test_burst_larger_than_limit_allows_the_larger_initial_burst(client, domain, fake_redis):
    set_rate_limit(client, domain, algorithm='token_bucket', limit=1, window_seconds=3600, burst=4)

    body = client.post(check_url(domain), {'client_ip': '203.0.113.9', 'requests': 6}, format='json').json()

    assert body['summary']['allowed'] == 4


@pytest.mark.parametrize('payload', [
    {},                                                 # per_ip scope needs client_ip
    {'client_ip': 'not-an-ip'},
    {'client_ip': '203.0.113.9', 'requests': 0},
    {'client_ip': '203.0.113.9', 'requests': 1001},
])
def test_check_validation(client, domain, fake_redis, payload):
    set_rate_limit(client, domain, limit=5)

    assert client.post(check_url(domain), payload, format='json').status_code == 400


def test_check_returns_503_when_redis_is_down(client, domain, monkeypatch):
    set_rate_limit(client, domain, limit=5)

    class Down:
        def register_script(self, *_):
            raise redis.ConnectionError('down')

    monkeypatch.setattr('domains.views.get_redis', lambda: Down())

    response = client.post(check_url(domain), {'client_ip': '203.0.113.9'}, format='json')

    assert response.status_code == 503


def test_check_is_owner_only_and_requires_login(bob_client, domain, fake_redis):
    assert bob_client.post(check_url(domain), {'client_ip': '203.0.113.9'}, format='json').status_code == 404
    assert APIClient().post(check_url(domain), {}, format='json').status_code == 401
