#
# Module implementing the Proactor pattern
#
# A proactor is used to initiate asynchronous I/O, and to wait for
# completion of previously initiated operations.
#

import errno
import logging
import os
import sys
import socket
import time
import weakref

from _winapi import CloseHandle

from . import base_events
from . import futures
from . import transports
from . import selector_events
from . import winsocketpair
from . import _overlapped


NULL = 0
INFINITE = 0xffffffff
ERROR_CONNECTION_REFUSED = 1225
ERROR_CONNECTION_ABORTED = 1236


class IocpProactor:

    def __init__(self, concurrency=0xffffffff):
        self._results = []
        self._iocp = _overlapped.CreateIoCompletionPort(
            _overlapped.INVALID_HANDLE_VALUE, NULL, 0, concurrency)
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
        ov = _overlapped.Overlapped(NULL)
        ov.WSARecv(conn.fileno(), nbytes, flags)
        return self._register(ov, conn, ov.getresult)

    def send(self, conn, buf, flags=0):
        self._register_with_iocp(conn)
        ov = _overlapped.Overlapped(NULL)
        ov.WSASend(conn.fileno(), buf, flags)
        return self._register(ov, conn, ov.getresult)

    def accept(self, listener):
        self._register_with_iocp(listener)
        conn = self._get_accept_socket()
        ov = _overlapped.Overlapped(NULL)
        ov.AcceptEx(listener.fileno(), conn.fileno())
        def finish_accept():
            addr = ov.getresult()
            conn.setsockopt(socket.SOL_SOCKET,
                            _overlapped.SO_UPDATE_ACCEPT_CONTEXT,
                            listener.fileno())
            conn.settimeout(listener.gettimeout())
            return conn, conn.getpeername()
        return self._register(ov, listener, finish_accept)

    def connect(self, conn, address):
        self._register_with_iocp(conn)
        _overlapped.BindLocal(conn.fileno(), len(address))
        ov = _overlapped.Overlapped(NULL)
        ov.ConnectEx(conn.fileno(), address)
        def finish_connect():
            try:
                ov.getresult()
            except OSError as e:
                if e.winerror == ERROR_CONNECTION_REFUSED:
                    raise ConnectionRefusedError(errno.ECONNREFUSED,
                                                 'connection refused')
                raise
            conn.setsockopt(socket.SOL_SOCKET,
                            _overlapped.SO_UPDATE_CONNECT_CONTEXT,
                            0)
            return conn
        return self._register(ov, conn, finish_connect)

    def _register_with_iocp(self, obj):
        if obj not in self._registered:
            self._registered.add(obj)
            _overlapped.CreateIoCompletionPort(obj.fileno(), self._iocp, 0, 0)

    def _register(self, ov, obj, callback):
        f = futures.Future()
        self._cache[ov.address] = (f, ov, obj, callback)
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
            status = _overlapped.GetQueuedCompletionStatus(self._iocp, ms)
            if status is None:
                return
            address = status[3]
            f, ov, obj, callback = self._cache.pop(address)
            try:
                value = callback()
            except OSError as e:
                f.set_exception(e)
                self._results.append(f)
            else:
                f.set_result(value)
                self._results.append(f)
            ms = 0

    def close(self):
        for (f, ov, obj, callback) in self._cache.values():
            try:
                ov.cancel()
            except OSError:
                pass

        while self._cache:
            if not self._poll(1000):
                logging.debug('taking long time to close proactor')

        self._results = []
        if self._iocp is not None:
            CloseHandle(self._iocp)
            self._iocp = None


class _IocpSocketTransport(transports.Transport):

    def __init__(self, event_loop, sock, protocol, waiter=None):
        self._event_loop = event_loop
        self._sock = sock
        self._protocol = protocol
        self._buffer = []
        self._read_fut = None
        self._write_fut = None
        self._closing = False  # Set when close() called.
        self._event_loop.call_soon(self._protocol.connection_made, self)
        self._event_loop.call_soon(self._loop_reading)
        if waiter is not None:
            self._event_loop.call_soon(waiter.set_result, None)

    def _loop_reading(self, f=None):
        try:
            assert f is self._read_fut
            if f:
                data = f.result()
                if not data:
                    self._event_loop.call_soon(self._protocol.eof_received)
                    self._read_fut = None
                    return
                self._event_loop.call_soon(self._protocol.data_received, data)
            self._read_fut = self._event_loop._proactor.recv(self._sock, 4096)
        except OSError as exc:
            if exc.winerror == ERROR_CONNECTION_ABORTED and self._closing:
                return
            self._fatal_error(exc)
        else:
            self._read_fut.add_done_callback(self._loop_reading)

    def write(self, data):
        assert isinstance(data, bytes)
        assert not self._closing
        if not data:
            return
        self._buffer.append(data)
        if not self._write_fut:
            self._loop_writing()

    def _loop_writing(self, f=None):
        try:
            assert f is self._write_fut
            if f:
                f.result()
            data = b''.join(self._buffer)
            self._buffer = []
            if not data:
                self._write_fut = None
                return
            self._write_fut = self._event_loop._proactor.send(self._sock, data)
        except OSError as exc:
            self._fatal_error(exc)
        else:
            self._write_fut.add_done_callback(self._loop_writing)

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
        if self._read_fut:            # XXX
            self._read_fut.cancel()
        self._write_fut = self._read_fut = None
        self._buffer = []
        self._event_loop.call_soon(self._call_connection_lost, exc)

    def _call_connection_lost(self, exc):
        try:
            self._protocol.connection_lost(exc)
        finally:
            self._sock.close()


class IocpEventLoop(base_events.BaseEventLoop):

    SocketTransport = _IocpSocketTransport

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
        self._make_self_pipe()

    def close(self):
        if self._proactor is not None:
            self._proactor.close()
            self._proactor = None
            self._selector = None
        self._ssock.close()
        self._csock.close()

    def sock_recv(self, sock, n):
        return self._proactor.recv(sock, n)

    def sock_sendall(self, sock, data):
        return self._proactor.send(sock, data)

    def sock_connect(self, sock, address):
        return self._proactor.connect(sock, address)

    def sock_accept(self, sock):
        return self._proactor.accept(sock)

    def _make_self_pipe(self):
        # A self-socket, really. :-)
        self._ssock, self._csock = winsocketpair.socketpair()
        self._ssock.setblocking(False)
        self._csock.setblocking(False)
        def loop(f=None):
            try:
                if f:
                    f.result()      # may raise
                f = self._proactor.recv(self._ssock, 4096)
            except:
                self.close()
                raise
            else:
                f.add_done_callback(loop)
        self.call_soon(loop)

    def _write_to_self(self):
        self._csock.send(b'x')

    def _start_serving(self, protocol_factory, sock):
        def loop(f=None):
            try:
                if f:
                    conn, addr = f.result()
                    protocol = protocol_factory()
                    transport = self.SocketTransport(self, conn, protocol)
                f = self._proactor.accept(sock)
            except OSError as exc:
                sock.close()
                logging.exception('Accept failed')
            else:
                f.add_done_callback(loop)
        self.call_soon(loop)

    def _process_events(self, event_list):
        pass    # XXX hard work currently done in poll
