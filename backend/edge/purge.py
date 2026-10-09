"""A stand-in for the edge cache that purge jobs act on, stored in Redis.

Keys look like  edgecache:<domain_id>:<path>.  Every operation is idempotent:
deleting something that is already gone simply counts 0, so a purge job can be
executed twice (redelivery, retry) with the same final state.
"""

import re

PREFIX = 'edgecache'
BATCH = 500


def object_key(domain_id, path):
    return f'{PREFIX}:{domain_id}:{path}'


def _glob_escape(text):
    """Make user text literal inside a Redis MATCH pattern."""
    return re.sub(r'([\\*?\[\]])', r'\\\1', text)


class EdgeCacheStore:
    def __init__(self, client):
        self._client = client

    def put(self, domain_id, path, ttl_seconds=86_400):
        self._client.set(object_key(domain_id, path), b'cached', ex=ttl_seconds)

    def list_paths(self, domain_id, limit=200):
        prefix = object_key(domain_id, '')
        match = f'{_glob_escape(prefix)}*'
        paths = []
        # SCAN walks the keyspace in small steps; KEYS would block Redis for everyone.
        for raw in self._client.scan_iter(match=match, count=BATCH):
            paths.append(raw.decode()[len(prefix):])
            if len(paths) >= limit:
                break
        return sorted(paths)

    def purge_all(self, domain_id):
        return self._purge_matching(f'{_glob_escape(object_key(domain_id, ""))}*')

    def purge_paths(self, domain_id, paths):
        removed = 0
        for path in paths:
            if path.endswith('/*'):
                # Prefix purge: "/blog/*" removes "/blog/" and everything below it.
                removed += self._purge_matching(f'{_glob_escape(object_key(domain_id, path[:-1]))}*')
            else:
                removed += self._client.unlink(object_key(domain_id, path))
        return removed

    def _purge_matching(self, pattern):
        removed, batch = 0, []
        for key in self._client.scan_iter(match=pattern, count=BATCH):
            batch.append(key)
            if len(batch) >= BATCH:
                removed += self._client.unlink(*batch)
                batch = []
        if batch:
            removed += self._client.unlink(*batch)
        return removed
