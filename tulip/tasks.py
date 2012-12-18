"""Support for tasks, coroutines and the scheduler."""

__all__ = ['coroutine', 'Task']

import inspect

from . import events
from . import futures


def coroutine(func):
    """Decorator to mark coroutines."""
    # TODO: This is a feel-good API only.  It is not enforced.
    assert inspect.isgeneratorfunction(func)
    func._is_coroutine = True  # Not sure who can use this.
    return func


# TODO: Do we need this?
def iscoroutinefunction(func):
    """Return True if func is a decorated coroutine function."""
    return (inspect.isgeneratorfunction(func) and
            getattr(func, '_is_coroutine', False))


# TODO: Do we need this?
def iscoroutine(obj):
    """Return True if obj is a coroutine object."""
    return inspect.isgenerator(obj)  # TODO: And what?


class Task(futures.Future):
    """A coroutine wrapped in a Future."""

    def __init__(self, coro):
        assert inspect.isgenerator(coro)  # Must be a coroutine *object*.
        super().__init__()
        self._event_loop = events.get_event_loop()
        self._coro = coro
        self._must_cancel = False
        self._event_loop.call_soon(self._step)

    def cancel(self):
        if self.done():
            return False
        self._must_cancel = True
        # _step() will call super().cancel() to call the callbacks.
        return True

    def cancelled(self):
        return self._must_cancel or super().cancelled()

    def _step(self):
        if self.done():
            return
        # We'll call either coro.throw(exc) or coro.send(value).
        # TODO: Set these from the result of the Future on which we waited.
        exc = None
        value = None
        if self._must_cancel:
            exc = futures.CancelledError
        coro = self._coro
        try:
            if exc is not None:
                coro.throw(exc)
            elif value is not None:
                coro.send(value)
            else:
                next(coro)
        except StopIteration as exc:
            if self._must_cancel:
                super().cancel()
            else:
                self.set_result(exc.value)
        except Exception as exc:
            if self._must_cancel:
                super().cancel()
            else:
                self.set_exception(exc)
        except BaseException as exc:
            if self._must_cancel:
                super().cancel()
            else:
                self.set_exception(exc)
            raise
        else:
            # XXX What if blocked for I/O?
            self._event_loop.call_soon(self._step)
