#!/usr/bin/env python3.3
"""Simple HTTP server.

This currenty exists just so we can benchmark this thing!
"""

# Stdlib imports.
import logging
import re
import socket
import sys

# Local imports.
import scheduling
import sockets


def handler(conn, addr):
    """COROUTINE: Handle one connection."""
    ##logging.info('Accepting connection from %r', addr)
    trans = sockets.SocketTransport(conn)
    rdr = sockets.BufferedReader(trans)

    # Read but ignore request line.
    request_line = yield from rdr.readline()

    # Consume headers but don't interpret them.
    while True:
        header_line = yield from rdr.readline()
        if not header_line.strip():
            break

    # Always send an empty 200 response and close.
    yield from trans.send(b'HTTP/1.0 200 Ok\r\n\r\n')
    trans.close()


def doit():
    """COROUTINE: Set the wheels in motion."""
    # Set up listener.
    listener = yield from sockets.create_listener('localhost', 8080,
                                                  af=socket.AF_INET)
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
