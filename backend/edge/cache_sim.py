"""Deterministic edge-cache simulator driven by a CachePolicy.

It replays a list of (path, time) requests against a bounded LRU cache and
reports HIT / MISS / STALE / BYPASS / DISABLED for each one. Time is supplied by
the caller, so the result is reproducible and needs no shared state.
"""

from dataclasses import dataclass

from .lru import LRUCache


@dataclass(frozen=True)
class CacheRules:
    enabled: bool
    ttl_seconds: int
    serve_stale_seconds: int
    include_query_string: bool
    bypass_paths: tuple


def rules_from_policy(policy):
    return CacheRules(
        enabled=policy.enabled,
        ttl_seconds=policy.default_ttl_seconds,
        serve_stale_seconds=policy.serve_stale_seconds,
        include_query_string=policy.query_string_mode == 'include',
        bypass_paths=tuple(policy.bypass_paths),
    )


def path_is_bypassed(path_only, prefixes):
    """Segment-aware prefix match: '/admin' matches '/admin' and '/admin/x', not '/administrator'."""
    for prefix in prefixes:
        base = prefix.rstrip('/')
        if path_only == base or path_only.startswith(base + '/'):
            return True
    return False


class EdgeCacheSimulator:
    def __init__(self, rules, capacity):
        self.rules = rules
        self._lru = LRUCache(capacity)
        self.evictions = 0

    def cache_key(self, path):
        if self.rules.include_query_string:
            return path
        return path.split('?', 1)[0]

    def request(self, path, at):
        path_only = path.split('?', 1)[0]
        key = self.cache_key(path)
        base = {'path': path, 'at': at, 'cache_key': key}

        if not self.rules.enabled:
            return {**base, 'outcome': 'DISABLED', 'reason': 'caching is disabled'}
        if path_is_bypassed(path_only, self.rules.bypass_paths):
            return {**base, 'outcome': 'BYPASS', 'reason': 'path matches a bypass rule'}
        if self.rules.ttl_seconds == 0:
            return {**base, 'outcome': 'BYPASS', 'reason': 'edge TTL is 0'}

        stored_at = self._lru.get(key)
        if stored_at is None:
            self._store(key, at)
            return {**base, 'outcome': 'MISS', 'reason': 'not in cache'}

        age = at - stored_at
        if age < self.rules.ttl_seconds:
            return {**base, 'outcome': 'HIT', 'age': age}
        if age < self.rules.ttl_seconds + self.rules.serve_stale_seconds:
            # Serve the expired copy immediately, refresh it in the background.
            self._store(key, at)
            return {**base, 'outcome': 'STALE', 'age': age, 'reason': 'expired, served while revalidating'}
        self._store(key, at)
        return {**base, 'outcome': 'MISS', 'age': age, 'reason': 'expired'}

    def _store(self, key, at):
        if self._lru.put(key, at) is not None:
            self.evictions += 1


def summarize(results, evictions):
    counts = {name: 0 for name in ('HIT', 'MISS', 'STALE', 'BYPASS', 'DISABLED')}
    for result in results:
        counts[result['outcome']] += 1
    total = len(results)
    return {
        'requests': total,
        'hits': counts['HIT'],
        'misses': counts['MISS'],
        'stale': counts['STALE'],
        'bypass': counts['BYPASS'] + counts['DISABLED'],
        # Requests answered without waiting for the origin / all requests.
        'hit_ratio': round((counts['HIT'] + counts['STALE']) / total, 4) if total else 0.0,
        # Requests that reached the origin: misses, plus background refreshes of stale objects.
        'origin_requests': counts['MISS'] + counts['STALE'] + counts['BYPASS'] + counts['DISABLED'],
        'evictions': evictions,
    }
