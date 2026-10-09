import logging
import random

from celery import shared_task
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from redis.exceptions import RedisError

from edge.purge import EdgeCacheStore
from edge.redis_client import get_redis

from .models import PurgeJob

logger = logging.getLogger(__name__)

MAX_RETRIES = 5


def retry_delay(retries):
    """Exponential backoff with jitter: ~2s, 4s, 8s ... capped at 60s.

    Jitter spreads retries out so that many jobs failing together (Redis was
    down) do not all hit the recovering service at the same instant.
    """
    ceiling = min(60, 2 ** (retries + 1))
    return ceiling / 2 + random.uniform(0, ceiling / 2)


def enqueue_purge(job_id):
    """Hand a committed job to the broker. Never raises: a broker outage is recorded on the job."""
    try:
        run_purge_job.delay(job_id)
    except Exception as exc:  # broker unreachable, serialization problem, ...
        logger.exception('Could not enqueue purge job %s', job_id)
        PurgeJob.objects.filter(pk=job_id).update(
            status=PurgeJob.Status.FAILED,
            error=f'enqueue failed: {exc.__class__.__name__}',
            finished_at=timezone.now(),
        )


def _fail(job_id, message):
    PurgeJob.objects.filter(pk=job_id).update(
        status=PurgeJob.Status.FAILED, error=message[:500], finished_at=timezone.now()
    )


@shared_task(bind=True, max_retries=MAX_RETRIES)
def run_purge_job(self, job_id):
    # Short transaction: claim the job, then do the slow work OUTSIDE it so no
    # database lock is held while we talk to Redis.
    with transaction.atomic():
        job = PurgeJob.objects.select_for_update().filter(pk=job_id).first()
        if job is None:
            logger.warning('Purge job %s no longer exists', job_id)
            return
        if job.status in (PurgeJob.Status.SUCCEEDED, PurgeJob.Status.FAILED):
            return  # duplicate or redelivered message: already finished, do nothing
        job.status = PurgeJob.Status.RUNNING
        job.attempts = F('attempts') + 1
        job.started_at = job.started_at or timezone.now()
        job.save(update_fields=['status', 'attempts', 'started_at'])
        job.refresh_from_db()

    try:
        store = EdgeCacheStore(get_redis())
        if job.kind == PurgeJob.Kind.ALL:
            purged = store.purge_all(job.domain_id)
        else:
            purged = store.purge_paths(job.domain_id, job.paths)
    except RedisError as exc:
        # Transient: the operation is idempotent, so trying again is always safe.
        if self.request.retries >= self.max_retries:
            _fail(job_id, f'gave up after {job.attempts} attempts: {exc.__class__.__name__}')
            return
        PurgeJob.objects.filter(pk=job_id).update(
            status=PurgeJob.Status.RETRYING, error=f'{exc.__class__.__name__}: will retry'
        )
        raise self.retry(exc=exc, countdown=retry_delay(self.request.retries)) from exc
    except Exception as exc:
        # Permanent (a bug, bad data): retrying would just repeat the failure.
        logger.exception('Purge job %s failed permanently', job_id)
        _fail(job_id, f'{exc.__class__.__name__}: {exc}')
        return

    PurgeJob.objects.filter(pk=job_id).update(
        status=PurgeJob.Status.SUCCEEDED, purged_count=purged, error='', finished_at=timezone.now()
    )
