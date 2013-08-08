"""Selector eventloop for Unix with signal handling."""

import errno
import fcntl
import os
import socket
import stat
import sys

try:
    import signal
except ImportError:  # pragma: no cover
    signal = None

from . import constants
from . import events
from . import selector_events
from . import transports
from .log import tulip_log


__all__ = ['SelectorEventLoop']


if sys.platform == 'win32':  # pragma: no cover
    raise ImportError('Signals are not really supported on Windows')


class SelectorEventLoop(selector_events.BaseSelectorEventLoop):
    """Unix event loop

    Adds signal handling to SelectorEventLoop
    """

    def __init__(self, selector=None):
        super().__init__(selector)
        self._signal_handlers = {}

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

        handle = events.make_handle(callback, args)
        self._signal_handlers[sig] = handle

        try:
            signal.signal(sig, self._handle_signal)
        except OSError as exc:
            del self._signal_handlers[sig]
            if not self._signal_handlers:
                try:
                    signal.set_wakeup_fd(-1)
                except ValueError as nexc:
                    tulip_log.info('set_wakeup_fd(-1) failed: %s', nexc)

            if exc.errno == errno.EINVAL:
                raise RuntimeError('sig {} cannot be caught'.format(sig))
            else:
                raise

    def _handle_signal(self, sig, arg):
        """Internal helper that is the actual signal handler."""
        handle = self._signal_handlers.get(sig)
        if handle is None:
            return  # Assume it's some race condition.
        if handle._cancelled:
            self.remove_signal_handler(sig)  # Remove it properly.
        else:
            self._add_callback_signalsafe(handle)

    def remove_signal_handler(self, sig):
        """Remove a handler for a signal.  UNIX only.

        Return True if a signal handler was removed, False if not.
        """
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
                tulip_log.info('set_wakeup_fd(-1) failed: %s', exc)

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
            raise ValueError(
                'sig {} out of range(1, {})'.format(sig, signal.NSIG))

    def _make_read_pipe_transport(self, pipe, protocol, waiter=None,
                                  extra=None):
        return _UnixReadPipeTransport(self, pipe, protocol, waiter, extra)

    def _make_write_pipe_transport(self, pipe, protocol, waiter=None,
                                   extra=None):
        return _UnixWritePipeTransport(self, pipe, protocol, waiter, extra)


def _set_nonblocking(fd):
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    flags = flags | os.O_NONBLOCK
    fcntl.fcntl(fd, fcntl.F_SETFL, flags)


class _UnixReadPipeTransport(transports.ReadTransport):

    max_size = 256 * 1024  # max bytes we read in one eventloop iteration

    def __init__(self, loop, pipe, protocol, waiter=None, extra=None):
        super().__init__(extra)
        self._extra['pipe'] = pipe
        self._loop = loop
        self._pipe = pipe
        self._fileno = pipe.fileno()
        _set_nonblocking(self._fileno)
        self._protocol = protocol
        self._closing = False
        self._loop.add_reader(self._fileno, self._read_ready)
        self._loop.call_soon(self._protocol.connection_made, self)
        if waiter is not None:
            self._loop.call_soon(waiter.set_result, None)

    def _read_ready(self):
        try:
            data = os.read(self._fileno, self.max_size)
        except (BlockingIOError, InterruptedError):
            pass
        except OSError as exc:
            self._fatal_error(exc)
        else:
            if data:
                self._protocol.data_received(data)
            else:
                self._loop.remove_reader(self._fileno)
                self._protocol.eof_received()

    def pause(self):
        self._loop.remove_reader(self._fileno)

    def resume(self):
        self._loop.add_reader(self._fileno, self._read_ready)

    def close(self):
        if not self._closing:
            self._close(None)

    def _fatal_error(self, exc):
        # should be called by exception handler only
        tulip_log.exception('Fatal error for %s', self)
        self._close(exc)

    def _close(self, exc):
        self._closing = True
        self._loop.remove_reader(self._fileno)
        self._loop.call_soon(self._call_connection_lost, exc)

    def _call_connection_lost(self, exc):
        try:
            self._protocol.connection_lost(exc)
        finally:
            self._pipe.close()


