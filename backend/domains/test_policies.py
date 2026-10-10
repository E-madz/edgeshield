import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .models import CachePolicy, Domain, Origin, RateLimitPolicy

User = get_user_model()
PASSWORD = 'S3cure-pass-phrase!'


def make_client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def alice(db):
    return User.objects.create_user(email='alice@example.com', password=PASSWORD)


@pytest.fixture
def bob(db):
    return User.objects.create_user(email='bob@example.com', password=PASSWORD)


@pytest.fixture
def alice_client(alice):
    return make_client(alice)


@pytest.fixture
def bob_client(bob):
    return make_client(bob)


@pytest.fixture
def domain(alice):
    return Domain.objects.create(owner=alice, name='www.example.com')


def cache_url(d):
    return f'/api/domains/{d.id}/cache-policy/'


def rate_url(d):
    return f'/api/domains/{d.id}/rate-limit-policy/'


# --- access control -------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('suffix', ['cache-policy', 'rate-limit-policy', 'config'])
def test_policy_endpoints_require_authentication(suffix):
    assert APIClient().get(f'/api/domains/1/{suffix}/').status_code == 401


@pytest.mark.parametrize('suffix', ['cache-policy', 'rate-limit-policy', 'config'])
def test_other_users_policies_return_404(bob_client, domain, suffix):
    url = f'/api/domains/{domain.id}/{suffix}/'

    assert bob_client.get(url).status_code == 404
    assert bob_client.patch(url, {'enabled': False}).status_code in (404, 405)
    assert not CachePolicy.objects.filter(domain=domain).exists()


# --- defaults and singleton behaviour -------------------------------------

def test_policies_are_created_with_defaults_on_first_access(alice_client, domain):
    cache = alice_client.get(cache_url(domain)).json()
    rate = alice_client.get(rate_url(domain)).json()

    assert cache['enabled'] is True and cache['default_ttl_seconds'] == 3600
    assert cache['query_string_mode'] == 'ignore' and cache['bypass_paths'] == []
    assert rate['enabled'] is False and rate['algorithm'] == 'token_bucket'
    assert rate['limit'] == 100 and rate['window_seconds'] == 60
    assert rate['refill_rate_per_second'] == pytest.approx(1.6667)


def test_repeated_access_never_creates_a_second_policy(alice_client, domain):
    for _ in range(3):
        alice_client.get(cache_url(domain))
        alice_client.get(rate_url(domain))

    assert CachePolicy.objects.filter(domain=domain).count() == 1
    assert RateLimitPolicy.objects.filter(domain=domain).count() == 1


def test_policies_are_deleted_with_the_domain(alice_client, domain):
    alice_client.get(cache_url(domain))
    alice_client.get(rate_url(domain))

    alice_client.delete(f'/api/domains/{domain.id}/')

    assert CachePolicy.objects.count() == 0 and RateLimitPolicy.objects.count() == 0


# --- cache policy ---------------------------------------------------------

def test_update_cache_policy_with_put_and_patch(alice_client, domain):
    response = alice_client.put(cache_url(domain), {
        'enabled': True, 'default_ttl_seconds': 600, 'browser_ttl_seconds': 60,
        'serve_stale_seconds': 30, 'query_string_mode': 'include',
        'bypass_paths': ['/admin', '/api/private'],
    }, format='json')
    assert response.status_code == 200

    patched = alice_client.patch(cache_url(domain), {'default_ttl_seconds': 900}, format='json').json()
    assert patched['default_ttl_seconds'] == 900
    assert patched['bypass_paths'] == ['/admin', '/api/private']  # untouched by the patch


def test_bypass_paths_are_deduplicated(alice_client, domain):
    response = alice_client.patch(cache_url(domain), {'bypass_paths': ['/a', '/b', '/a']}, format='json')

    assert response.json()['bypass_paths'] == ['/a', '/b']


@pytest.mark.parametrize('payload', [
    {'default_ttl_seconds': -1},
    {'default_ttl_seconds': 31_536_001},
    {'serve_stale_seconds': 604_801},
    {'query_string_mode': 'regex'},
    {'bypass_paths': 'admin'},
    {'bypass_paths': ['admin']},
    {'bypass_paths': ['/has space']},
    {'bypass_paths': ['/x?y=1']},
    {'bypass_paths': [123]},
    {'bypass_paths': ['/p'] * 3 + [f'/p{i}' for i in range(60)]},
])
def test_cache_policy_validation(alice_client, domain, payload):
    response = alice_client.patch(cache_url(domain), payload, format='json')

    assert response.status_code == 400


# --- rate limit policy ----------------------------------------------------

def test_token_bucket_with_burst(alice_client, domain):
    response = alice_client.patch(rate_url(domain), {
        'enabled': True, 'algorithm': 'token_bucket', 'limit': 10, 'window_seconds': 1, 'burst': 50,
    }, format='json')

    assert response.status_code == 200
    assert response.json()['refill_rate_per_second'] == 10.0
    assert RateLimitPolicy.objects.get(domain=domain).capacity == 50


def test_burst_defaults_to_limit_when_unset(alice_client, domain):
    alice_client.get(rate_url(domain))

    assert RateLimitPolicy.objects.get(domain=domain).capacity == 100


def test_sliding_window_rejects_burst(alice_client, domain):
    response = alice_client.patch(
        rate_url(domain), {'algorithm': 'sliding_window', 'burst': 20}, format='json'
    )

    assert response.status_code == 400
    assert 'burst' in response.json()


def test_patch_validation_uses_the_stored_value_for_missing_fields(alice_client, domain):
    alice_client.patch(rate_url(domain), {'burst': 20}, format='json')

    # Switching algorithm alone must fail: the stored burst is still set.
    assert alice_client.patch(rate_url(domain), {'algorithm': 'sliding_window'}, format='json').status_code == 400
    # Clearing burst in the same request makes it valid.
    assert alice_client.patch(
        rate_url(domain), {'algorithm': 'sliding_window', 'burst': None}, format='json'
    ).status_code == 200


@pytest.mark.parametrize('payload', [
    {'limit': 0},
    {'limit': 1_000_001},
    {'window_seconds': 0},
    {'window_seconds': 86_401},
    {'burst': 0},
    {'algorithm': 'leaky_bucket'},
    {'scope': 'per_country'},
    {'action': 'ban'},
])
def test_rate_limit_validation(alice_client, domain, payload):
    assert alice_client.patch(rate_url(domain), payload, format='json').status_code == 400


# --- config snapshot ------------------------------------------------------

def test_config_snapshot_contains_active_origins_and_policies(alice_client, domain):
    Origin.objects.create(domain=domain, host='a.example.com')
    Origin.objects.create(domain=domain, host='b.example.com', is_active=False)

    body = alice_client.get(f'/api/domains/{domain.id}/config/').json()

    assert body['domain'] == 'www.example.com'
    assert [o['host'] for o in body['origins']] == ['a.example.com']
    assert body['cache']['default_ttl_seconds'] == 3600
    assert body['rate_limit']['enabled'] is False
    assert body['version']


def test_config_version_changes_when_a_policy_changes(alice_client, domain):
    url = f'/api/domains/{domain.id}/config/'
    before = alice_client.get(url).json()['version']

    alice_client.patch(rate_url(domain), {'enabled': True}, format='json')

    assert alice_client.get(url).json()['version'] > before
