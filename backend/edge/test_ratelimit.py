"""Behaviour is specified once and run against the in-memory AND the Redis
implementation (fakeredis), so both must give identical answers."""

import os
import threading

import fakeredis
import pytest
import redis

from edge.ratelimit import (
    InMemorySlidingWindow,
    InMemoryTokenBucket,
    RedisSlidingWindow,
    RedisTokenBucket,
)


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis()


@pytest.fixture(params=['memory', 'redis'])
def token_bucket(request, fake_redis):
    def factory(capacity, rate):
        if request.param == 'memory':
            return InMemoryTokenBucket(capacity, rate)
        return RedisTokenBucket(fake_redis, capacity, rate)
    return factory


@pytest.fixture(params=['memory', 'redis'])
def sliding_window(request, fake_redis):
    def factory(limit, window):
        if request.param == 'memory':
            return InMemorySlidingWindow(limit, window)
        return RedisSlidingWindow(fake_redis, limit, window)
    return factory


# --- token bucket ---------------------------------------------------------

def test_bucket_allows_a_burst_up_to_capacity_then_blocks(token_bucket):
    limiter = token_bucket(capacity=5, rate=1)

    decisions = [limiter.allow('ip', now=100.0) for _ in range(7)]

    assert [d.allowed for d in decisions] == [True] * 5 + [False] * 2
    assert [d.remaining for d in decisions[:5]] == [4, 3, 2, 1, 0]


def test_bucket_retry_after_tells_when_a_token_is_available(token_bucket):
    limiter = token_bucket(capacity=1, rate=2)  # one token every 0.5 s
    limiter.allow('ip', now=0.0)

    blocked = limiter.allow('ip', now=0.1)

    assert blocked.allowed is False
    assert blocked.retry_after == pytest.approx(0.4)


def test_bucket_refills_over_time_but_never_above_capacity(token_bucket):
    limiter = token_bucket(capacity=3, rate=1)
    for _ in range(3):
        limiter.allow('ip', now=0.0)
    assert limiter.allow('ip', now=0.0).allowed is False

    assert limiter.allow('ip', now=1.0).allowed is True      # one token came back
    assert limiter.allow('ip', now=1.0).allowed is False
    after_long_idle = [limiter.allow('ip', now=1000.0).allowed for _ in range(5)]
    assert after_long_idle == [True, True, True, False, False]  # capped at 3, not 1000


def test_bucket_keys_are_independent(token_bucket):
    limiter = token_bucket(capacity=1, rate=1)

    assert limiter.allow('a', now=0.0).allowed is True
    assert limiter.allow('a', now=0.0).allowed is False
    assert limiter.allow('b', now=0.0).allowed is True


def test_bucket_survives_a_clock_that_goes_backwards(token_bucket):
    limiter = token_bucket(capacity=2, rate=1)
    limiter.allow('ip', now=100.0)
    limiter.allow('ip', now=100.0)

    # Another server with a slightly slow clock must not mint free tokens.
    assert limiter.allow('ip', now=99.0).allowed is False


def test_bucket_rejects_cost_above_capacity(token_bucket):
    with pytest.raises(ValueError):
        token_bucket(capacity=2, rate=1).allow('ip', cost=3, now=0.0)


# --- sliding window -------------------------------------------------------

def test_window_allows_limit_requests_then_blocks(sliding_window):
    limiter = sliding_window(limit=3, window=10)

    decisions = [limiter.allow('ip', now=float(i)) for i in range(5)]

    assert [d.allowed for d in decisions] == [True, True, True, False, False]
    assert [d.remaining for d in decisions[:3]] == [2, 1, 0]


def test_window_retry_after_is_when_the_oldest_request_expires(sliding_window):
    limiter = sliding_window(limit=2, window=10)
    limiter.allow('ip', now=0.0)
    limiter.allow('ip', now=4.0)

    blocked = limiter.allow('ip', now=6.0)

    assert blocked.allowed is False
    assert blocked.retry_after == pytest.approx(4.0)  # oldest (t=0) leaves the window at t=10


def test_window_slides_instead_of_resetting_at_fixed_boundaries(sliding_window):
    limiter = sliding_window(limit=2, window=10)
    limiter.allow('ip', now=9.0)
    limiter.allow('ip', now=9.5)

    # A fixed-window counter would reset at t=10 and allow a double burst here.
    assert limiter.allow('ip', now=10.0).allowed is False
    assert limiter.allow('ip', now=19.0).allowed is True   # the t=9 request has just expired
    assert limiter.allow('ip', now=19.1).allowed is False  # t=9.5 and t=19 still count


def test_window_counts_same_instant_requests_separately(sliding_window):
    limiter = sliding_window(limit=3, window=10)

    assert [limiter.allow('ip', now=5.0).allowed for _ in range(4)] == [True, True, True, False]


def test_window_keys_are_independent(sliding_window):
    limiter = sliding_window(limit=1, window=10)

    assert limiter.allow('a', now=0.0).allowed is True
    assert limiter.allow('b', now=0.0).allowed is True
    assert limiter.allow('a', now=0.0).allowed is False


def test_window_rejects_cost_above_limit(sliding_window):
    with pytest.raises(ValueError):
        sliding_window(limit=2, window=10).allow('ip', cost=3, now=0.0)


# --- redis specifics ------------------------------------------------------

def test_redis_keys_expire_so_idle_clients_leave_no_garbage(fake_redis):
    bucket = RedisTokenBucket(fake_redis, capacity=10, refill_per_second=1)
    window = RedisSlidingWindow(fake_redis, limit=10, window_seconds=30)
    bucket.allow('ip', now=0.0)
    window.allow('ip', now=0.0)

    assert 0 < fake_redis.ttl(bucket.key('ip')) <= 21
    assert 0 < fake_redis.ttl(window.key('ip')) <= 31


def test_redis_state_is_shared_between_limiter_instances(fake_redis):
    first = RedisTokenBucket(fake_redis, capacity=2, refill_per_second=1)
    second = RedisTokenBucket(fake_redis, capacity=2, refill_per_second=1)  # "another app server"

    assert first.allow('ip', now=0.0).allowed is True
    assert second.allow('ip', now=0.0).allowed is True
    assert first.allow('ip', now=0.0).allowed is False


# --- real Redis: atomicity under concurrency ------------------------------

@pytest.fixture
def real_redis():
    client = redis.Redis.from_url(os.environ.get('TEST_REDIS_URL', 'redis://localhost:6379/15'))
    try:
        client.ping()
    except redis.RedisError:
        pytest.skip('no real Redis available (set TEST_REDIS_URL to run)')
    client.flushdb()
    yield client
    client.flushdb()


@pytest.mark.parametrize('kind', ['bucket', 'window'])
def test_exactly_limit_requests_pass_under_concurrency(real_redis, kind):
    limit = 25
    if kind == 'bucket':
        limiter = RedisTokenBucket(real_redis, capacity=limit, refill_per_second=0.0001)
    else:
        limiter = RedisSlidingWindow(real_redis, limit=limit, window_seconds=60)
    results, lock = [], threading.Lock()

    def worker():
        for _ in range(20):
            decision = limiter.allow('shared')
            with lock:
                results.append(decision.allowed)

    threads = [threading.Thread(target=worker) for _ in range(10)]  # 200 racing requests
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 200
    assert sum(results) == limit  # a non-atomic GET/SET implementation would let extra requests through
