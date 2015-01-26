"""Fetch one URL and write its content to stdout.

This version adds a primitive connection pool, redirect following and
chunked transfer-encoding.  It also supports a --iocp flag.
"""

import sys
import urllib.parse
from http.client import BadStatusLine

from asyncio import *


class ConnectionPool:
    # TODO: Locking?  Close idle connections?

    def __init__(self, verbose=False):
        self.verbose = verbose
        self.connections = {}  # {(host, port, ssl): (reader, writer)}

    def close(self):
        for _, writer in self.connections.values():
            writer.close()

    @coroutine
    def open_connection(self, host, port, ssl):
        port = port or (443 if ssl else 80)
        ipaddrs = yield from get_event_loop().getaddrinfo(host, port)
        if self.verbose:
            print('* %s resolves to %s' %
                  (host, ', '.join(ip[4][0] for ip in ipaddrs)),
                  file=sys.stderr)
        for _, _, _, _, (h, p, *_) in ipaddrs:
            key = h, p, ssl
            conn = self.connections.get(key)
            if conn:
                reader, writer = conn
                if reader._eof:
                    self.connections.pop(key)
                    continue
                if self.verbose:
                    print('* Reusing pooled connection', key, file=sys.stderr)
                return conn
        reader, writer = yield from open_connection(host, port, ssl=ssl)
        host, port, *_ = writer.get_extra_info('peername')
        key = host, port, ssl
        self.connections[key] = reader, writer
        if self.verbose:
            print('* New connection', key, file=sys.stderr)
        return reader, writer


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

    def vprint(self, *args):
        if self.verbose:
            print(*args, file=sys.stderr)

    @coroutine
    def connect(self, pool):
        self.vprint('* Connecting to %s:%s using %s' %
                    (self.hostname, self.port, 'ssl' if self.ssl else 'tcp'))
        self.reader, self.writer = \
                     yield from pool.open_connection(self.hostname,
                                                     self.port,
                                                     ssl=self.ssl)
        self.vprint('* Connected to %s' %
                    (self.writer.get_extra_info('peername'),))

    @coroutine
    def putline(self, line):
        self.vprint('>', line)
        self.writer.write(line.encode('latin-1') + b'\r\n')
        ##yield from self.writer.drain()

    @coroutine
    def send_request(self):
        request = '%s %s %s' % (self.method, self.full_path, self.http_version)
        yield from self.putline(request)
        if 'host' not in {key.lower() for key, _ in self.headers}:
            self.headers.insert(0, ('Host', self.netloc))
        for key, value in self.headers:
            line = '%s: %s' % (key, value)
            yield from self.putline(line)
        yield from self.putline('')

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

    def vprint(self, *args):
        if self.verbose:
            print(*args, file=sys.stderr)

    @coroutine
    def getline(self):
        line = (yield from self.reader.readline()).decode('latin-1').rstrip()
        self.vprint('<', line)
        return line

    @coroutine
    def read_headers(self):
        status_line = yield from self.getline()
        status_parts = status_line.split(None, 2)
        if len(status_parts) != 3:
            raise BadStatusLine(status_line)
        self.http_version, status, self.reason = status_parts
        self.status = int(status)
        while True:
            header_line = yield from self.getline()
            if not header_line:
                break
            # TODO: Continuation lines.
            key, value = header_line.split(':', 1)
            self.headers.append((key, value.strip()))

    def get_redirect_url(self, default=None):
        if self.status not in (300, 301, 302, 303, 307):
            return default
        return self.get_header('Location', default)

    def get_header(self, key, default=None):
        key = key.lower()
        for k, v in self.headers:
            if k.lower() == key:
                return v
        return default

    @coroutine
    def read(self):
        nbytes = None
        for key, value in self.headers:
            if key.lower() == 'content-length':
                nbytes = int(value)
                break
        if nbytes is None:
            if self.get_header('transfer-encoding', '').lower() == 'chunked':
                blocks = []
                size = -1
                while size:
                    size_header = yield from self.reader.readline()
                    if not size_header:
                        break
                    parts = size_header.split(b';')
                    size = int(parts[0], 16)
                    if size:
                        block = yield from self.reader.readexactly(size)
                        assert len(block) == size, (len(block), size)
                        blocks.append(block)
                    crlf = yield from self.reader.readline()
                    assert crlf == b'\r\n', repr(crlf)
                body = b''.join(blocks)
            else:
                body = yield from self.reader.read()
        else:
            body = yield from self.reader.readexactly(nbytes)
        return body


@coroutine
def fetch(url, verbose=True, max_redirect=10):
    pool = ConnectionPool(verbose)
    try:
        for _ in range(max_redirect):
            request = Request(url, verbose)
            yield from request.connect(pool)
            yield from request.send_request()
            response = yield from request.get_response()
            body = yield from response.read()
            next_url = response.get_redirect_url()
            if not next_url:
                break
            url = urllib.parse.urljoin(url, next_url)
            print('redirect to', url, file=sys.stderr)
        return body
    finally:
        pool.close()


def main():
    if '--iocp' in sys.argv:
        from asyncio.windows_events import ProactorEventLoop
        loop = ProactorEventLoop()
        set_event_loop(loop)
    else:
        loop = get_event_loop()
    try:
        body = loop.run_until_complete(fetch(sys.argv[1], '-v' in sys.argv))
    finally:
        loop.close()
    sys.stdout.buffer.write(body)


if __name__ == '__main__':
    main()
