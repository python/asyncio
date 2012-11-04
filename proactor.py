#
# Module implementing the Proactor pattern
#
# A proactor is used to initiate asynchronous I/O, and to wait for
# completion of previously initiated operations.
#

import os
import sys
import errno
import socket
import select
import time
import warnings


__all__ = ['SelectProactor']

#
# Future class
#

class Future:

    def __init__(self):
        self._callbacks = []

    def result(self):
        # does not block for operation to complete
        assert self.done()
        if self.success:
            return self.value
        else:
            raise self.value

    def set_result(self, value):
        assert not self.done()
        self.success = True
        self.value = value
        self._invoke_callbacks()

    def set_exception(self, value):
        assert not self.done()
        self.success = False
        self.value = value
        self._invoke_callbacks()

    def done(self):
        return hasattr(self, 'success')

    def add_done_callback(self, func):
        if self.done():
            func(self)
        else:
            self._callbacks.append(func)

    def _invoke_callbacks(self):
        for func in self._callbacks:
            try:
                func(self)
            except Exception:
                sys.excepthook(*sys.exc_info())
        del self._callbacks

#
# Base class for all proactors
#

class BaseProactor:
    _Future = Future

    def __init__(self):
        self._results = []

    def poll(self, timeout=None):
        if not self._results:
            self._poll(timeout)
        tmp, self._results = self._results, []
        return tmp

    def filteredpoll(self, penders, timeout=None):
        if timeout is None:
            deadline = None
        elif timeout < 0:
            raise ValueError('negative timeout')
        else:
            deadline = time.monotonic() + timeout
        S = set(penders)
        while True:
            filtered = [x for x in self._results if x[0] in S]
            if filtered:
                self._results = [x for x in self._results if x[0] not in S]
                return filtered
            self._poll(timeout)
            if deadline is not None:
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    break

    def close(self):
        pass

#
# Initiator methods for proactors based on select()/poll()/epoll()/kqueue()
#

READABLE = 0
WRITABLE = 1

class ReadyBaseProactor(BaseProactor):
    eager = True

    def __init__(self):
        super().__init__()
        self._queue = [{}, {}]

    def pollable(self):
        return any(self._queue)

    def recv(self, sock, nbytes, flags=0):
        return self._register(self.eager, sock.fileno(), READABLE,
                              sock.recv, nbytes, flags)

    def send(self, sock, buf, flags=0):
        return self._register(self.eager, sock.fileno(), WRITABLE,
                              sock.send, buf, flags)

    def accept(self, sock):
        def _accept():
            conn, addr = sock.accept()
            conn.settimeout(0)
            return conn, addr
        return self._register(self.eager, sock.fileno(), READABLE, _accept)

    def connect(self, sock, addr):
        assert sock.gettimeout() == 0
        err = sock.connect_ex(addr)
        if err not in self._connection_errors:
            raise OSError(err, os.strerror(err))
        def _connect():
            err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if err != 0:
                raise OSError(err, os.strerror(err))
        return self._register(False, sock.fileno(), WRITABLE, _connect)

    # hacks to support SSL
    def _readable(self, sock):
        return self._register(False, sock.fileno(), READABLE, lambda:None)

    def _writable(self, sock):
        return self._register(False, sock.fileno(), WRITABLE, lambda:None)

#
# Proactor using select()
#

class SelectProactor(ReadyBaseProactor):
    _connection_errors = {0, errno.EINPROGRESS}
    _select = select.select

    def _poll(self, timeout=None):
        rfds, wfds, xfds = self._select(self._queue[READABLE].keys(),
                                        self._queue[WRITABLE].keys(),
                                        (), timeout)
        for fd in rfds:
            self._handle(fd, READABLE)
        for fd in wfds:
            self._handle(fd, WRITABLE)

    def _handle(self, fd, kind):
        Q = self._queue[kind][fd]
        f, callback, args = Q.pop(0)
        try:
            f.set_result(callback(*args))
        except OSError as e:
            f.set_exception(e)
        self._results.append(f)
        if not Q:
            del self._queue[kind][fd]

    def _register(self, eager, fd, kind, callback, *args):
        f = self._Future()
        if eager:
            try:
                f.set_result(callback(*args))
                return f
            except BlockingIOError:
                pass
        queue = self._queue[kind]
        if fd not in queue:
            queue[fd] = []
        queue[fd].append((f, callback, args))
        return f

    if sys.platform == 'win32':
        # Windows insists on being awkward...
        _connection_errors = {0, errno.WSAEWOULDBLOCK}

        def _select(self, rfds, wfds, _, timeout=None):
            if not (rfds or wfds):
                time.sleep(timeout)
                return [], [], []
            else:
                rfds, wfds, xfds = select.select(rfds, wfds, wfds, timeout)
                return rfds, wfds + xfds, []


#
# Proactor using poll()
#

