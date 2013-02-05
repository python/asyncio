"""Support for tasks, coroutines and the scheduler."""

__all__ = ['coroutine', 'task', 'Task',
           'FIRST_COMPLETED', 'FIRST_EXCEPTION', 'ALL_COMPLETED',
           'wait', 'as_completed', 'sleep',
           ]

import concurrent.futures
import inspect
import logging
import time

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

    def __init__(self, coro, event_loop=None):
        assert inspect.isgenerator(coro)  # Must be a coroutine *object*.
        super().__init__(event_loop=event_loop)  # Sets self._event_loop.
        self._coro = coro
        self._must_cancel = False
        self._event_loop.call_soon(self._step)

    def __repr__(self):
        res = super().__repr__()
        if (self._must_cancel and
            self._state == futures._PENDING and
            '<PENDING' in res):
            res = res.replace('<PENDING', '<CANCELLING', 1)
        i = res.find('<')
        if i < 0:
            i = len(res)  # pragma: no cover
        res = res[:i] + '(<{}>)'.format(self._coro.__name__) + res[i:]
        return res

    def cancel(self):
        if self.done():
            return False
        self._must_cancel = True
        # _step() will call super().cancel() to call the callbacks.
        self._event_loop.call_soon(self._step_maybe)
        return True

    def cancelled(self):
        return self._must_cancel or super().cancelled()

    def _step_maybe(self):
        # Helper for cancel().
        if not self.done():
            return self._step()

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
                logging.exception('Exception in task')
        except BaseException as exc:
            if self._must_cancel:
                super().cancel()
            else:
                self.set_exception(exc)
                logging.exception('BaseException in task')
            raise
        else:
            # XXX No check for self._must_cancel here?
            if isinstance(result, futures.Future):
                result.add_done_callback(self._wakeup)
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

    def _wakeup(self, future):
        try:
            value = future.result()
        except Exception as exc:
            self._step(None, exc)
        else:
            self._step(value, None)


# wait() and as_completed() similar to those in PEP 3148.

FIRST_COMPLETED = concurrent.futures.FIRST_COMPLETED
FIRST_EXCEPTION = concurrent.futures.FIRST_EXCEPTION
ALL_COMPLETED = concurrent.futures.ALL_COMPLETED


# Even though this *is* a @coroutine, we don't mark it as such!
def wait(fs, timeout=None, return_when=ALL_COMPLETED):
    """Wait for the Futures and and coroutines given by fs to complete.

    Coroutines will be wrapped in Tasks.

    Returns two sets of Future: (done, pending).

    Usage:

        done, pending = yield from tulip.wait(fs)

    Note: This does not raise TimeoutError!  Futures that aren't done
    when the timeout occurs are returned in the second set.
    """
    fs = _wrap_coroutines(fs)
    return _wait(fs, timeout, return_when)


@coroutine
def _wait(fs, timeout=None, return_when=ALL_COMPLETED):
    """Internal helper: Like wait() but does not wrap coroutines."""
    done, pending = set(), set()
    errors = 0
    for f in fs:
        if f.done():
            done.add(f)
            if not f.cancelled() and f.exception() is not None:
                errors += 1
        else:
            pending.add(f)
    if (not pending or
        timeout != None and timeout <= 0 or
        return_when == FIRST_COMPLETED and done or
        return_when == FIRST_EXCEPTION and errors):
        return done, pending
    bail = futures.Future()  # Will always be cancelled eventually.
    timeout_handler = None
    debugstuff = locals()
    if timeout is not None:
        loop = events.get_event_loop()
        timeout_handler = loop.call_later(timeout, bail.cancel)
    def _on_completion(f):
        pending.remove(f)
        done.add(f)
        if (not pending or
            return_when == FIRST_COMPLETED or
            (return_when == FIRST_EXCEPTION and
             not f.cancelled() and
             f.exception() is not None)):
            bail.cancel()
    try:
        for f in pending:
            f.add_done_callback(_on_completion)
        try:
            yield from bail
        except futures.CancelledError:
            pass
    finally:
        for f in pending:
            f.remove_done_callback(_on_completion)
        if timeout_handler is not None:
            timeout_handler.cancel()
    really_done = set(f for f in pending if f.done())
    if really_done:  # pragma: no cover
        # We don't expect this to ever happen.  Or do we?
        done.update(really_done)
        pending.difference_update(really_done)
    return done, pending


# This is *not* a @coroutine!  It is just an iterator (yielding Futures).
def as_completed(fs, timeout=None):
    """Return an iterator whose values, when waited for, are Futures.

    This differs from PEP 3148; the proper way to use this is:

        for f in as_completed(fs):
            result = yield from f  # The 'yield from' may raise.
            # Use result.

    Raises TimeoutError if the timeout occurs before all Futures are
    done.

    Note: The futures 'f' are not necessarily members of fs.
    """
    deadline = None
    if timeout is not None:
        deadline = time.monotonic() + timeout
    done = None  # Make nonlocal happy.
    fs = _wrap_coroutines(fs)
    while fs:
        if deadline is not None:
            timeout = deadline - time.monotonic()
        @coroutine
        def _wait_for_some():
            nonlocal done, fs
            done, fs = yield from _wait(fs, timeout=timeout,
                                        return_when=FIRST_COMPLETED)
            if not done:
                fs = set()
                raise futures.TimeoutError()
            return done.pop().result()  # May raise.
        yield Task(_wait_for_some())
        for f in done:
            yield f


def _wrap_coroutines(fs):
    """Internal helper to process an iterator of Futures and coroutines.

    Returns a set of Futures.
    """
    wrapped = set()
    for f in fs:
        if not isinstance(f, futures.Future):
            assert iscoroutine(f)
            f = Task(f)
        wrapped.add(f)
    return wrapped


def sleep(when, result=None):
    """Return a Future that completes after a given time (in seconds).

    It's okay to cancel the Future.

    Undocumented feature: sleep(when, x) sets the Future's result to x.
    """
    future = futures.Future()
    future._event_loop.call_later(when, future.set_result, result)
    return future
