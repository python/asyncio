"""A Future class similar to the one in PEP 3148."""

__all__ = ['CancelledError', 'TimeoutError',
           'InvalidStateError', 'InvalidTimeoutError',
           'Future',
           ]

import concurrent.futures._base
import io
import logging
import traceback

from . import events
from .log import tulip_log

# States for Future.
_PENDING = 'PENDING'
_CANCELLED = 'CANCELLED'
_FINISHED = 'FINISHED'

# TODO: Do we really want to depend on concurrent.futures internals?
Error = concurrent.futures._base.Error
CancelledError = concurrent.futures.CancelledError
TimeoutError = concurrent.futures.TimeoutError

STACK_DEBUG = logging.DEBUG - 1  # heavy-duty debugging


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

    # Class variables serving as defaults for instance variables.
    _state = _PENDING
    _result = None
    _exception = None
    _timeout_handle = None
    _event_loop = None

    _blocking = False  # proper use of future (yield vs yield from)

    # result of the future has to be requested
    _debug_stack = None
    _debug_result_requested = False

    def __init__(self, *, event_loop=None, timeout=None):
        """Initialize the future.

        The optional event_loop argument allows to explicitly set the event
        loop object used by the future. If it's not provided, the future uses
        the default event loop.
        """
        if event_loop is None:
            self._event_loop = events.get_event_loop()
        else:
            self._event_loop = event_loop
        self._callbacks = []

        if timeout is not None:
            self._timeout_handle = self._event_loop.call_later(
                timeout, self.cancel)

        if __debug__:
            if self._event_loop.get_log_level() <= STACK_DEBUG:
                out = io.StringIO()
                traceback.print_stack(file=out)
                self._debug_stack = out.getvalue()

    def __repr__(self):
        res = self.__class__.__name__
        if self._state == _FINISHED:
            if self._exception is not None:
                res += '<exception={!r}>'.format(self._exception)
            else:
                res += '<result={!r}>'.format(self._result)
        elif self._callbacks:
            size = len(self._callbacks)
            if size > 2:
                res += '<{}, [{}, <{} more>, {}]>'.format(
                    self._state, self._callbacks[0],
                    size-2, self._callbacks[-1])
            else:
                res += '<{}, {}>'.format(self._state, self._callbacks)
        else:
            res += '<{}>'.format(self._state)
        return res

    def cancel(self):
        """Cancel the future and schedule callbacks.

        If the future is already done or cancelled, return False.  Otherwise,
        change the future's state to cancelled, schedule the callbacks and
        return True.
        """
        if self._state != _PENDING:
            return False
        self._state = _CANCELLED
        self._schedule_callbacks()
        return True

    def _schedule_callbacks(self):
        """Internal: Ask the event loop to call all callbacks.

        The callbacks are scheduled to be called as soon as possible. Also
        clears the callback list.
        """
        # Cancel timeout handle
        if self._timeout_handle is not None:
            self._timeout_handle.cancel()
            self._timeout_handle = None

        callbacks = self._callbacks[:]
        if not callbacks:
            return

        self._callbacks[:] = []
        for callback in callbacks:
            self._event_loop.call_soon(callback, self)

    def cancelled(self):
        """Return True if the future was cancelled."""
        return self._state == _CANCELLED

    def running(self):
        """Always return False.

        This method is for compatibility with concurrent.futures; we don't
        have a running state.
        """
        return False  # We don't have a running state.

    def done(self):
        """Return True if the future is done.

        Done means either that a result / exception are available, or that the
        future was cancelled.
        """
        return self._state != _PENDING

    def result(self, timeout=0):
        """Return the result this future represents.

        If the future has been cancelled, raises CancelledError.  If the
        future's result isn't yet available, raises InvalidStateError.  If
        the future is done and has an exception set, this exception is raised.
        Timeout values other than 0 are not supported.
        """
        if __debug__:
            self._debug_result_requested = True
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
        """Return the exception that was set on this future.

        The exception (or None if no exception was set) is returned only if
        the future is done.  If the future has been cancelled, raises
        CancelledError.  If the future isn't done yet, raises
        InvalidStateError.  Timeout values other than 0 are not supported.
        """
        if __debug__:
            self._debug_result_requested = True
        if timeout != 0:
            raise InvalidTimeoutError
        if self._state == _CANCELLED:
            raise CancelledError
        if self._state != _FINISHED:
            raise InvalidStateError
        return self._exception

    def add_done_callback(self, fn):
        """Add a callback to be run when the future becomes done.

        The callback is called with a single argument - the future object. If
        the future is already done when this is called, the callback is
        scheduled with call_soon.
        """
        if self._state != _PENDING:
            self._event_loop.call_soon(fn, self)
        else:
            self._callbacks.append(fn)

    # New method not in PEP 3148.

    def remove_done_callback(self, fn):
        """Remove all instances of a callback from the "call when done" list.

        Returns the number of callbacks removed.
        """
        filtered_callbacks = [f for f in self._callbacks if f != fn]
        removed_count = len(self._callbacks) - len(filtered_callbacks)
        if removed_count:
            self._callbacks[:] = filtered_callbacks
        return removed_count

    # So-called internal methods (note: no set_running_or_notify_cancel()).

    def set_result(self, result):
        """Mark the future done and set its result.

        If the future is already done when this method is called, raises
        InvalidStateError.
        """
        if self._state != _PENDING:
            raise InvalidStateError
        self._result = result
        self._state = _FINISHED
        self._schedule_callbacks()

    def set_exception(self, exception):
        """ Mark the future done and set an exception.

        If the future is already done when this method is called, raises
        InvalidStateError.
        """
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
        if not self.done():
            self._blocking = True
            yield self  # This tells Task to wait for completion.
        assert self.done(), "yield from wasn't used with future"
        return self.result()  # May raise too.

    if __debug__:
        def __del__(self):
            if (not self._debug_result_requested and
                self._state != _CANCELLED and
                self._event_loop is not None):

                level = self._event_loop.get_log_level()
                if level > logging.WARNING:
                    return

                r_self = repr(self)

                if self._state == _PENDING:
                    tulip_log.error(
                        'Future abandoned before completion: %s', r_self)
                    if (self._debug_stack and level <= STACK_DEBUG):
                        tulip_log.error(self._debug_stack)

                else:
                    exc = self._exception
                    if exc is not None:
                        tulip_log.exception(
                            'Future raised an exception and '
                            'nobody caught it: %s', r_self,
                            exc_info=(exc.__class__, exc, exc.__traceback__))
                        if (self._debug_stack and level <= STACK_DEBUG):
                            tulip_log.error(self._debug_stack)
                    else:
                        tulip_log.error(
                            'Future result has not been requested: %s', r_self)
                        if (self._debug_stack and level <= STACK_DEBUG):
                            tulip_log.error(self._debug_stack)
