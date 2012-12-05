#!/usr/bin/env python3.3
"""Example HTTP client using yield-from coroutines (PEP 380).

Requires Python 3.3.

There are many micro-optimizations possible here, but that's not the point.

Some incomplete laundry lists:

TODO:
- Take test urls from command line.
- Move urlfetch to a separate module.
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
import time
import socket
import sys

# Local imports (keep in alphabetic order).
import scheduling
import http_client



def doit2():
    argses = [
        ('localhost', 8080, '/'),
        ('127.0.0.1', 8080, '/home'),
        ('python.org', 80, '/'),
        ('xkcd.com', 443, '/'),
        ]
    results = yield from scheduling.map_over(
        lambda args: http_client.urlfetch(*args), argses, timeout=2)
    for res in results:
        print('-->', res)
    return []


def doit():
    TIMEOUT = 2
    tasks = set()

    # This references NDB's default test service.
    # (Sadly the service is single-threaded.)
    task1 = scheduling.Task(http_client.urlfetch('localhost', 8080, path='/'),
                            'root', timeout=TIMEOUT)
    tasks.add(task1)
    task2 = scheduling.Task(http_client.urlfetch('127.0.0.1', 8080,
                                                 path='/home'),
                            'home', timeout=TIMEOUT)
    tasks.add(task2)

    # Fetch python.org home page.
    task3 = scheduling.Task(http_client.urlfetch('python.org', 80, path='/'),
                            'python', timeout=TIMEOUT)
    tasks.add(task3)

    # Fetch XKCD home page using SSL.  (Doesn't like IPv6.)
    task4 = scheduling.Task(http_client.urlfetch('xkcd.com', ssl=True, path='/',
                                                 af=socket.AF_INET),
                            'xkcd', timeout=TIMEOUT)
    tasks.add(task4)

##     # Fetch many links from python.org (/x.y.z).
##     for x in '123':
##         for y in '0123456789':
##             path = '/{}.{}'.format(x, y)
##             g = http_client.urlfetch('82.94.164.162', 80,
##                                      path=path, hdrs={'host': 'python.org'})
##             t = scheduling.Task(g, path, timeout=2)
##             tasks.add(t)

##     print(tasks)
    yield from scheduling.Task(scheduling.sleep(1), timeout=0.2).wait()
    winners = yield from scheduling.wait_any(tasks)
    print('And the winners are:', [w.name for w in winners])
    tasks = yield from scheduling.wait_all(tasks)
    print('And the players were:', [t.name for t in tasks])
    return tasks


def logtimes(real):
    utime, stime, cutime, cstime, unused = os.times()
    logging.info('real %10.3f', real)
    logging.info('user %10.3f', utime + cutime)
    logging.info('sys  %10.3f', stime + cstime)


def main():
    t0 = time.time()

    # Initialize logging.
    if '-d' in sys.argv:
        level = logging.DEBUG
    elif '-v' in sys.argv:
        level = logging.INFO
    elif '-q' in sys.argv:
        level = logging.ERROR
    else:
        level = logging.WARN
    logging.basicConfig(level=level)

    # Run scheduler, starting it off with doit().
    task = scheduling.run(doit())
    if task.exception:
        print('Exception:', repr(task.exception))
        if isinstance(task.exception, AssertionError):
            raise task.exception
    else:
        for t in task.result:
            print(t.name + ':',
                  repr(t.exception) if t.exception else t.result)

    # Report real, user, sys times.
    t1 = time.time()
    logtimes(t1-t0)


if __name__ == '__main__':
    main()