if hasattr(select, 'poll'):
    __all__.append('PollProactor')

    from select import POLLIN, POLLPRI, POLLOUT, POLLHUP, POLLERR, POLLNVAL

    FLAG = [POLLIN, POLLOUT]
    READ_EXTRA_FLAGS = POLLIN | POLLHUP | POLLNVAL | POLLERR
    WRITE_EXTRA_FLAGS = POLLOUT | POLLHUP | POLLNVAL | POLLERR

    class PollProactor(ReadyBaseProactor):
        _connection_errors = {0, errno.EINPROGRESS}
        _make_poller = select.poll
        _uses_msecs = True

        def __init__(self):
            super().__init__()
            self._poller = self._make_poller()
            self._flag = {}

        def _poll(self, timeout=None):
            if timeout is None:
                timeout = -1
            elif timeout < 0:
                raise ValueError('negative timeout')
            elif self._uses_msecs:
                timeout = int(timeout*1000 + 0.5)
            ready = self._poller.poll(timeout)
            for fd, flags in ready:
                if fd in self._queue[READABLE] and flags & READ_EXTRA_FLAGS:
                    self._handle(fd, READABLE)
                if fd in self._queue[WRITABLE] and flags & WRITE_EXTRA_FLAGS:
                    self._handle(fd, WRITABLE)

        def _handle(self, fd, kind):
            Q = self._queue[kind][fd]
            f, callback, args = Q.pop(0)
            try:
                f.set_result(callback(*args))
            except OSError as e:
                f.set_exception(e)
            self._results.append(f)
            if not Q:
                del self._queue[kind][fd]
                flag = self._flag[fd] = self._flag[fd] & ~FLAG[kind]
                if flag == 0:
                    del self._flag[fd]
                    self._poller.unregister(fd)
                else:
                    self._poller.modify(fd, flag)

        def _register(self, eager, fd, kind, callback, *args):
            f = self._Future()
            if eager:
                try:
                    f.set_result(callback(*args))
                    return f
                except BlockingIOError:
                    pass
            queue = self._queue[kind]
            if fd not in queue:
                queue[fd] = []
                old_flag = self._flag.get(fd, 0)
                flag = self._flag[fd] = old_flag | FLAG[kind]
                if old_flag == 0:
                    self._poller.register(fd, flag)
                else:
                    self._poller.modify(fd, flag)
            queue[fd].append((f, callback, args))
            return f

#
# Proactor using epoll()
#

if hasattr(select, 'epoll'):
    assert (select.EPOLLIN, select.EPOLLOUT) == (POLLIN, POLLOUT)

    __all__.append('EpollProactor')

    class EpollProactor(PollProactor):
        _make_poller = select.epoll
        _uses_msecs = False


#
# Proactor using overlapped IO and a completion port
#

try:
    from _overlapped import *
except ImportError:
    if sys.platform == 'win32':
        warnings.warn('IOCP support not compiled')
else:
    __all__.append('IocpProactor')

    from _winapi import CloseHandle
    import weakref

    class IocpProactor(BaseProactor):
        def __init__(self, concurrency=0xffffffff):
            super().__init__()
            self._iocp = CreateIoCompletionPort(
                INVALID_HANDLE_VALUE, NULL, 0, concurrency)
            self._cache = {}
            self._registered = weakref.WeakSet()

        def pollable(self):
            return bool(self._cache)

        def recv(self, conn, nbytes, flags=0):
            self._register_obj(conn)
            ov = Overlapped(NULL)
            ov.WSARecv(conn.fileno(), nbytes, flags)
            return self._register(ov, conn, ov.getresult)

        def send(self, conn, buf, flags=0):
            self._register_obj(conn)
            ov = Overlapped(NULL)
            ov.WSASend(conn.fileno(), buf, flags)
            return self._register(ov, conn, ov.getresult)

        def accept(self, listener):
            self._register_obj(listener)
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
            self._register_obj(conn)
            BindLocal(conn.fileno())
            ov = Overlapped(NULL)
            ov.ConnectEx(conn.fileno(), address)
            def finish_connect():
                ov.getresult()
                conn.setsockopt(socket.SOL_SOCKET,
                                SO_UPDATE_CONNECT_CONTEXT, 0)
                return conn
            return self._register(ov, conn, finish_connect)

        def _readable(self, sock):
            raise NotImplementedError('IocpProactor._readable()')

        def _writable(self, sock):
            raise NotImplementedError('IocpProactor._writable()')

        def _register_obj(self, obj):
            if obj not in self._registered:
                self._registered.add(obj)
                CreateIoCompletionPort(obj.fileno(), self._iocp, 0, 0)

        def _register(self, ov, obj, callback, discard=False):
            # we prevent ov and obj from being garbage collected
            f = None if discard else self._Future()
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
            if self._iocp is not None:
                CloseHandle(self._iocp)
                self._iocp = None

        __del__ = close

#
# Select default proactor (IOCP does not do SSL or ipv6)
#

for _ in ('EpollProactor', 'IocpProactor', 'PollProactor', 'SelectProactor'):
    if _ in globals():
        Proactor = globals()[_]
        break
del _

# Proactor = SelectProactor
