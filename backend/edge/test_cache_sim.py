import pytest

from edge.cache_sim import CacheRules, EdgeCacheSimulator, path_is_bypassed, summarize


def rules(**overrides):
    base = dict(enabled=True, ttl_seconds=60, serve_stale_seconds=0,
                include_query_string=False, bypass_paths=())
    base.update(overrides)
    return CacheRules(**base)


def run(sim, *requests):
    return [sim.request(path, at)['outcome'] for path, at in requests]


def test_miss_then_hit_then_expiry():
    sim = EdgeCacheSimulator(rules(ttl_seconds=60), capacity=10)

    assert run(sim, ('/a', 0), ('/a', 30), ('/a', 59.9), ('/a', 60), ('/a', 70)) == \
        ['MISS', 'HIT', 'HIT', 'MISS', 'HIT']


def test_stale_while_revalidate_window():
    sim = EdgeCacheSimulator(rules(ttl_seconds=10, serve_stale_seconds=20), capacity=10)

    assert run(sim, ('/a', 0), ('/a', 15), ('/a', 16), ('/a', 100)) == \
        ['MISS', 'STALE', 'HIT', 'MISS']  # 15 is stale (and refreshes); 16 is fresh again; 100 is too old


def test_query_string_ignored_or_part_of_the_key():
    ignore = EdgeCacheSimulator(rules(include_query_string=False), capacity=10)
    include = EdgeCacheSimulator(rules(include_query_string=True), capacity=10)

    assert run(ignore, ('/a?x=1', 0), ('/a?x=2', 1)) == ['MISS', 'HIT']
    assert run(include, ('/a?x=1', 0), ('/a?x=2', 1), ('/a?x=1', 2)) == ['MISS', 'MISS', 'HIT']


def test_bypass_paths_are_segment_aware():
    assert path_is_bypassed('/admin', ['/admin'])
    assert path_is_bypassed('/admin/users', ['/admin'])
    assert path_is_bypassed('/admin/users', ['/admin/'])
    assert not path_is_bypassed('/administrator', ['/admin'])
    assert path_is_bypassed('/anything', ['/'])


def test_bypassed_and_disabled_requests_are_never_cached():
    sim = EdgeCacheSimulator(rules(bypass_paths=('/admin',)), capacity=10)
    assert run(sim, ('/admin/x', 0), ('/admin/x', 1)) == ['BYPASS', 'BYPASS']

    off = EdgeCacheSimulator(rules(enabled=False), capacity=10)
    assert run(off, ('/a', 0), ('/a', 1)) == ['DISABLED', 'DISABLED']

    zero = EdgeCacheSimulator(rules(ttl_seconds=0), capacity=10)
    assert run(zero, ('/a', 0), ('/a', 1)) == ['BYPASS', 'BYPASS']


def test_lru_eviction_makes_old_objects_miss_again():
    sim = EdgeCacheSimulator(rules(ttl_seconds=1000), capacity=2)

    outcomes = run(sim, ('/a', 0), ('/b', 1), ('/a', 2), ('/c', 3), ('/b', 4), ('/a', 5))

    # /c evicts /b (least recently used), so /b misses; that in turn evicts /a.
    assert outcomes == ['MISS', 'MISS', 'HIT', 'MISS', 'MISS', 'MISS']
    assert sim.evictions == 3


def test_summary_numbers():
    sim = EdgeCacheSimulator(rules(ttl_seconds=10, serve_stale_seconds=10, bypass_paths=('/p',)), capacity=10)
    results = [sim.request(p, t) for p, t in
               [('/a', 0), ('/a', 1), ('/a', 12), ('/p', 13), ('/b', 14)]]

    summary = summarize(results, sim.evictions)

    assert summary == {
        'requests': 5, 'hits': 1, 'misses': 2, 'stale': 1, 'bypass': 1,
        'hit_ratio': 0.4, 'origin_requests': 4, 'evictions': 0,
    }


def test_summary_of_nothing_is_safe():
    assert summarize([], 0)['hit_ratio'] == 0.0
