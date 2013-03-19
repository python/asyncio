"""Selector eventloop for Unix with signal handling."""

import errno
import logging
import socket
import sys

try:
    import signal
except ImportError:  # pragma: no cover
    signal = None

from . import events
from . import selector_events


__all__ = ['SelectorEventLoop']


if sys.platform == 'win32':  # pragma: no cover
    raise ImportError('Signals are not really supported on Windows')


class SelectorEventLoop(selector_events.BaseSelectorEventLoop):
    """Unix event loop

    Adds signal handling to SelectorEventLoop
    """
    def _socketpair(self):
        return socket.socketpair()

    def add_signal_handler(self, sig, callback, *args):
        """Add a handler for a signal.  UNIX only.

        Raise ValueError if the signal number is invalid or uncatchable.
        Raise RuntimeError if there is a problem setting up the handler.
        """
        self._check_signal(sig)
        try:
            # set_wakeup_fd() raises ValueError if this is not the
            # main thread.  By calling it early we ensure that an
            # event loop running in another thread cannot add a signal
            # handler.
            signal.set_wakeup_fd(self._csock.fileno())
        except ValueError as exc:
            raise RuntimeError(str(exc))
        handler = events.make_handler(callback, args)
        self._signal_handlers[sig] = handler
        try:
            signal.signal(sig, self._handle_signal)
        except OSError as exc:
            del self._signal_handlers[sig]
            if not self._signal_handlers:
                try:
                    signal.set_wakeup_fd(-1)
                except ValueError as nexc:
                    logging.info('set_wakeup_fd(-1) failed: %s', nexc)
            if exc.errno == errno.EINVAL:
                raise RuntimeError('sig {} cannot be caught'.format(sig))
            else:
                raise
        return handler

    def _handle_signal(self, sig, arg):
        """Internal helper that is the actual signal handler."""
        handler = self._signal_handlers.get(sig)
        if handler is None:
            return  # Assume it's some race condition.
        if handler.cancelled:
            self.remove_signal_handler(sig)  # Remove it properly.
        else:
            self.call_soon_threadsafe(handler)

    def remove_signal_handler(self, sig):
        """Remove a handler for a signal.  UNIX only.

        Return True if a signal handler was removed, False if not."""
        self._check_signal(sig)
        try:
            del self._signal_handlers[sig]
        except KeyError:
            return False
        if sig == signal.SIGINT:
            handler = signal.default_int_handler
        else:
            handler = signal.SIG_DFL
        try:
            signal.signal(sig, handler)
        except OSError as exc:
            if exc.errno == errno.EINVAL:
                raise RuntimeError('sig {} cannot be caught'.format(sig))
            else:
                raise
        if not self._signal_handlers:
            try:
                signal.set_wakeup_fd(-1)
            except ValueError as exc:
                logging.info('set_wakeup_fd(-1) failed: %s', exc)
        return True

    def _check_signal(self, sig):
        """Internal helper to validate a signal.

        Raise ValueError if the signal number is invalid or uncatchable.
        Raise RuntimeError if there is a problem setting up the handler.
        """
        if not isinstance(sig, int):
            raise TypeError('sig must be an int, not {!r}'.format(sig))
        if signal is None:
            raise RuntimeError('Signals are not supported')
        if not (1 <= sig < signal.NSIG):
            raise ValueError('sig {} out of range(1, {})'.format(sig,
                                                                 signal.NSIG))
