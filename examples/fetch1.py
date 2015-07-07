"""Fetch one URL and write its content to stdout.

This version adds URL parsing (including SSL) and a Response object.
"""

from __future__ import print_function
import sys
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse

from trollius import *


class Response:

    def __init__(self, verbose=True):
        self.verbose = verbose
        self.http_version = None  # 'HTTP/1.1'
        self.status = None  # 200
        self.reason = None  # 'Ok'
        self.headers = []  # [('Content-Type', 'text/html')]

    @coroutine
    def read(self, reader):
        @coroutine
        def getline():
            line = (yield From(reader.readline()))
            line = line.decode('latin-1').rstrip()
            raise Return(line)
        status_line = yield From(getline())
        if self.verbose: print('<', status_line, file=sys.stderr)
        self.http_version, status, self.reason = status_line.split(None, 2)
        self.status = int(status)
        while True:
            header_line = yield From(getline())
            if not header_line:
                break
            if self.verbose: print('<', header_line, file=sys.stderr)
            # TODO: Continuation lines.
            key, value = header_line.split(':', 1)
            self.headers.append((key, value.strip()))
        if self.verbose: print(file=sys.stderr)


@coroutine
def fetch(url, verbose=True):
    parts = urlparse(url)
    if parts.scheme == 'http':
        ssl = False
    elif parts.scheme == 'https':
        ssl = True
    else:
        print('URL must use http or https.')
        sys.exit(1)
    port = parts.port
    if port is None:
        port = 443 if ssl else 80
    path = parts.path or '/'
    if parts.query:
        path += '?' + parts.query
    request = 'GET %s HTTP/1.0\r\n\r\n' % path
    if verbose:
        print('>', request, file=sys.stderr, end='')
    r, w = yield From(open_connection(parts.hostname, port, ssl=ssl))
    w.write(request.encode('latin-1'))
    response = Response(verbose)
    yield From(response.read(r))
    body = yield From(r.read())
    raise Return(body)


def main():
    loop = get_event_loop()
    try:
        body = loop.run_until_complete(fetch(sys.argv[1], '-v' in sys.argv))
    finally:
        loop.close()
    print(body.decode('latin-1'), end='')


if __name__ == '__main__':
    main()
