from unittest import mock

import fakeredis
import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework.test import APIClient

from config.celery import app as celery_app
from edge.purge import EdgeCacheStore

from .models import Domain, PurgeJob
from .tasks import MAX_RETRIES, retry_delay, run_purge_job

User = get_user_model()
PASSWORD = 'S3cure-pass-phrase!'


@pytest.fixture(autouse=True)
def eager_celery():
    """Run tasks synchronously in the calling process (no broker or worker needed)."""
    previous = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    yield
    celery_app.conf.task_always_eager = previous


@pytest.fixture
def fake_redis(monkeypatch):
    server = fakeredis.FakeRedis()
    for target in ('domains.views.get_redis', 'domains.tasks.get_redis'):
        monkeypatch.setattr(target, lambda: server)
    return server


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
def store(fake_redis):
    return EdgeCacheStore(fake_redis)


def purges_url(d):
    return f'/api/domains/{d.id}/purges/'


@pytest.fixture
def post_purge(client, django_capture_on_commit_callbacks):
    """POST a purge and run the on_commit hook (which enqueues the task) like a real commit would."""
    def post(domain, body, key=None):
        extra = {'HTTP_IDEMPOTENCY_KEY': key} if key else {}
        with django_capture_on_commit_callbacks(execute=True):
            return client.post(purges_url(domain), body, format='json', **extra)
    return post


# --- happy path -----------------------------------------------------------

def test_purge_all_is_accepted_then_executed(post_purge, client, domain, store):
    store.put(domain.pk, '/a')
    store.put(domain.pk, '/b')

    response = post_purge(domain, {'kind': 'all'})

    assert response.status_code == 202
    assert response['Location'].endswith(f'/api/domains/{domain.id}/purges/{response.json()["id"]}/')
    job = client.get(response['Location']).json()
    assert job['status'] == 'succeeded' and job['purged_count'] == 2 and job['attempts'] == 1
    assert store.list_paths(domain.pk) == []


def test_purge_paths_and_prefixes_leave_other_objects_alone(post_purge, domain, store):
    for path in ('/img/a.png', '/blog/1', '/blog/2', '/keep'):
        store.put(domain.pk, path)

    response = post_purge(domain, {'kind': 'paths', 'paths': ['/img/a.png', '/blog/*']})

    assert response.status_code == 202
    assert PurgeJob.objects.get().purged_count == 3
    assert store.list_paths(domain.pk) == ['/keep']


def test_job_stays_pending_until_a_worker_picks_it_up(client, domain, store):
    store.put(domain.pk, '/a')

    response = client.post(purges_url(domain), {'kind': 'all'}, format='json')  # callbacks NOT executed

    assert response.json()['status'] == 'pending'
    assert store.list_paths(domain.pk) == ['/a']


# --- request validation ---------------------------------------------------

@pytest.mark.parametrize('body', [
    {},
    {'kind': 'everything'},
    {'kind': 'paths'},
    {'kind': 'paths', 'paths': []},
    {'kind': 'all', 'paths': ['/a']},
    {'kind': 'paths', 'paths': ['no-slash']},
    {'kind': 'paths', 'paths': ['/a b']},
    {'kind': 'paths', 'paths': ['/a?x=1']},
    {'kind': 'paths', 'paths': ['/a*b']},
    {'kind': 'paths', 'paths': ['/a[1]']},
    {'kind': 'paths', 'paths': [f'/p{i}' for i in range(101)]},
])
def test_purge_validation(post_purge, domain, body):
    assert post_purge(domain, body).status_code == 400
    assert PurgeJob.objects.count() == 0


# --- idempotency ----------------------------------------------------------

def test_same_key_and_body_returns_the_original_job(post_purge, domain):
    first = post_purge(domain, {'kind': 'paths', 'paths': ['/a', '/b']}, key='order-42')
    again = post_purge(domain, {'kind': 'paths', 'paths': ['/b', '/a', '/a']}, key='order-42')  # same request, reordered

    assert first.status_code == 202 and again.status_code == 200
    assert again['Idempotent-Replayed'] == 'true'
    assert again.json()['id'] == first.json()['id']
    assert PurgeJob.objects.count() == 1


