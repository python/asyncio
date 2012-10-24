"""Socket wrappers to go with scheduling.py.

Classes:

- SocketTransport: a transport implementation wrapping a socket.
- BufferedReader: a buffer wrapping the read end of a transport.

Functions (all coroutines):

- connect(): connect a socket.
- getaddrinfo(): look up an address.
- create_connection(): look up an address and return a connected socket for it.
- create_transport(): look up an address and return a connected trahsport.

TODO:
- Improve transport abstraction.
- Make a nice protocol abstraction.
- Unittests.
- A write() call that isn't a generator (needed so you can substitute it
  for sys.stderr, pass it to logging.StreamHandler, etc.).
- Move getaddrinfo() call here.
"""

__author__ = 'Guido van Rossum <guido@python.org>'

import errno
import re
import socket


class SocketTransport:
    """Transport wrapping a socket.

    The socket must already be connected.
    """

    def __init__(self, sock):
        self.sock = sock

    def recv(self, n):
        """COROUTINE: Read up to n bytes, blocking at most once."""
        assert n >= 0, n
        scheduler.block_r(self.sock.fileno())
        yield
        return self.sock.recv(n)

    def send(self, data):
        """COROUTINE; Send data to the socket, blocking until all written."""
        while data:
            scheduler.block_w(self.sock.fileno())
            yield
            n = self.sock.send(data)
            assert 0 <= n <= len(data), (n, len(data))
            if n == len(data):
                break
            data = data[n:]

    def shutdown(self, flag):
        """Call shutdown() on the socket.  (Not a coroutine.)

        This is like closing one direction.

        The argument must be 'r', 'w' or 'rw'.
        """
        if flag == 'r':
            flag = socket.SHUT_RD
        elif flag == 'w':
            flag = socket.SHUT_WR
        elif flag == 'rw':
            flag = socket.SHUT_RDWR
        else:
            raise ValueError('flag must be r, w or rw, not %s' % flag)
        self.sock.shutdown(flag)

    def close(self):
        """Close the socket.  (Not a coroutine.)"""
        self.sock.close()


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
    scheduler.block_w(sock.fileno())
    yield
    err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
    if err != 0:
        raise IOError(err, 'Connection refused')


def getaddrinfo(host, port, af=0, socktype=0, proto=0):
    """COROUTINE: Look up an address and return a list of infos for it.

    Each info is a tuple (af, socktype, protocol, canonname, address).
    """
    infos = yield from scheduler.call_in_thread(socket.getaddrinfo,
                                                host, port, af,
                                                socktype, proto)
    return infos


def create_connection(address):
    """COROUTINE: Look up address and create a socket connected to it."""
    host, port = address
    match = re.match(r'(\d+)(\.\d+)(\.\d+)(\.\d+)\Z', host)
    if match:
        d1, d2, d3, d4 = map(int, match.groups())
        if not (0 <= d1 <= 255 and 0 <= d2 <= 255 and
                0 <= d3 <= 255 and 0 <= d4 <= 255):
            match = None
    if not match:
        infos = yield from getaddrinfo(host, port, socktype=socket.SOCK_STREAM)
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


def create_transport(address):
    """COROUTINE: Look up address and create a transport connected to it."""
    sock = yield from create_connection(address)
    return SocketTransport(sock)
