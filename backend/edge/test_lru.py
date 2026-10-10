import threading

import pytest

from edge.lru import LRUCache


def test_evicts_least_recently_used():
    cache = LRUCache(2)
    cache.put('a', 1)
    cache.put('b', 2)

    assert cache.put('c', 3) == 'a'
    assert 'a' not in cache and 'b' in cache and 'c' in cache


def test_get_refreshes_recency():
    cache = LRUCache(2)
    cache.put('a', 1)
    cache.put('b', 2)
    assert cache.get('a') == 1  # 'a' is now newer than 'b'

    assert cache.put('c', 3) == 'b'
    assert cache.keys_by_recency() == ['c', 'a']


def test_put_existing_key_updates_value_and_recency_without_eviction():
    cache = LRUCache(2)
    cache.put('a', 1)
    cache.put('b', 2)

    assert cache.put('a', 10) is None
    assert cache.get('a') == 10
    assert cache.put('c', 3) == 'b'


def test_peek_and_contains_do_not_change_recency():
    cache = LRUCache(2)
    cache.put('a', 1)
    cache.put('b', 2)
    cache.peek('a')
    assert 'a' in cache

    assert cache.put('c', 3) == 'a'


def test_missing_key_returns_default():
    cache = LRUCache(1)

    assert cache.get('x') is None
    assert cache.get('x', 'fallback') == 'fallback'


def test_capacity_one():
    cache = LRUCache(1)
    cache.put('a', 1)

    assert cache.put('b', 2) == 'a'
    assert len(cache) == 1 and cache.get('b') == 2


def test_delete():
    cache = LRUCache(3)
    cache.put('a', 1)
    cache.put('b', 2)

    assert cache.delete('a') is True
    assert cache.delete('a') is False
    assert cache.keys_by_recency() == ['b']
    cache.put('c', 3)
    cache.put('d', 4)
    assert len(cache) == 3  # the freed slot was reusable


@pytest.mark.parametrize('capacity', [0, -1, 1.5, None])
def test_invalid_capacity(capacity):
    with pytest.raises(ValueError):
        LRUCache(capacity)


def test_stays_within_capacity_under_threads():
    cache = LRUCache(50)

    def worker(offset):
        for i in range(500):
            cache.put((offset, i), i)
            cache.get((offset, i - 1))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(cache) == 50
    assert len(cache.keys_by_recency()) == 50  # the linked list stayed consistent with the map
