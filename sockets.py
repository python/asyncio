"""Socket wrappers to go with scheduling.py.

Classes:

- SocketTransport: a transport implementation wrapping a socket.
- SslTransport: a transport implementation wrapping SSL around a socket.
- BufferedReader: a buffer wrapping the read end of a transport.

Functions (all coroutines):

- connect(): connect a socket.
- getaddrinfo(): look up an address.
- create_connection(): look up address and return a connected socket for it.
- create_transport(): look up address and return a connected transport.

TODO:
- Improve transport abstraction.
- Make a nice protocol abstraction.
- Unittests.
- A write() call that isn't a generator (needed so you can substitute it
  for sys.stderr, pass it to logging.StreamHandler, etc.).
"""

__author__ = 'Guido van Rossum <guido@python.org>'

# Stdlib imports.
import errno
import socket
import ssl

# Local imports.
import scheduling

# Errno values indicating the connection was disconnected.
_DISCONNECTED = frozenset((errno.ECONNRESET,
                           errno.ENOTCONN,
                           errno.ESHUTDOWN,
                           errno.ECONNABORTED,
                           errno.EPIPE,
                           errno.EBADF,
                           ))

# Errno values indicating the socket isn't ready for I/O just yet.
_TRYAGAIN = frozenset((errno.EAGAIN, errno.EWOULDBLOCK))


class SocketTransport:
    """Transport wrapping a socket.

    The socket must already be connected in non-blocking mode.
    """

    def __init__(self, sock):
        self.sock = sock

    def recv(self, n):
        """COROUTINE: Read up to n bytes, blocking as needed.

        Always returns at least one byte, except if the socket was
        closed or disconnected and there's no more data; then it
        returns b''.
        """
        assert n >= 0, n
        while True:
            try:
                return self.sock.recv(n)
            except socket.error as err:
                if err.errno in _TRYAGAIN:
                    pass
                elif err.errno in _DISCONNECTED:
                    return b''
                else:
                    raise  # Unexpected, propagate.
            yield from scheduling.block_r(self.sock.fileno())

    def send(self, data):
        """COROUTINE; Send data to the socket, blocking until all written.

        Return True if all went well, False if socket was disconnected.
        """
        while data:
            try:
                n = self.sock.send(data)
            except socket.error as err:
                if err.errno in _TRYAGAIN:
                    pass
                elif err.errno in _DISCONNECTED:
                    return False
                else:
                    raise  # Unexpected, propagate.
            else:
                assert 0 <= n <= len(data), (n, len(data))
                if n == len(data):
                    break
                data = data[n:]
                continue
            yield from scheduling.block_w(self.sock.fileno())

        return True

    def close(self):
        """Close the socket.  (Not a coroutine.)"""
        self.sock.close()


class SslTransport:
    """Transport wrapping a socket in SSL.

    The socket must already be connected at the TCP level in
    non-blocking mode.
    """

    def __init__(self, rawsock, sslcontext=None):
        self.rawsock = rawsock
        self.sslcontext = sslcontext or ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        self.sslsock = self.sslcontext.wrap_socket(
            self.rawsock, do_handshake_on_connect=False)

    def do_handshake(self):
        """COROUTINE: Finish the SSL handshake."""
        while True:
            try:
                self.sslsock.do_handshake()
            except ssl.SSLWantReadError:
                yield from scheduling.block_r(self.sslsock.fileno())
            except ssl.SSLWantWriteError:
                yield from scheduling.block_w(self.sslsock.fileno())
            else:
                break

    def recv(self, n):
        """COROUTINE: Read up to n bytes.

        This blocks until at least one byte is read, or until EOF.
        """
        while True:
            try:
                return self.sslsock.recv(n)
            except ssl.SSLWantReadError:
                yield from scheduling.block_r(self.sslsock.fileno())
            except ssl.SSLWantWriteError:
                yield from scheduling.block_w(self.sslsock.fileno())
            except socket.error as err:
                if err.errno in _TRYAGAIN:
                    yield from scheduling.block_r(self.sslsock.fileno())
                elif err.errno in _DISCONNECTED:
                    # Can this happen?
                    return b''
                else:
                    raise  # Unexpected, propagate.

    def send(self, data):
        """COROUTINE: Send data to the socket, blocking as needed."""
        while data:
            try:
                n = self.sslsock.send(data)
            except ssl.SSLWantReadError:
                yield from scheduling.block_r(self.sslsock.fileno())
            except ssl.SSLWantWriteError:
                yield from scheduling.block_w(self.sslsock.fileno())
            except socket.error as err:
                if err.errno in _TRYAGAIN:
                    yield from scheduling.block_w(self.sslsock.fileno())
                elif err.errno in _DISCONNECTED:
                    return False
                else:
                    raise  # Unexpected, propagate.
            if n == len(data):
                break
            data = data[n:]

        return True

    def close(self):
        """Close the socket.  (Not a coroutine.)

        This also closes the raw socket.
        """
        self.sslsock.close()

    # TODO: More SSL-specific methods, e.g. certificate stuff, unwrap(), ...


