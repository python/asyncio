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
import os
import re
import time
import socket
import sys

# Initialize logging before we import polling.
# TODO: Change polling.py so we can do this in main().
if '-d' in sys.argv:
    level = logging.DEBUG
elif '-v' in sys.argv:
    level = logging.INFO
elif '-q' in sys.argv:
    level = logging.ERROR
else:
    level = logging.WARN
logging.basicConfig(level=level)

# Local imports (keep in alphabetic order).
import polling
import scheduling
import sockets

eventloop = polling.EventLoop()
threadrunner = polling.ThreadRunner(eventloop)
scheduler = scheduling.Scheduler(eventloop, threadrunner)

sockets.scheduler = scheduler  # TODO: Find a better way.


def urlfetch(host, port=80, method='GET', path='/',
             body=None, hdrs=None, encoding='utf-8', ssl=None, af=0):
    t0 = time.time()
    trans = yield from sockets.create_transport(host, port, ssl=ssl, af=af)
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

    # Read HTTP response line.
    rdr = sockets.BufferedReader(trans)
    resp = yield from rdr.readline()
    m = re.match(br'(?ix) http/(\d\.\d) \s+ (\d\d\d) \s+ ([^\r]*)\r?\n\Z',
                 resp)
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
    return (host, port, path, int(status), len(data), round(t1-t0, 3))


def doit():
    # This references NDB's default test service.
    # (Sadly the service is single-threaded.)
    task1 = scheduler.newtask(urlfetch('localhost', 8080, path='/'),
                              'root', timeout=2)
    task2 = scheduler.newtask(urlfetch('localhost', 8080, path='/home'),
                              'home', timeout=2)

    # Fetch python.org home page.
    task3 = scheduler.newtask(urlfetch('python.org', 80, path='/'),
                              'python', timeout=2)

    tasks = {task1, task2, task3}

    # Fetch XKCD home page using SSL.  (Doesn't like IPv6.)
    task4 = scheduler.newtask(urlfetch('xkcd.com', 443, path='/',
                                       af=socket.AF_INET),
                              'xkcd', timeout=2)
    tasks.add(task4)

##     # Fetch many links from python.org (/x.y.z).
##     for x in '123':
##         for y in '0123456789':
##             path = '/{}.{}'.format(x, y)
##             g = urlfetch('82.94.164.162', 80,
##                          path=path, hdrs={'host': 'python.org'})
##             t = scheduler.newtask(g, path, timeout=2)
##             tasks.add(t)

##    print(tasks)
    for t in tasks:
        t.start()
    scheduler.run()
##    print(tasks)
    for t in tasks:
        print(t.name + ':', t.exception or t.result)


def logtimes(real):
    utime, stime, cutime, cstime, unused = os.times()
    logging.info('real %10.3f', real)
    logging.info('user %10.3f', utime + cutime)
    logging.info('sys  %10.3f', stime + cstime)


def main():
    t0 = time.time()
    doit()
    t1 = time.time()
    logtimes(t1-t0)


if __name__ == '__main__':
    main()
