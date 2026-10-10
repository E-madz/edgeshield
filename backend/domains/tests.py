import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from .models import Domain, Origin

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


# --- authentication -------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('url', ['/api/domains/', '/api/domains/1/', '/api/domains/1/origins/'])
def test_endpoints_require_authentication(url):
    assert APIClient().get(url).status_code == 401


# --- domains: create / validate -------------------------------------------

def test_create_domain_assigns_owner_from_request_not_payload(alice_client, alice, bob):
    response = alice_client.post('/api/domains/', {'name': 'Shop.Example.com', 'owner': bob.id})

    assert response.status_code == 201
    body = response.json()
    assert body['name'] == 'shop.example.com'
    assert body['origins_count'] == 0
    assert Domain.objects.get(pk=body['id']).owner == alice


@pytest.mark.parametrize(
    'name', ['localhost', 'http://example.com', 'exa mple.com', '-bad.example.com', 'a..b.com', '']
)
def test_create_domain_rejects_invalid_hostnames(alice_client, name):
    response = alice_client.post('/api/domains/', {'name': name})

    assert response.status_code == 400
    assert 'name' in response.json()


def test_create_domain_rejects_duplicates_across_users(alice_client, bob_client, domain):
    response = bob_client.post('/api/domains/', {'name': 'WWW.example.com'})

    assert response.status_code == 400
    assert 'name' in response.json()


# --- domains: ownership isolation -----------------------------------------

def test_list_only_shows_own_domains(alice_client, bob, domain):
    Domain.objects.create(owner=bob, name='bobs.example.com')

    body = alice_client.get('/api/domains/').json()

    assert body['count'] == 1
    assert body['results'][0]['name'] == 'www.example.com'


@pytest.mark.parametrize('method', ['get', 'patch', 'delete'])
def test_other_users_domain_returns_404(bob_client, domain, method):
    response = getattr(bob_client, method)(f'/api/domains/{domain.id}/')

    assert response.status_code == 404
    assert Domain.objects.filter(pk=domain.pk).exists()


def test_update_domain(alice_client, domain):
    response = alice_client.patch(f'/api/domains/{domain.id}/', {'is_active': False})

    assert response.status_code == 200
    domain.refresh_from_db()
    assert domain.is_active is False


def test_rename_to_own_current_name_is_allowed(alice_client, domain):
    response = alice_client.patch(f'/api/domains/{domain.id}/', {'name': 'www.example.com'})

    assert response.status_code == 200


def test_delete_domain_cascades_to_origins(alice_client, domain):
    Origin.objects.create(domain=domain, host='origin.example.com')

    assert alice_client.delete(f'/api/domains/{domain.id}/').status_code == 204
    assert Origin.objects.count() == 0


# --- pagination / search / ordering ---------------------------------------

def test_list_is_paginated_with_capped_page_size(alice_client, alice):
    Domain.objects.bulk_create(
        [Domain(owner=alice, name=f'site{i}.example.com') for i in range(25)]
    )

    first = alice_client.get('/api/domains/').json()
    assert first['count'] == 25
    assert len(first['results']) == 20
    assert first['next'] is not None

    second = alice_client.get('/api/domains/?page=2').json()
    assert len(second['results']) == 5

    capped = alice_client.get('/api/domains/?page_size=1000').json()
    assert len(capped['results']) == 25  # 25 rows < the 100 cap
    assert alice_client.get('/api/domains/?page=99').status_code == 404


def test_search_and_ordering(alice_client, alice):
    for name in ('b.example.com', 'a.example.com', 'c.other.org'):
        Domain.objects.create(owner=alice, name=name)

    searched = alice_client.get('/api/domains/?search=example').json()
    assert {d['name'] for d in searched['results']} == {'a.example.com', 'b.example.com'}

    ordered = alice_client.get('/api/domains/?ordering=name').json()
    assert [d['name'] for d in ordered['results']] == ['a.example.com', 'b.example.com', 'c.other.org']


# --- N+1: the number of queries must not grow with the number of rows ------

def _count_queries(client, url):
    with CaptureQueriesContext(connection) as ctx:
        assert client.get(url).status_code == 200
    return len(ctx)


