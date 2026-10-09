from django.core.exceptions import ImproperlyConfigured

from .ratelimit import RedisSlidingWindow, RedisTokenBucket


def build_redis_limiter(policy, client, namespace='rl'):
    """Turn a stored RateLimitPolicy into a ready-to-use Redis limiter.

    The algorithm is part of the key prefix: a token bucket stores a hash and a
    sliding window a sorted set, and Redis raises WRONGTYPE if one overwrites
    the other after the policy switches algorithm.
    """
    if policy.algorithm == 'token_bucket':
        return RedisTokenBucket(
            client,
            capacity=policy.capacity,
            refill_per_second=policy.limit / policy.window_seconds,
            prefix=f'{namespace}:tb:{policy.domain_id}',
        )
    if policy.algorithm == 'sliding_window':
        return RedisSlidingWindow(
            client,
            limit=policy.limit,
            window_seconds=policy.window_seconds,
            prefix=f'{namespace}:sw:{policy.domain_id}',
        )
    raise ImproperlyConfigured(f'Unknown algorithm {policy.algorithm!r}')


def client_key(policy, client_ip):
    return 'global' if policy.scope == 'global' else f'ip:{client_ip}'
