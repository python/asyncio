"""Support for tasks, coroutines and the scheduler."""

__all__ = ['coroutine', 'Task']

import inspect

from . import futures


def coroutine(func):
    """Decorator to mark coroutines."""
    assert inspect.isgeneratorfunction(func)
    func._is_coroutine = True
    return func


class Task(futures.Future):
    """A coroutine wrapped in a Future."""

    def __init__(self, coro):
        super().__init__()
        assert inspect.isgenerator(coro)  # Must be a coroutine *object*
        # XXX Now what?
