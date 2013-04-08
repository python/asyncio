#!/usr/bin/env python3.3
"""Example echo server."""

# Stdlib imports.
import logging
import socket
import sys

# Local imports.
import scheduling
import sockets


def handler(conn, addr):
    """COROUTINE: Handle one connection."""
    logging.info('Accepting connection from %r', addr)
    trans = sockets.SocketTransport(conn)
    rdr = sockets.BufferedReader(trans)
    while True:
        line = yield from rdr.readline()
        logging.debug('Received: %r from %r', line, addr)
        if not line:
            break
        yield from trans.send(line.upper())
    logging.debug('Closing %r', addr)
    trans.close()


def doit():
    """COROUTINE: Set the wheels in motion."""
    # Set up listener.
    listener = yield from sockets.create_listener('localhost', 1111,
                                                  af=socket.AF_INET,
                                                  backlog=100)
    logging.info('Listening on %r', listener.sock.getsockname())

    # Loop accepting connections.
    while True:
        conn, addr = yield from listener.accept()
        t = scheduling.Task(handler(conn, addr))


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

    # Run scheduler, starting it off with doit().
    scheduling.run(doit())


if __name__ == '__main__':
    main()
