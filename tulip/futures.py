"""A Future class similar to the one in PEP 3148."""

__all__ = ['Future', 'InvalidStateError', 'InvalidTimeoutError', 'sleep']

import concurrent.futures._base

from . import events

# States for Future.
_PENDING = 'PENDING'  # Next states: _CANCELLED, _RUNNING.
_CANCELLED = 'CANCELLED'  # End state.
_RUNNING = 'RUNNING'  # Next state: _FINISHED.
_FINISHED = 'FINISHED'  # End state.
_DONE_STATES = (_CANCELLED, _FINISHED)


Error = concurrent.futures._base.Error


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
    # set_running_or_notify_cancel() just sets the state.

    # Class variables serving to as defaults for instance variables.
    _state = _PENDING
    _result = None
    _exception = None

    def __init__(self):
        """XXX"""
        self._callbacks = []

    def __repr__(self):
        """XXX"""
        if self._callbacks:
            # TODO: Maybe limit the list of callbacks if there are many?
            return 'Future<{}, {}>'.format(self._state, self._callbacks)
        else:
            return 'Future<{}>'.format(self._state)

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
        event_loop = events.get_event_loop()
        for callback in self._callbacks:
            event_loop.call_soon(callback, self)

    def cancelled(self):
        """XXX"""
        return self._state == _CANCELLED

    def running(self):
        """XXX"""
        return self._state == _RUNNING

    def done(self):
        """XXX"""
        return self._state in _DONE_STATES

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

    def add_done_callback(self, function):
        """XXX"""
        if self._state in _DONE_STATES:
            events.get_event_loop().call_soon(function, self)
        else:
            self._callbacks.append(function)

    # So-called internal methods.

    def set_running_or_notify_cancel(self):
        """XXX"""
        if self._state == _CANCELLED:
            return False
        if self._state != _PENDING:
            raise InvalidStateError
        self._state = _RUNNING
        return True

    def set_result(self, result):
        """XXX"""
        if self._state != _RUNNING:
            raise InvalidStateError
        self._result = result
        self._state = _FINISHED
        self._schedule_callbacks()

    def set_exception(self, exception):
        """XXX"""
        if self._state != _RUNNING:
            raise InvalidStateError
        self._exception = exception
        self._state = _FINISHED
        self._schedule_callbacks()


# TODO: Is this the right module for sleep()?
def sleep(when, result=None):
    """Return a Future that completes after a given time (in seconds).

    It's okay to cancel the Future.

    Undocumented feature: sleep(when, x) sets the Future's result to x.
    """
    event_loop = events.get_event_loop()
    future = Future()
    event_loop.call_later(when, _done_sleeping, future, result)
    return future


def _done_sleeping(future, result=None):
    """Helper for sleep() to set the result."""
    if future.set_running_or_notify_cancel():
        future.set_result(result)
