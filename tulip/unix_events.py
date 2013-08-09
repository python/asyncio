"""Selector eventloop for Unix with signal handling."""

import collections
import errno
import fcntl
import functools
import os
import socket
import stat
import subprocess
import sys
import weakref

try:
    import signal
except ImportError:  # pragma: no cover
    signal = None

from . import constants
from . import events
from . import protocols
from . import selector_events
from . import tasks
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
        self._subprocesses = weakref.WeakValueDictionary()

    def _socketpair(self):
        return socket.socketpair()

    def close(self):
        if signal is not None:
            handler = self._signal_handlers.get(signal.SIGCHLD)
            if handler is not None:
                self.remove_signal_handler(signal.SIGCHLD)
        super().close()

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

    @tasks.coroutine
    def _make_subprocess_transport(self, protocol, args, shell,
                                   stdin, stdout, stderr, bufsize,
                                   extra=None, **kwargs):
        self._reg_sigchld()
        transp = _UnixSubprocessTransport(self, protocol, args, shell,
                                          stdin, stdout, stderr, bufsize,
                                          extra=None, **kwargs)
        self._subprocesses[transp.get_pid()] = transp
        yield from transp._post_init()
        return transp

    def _reg_sigchld(self):
        assert signal, "signal support is required"
        if signal.SIGCHLD not in self._signal_handlers:
            self.add_signal_handler(signal.SIGCHLD,
                                    self._sig_chld)

    def _sig_chld(self):
        try:
            while True:
                grp = os.getpgrp()
                ret = os.waitid(os.P_PGID, grp,
                                os.WNOHANG|os.WNOWAIT|os.WEXITED)
                if ret is None:
                    break
                pid = ret.si_pid
                transp = self._subprocesses.get(pid)
                if transp is not None:
                    transp._poll()
        except ChildProcessError:
            pass


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
                self._closing = True
                self._loop.remove_reader(self._fileno)
                self._loop.call_soon(self._protocol.eof_received)
                self._loop.call_soon(self._call_connection_lost, None)

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


class _UnixWriteSubprocessPipeProto(protocols.BaseProtocol):
    pipe = None

    def __init__(self, proc, fd):
        self.proc = proc
        self.fd = fd
        self.connected = False
        self.disconnected = False
        proc._pipes[fd] = self

    def connection_made(self, transport):
        self.connected = True
        self.pipe = transport
        self.proc._pipe_connection_made(self.fd)

    def connection_lost(self, exc):
        self.disconnected = True
        self.proc._pipe_connection_lost(self.fd, exc)


class _UnixReadSubprocessPipeProto(_UnixWriteSubprocessPipeProto,
                                   protocols.Protocol):

    def data_received(self, data):
        self.proc._pipe_data_received(self.fd, data)

    def eof_received(self):
        pass


class _UnixSubprocessTransport(transports.SubprocessTransport):

    def __init__(self, loop, protocol, args, shell,
                 stdin, stdout, stderr, bufsize,
                 extra=None, **kwargs):
        super().__init__(extra)
        self._protocol = protocol
        self._loop = loop

        self._pipes = {}
        if stdin == subprocess.PIPE:
            self._pipes[0] = None
        if stdout == subprocess.PIPE:
            self._pipes[1] = None
        if stderr == subprocess.PIPE:
            self._pipes[2] = None
        self._pending_calls = collections.deque()
        self._exited = False
        self._done = False

        self._proc = subprocess.Popen(
            args, shell=shell, stdin=stdin, stdout=stdout, stderr=stderr,
            universal_newlines=False, bufsize=bufsize, **kwargs)
        self._extra['subprocess'] = self._proc

        if not self._pipes:
            self._loop.call_soon(self._protocol.connection_made, self)

    def close(self):
        for proto in self._pipes.values():
            proto.pipe.close()
        self.terminate()

    def get_pid(self):
        return self._proc.pid

    def get_returncode(self):
        return self._proc.returncode

    def get_pipe_transport(self, fd):
        if fd in self._pipes:
            return self._pipes[fd].pipe
        else:
            return None

    def send_signal(self, signal):
        self._proc.send_signal(signal)

    def terminate(self):
        self._proc.terminate()

    def kill(self):
        self._proc.kill()

    @tasks.coroutine
    def _post_init(self):
        proc = self._proc
        loop = self._loop
        if proc.stdin is not None:
            transp, proto = yield from loop.connect_write_pipe(
                functools.partial(_UnixWriteSubprocessPipeProto, self, 0),
                proc.stdin)
        if proc.stdout is not None:
            transp, proto = yield from loop.connect_read_pipe(
                functools.partial(_UnixReadSubprocessPipeProto, self, 1),
                proc.stdout)
        if proc.stderr is not None:
            transp, proto = yield from loop.connect_read_pipe(
                functools.partial(_UnixReadSubprocessPipeProto, self, 2),
                proc.stderr)
        self._poll()

    def _call(self, cb, *data):
        if self._pending_calls is not None:
            self._pending_calls.append((cb, data))
        else:
            self._loop.call_soon(cb, *data)

    def _pipe_connection_made(self, fd):
        if all(p is not None and p.connected for p in self._pipes.values()):
            self._loop.call_soon(self._protocol.connection_made, self)
            for callback, data in self._pending_calls:
                self._loop.call_soon(callback, *data)
            self._pending_calls = None

    def _pipe_connection_lost(self, fd, exc):
        self._call(self._protocol.pipe_connection_lost, fd, exc)
        self._poll()

    def _pipe_data_received(self, fd, data):
        self._call(self._protocol.pipe_data_received, fd, data)

    def _poll(self):
        if not self._exited:
            returncode = self._proc.poll()
            if returncode is not None:
                self._exited = True
                self._call(self._protocol.process_exited)
        if self._exited and not self._done:
            if all(p is not None and p.disconnected
                   for p in self._pipes.values()):
                self._call(self._protocol.connection_lost, None)
                self._done = True
