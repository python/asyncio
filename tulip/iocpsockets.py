import collections
import errno
import socket
import weakref
import _winapi

from .selectors import _BaseSelector, EVENT_READ, EVENT_WRITE, EVENT_CONNECT
from ._overlapped import *


class IocpSocket:

    _state = 'unknown'

    def __init__(self, selector, sock=None, family=socket.AF_INET,
                 type=socket.SOCK_STREAM, proto=0):
        if sock is None:
            sock = socket.socket(family, type, proto)
        self._sock = sock
        self._fd = self._sock.fileno()
        self._selector = selector
        self._pending = {EVENT_READ:False, EVENT_WRITE:False}
        self._result = {EVENT_READ:None, EVENT_WRITE:None}
        CreateIoCompletionPort(self._fd, selector._iocp, 0, 0)
        # XXX SetFileCompletionNotificationModes() requires Vista or later
        SetFileCompletionNotificationModes(
             self._fd, FILE_SKIP_COMPLETION_PORT_ON_SUCCESS)
        selector._fd_to_fileobj[sock.fileno()] = self

    def __getattr__(self, name):
        return getattr(self._sock, name)

    def listen(self, backlog):
        self._sock.listen(backlog)
        self._state = 'listening'

    def send(self, buf):
        if self._pending[EVENT_WRITE]:
            raise BlockingIOError(errno.EAGAIN, 'try again')

        res = self._result[EVENT_WRITE]
        if res and not res[0]:
            self._result[EVENT_WRITE] = None
            raise res[1]

        try:
            return self._sock.send(buf)
        except BlockingIOError:
            return self._send(buf)

    def sendall(self, buf):
        if self._pending[EVENT_WRITE]:
            raise BlockingIOError(errno.EAGAIN, 'try again')

        res = self._result[EVENT_WRITE]
        if res and not res[0]:
            self._result[EVENT_WRITE] = None
            raise res[1]

        return self._send(buf)

    def _send(self, buf):
        def callback():
            self._sock._decref_socketios()
            if ov.getresult() < len(buf):
                # partial writes only happen if something has broken
                raise RuntimeError('partial write -- should not get here')

        ov = Overlapped(0)
        ov.WSASend(self._fd, buf, 0)
        self._sock._io_refs += 1        # prevent real close till send complete
        if ov.pending:
            self._selector._defer(self, ov, EVENT_WRITE, callback)
        else:
            callback()
        return len(buf)

    def recv(self, length):
        if self._pending[EVENT_READ]:
            raise BlockingIOError(errno.EAGAIN, 'try again')

        res = self._result[EVENT_READ]
        if res and not res[0]:
            self._result[EVENT_READ] = None
            raise res[1]

        try:
            return self._sock.recv(length)
        except BlockingIOError:
            pass

        # a zero length read will block till socket is readable
        ov = Overlapped(0)
        ov.WSARecv(self._fd, 0, 0)
        if ov.pending:
            self._selector._defer(self, ov, EVENT_READ, ov.getresult)
            raise BlockingIOError(errno.EAGAIN, 'try again')
        else:
            return self._sock.recv(length)

    def connect(self, address):
        if self._state != 'unknown':
            raise ValueError('socket is in state %r' % self._state)

        self._state = 'connecting'
        BindLocal(self._fd, len(address))
        ov = Overlapped(0)

        try:
            ov.ConnectEx(self._fd, address)
        except OSError as e:
            if e.winerror == 10022:
                raise ConnectionRefusedError(
                    errno.ECONNREFUSED, e.strerror, None, e.winerror)
            else:
                raise

        def callback():
            try:
                ov.getresult(False)
            except OSError as e:
                self._state = 'broken'
                if e.winerror == 1225:
                    self._error = errno.ECONNREFUSED
                else:
                    self._error = e.errno
                raise
            else:
                self._state = 'connected'

        if ov.pending:
            self._selector._defer(self, ov, EVENT_WRITE, callback)
            raise BlockingIOError(errno.EINPROGRESS, 'connect in progress')
        else:
            callback()

    def getsockopt(self, level, optname, buflen=None):
        if ((level, optname) == (socket.SOL_SOCKET, socket.SO_ERROR)):
            if self._state == 'connecting':
                return errno.EINPROGRESS
            elif self._state == 'broken':
                return self._error
        if buflen is None:
            return self._sock.getsockopt(level, optname)
        else:
            return self._sock.getsockopt(level, optname, buflen)

    def accept(self):
        if self._state != 'listening':
            raise ValueError('socket is in state %r' % self._state)

        res = self._result[EVENT_READ]
        if res:
            success, value = self._result[EVENT_READ]
            self._result[EVENT_READ] = None
            if success:
                return value
            else:
                raise value

        if self._pending[EVENT_READ]:
            raise BlockingIOError(errno.EAGAIN, 'try again')

        def callback():
            ov.getresult(False)
            conn._sock.setsockopt(
                socket.SOL_SOCKET, SO_UPDATE_ACCEPT_CONTEXT, self._fd)
            conn._state = 'connected'
            return conn, conn.getpeername()

        conn = socket.socket(self.family, self.type, self.proto)
        conn = self._selector.wrap_socket(conn)
        ov = Overlapped(0)
        ov.AcceptEx(self._fd, conn.fileno())
        if ov.pending:
            self._selector._defer(self, ov, EVENT_READ, callback)
            raise BlockingIOError(errno.EAGAIN, 'try again')
        else:
            return callback()

    # XXX how do we deal with shutdown?
    # XXX connect_ex, makefile, ...?


