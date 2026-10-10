import fakeredis
import pytest

from edge.purge import EdgeCacheStore, object_key


@pytest.fixture
def client():
    return fakeredis.FakeRedis()


@pytest.fixture
def store(client):
    return EdgeCacheStore(client)


def seed(store, domain_id, *paths):
    for path in paths:
        store.put(domain_id, path)


def test_purge_all_only_touches_the_given_domain(store):
    seed(store, 1, '/a', '/b')
    seed(store, 2, '/a')

    assert store.purge_all(1) == 2
    assert store.list_paths(1) == []
    assert store.list_paths(2) == ['/a']


def test_domain_ids_that_share_a_prefix_do_not_collide(store):
    seed(store, 1, '/x')
    seed(store, 10, '/x')

    store.purge_all(1)

    assert store.list_paths(10) == ['/x']


def test_purge_exact_paths(store):
    seed(store, 1, '/a', '/a/b', '/c')

    assert store.purge_paths(1, ['/a', '/missing']) == 1
    assert store.list_paths(1) == ['/a/b', '/c']


def test_prefix_wildcard_removes_the_whole_subtree(store):
    seed(store, 1, '/blog/', '/blog/post-1', '/blog/2024/post-2', '/blogger', '/about')

    assert store.purge_paths(1, ['/blog/*']) == 3
    assert store.list_paths(1) == ['/about', '/blogger']  # '/blogger' is not under '/blog/'


def test_glob_characters_in_data_are_taken_literally(store, client):
    client.set(object_key(1, '/weird[1]'), b'x')
    client.set(object_key(1, '/weird1'), b'x')

    assert store.purge_paths(1, ['/weird1']) == 1
    assert store.list_paths(1) == ['/weird[1]']
    assert store.purge_all(1) == 1


def test_purging_twice_is_safe(store):
    seed(store, 1, '/a', '/b')

    assert store.purge_all(1) == 2
    assert store.purge_all(1) == 0  # idempotent: same end state, nothing left to delete


def test_large_purges_are_processed_in_batches(store, client):
    pipe = client.pipeline()
    for i in range(1200):
        pipe.set(object_key(7, f'/item/{i}'), b'x')
    pipe.execute()

    assert store.purge_all(7) == 1200
    assert client.dbsize() == 0


def test_list_is_limited(store):
    seed(store, 1, *[f'/p{i}' for i in range(30)])

    assert len(store.list_paths(1, limit=10)) == 10
