#!/usr/bin/env python3.3
"""Example echo client."""

# Stdlib imports.
import logging
import socket
import sys
import time

# Local imports.
import scheduling
import sockets


def echoclient(host, port):
    """COROUTINE"""
    testdata = b'hi hi hi ha ha ha\n'
    try:
        trans = yield from sockets.create_transport(host, port,
                                                    af=socket.AF_INET)
    except OSError:
        return False
    try:
        ok = yield from trans.send(testdata)
        if ok:
            response = yield from trans.recv(100)
            ok = response == testdata.upper()
        return ok
    finally:
        trans.close()


def doit(n):
    """COROUTINE"""
    t0 = time.time()
    tasks = set()
    for i in range(n):
        t = scheduling.Task(echoclient('127.0.0.1', 1111), 'client-%d' % i)
        tasks.add(t)
    ok = 0
    bad = 0
    for t in tasks:
        try:
            yield from t
        except Exception:
            bad += 1
        else:
            ok += 1
    t1 = time.time()
    print('ok: ', ok)
    print('bad:', bad)
    print('dt: ', round(t1-t0, 6))


def main():
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

    # Get integer from command line.
    n = 1
    for arg in sys.argv[1:]:
        if not arg.startswith('-'):
            n = int(arg)
            break

    # Run scheduler, starting it off with doit().
    scheduling.run(doit(n))


if __name__ == '__main__':
    main()
