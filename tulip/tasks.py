"""Support for tasks, coroutines and the scheduler."""

__all__ = ['coroutine', 'task', 'Task',
           'FIRST_COMPLETED', 'FIRST_EXCEPTION', 'ALL_COMPLETED',
           'wait', 'as_completed', 'sleep',
           ]

import concurrent.futures
import functools
import inspect
import time

from . import futures


def coroutine(func):
    """Decorator to mark coroutines.

    Decorator wraps non generator functions and returns generator wrapper.
    If non generator function returns generator of Future it yield-from it.

    TODO: This is a feel-good API only. It is not enforced.
    """
    if inspect.isgeneratorfunction(func):
        coro = func
    else:
        @functools.wraps(func)
        def coro(*args, **kw):
            res = func(*args, **kw)
            if isinstance(res, futures.Future) or inspect.isgenerator(res):
                res = yield from res
            return res

    coro._is_coroutine = True  # Not sure who can use this.
    return coro


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
    if inspect.isgeneratorfunction(func):
        coro = func
    else:
        def coro(*args, **kw):
            res = func(*args, **kw)
            if isinstance(res, futures.Future) or inspect.isgenerator(res):
                res = yield from res
            return res

    def task_wrapper(*args, **kwds):
        return Task(coro(*args, **kwds))

    return task_wrapper


class Task(futures.Future):
    """A coroutine wrapped in a Future."""

    def __init__(self, coro, event_loop=None, timeout=None):
        assert inspect.isgenerator(coro)  # Must be a coroutine *object*.
        super().__init__(event_loop=event_loop, timeout=timeout)
        self._coro = coro
        self._fut_waiter = None
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
            i = len(res)
        res = res[:i] + '(<{}>)'.format(self._coro.__name__) + res[i:]
        return res

    def cancel(self):
        if self.done():
            return False
        self._must_cancel = True
        # _step() will call super().cancel() to call the callbacks.
        if self._fut_waiter is not None:
            return self._fut_waiter.cancel()
        else:
            self._event_loop.call_soon(self._step_maybe)
            return True

    def cancelled(self):
        return self._must_cancel or super().cancelled()

    def _step_maybe(self):
        # Helper for cancel().
        if not self.done():
            return self._step()

    def _step(self, value=None, exc=None):
        assert not self.done(), \
            '_step(): already done: {!r}, {!r}, {!r}'.format(self, value, exc)

        self._fut_waiter = None

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
            # XXX No check for self._must_cancel here?
            if isinstance(result, futures.Future):
                if not result._blocking:
                    result.set_exception(
                        RuntimeError(
                            'yield was used instead of yield from '
                            'in task {!r} with {!r}'.format(self, result)))

                result._blocking = False
                result.add_done_callback(self._wakeup)
                self._fut_waiter = result

            elif isinstance(result, concurrent.futures.Future):
                # This ought to be more efficient than wrap_future(),
                # because we don't create an extra Future.
                result.add_done_callback(
                    lambda future:
                        self._event_loop.call_soon_threadsafe(
                            self._wakeup, future))
            else:
                if inspect.isgenerator(result):
                    self._event_loop.call_soon(
                        self._step, None,
                        RuntimeError(
                            'yield was used instead of yield from for '
                            'generator in task {!r} with {}'.format(
                                self, result)))
                else:
                    if result is not None:
                        self._event_loop.call_soon(
                            self._step, None,
                            RuntimeError(
                                'Task received bad yield: {!r}'.format(result)))
                    else:
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
        timeout is not None and timeout <= 0 or
        return_when == FIRST_COMPLETED and done or
        return_when == FIRST_EXCEPTION and errors):
        return done, pending

    # Will always be cancelled eventually.
    bail = futures.Future(timeout=timeout)

    def _on_completion(fut):
        pending.remove(fut)
        done.add(fut)
        if (not pending or
            return_when == FIRST_COMPLETED or
            (return_when == FIRST_EXCEPTION and
             not fut.cancelled() and
             fut.exception() is not None)):
            bail.cancel()

            for f in pending:
                f.remove_done_callback(_on_completion)

    for f in pending:
        f.add_done_callback(_on_completion)
    try:
        yield from bail
    except futures.CancelledError:
        pass

    really_done = set(f for f in pending if f.done())
    if really_done:
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


@coroutine
def sleep(when, result=None):
    """Coroutine that completes after a given time (in seconds)."""
    future = futures.Future()
    h = future._event_loop.call_later(when, future.set_result, result)
    try:
        return (yield from future)
    finally:
        h.cancel()
