#!/usr/bin/env python3.3
"""Example HTTP client using yield-from coroutines (PEP 380).

Requires Python 3.3.

There are many micro-optimizations possible here, but that's not the point.

Some incomplete laundry lists:

TODO:
- Cancellation.
- A more varied set of test URLs.
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

# Standard library imports (keep in alphabetic order).
import collections
import errno
import logging
import re
import select
import socket
import time

# Local imports (keep in alphabetic order).
import polling


class Scheduler:

    def __init__(self, ioloop):
        self.ioloop = ioloop
        self.current = None
        self.current_name = None

    def run(self):
        self.ioloop.run()

    def start(self, task, name):
        self.ioloop.call_soon(self.run_task, task, name)

    def run_task(self, task, name):
        try:
            self.current = task
            self.current_name = name
            next(self.current)
        except StopIteration:
            pass
        except Exception:
            logging.exception('Exception in task %r', name)
        else:
            if self.current is not None:
                self.start(task, name)
        finally:
            self.current = None
            self.current_name = None
        

    def block_r(self, fd):
        self.block(fd, 'r')

    def block_w(self, fd):
        self.block(fd, 'w')

    def block(self, fd, flag):
        assert isinstance(fd, int), repr(fd)
        assert flag in ('r', 'w'), repr(flag)
        assert self.current is not None
        task = self.current
        self.current = None
        if flag == 'r':
            method = self.ioloop.add_reader
            callback = self.unblock_r
        else:
            method = self.ioloop.add_writer
            callback = self.unblock_w
        method(fd, callback, fd, task, self.current_name)

    def unblock_r(self, fd, task, name):
        self.ioloop.remove_reader(fd)
        self.start(task, name)

    def unblock_w(self, fd, task, name):
        self.ioloop.remove_writer(fd)
        self.start(task, name)


sched = Scheduler(polling.best_pollster())


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
    # This references NDB's default test service.
    # (Sadly the service is single-threaded.)
    gen1 = urlfetch('127.0.0.1', 8080, path='/', hdrs={'host': 'localhost'})
    sched.start(gen1, 'gen1')
    gen2 = urlfetch('127.0.0.1', 8080, path='/home', hdrs={'host': 'localhost'})
    sched.start(gen2, 'gen2')

##     # Fetch python.org home page.
##     gen3 = urlfetch('82.94.164.162', 80, path='/',
##                     hdrs={'host': 'python.org'})
##     sched.run(gen3, 'gen3')

##     # Fetch many links from python.org (/x.y.z).
##     for x in '123':
##         for y in '0123456789':
##             path = '/{}.{}'.format(x, y)
##             g = urlfetch('82.94.164.162', 80,
##                          path=path, hdrs={'host': 'python.org'})
##             sched.run(g, path)
    sched.run()


def main():
    doit()


if __name__ == '__main__':
    main()
