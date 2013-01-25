#
# Module implementing the Proactor pattern
#
# A proactor is used to initiate asynchronous I/O, and to wait for
# completion of previously initiated operations.
#

import errno
import logging
import os
import heapq
import sys
import socket
import time
import weakref

from _winapi import CloseHandle

from . import transports

from .futures import Future
from .unix_events import BaseEventLoop, _StopError
from .winsocketpair import socketpair
from ._overlapped import *


_TRYAGAIN = frozenset()     # XXX


class IocpProactor:

    def __init__(self, concurrency=0xffffffff):
        self._results = []
        self._iocp = CreateIoCompletionPort(
            INVALID_HANDLE_VALUE, NULL, 0, concurrency)
        self._cache = {}
        self._registered = weakref.WeakSet()

    def registered_count(self):
        return len(self._cache)

    def select(self, timeout=None):
        if not self._results:
            self._poll(timeout)
        tmp, self._results = self._results, []
        return tmp

    def recv(self, conn, nbytes, flags=0):
        self._register_with_iocp(conn)
        ov = Overlapped(NULL)
        ov.WSARecv(conn.fileno(), nbytes, flags)
        return self._register(ov, conn, ov.getresult)

    def send(self, conn, buf, flags=0):
        self._register_with_iocp(conn)
        ov = Overlapped(NULL)
        ov.WSASend(conn.fileno(), buf, flags)
        return self._register(ov, conn, ov.getresult)

    def accept(self, listener):
        self._register_with_iocp(listener)
        conn = self._get_accept_socket()
        ov = Overlapped(NULL)
        ov.AcceptEx(listener.fileno(), conn.fileno())
        def finish_accept():
            addr = ov.getresult()
            conn.setsockopt(socket.SOL_SOCKET,
                            SO_UPDATE_ACCEPT_CONTEXT, listener.fileno())
            conn.settimeout(listener.gettimeout())
            return conn, conn.getpeername()
        return self._register(ov, listener, finish_accept)

    def connect(self, conn, address):
        self._register_with_iocp(conn)
        BindLocal(conn.fileno(), len(address))
        ov = Overlapped(NULL)
        ov.ConnectEx(conn.fileno(), address)
        def finish_connect():
            try:
                ov.getresult()
            except OSError as e:
                if e.winerror == 1225:
                    raise ConnectionRefusedError(errno.ECONNREFUSED,
                                                 'connection refused')
                raise
            conn.setsockopt(socket.SOL_SOCKET,
                            SO_UPDATE_CONNECT_CONTEXT, 0)
            return conn
        return self._register(ov, conn, finish_connect)

    def _register_with_iocp(self, obj):
        if obj not in self._registered:
            self._registered.add(obj)
            CreateIoCompletionPort(obj.fileno(), self._iocp, 0, 0)
            SetFileCompletionNotificationModes(obj.fileno(),
                FILE_SKIP_COMPLETION_PORT_ON_SUCCESS)

    def _register(self, ov, obj, callback):
        f = Future()
        if ov.error == ERROR_IO_PENDING:
            # we must prevent ov and obj from being garbage collected
            self._cache[ov.address] = (f, ov, obj, callback)
        else:
            try:
                f.set_result(callback())
            except Exception as e:
                f.set_exception(e)
        return f

    def _get_accept_socket(self):
        s = socket.socket()
        s.settimeout(0)
        return s

    def _poll(self, timeout=None):
        if timeout is None:
            ms = INFINITE
        elif timeout < 0:
            raise ValueError("negative timeout")
        else:
            ms = int(timeout * 1000 + 0.5)
            if ms >= INFINITE:
                raise ValueError("timeout too big")
        while True:
            status = GetQueuedCompletionStatus(self._iocp, ms)
            if status is None:
                return
            f, ov, obj, callback = self._cache.pop(status[3])
            try:
                value = callback()
            except OSError as e:
                if f is None:
                    sys.excepthook(*sys.exc_info())
                    continue
                f.set_exception(e)
                self._results.append(f)
            else:
                if f is None:
                    continue
                f.set_result(value)
                self._results.append(f)
            ms = 0

    def close(self, *, CloseHandle=CloseHandle):
        for address, (f, ov, obj, callback) in list(self._cache.items()):
            try:
                ov.cancel()
            except OSError:
                pass

        while self._cache:
            status = GetQueuedCompletionStatus(self._iocp, 1000)
            if status is None:
                logging.debug('taking long time to close proactor')
                continue
            self._cache.pop(status[3])

        if self._iocp is not None:
            CloseHandle(self._iocp)
            self._iocp = None


