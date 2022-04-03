import datetime
import collections.abc
import bisect
from operator import itemgetter


class MaxAgeSet(collections.abc.MutableSet):
    def __init__(self, max_age: datetime.timedelta, iterable=None):
        """Set that keeps items only for a period of max_age.
        It does not use a monotonic clock and as such is susceptible
        to time jumps."""
        self._items = []  # contains tuple(date, item)
        self._max_age = max_age
        if iterable is None:
            return
        for i in iterable:
            self.add(i)

    def add(self, item):
        if item in self:
            return
        self._items.append((datetime.datetime.now(), item))

    def discard(self, value):
        for i, item in self._items:
            if item[1] == value:
                self._items.pop(i)
                return

    def __contains__(self, x):
        self.cleanup()
        return any(x == i[1] for i in self._items)

    def __len__(self):
        self.cleanup()
        return len(self._items)

    def __iter__(self):
        self.cleanup()
        for i in self._items():
            yield i[1]

    def cleanup(self):
        oldest = datetime.datetime.now() - self._max_age
        idx = bisect.bisect_left(self._items, oldest, key=itemgetter(0))
        self._items = self._items[idx:]
