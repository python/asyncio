"""A Future class similar to the one in PEP 3148."""

__all__ = ['CancelledError', 'TimeoutError',
           'InvalidStateError', 'InvalidTimeoutError',
           'Future', 'sleep',
           ]

import concurrent.futures._base

from . import events

# States for Future.
_PENDING = 'PENDING'
_CANCELLED = 'CANCELLED'
_FINISHED = 'FINISHED'

# TODO: Do we really want to depend on concurrent.futures internals?
Error = concurrent.futures._base.Error
CancelledError = concurrent.futures.CancelledError
TimeoutError = concurrent.futures.CancelledError


class InvalidStateError(Error):
    """The operation is not allowed in this state."""
    # TODO: Show the future, its state, the method, and the required state.


class InvalidTimeoutError(Error):
    """Called result() or exception() with timeout != 0."""
    # TODO: Print a nice error message.


class Future:
    """This class is *almost* compatible with concurrent.futures.Future.

    Differences:

    - result() and exception() do not take a timeout argument and
      raise an exception when the future isn't done yet.

    - Callbacks registered with add_done_callback() are always called
      via the event loop's call_soon_threadsafe().

    - This class is not compatible with the wait() and as_completed()
      methods in the concurrent.futures package.

    (In Python 3.4 or later we may be able to unify the implementations.)
    """

    # TODO: PEP 3148 seems to say that cancel() does not call the
    # callbacks, but set_running_or_notify_cancel() does (if cancel()
    # was called).  Here, cancel() schedules the callbacks, and
    # set_running_or_notify_cancel() is not supported.

    # Class variables serving to as defaults for instance variables.
    _state = _PENDING
    _result = None
    _exception = None

    def __init__(self):
        """XXX"""
        self._event_loop = events.get_event_loop()
        self._callbacks = []

    def __repr__(self):
        """XXX"""
        res = self.__class__.__name__
        if self._state == _FINISHED:
            if self._exception is not None:
                res += '<exception={}>'.format(self._exception)
            else:
                res += '<result={}>'.format(self._result)
        elif self._callbacks:
            size = len(self._callbacks)
            if size > 2:
                res += '<{}, [{}, <{} more>, {}]>'.format(
                    self._state, self._callbacks[0],
                    size-2, self._callbacks[-1])
            else:
                res += '<{}, {}>'.format(self._state, self._callbacks)
        else:
            res +='<{}>'.format(self._state)
        return res

    def cancel(self):
        """XXX"""
        if self._state != _PENDING:
            return False
        self._state = _CANCELLED
        self._schedule_callbacks()
        return True

    def _schedule_callbacks(self):
        """XXX"""
        callbacks = self._callbacks[:]
        if not callbacks:
            return
        # Is it worth emptying the callbacks?  It may reduce the
        # usefulness of repr().
        self._callbacks[:] = []
        for callback in callbacks:
            self._event_loop.call_soon(callback, self)

    def cancelled(self):
        """XXX"""
        return self._state == _CANCELLED

    def running(self):
        """XXX"""
        return False  # We don't have a running state.

    def done(self):
        """XXX"""
        return self._state != _PENDING

    def result(self, timeout=0):
        """XXX"""
        if timeout != 0:
            raise InvalidTimeoutError
        if self._state == _CANCELLED:
            raise CancelledError
        if self._state != _FINISHED:
            raise InvalidStateError
        if self._exception is not None:
            raise self._exception
        return self._result

    def exception(self, timeout=0):
        """XXX"""
        if timeout != 0:
            raise InvalidTimeoutError
        if self._state == _CANCELLED:
            raise CancelledError
        if self._state != _FINISHED:
            raise InvalidStateError
        return self._exception

    def add_done_callback(self, fn):
        """XXX"""
        if self._state != _PENDING:
            self._event_loop.call_soon(fn, self)
        else:
            self._callbacks.append(fn)

    # So-called internal methods (note: no set_running_or_notify_cancel()).

    def set_result(self, result):
        """XXX"""
        if self._state != _PENDING:
            raise InvalidStateError
        self._result = result
        self._state = _FINISHED
        self._schedule_callbacks()

    def set_exception(self, exception):
        """XXX"""
        if self._state != _PENDING:
            raise InvalidStateError
        self._exception = exception
        self._state = _FINISHED
        self._schedule_callbacks()

    # Truly internal methods.

    def _copy_state(self, other):
        """Internal helper to copy state from another Future.

        The other Future may be a concurrent.futures.Future.
        """
        assert other.done()
        assert not self.done()
        if other.cancelled():
            self.cancel()
        else:
            exception = other.exception()
            if exception is not None:
                self.set_exception(exception)
            else:
                result = other.result()
                self.set_result(result)

    def __iter__(self):
        yield self  # This tells Task to wait for completion.
        return self.result()  # May raise too.


# TODO: Is this the right module for sleep()?
def sleep(when, result=None):
    """Return a Future that completes after a given time (in seconds).

    It's okay to cancel the Future.

    Undocumented feature: sleep(when, x) sets the Future's result to x.
    """
    future = Future()
    future._event_loop.call_later(when, future.set_result, result)
    return future