class IocpEventLoop(BaseEventLoop):

    @staticmethod
    def SocketTransport(*args, **kwds):
        return _IocpSocketTransport(*args, **kwds)

    @staticmethod
    def SslTransport(*args, **kwds):
        raise NotImplementedError

    def __init__(self, proactor=None):
        super().__init__()
        if proactor is None:
            proactor = IocpProactor()
            logging.debug('Using proactor: %s', proactor.__class__.__name__)
        self._proactor = proactor
        self._selector = proactor   # convenient alias
        self._readers = {}
        self._make_self_pipe()

    def close(self):
        if self._proactor is not None:
            self._proactor.close()
            self._proactor = None
        self._ssock.close()
        self._csock.close()

    def _make_self_pipe(self):
        # A self-socket, really. :-)
        self._ssock, self._csock = socketpair()
        self._ssock.setblocking(False)
        self._csock.setblocking(False)
        def loop(f=None):
            if f and f.exception():
                self.close()
                raise f.exception()
            f = self._proactor.recv(self._ssock, 4096)
            self.call_soon(f.add_done_callback, loop)
        self.call_soon(loop)

    def _write_to_self(self):
        self._proactor.send(self._csock, b'x')

    def _start_serving(self, protocol_factory, sock):
        def loop(f=None):
            try:
                if f:
                    conn, addr = f.result()
                    protocol = protocol_factory()
                    transport = self.SocketTransport(self, conn, protocol)
                f = self._proactor.accept(sock)
                self.call_soon(f.add_done_callback, loop)
            except OSError as exc:
                if exc.errno in _TRYAGAIN:
                    self.call_soon(loop)
                else:
                    sock.close()
                    logging.exception('Accept failed')
        self.call_soon(loop)

    def sock_recv(self, sock, n):
        return self._proactor.recv(sock, n)

    def sock_sendall(self, sock, data):
        return self._proactor.send(sock, data)

    def sock_connect(self, sock, address):
        return self._proactor.connect(sock, address)

    def sock_accept(self, sock):
        return self._proactor.accept(sock)

    def _process_events(self, event_list):
        pass    # XXX hard work currently done in poll


class _IocpSocketTransport(transports.Transport):

    def __init__(self, event_loop, sock, protocol, waiter=None):
        self._event_loop = event_loop
        self._sock = sock
        self._protocol = protocol
        self._buffer = []
        self._write_fut = None
        self._closing = False  # Set when close() called.
        self._event_loop.call_soon(self._protocol.connection_made, self)
        self._event_loop.call_soon(self._loop_reading)
        if waiter is not None:
            self._event_loop.call_soon(waiter.set_result, None)

    def _loop_reading(self, f=None):
        try:
            if f:
                data = f.result()
                if not data:
                    self._event_loop.call_soon(self._protocol.eof_received)
                    return
                self._event_loop.call_soon(self._protocol.data_received, data)
            f = self._event_loop._proactor.recv(self._sock, 4096)
            self._event_loop.call_soon(
                f.add_done_callback, self._loop_reading)
        except OSError as exc:
            self._fatal_error(exc)

    def write(self, data):
        assert isinstance(data, bytes)
        assert not self._closing
        if not data:
            return

        if self._write_fut is not None:
            self._buffer.append(data)
            return

        def callback(f):
            if f.exception():
                self._fatal_error(f.exception())
            # XXX should check for partial write
            data = b''.join(self._buffer)
            if data:
                self._buffer = []
                self._write_fut = self._event_loop._proactor.send(
                    self._sock, data)
                assert f is self._write_fut
                self._write_fut = None

        self._write_fut = self._event_loop._proactor.send(self._sock, data)
        self._write_fut.add_done_callback(callback)

    # TODO: write_eof(), can_write_eof().

    def abort(self):
        self._fatal_error(None)

    def close(self):
        self._closing = True
        if self._write_fut:
            self._write_fut.cancel()
        if not self._buffer:
            self._event_loop.call_soon(self._call_connection_lost, None)

    def _fatal_error(self, exc):
        logging.exception('Fatal error for %s', self)
        if self._write_fut:
            self._write_fut.cancel()
        # if self._read_fut:            # XXX
        #     self._read_fut.cancel()
        self._write_fut = self._read_fut = None
        self._buffer = []
        self._event_loop.call_soon(self._call_connection_lost, exc)

    def _call_connection_lost(self, exc):
        try:
            self._protocol.connection_lost(exc)
        finally:
            self._sock.close()
