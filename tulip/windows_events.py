"""Selector and proactor eventloops for Windows."""

import errno
import socket
import weakref
import struct
import _winapi

from . import futures
from . import proactor_events
from . import selector_events
from . import windows_utils
from . import _overlapped
from .log import tulip_log


__all__ = ['SelectorEventLoop', 'ProactorEventLoop', 'IocpProactor']


NULL = 0
INFINITE = 0xffffffff
ERROR_CONNECTION_REFUSED = 1225
ERROR_CONNECTION_ABORTED = 1236


class _OverlappedFuture(futures.Future):
    """Subclass of Future which represents an overlapped operation.

    Cancelling it will immediately cancel the overlapped operation.
    """

    def __init__(self, ov, *, loop=None):
        super().__init__(loop=loop)
        self.ov = ov

    def cancel(self):
        try:
            self.ov.cancel()
        except OSError:
            pass
        return super().cancel()


class SelectorEventLoop(selector_events.BaseSelectorEventLoop):
    def _socketpair(self):
        return windows_utils.socketpair()


class ProactorEventLoop(proactor_events.BaseProactorEventLoop):
    def __init__(self, proactor=None):
        if proactor is None:
            proactor = IocpProactor()
        super().__init__(proactor)

    def _socketpair(self):
        return windows_utils.socketpair()


class IocpProactor:

    def __init__(self, concurrency=0xffffffff):
        self._loop = None
        self._results = []
        self._iocp = _overlapped.CreateIoCompletionPort(
            _overlapped.INVALID_HANDLE_VALUE, NULL, 0, concurrency)
        self._cache = {}
        self._registered = weakref.WeakSet()
        self._stopped_serving = weakref.WeakSet()

    def set_loop(self, loop):
        self._loop = loop

    def select(self, timeout=None):
        if not self._results:
            self._poll(timeout)
        tmp = self._results
        self._results = []
        return tmp

    def recv(self, conn, nbytes, flags=0):
        self._register_with_iocp(conn)
        ov = _overlapped.Overlapped(NULL)
        handle = getattr(conn, 'handle', None)
        if handle is None:
            ov.WSARecv(conn.fileno(), nbytes, flags)
        else:
            ov.ReadFile(handle, nbytes)
        return self._register(ov, conn, ov.getresult)

    def send(self, conn, buf, flags=0):
        self._register_with_iocp(conn)
        ov = _overlapped.Overlapped(NULL)
        handle = getattr(conn, 'handle', None)
        if handle is None:
            ov.WSASend(conn.fileno(), buf, flags)
        else:
            ov.WriteFile(handle, buf)
        return self._register(ov, conn, ov.getresult)

    def accept(self, listener):
        self._register_with_iocp(listener)
        conn = self._get_accept_socket()
        ov = _overlapped.Overlapped(NULL)
        ov.AcceptEx(listener.fileno(), conn.fileno())

        def finish_accept():
            ov.getresult()
            buf = struct.pack('@P', listener.fileno())
            conn.setsockopt(socket.SOL_SOCKET,
                            _overlapped.SO_UPDATE_ACCEPT_CONTEXT,
                            buf)
            conn.settimeout(listener.gettimeout())
            return conn, conn.getpeername()

        return self._register(ov, listener, finish_accept)

    def connect(self, conn, address):
        self._register_with_iocp(conn)
        # the socket needs to be locally bound before we call ConnectEx()
        try:
            _overlapped.BindLocal(conn.fileno(), len(address))
        except OSError as e:
            if e.winerror != errno.WSAEINVAL:
                raise
            # probably already locally bound; check using getsockname()
            if conn.getsockname()[1] == 0:
                raise
        ov = _overlapped.Overlapped(NULL)
        ov.ConnectEx(conn.fileno(), address)

        def finish_connect():
            ov.getresult()
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
        f = _OverlappedFuture(ov, loop=self._loop)
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
            if obj in self._stopped_serving:
                f.cancel()
            elif not f.cancelled():
                try:
                    value = callback()
                except OSError as e:
                    f.set_exception(e)
                    self._results.append(f)
                else:
                    f.set_result(value)
                    self._results.append(f)
            ms = 0

    def stop_serving(self, obj):
        # obj is a socket or pipe handle.  It will be closed in
        # BaseProactorEventLoop.stop_serving() which will make any
        # pending operations fail quickly.
        self._stopped_serving.add(obj)

    def close(self):
        for (f, ov, obj, callback) in self._cache.values():
            try:
                ov.cancel()
            except OSError:
                pass

        while self._cache:
            if not self._poll(1):
                tulip_log.debug('taking long time to close proactor')

        self._results = []
        if self._iocp is not None:
            _winapi.CloseHandle(self._iocp)
            self._iocp = None