def test_domain_list_query_count_is_constant(alice_client, alice):
    def add_domains(n, start):
        for i in range(start, start + n):
            d = Domain.objects.create(owner=alice, name=f'site{i}.example.com')
            Origin.objects.bulk_create(
                [Origin(domain=d, host=f'o{j}.example.com') for j in range(3)]
            )

    add_domains(2, 0)
    few = _count_queries(alice_client, '/api/domains/')
    add_domains(15, 2)
    many = _count_queries(alice_client, '/api/domains/')

    assert few == many


def test_domain_detail_query_count_is_constant(alice_client, alice):
    d1 = Domain.objects.create(owner=alice, name='one.example.com')
    d2 = Domain.objects.create(owner=alice, name='two.example.com')
    Origin.objects.create(domain=d1, host='a.example.com')
    Origin.objects.bulk_create([Origin(domain=d2, host=f'o{i}.example.com') for i in range(10)])

    assert _count_queries(alice_client, f'/api/domains/{d1.id}/') == \
        _count_queries(alice_client, f'/api/domains/{d2.id}/')


# --- origins --------------------------------------------------------------

def test_create_and_list_origins(alice_client, domain):
    response = alice_client.post(
        f'/api/domains/{domain.id}/origins/',
        {'host': 'Origin.Example.com', 'port': 8443, 'scheme': 'https', 'weight': 5},
    )

    assert response.status_code == 201
    assert response.json()['host'] == 'origin.example.com'
    listed = alice_client.get(f'/api/domains/{domain.id}/origins/').json()
    assert listed['count'] == 1
    detail = alice_client.get(f'/api/domains/{domain.id}/').json()
    assert [o['host'] for o in detail['origins']] == ['origin.example.com']


def test_origin_accepts_ip_address(alice_client, domain):
    response = alice_client.post(f'/api/domains/{domain.id}/origins/', {'host': '203.0.113.10'})

    assert response.status_code == 201


@pytest.mark.parametrize('payload', [
    {'host': 'not a host'},
    {'host': 'origin.example.com', 'port': 0},
    {'host': 'origin.example.com', 'port': 70000},
    {'host': 'origin.example.com', 'scheme': 'ftp'},
    {'host': 'origin.example.com', 'weight': 0},
    {'host': 'origin.example.com', 'weight': 101},
])
def test_origin_validation(alice_client, domain, payload):
    response = alice_client.post(f'/api/domains/{domain.id}/origins/', payload)

    assert response.status_code == 400


def test_duplicate_origin_is_rejected(alice_client, domain):
    Origin.objects.create(domain=domain, host='origin.example.com', port=443)

    response = alice_client.post(
        f'/api/domains/{domain.id}/origins/', {'host': 'origin.example.com', 'port': 443}
    )

    assert response.status_code == 400
    # same host on another port is a different origin
    assert alice_client.post(
        f'/api/domains/{domain.id}/origins/', {'host': 'origin.example.com', 'port': 8443}
    ).status_code == 201


def test_cannot_touch_origins_of_other_users_domain(bob_client, domain):
    origin = Origin.objects.create(domain=domain, host='origin.example.com')
    base = f'/api/domains/{domain.id}/origins/'

    assert bob_client.get(base).status_code == 404
    assert bob_client.post(base, {'host': 'evil.example.com'}).status_code == 404
    assert bob_client.delete(f'{base}{origin.id}/').status_code == 404
    assert Origin.objects.filter(pk=origin.pk).exists()


def test_origin_must_belong_to_the_domain_in_the_url(alice_client, alice, domain):
    other = Domain.objects.create(owner=alice, name='other.example.com')
    origin = Origin.objects.create(domain=other, host='origin.example.com')

    response = alice_client.get(f'/api/domains/{domain.id}/origins/{origin.id}/')

    assert response.status_code == 404


def test_update_and_delete_origin(alice_client, domain):
    origin = Origin.objects.create(domain=domain, host='origin.example.com')
    url = f'/api/domains/{domain.id}/origins/{origin.id}/'

    assert alice_client.patch(url, {'weight': 10}).json()['weight'] == 10
    assert alice_client.delete(url).status_code == 204
