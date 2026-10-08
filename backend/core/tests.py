from unittest import mock

import pytest
from django.db import OperationalError
from rest_framework.test import APIClient


@pytest.fixture
def client():
    return APIClient()


@pytest.mark.django_db
def test_readiness_ok_when_database_is_up(client):
    response = client.get('/api/health/')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok', 'database': 'up'}


def test_readiness_returns_503_when_database_is_down(client):
    with mock.patch('core.views.connection') as fake_connection:
        fake_connection.cursor.side_effect = OperationalError('db down')
        response = client.get('/api/health/')

    assert response.status_code == 503
    assert response.json() == {'status': 'unavailable', 'database': 'down'}


def test_liveness_never_touches_the_database(client):
    with mock.patch('core.views.connection') as fake_connection:
        response = client.get('/api/health/live/')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
    fake_connection.cursor.assert_not_called()