class BufferedReader:
    """A buffered reader wrapping a transport."""

    def __init__(self, trans, limit=8192):
        self.trans = trans
        self.limit = limit
        self.buffer = b''
        self.eof = False

    def read(self, n):
        """COROUTINE: Read up to n bytes, blocking at most once."""
        assert n >= 0, n
        if not self.buffer and not self.eof:
            yield from self._fillbuffer(max(n, self.limit))
        return self._getfrombuffer(n)

    def readexactly(self, n):
        """COUROUTINE: Read exactly n bytes, or until EOF."""
        blocks = []
        count = 0
        while count < n:
            block = yield from self.read(n - count)
            if not block:
                break
            blocks.append(block)
            count += len(block)
        return b''.join(blocks)

    def readline(self):
        """COROUTINE: Read up to newline or limit, whichever comes first."""
        end = self.buffer.find(b'\n') + 1  # Point past newline, or 0.
        while not end and not self.eof and len(self.buffer) < self.limit:
            anchor = len(self.buffer)
            yield from self._fillbuffer(self.limit)
            end = self.buffer.find(b'\n', anchor) + 1
        if not end:
            end = len(self.buffer)
        if end > self.limit:
            end = self.limit
        return self._getfrombuffer(end)

    def _getfrombuffer(self, n):
        """Read up to n bytes without blocking (not a coroutine)."""
        if n >= len(self.buffer):
            result, self.buffer = self.buffer, b''
        else:
            result, self.buffer = self.buffer[:n], self.buffer[n:]
        return result

    def _fillbuffer(self, n):
        """COROUTINE: Fill buffer with one (up to) n bytes from transport."""
        assert not self.eof, '_fillbuffer called at eof'
        data = yield from self.trans.recv(n)
        if data:
            self.buffer += data
        else:
            self.eof = True


def connect(sock, address):
    """COROUTINE: Connect a socket to an address."""
    try:
        sock.connect(address)
    except socket.error as err:
        if err.errno != errno.EINPROGRESS:
            raise
    yield from scheduling.block_w(sock.fileno())
    err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
    if err != 0:
        raise IOError(err, 'Connection refused')


def getaddrinfo(host, port, af=0, socktype=0, proto=0):
    """COROUTINE: Look up an address and return a list of infos for it.

    Each info is a tuple (af, socktype, protocol, canonname, address).
    """
    infos = yield from scheduling.call_in_thread(socket.getaddrinfo,
                                                 host, port, af,
                                                 socktype, proto)
    return infos


def create_connection(host, port, af=0, socktype=socket.SOCK_STREAM, proto=0):
    """COROUTINE: Look up address and create a socket connected to it."""
    infos = yield from getaddrinfo(host, port, af, socktype, proto)
    if not infos:
        raise IOError('getaddrinfo() returned an empty list')
    exc = None
    for af, socktype, proto, cname, address in infos:
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)
            sock.setblocking(False)
            yield from connect(sock, address)
            break
        except socket.error as err:
            if sock is not None:
                sock.close()
            if exc is None:
                exc = err
    else:
        raise exc
    return sock


def create_transport(host, port, af=0, ssl=None):
    """COROUTINE: Look up address and create a transport connected to it."""
    if ssl is None:
        ssl = (port == 443)
    sock = yield from create_connection(host, port, af)
    if ssl:
        trans = SslTransport(sock)
        yield from trans.do_handshake()
    else:
        trans = SocketTransport(sock)
    return trans


class Listener:
    """Wrapper for a listening socket."""

    def __init__(self, sock):
        self.sock = sock

    def accept(self):
        """COROUTINE: Accept a connection."""
        while True:
            try:
                conn, addr = self.sock.accept()
            except socket.error as err:
                if err.errno in _TRYAGAIN:
                    yield from scheduling.block_r(self.sock.fileno())
                else:
                    raise  # Unexpected, propagate.
            else:
                conn.setblocking(False)
                return conn, addr


def create_listener(host, port, af=0, socktype=0, proto=0,
                    backlog=5, reuse_addr=True):
    """COROUTINE: Look up address and create a listener for it."""
    infos = yield from getaddrinfo(host, port, af, socktype, proto)
    if not infos:
        raise IOError('getaddrinfo() returned an empty list')
    exc = None
    for af, socktype, proto, cname, address in infos:
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)
            sock.setblocking(False)
            if reuse_addr:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(address)
            sock.listen(backlog)
            break
        except socket.error as err:
            if sock is not None:
                sock.close()
            if exc is None:
                exc = err
    else:
        raise exc
    return Listener(sock)
