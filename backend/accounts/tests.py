from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from rest_framework.test import APIClient

from core.throttling import ResilientScopedRateThrottle

User = get_user_model()

REGISTER_URL = '/api/auth/register/'
LOGIN_URL = '/api/auth/login/'
REFRESH_URL = '/api/auth/refresh/'
ME_URL = '/api/auth/me/'
PASSWORD = 'S3cure-pass-phrase!'


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(email='ali@example.com', password=PASSWORD)


# --- registration ---------------------------------------------------------

@pytest.mark.django_db
def test_register_creates_user_with_hashed_password(client):
    response = client.post(REGISTER_URL, {'email': 'Ali@Example.com', 'password': PASSWORD})

    assert response.status_code == 201
    assert response.json()['email'] == 'ali@example.com'
    assert 'password' not in response.json()
    created = User.objects.get(email='ali@example.com')
    assert created.password != PASSWORD
    assert created.check_password(PASSWORD)


@pytest.mark.django_db
def test_register_rejects_duplicate_email_case_insensitively(client, user):
    response = client.post(REGISTER_URL, {'email': 'ALI@example.com', 'password': PASSWORD})

    assert response.status_code == 400
    assert 'email' in response.json()


@pytest.mark.django_db
@pytest.mark.parametrize('password', ['short1', '12345678901', 'password123'])
def test_register_rejects_weak_passwords(client, password):
    response = client.post(REGISTER_URL, {'email': 'new@example.com', 'password': password})

    assert response.status_code == 400
    assert 'password' in response.json()


@pytest.mark.django_db
def test_register_turns_database_uniqueness_race_into_400(client):
    # Simulates two concurrent sign-ups: validation passes, the UNIQUE index rejects.
    with mock.patch.object(User.objects, 'create_user', side_effect=IntegrityError):
        response = client.post(REGISTER_URL, {'email': 'race@example.com', 'password': PASSWORD})

    assert response.status_code == 400
    assert 'email' in response.json()


# --- login / refresh ------------------------------------------------------

def test_login_returns_access_and_refresh_tokens(client, user):
    response = client.post(LOGIN_URL, {'email': 'ali@example.com', 'password': PASSWORD})

    assert response.status_code == 200
    assert set(response.json()) == {'access', 'refresh'}


def test_login_is_case_insensitive_on_email(client, user):
    response = client.post(LOGIN_URL, {'email': 'ALI@EXAMPLE.COM', 'password': PASSWORD})

    assert response.status_code == 200


def test_login_with_wrong_password_returns_401(client, user):
    response = client.post(LOGIN_URL, {'email': 'ali@example.com', 'password': 'wrong'})

    assert response.status_code == 401


def test_refresh_returns_new_access_token(client, user):
    tokens = client.post(LOGIN_URL, {'email': 'ali@example.com', 'password': PASSWORD}).json()

    response = client.post(REFRESH_URL, {'refresh': tokens['refresh']})

    assert response.status_code == 200
    assert 'access' in response.json()


def test_refresh_rejects_an_access_token(client, user):
    tokens = client.post(LOGIN_URL, {'email': 'ali@example.com', 'password': PASSWORD}).json()

    response = client.post(REFRESH_URL, {'refresh': tokens['access']})

    assert response.status_code == 401


# --- me (protected endpoint) ----------------------------------------------

def test_me_without_token_returns_401(client):
    assert client.get(ME_URL).status_code == 401


def test_me_with_garbage_token_returns_401(client):
    client.credentials(HTTP_AUTHORIZATION='Bearer not-a-real-token')

    assert client.get(ME_URL).status_code == 401


def test_me_rejects_a_refresh_token(client, user):
    tokens = client.post(LOGIN_URL, {'email': 'ali@example.com', 'password': PASSWORD}).json()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['refresh']}")

    assert client.get(ME_URL).status_code == 401


def test_me_with_valid_access_token_returns_the_user(client, user):
    tokens = client.post(LOGIN_URL, {'email': 'ali@example.com', 'password': PASSWORD}).json()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    response = client.get(ME_URL)

    assert response.status_code == 200
    assert response.json()['email'] == 'ali@example.com'
    assert 'password' not in response.json()


# --- throttling -----------------------------------------------------------

@pytest.fixture
def auth_rate(monkeypatch):
    """DRF reads the rates when the module is imported, so patch the class attribute."""
    def set_rate(rate):
        monkeypatch.setattr(ResilientScopedRateThrottle, 'THROTTLE_RATES', {'auth': rate})
    return set_rate


def test_login_is_throttled_per_client_ip(client, user, auth_rate):
    auth_rate('3/min')

    attempts = [
        client.post(LOGIN_URL, {'email': 'ali@example.com', 'password': 'wrong'}).status_code
        for _ in range(5)
    ]

    assert attempts == [401, 401, 401, 429, 429]


@pytest.mark.django_db
def test_register_is_throttled_too(client, auth_rate):
    auth_rate('2/min')

    codes = [
        client.post(REGISTER_URL, {'email': f'u{i}@example.com', 'password': PASSWORD}).status_code
        for i in range(3)
    ]

    assert codes == [201, 201, 429]


def test_token_refresh_is_not_throttled(client, user, auth_rate):
    auth_rate('1/min')
    tokens = client.post(LOGIN_URL, {'email': 'ali@example.com', 'password': PASSWORD}).json()

    codes = {client.post(REFRESH_URL, {'refresh': tokens['refresh']}).status_code for _ in range(4)}

    assert codes == {200}


def test_other_clients_have_their_own_budget(user, auth_rate):
    auth_rate('2/min')
    first, second = APIClient(), APIClient()
    body = {'email': 'ali@example.com', 'password': 'wrong'}

    for _ in range(2):
        first.post(LOGIN_URL, body)

    assert first.post(LOGIN_URL, body).status_code == 429
    assert second.post(LOGIN_URL, body, REMOTE_ADDR='198.51.100.7').status_code == 401


def test_login_still_works_when_the_throttle_cache_is_down(client, user, settings):
    # A real Redis cache pointed at a closed port: every cache call raises ConnectionError.
    settings.CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': 'redis://127.0.0.1:1/0',
        }
    }

    response = client.post(LOGIN_URL, {'email': 'ali@example.com', 'password': PASSWORD})

    assert response.status_code == 200