def test_same_key_with_a_different_body_is_a_conflict(post_purge, domain):
    post_purge(domain, {'kind': 'paths', 'paths': ['/a']}, key='k1')

    response = post_purge(domain, {'kind': 'all'}, key='k1')

    assert response.status_code == 409
    assert PurgeJob.objects.count() == 1


def test_without_a_key_every_post_is_a_new_job(post_purge, domain):
    post_purge(domain, {'kind': 'all'})
    post_purge(domain, {'kind': 'all'})

    assert PurgeJob.objects.count() == 2


def test_keys_are_scoped_to_the_domain(post_purge, alice, domain):
    other = Domain.objects.create(owner=alice, name='other.example.com')

    assert post_purge(domain, {'kind': 'all'}, key='same').status_code == 202
    assert post_purge(other, {'kind': 'all'}, key='same').status_code == 202


@pytest.mark.parametrize('key', ['has space', 'x' * 65, 'emoji-✓'])
def test_malformed_idempotency_key(post_purge, domain, key):
    assert post_purge(domain, {'kind': 'all'}, key=key).status_code == 400


def test_replay_works_even_when_the_domain_is_busy(post_purge, domain):
    first = post_purge(domain, {'kind': 'all'}, key='k')
    PurgeJob.objects.bulk_create(
        [PurgeJob(domain=domain, kind='all', status='pending') for _ in range(5)]
    )

    assert post_purge(domain, {'kind': 'all'}, key='k').status_code == 200
    assert first.status_code == 202


def test_database_constraint_enforces_key_uniqueness(domain):
    PurgeJob.objects.create(domain=domain, kind='all', idempotency_key='dup')

    with pytest.raises(IntegrityError), transaction.atomic():
        PurgeJob.objects.create(domain=domain, kind='all', idempotency_key='dup')


def test_many_jobs_without_a_key_are_allowed(domain):
    PurgeJob.objects.create(domain=domain, kind='all')
    PurgeJob.objects.create(domain=domain, kind='all')

    assert PurgeJob.objects.count() == 2


# --- back-pressure, access control, failures at enqueue time ---------------

def test_too_many_active_jobs_returns_429_with_retry_after(post_purge, domain):
    PurgeJob.objects.bulk_create(
        [PurgeJob(domain=domain, kind='all', status='running') for _ in range(5)]
    )

    response = post_purge(domain, {'kind': 'all'})

    assert response.status_code == 429 and response['Retry-After'] == '5'


def test_finished_jobs_do_not_count_as_active(post_purge, domain):
    PurgeJob.objects.bulk_create(
        [PurgeJob(domain=domain, kind='all', status='succeeded') for _ in range(5)]
    )

    assert post_purge(domain, {'kind': 'all'}).status_code == 202


def test_other_users_cannot_see_or_create_jobs(bob_client, client, post_purge, domain):
    job_id = post_purge(domain, {'kind': 'all'}).json()['id']

    assert bob_client.post(purges_url(domain), {'kind': 'all'}, format='json').status_code == 404
    assert bob_client.get(purges_url(domain)).status_code == 404
    assert bob_client.get(f'{purges_url(domain)}{job_id}/').status_code == 404


def test_endpoints_require_authentication(domain):
    assert APIClient().post(purges_url(domain), {'kind': 'all'}, format='json').status_code == 401


def test_job_must_belong_to_the_domain_in_the_url(client, alice, domain, post_purge):
    job_id = post_purge(domain, {'kind': 'all'}).json()['id']
    other = Domain.objects.create(owner=alice, name='other.example.com')

    assert client.get(f'{purges_url(other)}{job_id}/').status_code == 404


def test_broker_outage_is_reported_and_recorded(client, domain, django_capture_on_commit_callbacks):
    with mock.patch('domains.tasks.run_purge_job.delay', side_effect=OSError('broker down')):
        with django_capture_on_commit_callbacks(execute=True):
            client.post(purges_url(domain), {'kind': 'all'}, format='json')
        job = PurgeJob.objects.get()

    assert job.status == 'failed' and job.error.startswith('enqueue failed')