class IocpSelector(_BaseSelector):

    def __init__(self, *, concurrency=0xffffffff):
        super().__init__()
        self._iocp = CreateIoCompletionPort(
            INVALID_HANDLE_VALUE, NULL, 0, concurrency)
        self._address_to_info = {}
        self._fd_to_fileobj = weakref.WeakValueDictionary()

    def wrap_socket(self, sock):
        sock.setblocking(False)
        return IocpSocket(self, sock)

    def _defer(self, sock, ov, flag, callback):
        sock._pending[flag] = True
        self._address_to_info[ov.address] = (sock, ov, flag, callback)

    def close(self):
        super().close()
        if self._iocp is not None:
            try:
                # cancel pending IO
                for info in self._address_to_info.values():
                    ov = info[1]
                    try:
                        ov.cancel()
                    except OSError as e:
                        # handle may have closed
                        pass            # XXX check e.winerror
                # wait for pending IO to stop
                while self._address_to_info:
                    status = GetQueuedCompletionStatus(self._iocp, 1000)
                    if status is None:
                        continue
                    self._address_to_info.pop(status[3], None)
            finally:
                _winapi.CloseHandle(self._iocp)
                self._iocp = None

    def select(self, timeout=None):
        # XXX currently this is O(n) where n is number of registered fds
        results = {}
        for fd, key in self._fd_to_key.items():
            fileobj = self._fd_to_fileobj[fd]
            if ((key.events & EVENT_READ)
                        and not fileobj._pending[EVENT_READ]):
                results[fd] = results.get(fd, 0) | EVENT_READ
            if ((key.events & (EVENT_WRITE|EVENT_CONNECT))
                        and not fileobj._pending[EVENT_WRITE]):
                results[fd] = results.get(fd, 0) | EVENT_WRITE | EVENT_CONNECT

        if results:
            ms = 0
        elif timeout is None:
            ms = INFINITE
        elif timeout < 0:
            raise ValueError('negative timeout')
        else:
            ms = int(timeout * 1000 + 0.5)
            if ms >= INFINITE:
                raise ValueError('timeout too big')

        while True:
            status = GetQueuedCompletionStatus(self._iocp, ms)
            if status is None:
                break
            try:
                fobj, ov, flag, callback = self._address_to_info.pop(status[3])
            except KeyError:
                continue
            fobj._pending[flag] = False
            try:
                value = callback()
            except OSError as e:
                fobj._result[flag] = (False, e)
            else:
                fobj._result[flag] = (True, value)
            key = self._fileobj_to_key.get(fobj)
            if key and (key.events & flag):
                results[fobj._fd] = results.get(fobj._fd, 0) | flag
            ms = 0

        tmp = []
        for fd, events in results.items():
            if events & EVENT_WRITE:
                events |= EVENT_CONNECT
            key = self._fd_to_key[fd]
            tmp.append((key.fileobj, events, key.data))
        return tmp


def main():
    from .winsocketpair import socketpair

    selector = IocpSelector()

    # listen
    listener = selector.wrap_socket(socket.socket())
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)

    # connect
    conn = selector.wrap_socket(socket.socket())
    try:
        conn.connect(listener.getsockname())
        # conn.connect(('127.0.0.1', 7868))
    except BlockingIOError:
        selector.register(conn, EVENT_WRITE)
        res = selector.select(5)
        # assert [(conn, EVENT_WRITE, None)] == res, res
        error = conn.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        assert error == 0, error
        selector.unregister(conn)

    # accept
    selector.register(listener, EVENT_READ)
    while True:
        try:
            a, addr = listener.accept()
            break
        except BlockingIOError:
            res = selector.select(1)
            assert [(listener, EVENT_READ, None)] == res
    selector.unregister(listener)


    selector.register(a, EVENT_WRITE)
    selector.register(conn, EVENT_READ)
    msgs = [b"hello"] * 100

    while selector.registered_count() > 0:
        for (f, event, data) in selector.select():
            if event & EVENT_READ:
                try:
                    msg = f.recv(20)
                except BlockingIOError:
                    print("READ BLOCKED")
                else:
                    print("read %r" % msg)
                    if not msg:
                        print("UNREGISTER READER")
                        selector.unregister(f)
                        f.close()
            if event & EVENT_WRITE:
                try:
                    nbytes = f.send(msgs.pop())
                except BlockingIOError:
                    print("WRITE BLOCKED")
                except IndexError:
                    print("UNREGISTER WRITER")
                    selector.unregister(f)
                    f.close()
                else:
                    print("bytes sent %r" % nbytes)


    a, b = socketpair()
    a = selector.wrap_socket(a)
    b = selector.wrap_socket(b)
    selector.register(a, EVENT_READ)
    selector.register(b, EVENT_WRITE)

    msg = b"x"*(1024*1024*16)
    view = memoryview(msg)
    res = []

    while selector.registered_count() > 0:
        for (f, event, data) in selector.select():
            if event & EVENT_READ:
                try:
                    data = f.recv(8192)
                except BlockingIOError:
                    print("READ BLOCKED")
                else:
                    res.append(data)
                    if not data:
                        print("UNREGISTER READER")
                        selector.unregister(f)
                        f.close()
            if event & EVENT_WRITE:
                try:
                    nbytes = f.send(view)
                except BlockingIOError:
                    print("WRITE BLOCKED")
                else:
                    assert nbytes == len(view)
                    if nbytes == 0:
                        print("UNREGISTER WRITER")
                        selector.unregister(f)
                        f.close()
                    else:
                        view = view[nbytes:]

    print(len(msg), sum(len(frag) for frag in res))

    selector.close()


if __name__ == '__main__':
    main()
