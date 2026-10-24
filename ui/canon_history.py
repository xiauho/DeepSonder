"""Bounded, in-memory canon editing history, independent of widget lifetimes."""
from dataclasses import dataclass, fields, is_dataclass
import sys
from time import monotonic


def memory_size(value, seen=None):
    """Count retained Python objects, including strings and dataclass members."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if is_dataclass(value):
        size += memory_size(vars(value), seen)
    elif isinstance(value, dict):
        size += sum(memory_size(k, seen) + memory_size(v, seen) for k, v in value.items())
    elif isinstance(value, (list, tuple, set, frozenset)):
        size += sum(memory_size(item, seen) for item in value)
    return size


def ordered_equal(left, right):
    """Field mappings use insertion order as author-visible document order."""
    if type(left) is not type(right):
        return False
    if is_dataclass(left):
        return all(ordered_equal(getattr(left, f.name), getattr(right, f.name)) for f in fields(left))
    if isinstance(left, dict):
        return list(left) == list(right) and all(ordered_equal(left[k], right[k]) for k in left)
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(ordered_equal(a, b) for a, b in zip(left, right))
    return left == right


@dataclass
class CanonSnapshot:
    entry: object
    values: dict
    view: dict

    def same_content(self, other):
        return ordered_equal(self.entry, other.entry) and ordered_equal(self.values, other.values)


class CanonEditHistory:
    """Keep at most 100 operations / 16 MiB; an oversized draft is never truncated.

    Snapshots include raw input so even temporarily invalid Markdown can be undone.
    History is never written to preferences or to project files.
    """
    MAX_STEPS = 100
    MAX_BYTES = 16 * 1024 * 1024
    MERGE_SECONDS = 1.0

    def __init__(self):
        self.states = []
        self.weights = []
        self.index = -1
        self._merge_key = None
        self._last_time = 0.0

    def clear(self, state=None):
        self.states = [] if state is None else [state]
        self.weights = [] if state is None else [memory_size(state)]
        self.index = len(self.states) - 1
        self.break_merge()

    @property
    def can_undo(self):
        return self.index > 0

    @property
    def can_redo(self):
        return 0 <= self.index < len(self.states) - 1

    @property
    def current(self):
        return self.states[self.index] if self.index >= 0 else None

    def break_merge(self):
        self._merge_key = None

    def update_view(self, view):
        if self.current is not None:
            self.current.view = view

    def replace_current(self, state):
        if self.current is not None:
            self.states[self.index] = state
            self.weights[self.index] = memory_size(state)
            self._trim()
        self.break_merge()

    def record(self, state, merge_key=None):
        if self.current is None:
            self.clear(state)
            return
        if state.same_content(self.current):
            return
        now = monotonic()
        merge = (merge_key is not None and merge_key == self._merge_key
                 and now - self._last_time <= self.MERGE_SECONDS
                 and not self.can_redo and self.can_undo)
        del self.states[self.index + 1:]
        del self.weights[self.index + 1:]
        if merge:
            self.states[self.index] = state
            self.weights[self.index] = memory_size(state)
        else:
            self.states.append(state)
            self.weights.append(memory_size(state))
            self.index += 1
        self._merge_key, self._last_time = merge_key, now
        self._trim()

    def _trim(self):
        while len(self.states) > 1 and (len(self.states) > self.MAX_STEPS + 1
                                       or sum(self.weights) > self.MAX_BYTES):
            # Preserve the current state even when it exceeds the budget alone.
            remove = 0 if self.index > 0 else len(self.states) - 1
            self.states.pop(remove)
            self.weights.pop(remove)
            if remove == 0:
                self.index -= 1

    def step(self, direction):
        if not (self.can_undo if direction < 0 else self.can_redo):
            return None
        self.break_merge()
        self.index += direction
        return self.current
