"""Support for tasks, coroutines and the scheduler."""

__all__ = ['coroutine', 'task', 'Task']

import concurrent.futures
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


def task(func):
    """Decorator for a coroutine to be wrapped in a Task."""
    def task_wrapper(*args, **kwds):
        coro = func(*args, **kwds)
        return Task(coro)
    return task_wrapper


class Task(futures.Future):
    """A coroutine wrapped in a Future."""

    def __init__(self, coro):
        assert inspect.isgenerator(coro)  # Must be a coroutine *object*.
        super().__init__()  # Sets self._event_loop.
        self._coro = coro
        self._must_cancel = False
        self._event_loop.call_soon(self._step)

    def __repr__(self):
        res = super().__repr__()
        i = res.find('<')
        if i < 0:
            i = len(res)
        res = res[:i] + '(<{}>)'.format(self._coro.__name__) + res[i:]
        return res

    def cancel(self):
        if self.done():
            return False
        self._must_cancel = True
        # _step() will call super().cancel() to call the callbacks.
        return True

    def cancelled(self):
        return self._must_cancel or super().cancelled()

    def _step(self, value=None, exc=None):
        if self.done():
            logging.warn('_step(): already done: %r, %r, %r', self, value, exc)
            return
        # We'll call either coro.throw(exc) or coro.send(value).
        if self._must_cancel:
            exc = futures.CancelledError
        coro = self._coro
        try:
            if exc is not None:
                result = coro.throw(exc)
            elif value is not None:
                result = coro.send(value)
            else:
                result = next(coro)
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
            def _wakeup(future):
                value = None
                exc = future.exception()
                if exc is None:
                    value = future.result()
                self._step(value, exc)
            if isinstance(result, futures.Future):
                result.add_done_callback(_wakeup)
            elif isinstance(result, concurrent.futures.Future):
                # This ought to be more efficient than wrap_future(),
                # because we don't create an extra Future.
                result.add_done_callback(
                    lambda future:
                        self._event_loop.call_soon_threadsafe(_wakeup, future))
            else:
                if result is not None:
                    logging.warn('_step(): bad yield: %r', result)
                self._event_loop.call_soon(self._step)