class _UnixWritePipeTransport(transports.WriteTransport):

    def __init__(self, loop, pipe, protocol, waiter=None, extra=None):
        super().__init__(extra)
        self._extra['pipe'] = pipe
        self._loop = loop
        self._pipe = pipe
        self._fileno = pipe.fileno()
        _set_nonblocking(self._fileno)
        self._protocol = protocol
        self._buffer = []
        self._conn_lost = 0
        self._closing = False  # Set when close() or write_eof() called.
        self._writing = True
        # Do nothing if it is a regular file.
        # Enable hack only if pipe is FIFO object.
        # Look on twisted.internet.process:ProcessWriter.__init__
        if stat.S_ISFIFO(os.fstat(self._fileno).st_mode):
            self._enable_read_hack = True
        else:
            # If the pipe is not a unix pipe, then the read hack is never
            # applicable.  This case arises when _UnixWritePipeTransport
            # is used by subprocess and stdout/stderr
            # are redirected to a normal file.
            self._enable_read_hack = False
        if self._enable_read_hack:
            self._loop.add_reader(self._fileno, self._read_ready)

        self._loop.call_soon(self._protocol.connection_made, self)
        if waiter is not None:
            self._loop.call_soon(waiter.set_result, None)

    def _read_ready(self):
        # pipe was closed by peer
        self._close()

    def write(self, data):
        assert isinstance(data, bytes), repr(data)
        assert not self._closing
        if not data:
            return

        if self._conn_lost:
            if self._conn_lost >= constants.LOG_THRESHOLD_FOR_CONNLOST_WRITES:
                tulip_log.warning('os.write(pipe, data) raised exception.')
            self._conn_lost += 1
            return

        if not self._buffer and self._writing:
            # Attempt to send it right away first.
            try:
                n = os.write(self._fileno, data)
            except (BlockingIOError, InterruptedError):
                n = 0
            except Exception as exc:
                self._conn_lost += 1
                self._fatal_error(exc)
                return
            if n == len(data):
                return
            elif n > 0:
                data = data[n:]
            self._loop.add_writer(self._fileno, self._write_ready)

        self._buffer.append(data)

    def _write_ready(self):
        if not self._writing:
            return

        data = b''.join(self._buffer)
        assert data, 'Data should not be empty'

        self._buffer.clear()
        try:
            n = os.write(self._fileno, data)
        except (BlockingIOError, InterruptedError):
            self._buffer.append(data)
        except Exception as exc:
            self._conn_lost += 1
            self._fatal_error(exc)
        else:
            if n == len(data):
                self._loop.remove_writer(self._fileno)
                if self._closing:
                    if self._enable_read_hack:
                        self._loop.remove_reader(self._fileno)
                    self._call_connection_lost(None)
                return
            elif n > 0:
                data = data[n:]

            self._buffer.append(data)  # Try again later.

    def can_write_eof(self):
        return True

    def write_eof(self):
        assert not self._closing
        assert self._pipe
        self._closing = True
        if not self._buffer:
            if self._enable_read_hack:
                self._loop.remove_reader(self._fileno)
            self._loop.call_soon(self._call_connection_lost, None)

    def close(self):
        if not self._closing:
            # write_eof is all what we needed to close the write pipe
            self.write_eof()

    def abort(self):
        self._close(None)

    def _fatal_error(self, exc):
        # should be called by exception handler only
        tulip_log.exception('Fatal error for %s', self)
        self._close(exc)

    def _close(self, exc=None):
        self._closing = True
        self._buffer.clear()
        self._loop.remove_writer(self._fileno)
        if self._enable_read_hack:
            self._loop.remove_reader(self._fileno)
        self._loop.call_soon(self._call_connection_lost, exc)

    def _call_connection_lost(self, exc):
        try:
            self._protocol.connection_lost(exc)
        finally:
            self._pipe.close()

    def pause_writing(self):
        if self._writing:
            if self._buffer:
                self._loop.remove_writer(self._fileno)
            self._writing = False

    def resume_writing(self):
        if not self._writing:
            if self._buffer:
                self._loop.add_writer(self._fileno, self._write_ready)
            self._writing = True

    def discard_output(self):
        if self._buffer:
            self._loop.remove_writer(self._fileno)
            self._buffer.clear()
