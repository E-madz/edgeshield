"""Redis-backed limiters shared by every app instance.

The read-modify-write cycle MUST be atomic. Doing GET, compute, SET from Python
lets two concurrent requests read the same state and both be allowed (a race).
A Lua script runs as one indivisible step inside Redis, so that cannot happen.
"""

import math
import time
import uuid

from .base import Decision

# KEYS[1] = bucket key; ARGV = capacity, refill/sec, now (sec), cost
TOKEN_BUCKET_LUA = """
local capacity = tonumber(ARGV[1])
local rate     = tonumber(ARGV[2])
local now      = tonumber(ARGV[3])
local cost     = tonumber(ARGV[4])

local state  = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts     = tonumber(state[2])
if tokens == nil then
  tokens = capacity
  ts = now
end

local elapsed = math.max(0, now - ts)
tokens = math.min(capacity, tokens + elapsed * rate)

local allowed = 0
local retry   = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry = (cost - tokens) / rate
end

redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(math.max(ts, now)))
-- Forget idle clients: after twice the full-refill time the bucket is full anyway.
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / rate * 2) + 1)
return {allowed, tostring(tokens), tostring(retry)}
"""

# KEYS[1] = sorted set of request times; ARGV = limit, window (sec), now (sec), cost, member prefix
SLIDING_WINDOW_LUA = """
local limit  = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local now    = tonumber(ARGV[3])
local cost   = tonumber(ARGV[4])

redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local count = redis.call('ZCARD', KEYS[1])

if count + cost <= limit then
  for i = 1, cost do
    redis.call('ZADD', KEYS[1], now, ARGV[5] .. ':' .. i)
  end
  redis.call('EXPIRE', KEYS[1], math.ceil(window) + 1)
  return {1, tostring(limit - count - cost), '0'}
end

local needed = count + cost - limit
local boundary = redis.call('ZRANGE', KEYS[1], needed - 1, needed - 1, 'WITHSCORES')
local retry = tonumber(boundary[2]) + window - now
if retry < 0 then retry = 0 end
return {0, tostring(math.max(0, limit - count)), tostring(retry)}
"""


def _decision(result):
    allowed, remaining, retry_after = result
    return Decision(
        allowed=int(allowed) == 1,
        remaining=max(0, int(float(remaining))),
        retry_after=float(retry_after),
    )


class RedisTokenBucket:
    def __init__(self, client, capacity, refill_per_second, prefix='rl:tb', clock=time.time):
        if capacity < 1 or refill_per_second <= 0:
            raise ValueError('capacity must be >= 1 and refill rate > 0')
        self.capacity = capacity
        self.rate = refill_per_second
        self._prefix = prefix
        self._clock = clock
        self._script = client.register_script(TOKEN_BUCKET_LUA)

    def key(self, name):
        return f'{self._prefix}:{name}'

    def allow(self, name, cost=1, now=None):
        if cost < 1 or cost > self.capacity:
            raise ValueError('cost must be between 1 and the capacity')
        now = self._clock() if now is None else now
        result = self._script(keys=[self.key(name)], args=[self.capacity, self.rate, now, cost])
        return _decision(result)


class RedisSlidingWindow:
    def __init__(self, client, limit, window_seconds, prefix='rl:sw', clock=time.time):
        if limit < 1 or window_seconds <= 0:
            raise ValueError('limit must be >= 1 and window > 0')
        self.limit = limit
        self.window = window_seconds
        self._prefix = prefix
        self._clock = clock
        self._script = client.register_script(SLIDING_WINDOW_LUA)

    def key(self, name):
        return f'{self._prefix}:{name}'

    def allow(self, name, cost=1, now=None):
        if cost < 1 or cost > self.limit:
            raise ValueError('cost must be between 1 and the limit')
        now = self._clock() if now is None else now
        # Sorted-set members must be unique or two requests at the same instant collapse into one.
        member = uuid.uuid4().hex
        result = self._script(
            keys=[self.key(name)], args=[self.limit, self.window, now, cost, member]
        )
        return _decision(result)
