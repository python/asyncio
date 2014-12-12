"""Fetch one URL and write its content to stdout.

This version adds URL parsing (including SSL) and a Response object.
"""

import sys
import urllib.parse

from asyncio import *


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
            return (yield from reader.readline()).decode('latin-1').rstrip()
        status_line = yield from getline()
        if self.verbose: print('<', status_line, file=sys.stderr)
        self.http_version, status, self.reason = status_line.split(None, 2)
        self.status = int(status)
        while True:
            header_line = yield from getline()
            if not header_line:
                break
            if self.verbose: print('<', header_line, file=sys.stderr)
            # TODO: Continuation lines.
            key, value = header_line.split(':', 1)
            self.headers.append((key, value.strip()))
        if self.verbose: print(file=sys.stderr)


@coroutine
def fetch(url, verbose=True):
    parts = urllib.parse.urlparse(url)
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
    r, w = yield from open_connection(parts.hostname, port, ssl=ssl)
    w.write(request.encode('latin-1'))
    response = Response(verbose)
    yield from response.read(r)
    body = yield from r.read()
    return body


def main():
    loop = get_event_loop()
    try:
        body = loop.run_until_complete(fetch(sys.argv[1], '-v' in sys.argv))
    finally:
        loop.close()
    print(body.decode('latin-1'), end='')


if __name__ == '__main__':
    main()
