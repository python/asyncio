"""Fetch one URL and write its content to stdout.

This version adds a Request object.
"""

import sys
import urllib.parse
from http.client import BadStatusLine

from asyncio import *


class Request:

    def __init__(self, url, verbose=True):
        self.url = url
        self.verbose = verbose
        self.parts = urllib.parse.urlparse(self.url)
        self.scheme = self.parts.scheme
        assert self.scheme in ('http', 'https'), repr(url)
        self.ssl = self.parts.scheme == 'https'
        self.netloc = self.parts.netloc
        self.hostname = self.parts.hostname
        self.port = self.parts.port or (443 if self.ssl else 80)
        self.path = (self.parts.path or '/')
        self.query = self.parts.query
        if self.query:
            self.full_path = '%s?%s' % (self.path, self.query)
        else:
            self.full_path = self.path
        self.http_version = 'HTTP/1.1'
        self.method = 'GET'
        self.headers = []
        self.reader = None
        self.writer = None

    @coroutine
    def connect(self):
        if self.verbose:
            print('* Connecting to %s:%s using %s' %
                  (self.hostname, self.port, 'ssl' if self.ssl else 'tcp'),
                  file=sys.stderr)
        self.reader, self.writer = yield from open_connection(self.hostname,
                                                              self.port,
                                                              ssl=self.ssl)
        if self.verbose:
            print('* Connected to %s' %
                  (self.writer.get_extra_info('peername'),),
                  file=sys.stderr)

    def putline(self, line):
        self.writer.write(line.encode('latin-1') + b'\r\n')

    @coroutine
    def send_request(self):
        request = '%s %s %s' % (self.method, self.full_path, self.http_version)
        if self.verbose: print('>', request, file=sys.stderr)
        self.putline(request)
        if 'host' not in {key.lower() for key, _ in self.headers}:
            self.headers.insert(0, ('Host', self.netloc))
        for key, value in self.headers:
            line = '%s: %s' % (key, value)
            if self.verbose: print('>', line, file=sys.stderr)
            self.putline(line)
        self.putline('')

    @coroutine
    def get_response(self):
        response = Response(self.reader, self.verbose)
        yield from response.read_headers()
        return response


class Response:

    def __init__(self, reader, verbose=True):
        self.reader = reader
        self.verbose = verbose
        self.http_version = None  # 'HTTP/1.1'
        self.status = None  # 200
        self.reason = None  # 'Ok'
        self.headers = []  # [('Content-Type', 'text/html')]

    @coroutine
    def getline(self):
        return (yield from self.reader.readline()).decode('latin-1').rstrip()

    @coroutine
    def read_headers(self):
        status_line = yield from self.getline()
        if self.verbose: print('<', status_line, file=sys.stderr)
        status_parts = status_line.split(None, 2)
        if len(status_parts) != 3:
            raise BadStatusLine(status_line)
        self.http_version, status, self.reason = status_parts
        self.status = int(status)
        while True:
            header_line = yield from self.getline()
            if not header_line:
                break
            if self.verbose: print('<', header_line, file=sys.stderr)
            # TODO: Continuation lines.
            key, value = header_line.split(':', 1)
            self.headers.append((key, value.strip()))
        if self.verbose: print(file=sys.stderr)

    @coroutine
    def read(self):
        nbytes = None
        for key, value in self.headers:
            if key.lower() == 'content-length':
                nbytes = int(value)
                break
        if nbytes is None:
            body = yield from self.reader.read()
        else:
            body = yield from self.reader.readexactly(nbytes)
        return body


@coroutine
def fetch(url, verbose=True):
    request = Request(url, verbose)
    yield from request.connect()
    yield from request.send_request()
    response = yield from request.get_response()
    body = yield from response.read()
    return body


def main():
    loop = get_event_loop()
    try:
        body = loop.run_until_complete(fetch(sys.argv[1], '-v' in sys.argv))
    finally:
        loop.close()
    sys.stdout.buffer.write(body)


if __name__ == '__main__':
    main()
