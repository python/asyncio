#!/usr/bin/env python3.3
"""Example HTTP client using yield-from coroutines (PEP 380).

Requires Python 3.3.

There are many micro-optimizations possible here, but that's not the point.

Some incomplete laundry lists:

TODO:
- Take test urls from command line.
- Profiling.
- Docstrings.
- Unittests.

FUNCTIONALITY:
- Connection pool (keep connection open).
- Chunked encoding (request and response).
- Pipelining, e.g. zlib (request and response).
- Automatic encoding/decoding.
"""

__author__ = 'Guido van Rossum <guido@python.org>'

# Standard library imports (keep in alphabetic order).
import logging
import re
import time

# Initialize logging before we import polling.
logging.basicConfig(level=logging.INFO)

# Local imports (keep in alphabetic order).
import polling
import scheduling
import sockets

eventloop = polling.EventLoop()
threadrunner = polling.ThreadRunner(eventloop)
scheduler = scheduling.Scheduler(eventloop, threadrunner)

sockets.scheduler = scheduler  # TODO: Find a better way.


def urlfetch(host, port=80, method='GET', path='/',
             body=None, hdrs=None, encoding='utf-8'):
    t0 = time.time()
    trans = yield from sockets.create_transport((host, port))
    yield from trans.send(method.encode(encoding) + b' ' +
                          path.encode(encoding) + b' HTTP/1.0\r\n')
    if hdrs:
        kwds = dict(hdrs)
    else:
        kwds = {}
    if 'host' not in kwds:
        kwds['host'] = host
    if body is not None:
        kwds['content_length'] = len(body)
    for header, value in kwds.items():
        yield from trans.send(header.replace('_', '-').encode(encoding) +
                              b': ' + value.encode(encoding) + b'\r\n')

    yield from trans.send(b'\r\n')
    if body is not None:
        yield from trans.send(body)
    trans.shutdown('w')  # Close the writing end of the socket.

    # Read HTTP response line.
    rdr = sockets.BufferedReader(trans)
    resp = yield from rdr.readline()
    m = re.match(br'(?ix) http/(\d\.\d) \s+ (\d\d\d) \s+ ([^\r]*)\r?\n\Z', resp)
    if not m:
        trans.close()
        raise IOError('No valid HTTP response: %r' % resp)
    http_version, status, message = m.groups()

    # Read HTTP headers.
    headers = []
    hdict = {}
    while True:
        line = yield from rdr.readline()
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
    data = yield from rdr.readexactly(size)
    trans.close()  # Can this block?
    t1 = time.time()
    print(host, port, path, status, len(data), '{:.3}'.format(t1-t0))


def doit():
    # This references NDB's default test service.
    # (Sadly the service is single-threaded.)
    gen1 = urlfetch('localhost', 8080, path='/')
    scheduler.start(gen1, 'gen1', timeout=2)

    gen2 = urlfetch('localhost', 8080, path='/home')
    scheduler.start(gen2, 'gen2', timeout=2)

    # Fetch python.org home page.
    gen3 = urlfetch('python.org', 80, path='/')
    scheduler.start(gen3, 'gen3', timeout=2)

##     # Fetch many links from python.org (/x.y.z).
##     for x in '123':
##         for y in '0123456789':
##             path = '/{}.{}'.format(x, y)
##             g = urlfetch('82.94.164.162', 80,
##                          path=path, hdrs={'host': 'python.org'})
##             scheduler.start(g, path)

    scheduler.run()


def main():
    doit()


if __name__ == '__main__':
    main()
