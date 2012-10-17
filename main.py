#!/usr/bin/env python3.3
"""Example HTTP client using yield-from coroutines (PEP 380).

Requires Python 3.3.

There are many micro-optimizations possible here, but that's not the point.

Some incomplete laundry lists:

TODO:
- Use poll() or better; need to figure out how to keep fds registered.
- Separate scheduler and event loop.
- A more varied set of test URLs.
- A Hg repo.
- Profiling.
- Unittests.

PATTERNS TO TRY:
- Wait for all, collate results.
- Wait for first N that are ready.
- Wait until some predicate becomes true.

FUNCTIONALITY:
- Connection pool (keep connection open).
- Chunked encoding (request and response).
- Pipelining, e.g. zlib (request and response).
- Automatic encoding/decoding.
- Thread pool and getaddrinfo() calling.
- A write() call that isn't a generator.
"""

__author__ = 'Guido van Rossum <guido@python.org>'

import collections
import errno
import logging
import re
import select
import socket
import time


class Scheduler:

    def __init__(self):
        self.runnable = collections.deque()
        self.current = None
        self.readers = {}
        self.writers = {}

    def run(self, task):
        self.runnable.append(task)

    def loop(self):
        while self.runnable or self.readers or self.writers:
            self.loop1()

    def loop1(self):
##         print('loop1')
        while self.runnable:
            self.current = self.runnable.popleft()
            try:
                next(self.current)
            except StopIteration:
                self.current = None
            except Exception:
                self.current = None
                logging.exception('Exception in task')
            else:
                if self.current is not None:
                    self.runnable.append(self.current)
                    self.current = None
        if self.readers or self.writers:
            # TODO: Schedule timed calls as well.
            # TODO: Use poll() or better.
            t0 = time.time()
            ready_r, ready_w, _ = select.select(self.readers, self.writers, [])
            t1 = time.time()
##             print('select({}, {}) took {:.3f} secs to return {}, {}'
##                   .format(list(self.readers), list(self.writers),
##                           t1 - t0, ready_r, ready_w))
            for fd in ready_r:
                self.unblock(self.readers.pop(fd))
            for fd in ready_w:
                self.unblock(self.writers.pop(fd))

    def unblock(self, task):
        assert task
        self.runnable.append(task)

    def block(self, queue, fd):
        assert isinstance(fd, int)
        assert fd not in queue
        assert self.current is not None
        queue[fd] = self.current
        self.current = None

    def block_r(self, fd):
        self.block(self.readers, fd)

    def block_w(self, fd):
        self.block(self.writers, fd)


sched = Scheduler()


class RawReader:

    def __init__(self, sock):
        self.sock = sock

    def read(self, n):
        """Read up to n bytes, blocking at most once."""
        assert n >= 0, n
        sched.block_r(self.sock.fileno())
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
        sched.block_w(sock.fileno())
        yield
        n = sock.send(data)
        assert 0 <= n <= len(data), (n, len(data))
        if n == len(data):
            break
        data = data[n:]


def newsocket():
    sock = socket.socket()
    sock.setblocking(False)
    return sock


def connect(sock, address):
##     print('connect:', address)
    err = sock.connect_ex(address)
    assert err == errno.EINPROGRESS, err
    sched.block_w(sock.fileno())
    yield
    err = sock.connect_ex(address)
    if err == errno.ECONNREFUSED:
        raise IOError('Connection refused')
    if err != errno.EISCONN:
        raise IOError('Connect error %d: %s' % (err, errno.errorcode.get(err)))


def urlfetch(host, port=80, method='GET', path='/',
             body=None, hdrs=None, encoding='utf-8'):
    t0 = time.time()
    # Must pass in an IP address.  Later we'll call getaddrinfo()
    # using a thread pool.  We'll also support IPv6.
    assert re.match(r'(\d+)(\.\d+)(\.\d+)(\.\d+)\Z', host), repr(host)
    sock = newsocket()
    yield from connect(sock, (host, port))
    yield from send(sock,
                    method.encode(encoding) + b' ' +
                    path.encode(encoding) + b' HTTP/1.0\r\n')
    if hdrs:
        kwds = dict(hdrs)
    else:
        kwds = {}
    if body is not None:
        kwds['content_length'] = len(body)
    for header, value in kwds.items():
        yield from send(sock,
                        header.replace('_', '-').encode(encoding) + b': ' +
                        value.encode(encoding) + b'\r\n')

    yield from send(sock, b'\r\n')
    if body is not None:
        yield from send(sock, body)
    ##sock.shutdown(1)  # Close the writing end of the socket.

    # Read HTTP response line.
    raw = RawReader(sock)
    buf = BufferedReader(raw)
    resp = yield from buf.readline()
##     print('resp =', repr(resp))
    m = re.match(br'(?ix) http/(\d\.\d) \s+ (\d\d\d) \s+ ([^\r]*)\r?\n\Z', resp)
    if not m:
        sock.close()
        raise IOError('No valid HTTP response: %r' % response)
    http_version, status, message = m.groups()

    # Read HTTP headers.
    headers = []
    hdict = {}
    while True:
        line = yield from buf.readline()
        if not line.strip():
            break
        m = re.match(br'([^\s:]+):\s*([^\r]*)\r?\n\Z', line)
        if not m:
            raise IOError('Invalid header: %r' % line)
        header, value = m.groups()
        headers.append((header, value))
        hdict[header.decode(encoding).lower()] = value.decode(encoding)

    # Read response body.
    content_length = hdict.get('content-length')
    if content_length is not None:
        size = int(content_length)  # TODO: Catch errors.
        assert size >= 0, size
    else:
        size = 2**20  # Protective limit (1 MB).
    data = yield from buf.readexactly(size)
    sock.close()  # Can this block?
    t1 = time.time()
##     print(http_version, status, message, headers, hdict, len(data))
    print(host, port, path, status, len(data), '{:.3}'.format(t1-t0))


def doit():
    gen1 = urlfetch('127.0.0.1', 8080, path='/', hdrs={'host': 'localhost'})
    gen2 = urlfetch('82.94.164.162', 80, path='/', hdrs={'host': 'python.org'})
    sched.run(gen1)
    sched.run(gen2)
    for x in '123':
        for y in '0123456789':
            g = urlfetch('82.94.164.162', 80,
                         path='/{}.{}'.format(x, y),
                         hdrs={'host': 'python.org'})
            sched.run(g)
    sched.loop()


def main():
    doit()


if __name__ == '__main__':
    main()
