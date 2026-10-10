from functools import lru_cache

import redis
from django.conf import settings


@lru_cache(maxsize=1)
def get_redis():
    """One shared client per process; redis-py manages a thread-safe connection pool."""
    return redis.Redis.from_url(
        settings.REDIS_URL, socket_timeout=1, socket_connect_timeout=1
    )