def test_list_is_paginated_and_filterable(client, domain):
    PurgeJob.objects.bulk_create(
        [PurgeJob(domain=domain, kind='all', status='succeeded') for _ in range(3)]
        + [PurgeJob(domain=domain, kind='all', status='failed')]
    )

    assert client.get(purges_url(domain)).json()['count'] == 4
    assert client.get(purges_url(domain) + '?status=failed').json()['count'] == 1


# --- the task itself ------------------------------------------------------

def test_running_a_finished_job_again_does_nothing(domain, store):
    job = PurgeJob.objects.create(domain=domain, kind='all', status='succeeded', attempts=1, purged_count=5)
    store.put(domain.pk, '/new')

    run_purge_job.apply(args=[job.pk])  # a duplicate / redelivered message

    job.refresh_from_db()
    assert job.attempts == 1 and job.purged_count == 5
    assert store.list_paths(domain.pk) == ['/new']  # the duplicate did not purge again


def test_failed_jobs_are_not_resurrected(domain):
    job = PurgeJob.objects.create(domain=domain, kind='all', status='failed', attempts=3)

    run_purge_job.apply(args=[job.pk])

    job.refresh_from_db()
    assert job.status == 'failed' and job.attempts == 3


def test_a_deleted_job_is_ignored(db):
    assert run_purge_job.apply(args=[999_999]).successful()


def test_transient_redis_errors_are_retried_until_success(domain, store):
    job = PurgeJob.objects.create(domain=domain, kind='all')
    real = EdgeCacheStore.purge_all
    calls = {'n': 0}

    def flaky(self, domain_id):
        calls['n'] += 1
        if calls['n'] < 3:
            raise RedisConnectionError('redis blip')
        return real(self, domain_id)

    store.put(domain.pk, '/a')
    with mock.patch.object(EdgeCacheStore, 'purge_all', flaky):
        run_purge_job.apply(args=[job.pk])

    job.refresh_from_db()
    assert job.status == 'succeeded' and job.attempts == 3 and job.purged_count == 1
    assert job.error == ''


def test_giving_up_after_max_retries(domain):
    job = PurgeJob.objects.create(domain=domain, kind='all')

    with mock.patch.object(EdgeCacheStore, 'purge_all', side_effect=RedisConnectionError('down')):
        run_purge_job.apply(args=[job.pk])

    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.attempts == MAX_RETRIES + 1  # first try + MAX_RETRIES retries
    assert job.error.startswith('gave up')


def test_permanent_errors_are_not_retried(domain):
    job = PurgeJob.objects.create(domain=domain, kind='all')

    with mock.patch.object(EdgeCacheStore, 'purge_all', side_effect=ValueError('bug')):
        run_purge_job.apply(args=[job.pk])

    job.refresh_from_db()
    assert job.status == 'failed' and job.attempts == 1 and 'ValueError' in job.error


@pytest.mark.parametrize('retries', range(0, 10))
def test_backoff_grows_then_is_capped(retries):
    ceiling = min(60, 2 ** (retries + 1))

    for _ in range(50):
        assert ceiling / 2 <= retry_delay(retries) <= ceiling


# --- demo cache objects endpoint -------------------------------------------

def test_seed_and_list_cache_objects(client, domain, fake_redis):
    assert client.post(f'/api/domains/{domain.id}/cache-objects/', {'paths': ['/b', '/a', '/a']}, format='json').status_code == 201

    body = client.get(f'/api/domains/{domain.id}/cache-objects/').json()

    assert body == {'count': 2, 'paths': ['/a', '/b']}


@pytest.mark.parametrize('paths', [[], ['/blog/*'], ['nope'], ['/a b']])
def test_seed_validation(client, domain, fake_redis, paths):
    response = client.post(f'/api/domains/{domain.id}/cache-objects/', {'paths': paths}, format='json')

    assert response.status_code == 400


def test_cache_objects_owner_only_and_redis_down(bob_client, client, domain, monkeypatch):
    assert bob_client.get(f'/api/domains/{domain.id}/cache-objects/').status_code == 404

    class Down:
        def scan_iter(self, *a, **k):
            raise RedisConnectionError('down')

    monkeypatch.setattr('domains.views.get_redis', lambda: Down())
    assert client.get(f'/api/domains/{domain.id}/cache-objects/').status_code == 503
