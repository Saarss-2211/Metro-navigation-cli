"""Small data structures used by MetroNav (implemented without dependencies)."""
from __future__ import annotations


class MinHeap:
    def __init__(self):
        self._items = []

    def __bool__(self):
        return bool(self._items)

    def push(self, priority, item):
        a = self._items
        a.append((priority, item))
        i = len(a) - 1
        while i:
            p = (i - 1) // 2
            if a[p][0] <= priority:
                break
            a[i] = a[p]
            i = p
        a[i] = (priority, item)

    def pop(self):
        if not self._items:
            raise IndexError("pop from empty heap")
        a = self._items
        root = a[0]
        last = a.pop()
        if a:
            i, n = 0, len(a)
            while 2 * i + 1 < n:
                child = 2 * i + 1
                if child + 1 < n and a[child + 1][0] < a[child][0]:
                    child += 1
                if a[child][0] >= last[0]:
                    break
                a[i] = a[child]
                i = child
            a[i] = last
        return root


class Trie:
    END = ""

    def __init__(self, values=()):
        self.root = {}
        for value in values:
            self.add(value)

    def add(self, value):
        node = self.root
        for char in value.casefold():
            node = node.setdefault(char, {})
        node.setdefault(self.END, value)

    def autocomplete(self, prefix, limit=10):
        node = self.root
        for char in prefix.casefold():
            if char not in node:
                return []
            node = node[char]
        found = []
        stack = [node]
        while stack and len(found) < limit:
            cur = stack.pop()
            if self.END in cur:
                found.append(cur[self.END])
            stack.extend(cur[k] for k in sorted(cur, reverse=True) if k != self.END)
        return sorted(found, key=str.casefold)[:limit]


class _Node:
    __slots__ = ("key", "value", "prev", "next")

    def __init__(self, key=None, value=None):
        self.key, self.value = key, value
        self.prev = self.next = None


class LRUCache:
    """O(1) LRU cache backed by a dict and a doubly linked list."""
    def __init__(self, capacity=128):
        self.capacity = capacity
        self.items = {}
        self.head, self.tail = _Node(), _Node()
        self.head.next, self.tail.prev = self.tail, self.head

    def _unlink(self, n):
        n.prev.next, n.next.prev = n.next, n.prev

    def _front(self, n):
        n.prev, n.next = self.head, self.head.next
        self.head.next.prev = n
        self.head.next = n

    def get(self, key):
        n = self.items.get(key)
        if n is None:
            return None
        self._unlink(n)
        self._front(n)
        return n.value

    def put(self, key, value):
        n = self.items.get(key)
        if n:
            n.value = value
            self._unlink(n)
        else:
            n = _Node(key, value)
            self.items[key] = n
        self._front(n)
        if len(self.items) > self.capacity:
            old = self.tail.prev
            self._unlink(old)
            del self.items[old.key]

    def clear(self):
        self.items.clear()
        self.head.next, self.tail.prev = self.tail, self.head


class UnionFind:
    def __init__(self, items=()):
        self.parent, self.rank = {}, {}
        for item in items:
            self.add(item)

    def add(self, x):
        if x not in self.parent:
            self.parent[x], self.rank[x] = x, 0

    def find(self, x):
        self.add(x)
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        return True


class PriorityQueue:
    """Stable priority queue: lower numeric priority is served first."""
    def __init__(self):
        self.heap, self.sequence = MinHeap(), 0

    def push(self, priority, value):
        self.sequence += 1
        self.heap.push((priority, self.sequence), value)

    def pop(self):
        return self.heap.pop()[1]

    def __bool__(self):
        return bool(self.heap)
