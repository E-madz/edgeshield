"""A bounded least-recently-used cache with O(1) get and put.

Classic design: a hash map for O(1) lookup plus a doubly linked list that keeps
entries ordered by recency (head = most recent, tail = next to evict). Moving a
node to the head is O(1) because every node knows its neighbours.
"""

import threading


class _Node:
    __slots__ = ('key', 'value', 'prev', 'next')

    def __init__(self, key=None, value=None):
        self.key = key
        self.value = value
        self.prev = None
        self.next = None


class LRUCache:
    def __init__(self, capacity):
        if not isinstance(capacity, int) or capacity < 1:
            raise ValueError('capacity must be a positive integer')
        self.capacity = capacity
        self._map = {}
        # Sentinel nodes remove all "is this the first/last element" edge cases.
        self._head = _Node()
        self._tail = _Node()
        self._head.next = self._tail
        self._tail.prev = self._head
        self._lock = threading.RLock()

    def __len__(self):
        return len(self._map)

    def __contains__(self, key):
        """Membership test that does NOT count as a use."""
        return key in self._map

    def get(self, key, default=None):
        """Return the value and mark the key as most recently used."""
        with self._lock:
            node = self._map.get(key)
            if node is None:
                return default
            self._unlink(node)
            self._push_front(node)
            return node.value

    def peek(self, key, default=None):
        """Return the value without changing recency."""
        with self._lock:
            node = self._map.get(key)
            return default if node is None else node.value

    def put(self, key, value):
        """Insert or update. Returns the evicted key, or None if nothing was evicted."""
        with self._lock:
            node = self._map.get(key)
            if node is not None:
                node.value = value
                self._unlink(node)
                self._push_front(node)
                return None

            evicted = None
            if len(self._map) >= self.capacity:
                lru = self._tail.prev
                self._unlink(lru)
                del self._map[lru.key]
                evicted = lru.key

            node = _Node(key, value)
            self._map[key] = node
            self._push_front(node)
            return evicted

    def delete(self, key):
        with self._lock:
            node = self._map.pop(key, None)
            if node is None:
                return False
            self._unlink(node)
            return True

    def keys_by_recency(self):
        """Most recently used first (for tests and debugging)."""
        with self._lock:
            keys, node = [], self._head.next
            while node is not self._tail:
                keys.append(node.key)
                node = node.next
            return keys

    def _unlink(self, node):
        node.prev.next = node.next
        node.next.prev = node.prev

    def _push_front(self, node):
        node.prev = self._head
        node.next = self._head.next
        self._head.next.prev = node
        self._head.next = node
