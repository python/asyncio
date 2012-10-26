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
import re
import socket
import ssl

# Local imports.
import scheduling


class SocketTransport:
    """Transport wrapping a socket.

    The socket must already be connected in non-blocking mode.
    """

    def __init__(self, sock):
        self.sock = sock

    def recv(self, n):
        """COROUTINE: Read up to n bytes, blocking at most once."""
        assert n >= 0, n
        scheduling.block_r(self.sock.fileno())
        yield
        return self.sock.recv(n)

    def send(self, data):
        """COROUTINE; Send data to the socket, blocking until all written."""
        while data:
            scheduling.block_w(self.sock.fileno())
            yield
            n = self.sock.send(data)
            assert 0 <= n <= len(data), (n, len(data))
            if n == len(data):
                break
            data = data[n:]

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
                scheduling.block_r(self.sslsock.fileno())
                yield
            except ssl.SSLWantWriteError:
                scheduling.block_w(self.sslsock.fileno())
                yield
            else:
                break

    def recv(self, n):
        """COROUTINE: Read up to n bytes.

        This blocks until at least one byte is read, or until EOF.
        """
        while True:
            try:
                return self.sslsock.recv(n)
            except socket.error as err:
                scheduling.block_r(self.sslsock.fileno())
                yield

    def send(self, data):
        """COROUTINE: Send data to the socket, blocking as needed."""
        while data:
            try:
                n = self.sslsock.send(data)
            except socket.error as err:
                scheduling.block_w(self.sslsock.fileno())
                yield
            if n == len(data):
                break
            data = data[n:]

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
        while n > count:
            block = yield from self.read(n - count)
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
    scheduling.block_w(sock.fileno())
    yield
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


def create_connection(host, port, af=0, socktype=socket.SOCK_STREAM):
    """COROUTINE: Look up address and create a socket connected to it."""
    match = re.match(r'(\d+)\.(\d+)\.(\d+)\.(\d+)\Z', host)
    if match:
        d1, d2, d3, d4 = map(int, match.groups())
        if not (0 <= d1 <= 255 and 0 <= d2 <= 255 and
                0 <= d3 <= 255 and 0 <= d4 <= 255):
            match = None
    if not match:
        infos = yield from getaddrinfo(host, port,
                                       af=af, socktype=socket.SOCK_STREAM)
    else:
        infos = [(socket.AF_INET, socket.SOCK_STREAM, socket.SOL_TCP, '',
                  (host, port))]
    assert infos, 'No address info for (%r, %r)' % (host, port)
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
        if exc is not None:
            raise exc
    return sock


def create_transport(host, port, af=0, ssl=None):
    """COROUTINE: Look up address and create a transport connected to it."""
    if ssl is None:
        ssl = (port == 443)
    sock = yield from create_connection(host, port, af=af)
    if ssl:
        trans = SslTransport(sock)
        yield from trans.do_handshake()
    else:
        trans = SocketTransport(sock)
    return trans
