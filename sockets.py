"""Socket wrappers to go with scheduling.py.

TODO:
- Make nice transport and protocol abstractions.
- Refactor RawReader -> Connection, with read/write operations.
- Docstrings.
- Unittests.
- A write() call that isn't a generator (needed so you can substitute it
  for sys.stderr, pass it to logging.StreamHandler, etc.).
- Move getaddrinfo() call here.
"""

__author__ = 'Guido van Rossum <guido@python.org>'

import errno
import socket


class RawReader:
    # TODO: Merge with send() and newsocket() functions.

    def __init__(self, sock):
        self.sock = sock

    def read(self, n):
        """Read up to n bytes, blocking at most once."""
        assert n >= 0, n
        scheduler.block_r(self.sock.fileno())
        yield
        return self.sock.recv(n)


class BufferedReader:

    def __init__(self, raw, limit=8192):
        self.raw = raw
        self.limit = limit
        self.buffer = b''
        self.eof = False

    def read(self, n):
        """Read up to n bytes, blocking at most once."""
        assert n >= 0, n
        if not self.buffer and not self.eof:
            yield from self.fillbuffer(max(n, self.limit))
        return self.getfrombuffer(n)

    def readexactly(self, n):
        """Read exactly n bytes, or until EOF."""
        blocks = []
        count = 0
        while n > count:
            block = yield from self.read(n - count)
            blocks.append(block)
            count += len(block)
        return b''.join(blocks)

    def readline(self):
        """Read up to newline or limit, whichever comes first."""
        end = self.buffer.find(b'\n') + 1  # Point past newline, or 0.
        while not end and not self.eof and len(self.buffer) < self.limit:
            anchor = len(self.buffer)
            yield from self.fillbuffer(self.limit)
            end = self.buffer.find(b'\n', anchor) + 1
        if not end:
            end = len(self.buffer)
        if end > self.limit:
            end = self.limit
        return self.getfrombuffer(end)

    def getfrombuffer(self, n):
        """Read up to n bytes without blocking."""
        if n >= len(self.buffer):
            result, self.buffer = self.buffer, b''
        else:
            result, self.buffer = self.buffer[:n], self.buffer[n:]
        return result

    def fillbuffer(self, n):
        """Fill buffer with one (up to) n bytes from raw reader."""
        assert not self.eof, 'fillbuffer called at eof'
        data = yield from self.raw.read(n)
##        print('fillbuffer:', repr(data)[:100])
        if data:
            self.buffer += data
        else:
            self.eof = True


def send(sock, data):
##     print('send:', repr(data))
    while data:
        scheduler.block_w(sock.fileno())
        yield
        n = sock.send(data)
        assert 0 <= n <= len(data), (n, len(data))
        if n == len(data):
            break
        data = data[n:]


def newsocket(af, socktype, proto):
    sock = socket.socket(af, socktype, proto)
    sock.setblocking(False)
    return sock


def connect(sock, address):
##     print('connect:', address)
    try:
        sock.connect(address)
    except socket.error as err:
        if err.errno != errno.EINPROGRESS:
            raise
    scheduler.block_w(sock.fileno())
    yield
    err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
    if err != 0:
        raise IOError('Connection refused')

