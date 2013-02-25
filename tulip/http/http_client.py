"""HTTP Client for Tulip.

Most basic usage:

  sts, headers, response = yield from http_client.fetch(url,
       method='GET', headers={}, request=b'')
  assert isinstance(sts, int)
  assert isinstance(headers, dict)
    # sort of; case insensitive (what about multiple values for same header?)
  headers['status'] == '200 Ok'  # or some such
  assert isinstance(response, bytes)

However you can also open a stream:

  f, wstream = http_client.open_stream(url, method, headers)
  wstream.write(b'abc')
  wstream.writelines([b'def', b'ghi'])
  wstream.write_eof()
  sts, headers, rstream = yield from f
  response = yield from rstream.read()

TODO: Reuse email.Message class (or its subclass, http.client.HTTPMessage).
TODO: How do we do connection keep alive?  Pooling?
"""

__all__ = ['StreamReader', 'HttpClientProtocol']


import collections
import email.message
import email.parser
import re

import tulip


# TODO: Move to another module.
class StreamReader:

    def __init__(self, limit=2**16):
        self.limit = limit  # Max line length.  (Security feature.)
        self.buffer = collections.deque()  # Deque of bytes objects.
        self.byte_count = 0  # Bytes in buffer.
        self.line_count = 0  # Number of complete lines in buffer.
        self.eof = False  # Whether we're done.
        self.waiter = None  # A future.

    def feed_eof(self):
        self.eof = True
        waiter = self.waiter
        if waiter is not None:
            self.waiter = None
            waiter.set_result(True)

    def feed_data(self, data):
        if not data:
            return
        self.buffer.append(data)
        self.line_count += data.count(b'\n')
        self.byte_count += len(data)
        waiter = self.waiter
        if waiter is not None:
            self.waiter = None
            waiter.set_result(False)

    @tulip.coroutine
    def readline(self):
        parts = []
        parts_size = 0
        not_enough = True

        while not_enough:
            while self.buffer and not_enough:
                data = self.buffer.popleft()
                ichar = data.find(b'\n')
                if ichar < 0:
                    parts.append(data)
                    parts_size += len(data)
                else:
                    ichar += 1
                    head, tail = data[:ichar], data[ichar:]
                    if tail:
                        self.buffer.appendleft(tail)
                    self.line_count -= 1
                    not_enough = False
                    parts.append(head)
                    parts_size += len(head)

                if parts_size > self.limit:
                    self.byte_count -= parts_size
                    raise ValueError('Line is too long')

            if self.eof:
                break

            if not_enough:
                assert self.waiter is None
                self.waiter = tulip.Future()
                yield from self.waiter

        line = b''.join(parts)
        self.byte_count -= parts_size

        return line

    @tulip.coroutine
    def read(self, n=-1):
        if not n:
            return b''
        if n < 0:
            while not self.eof:
                assert not self.waiter
                self.waiter = tulip.Future()
                yield from self.waiter
        else:
            if not self.byte_count and not self.eof:
                assert not self.waiter
                self.waiter = tulip.Future()
                yield from self.waiter
        if n < 0 or self.byte_count <= n:
            data = b''.join(self.buffer)
            self.buffer.clear()
            self.byte_count = 0
            self.line_count = 0
            return data
        parts = []
        parts_bytes = 0
        while self.buffer and parts_bytes < n:
            data = self.buffer.popleft()
            data_bytes = len(data)
            if n < parts_bytes + data_bytes:
                data_bytes = n - parts_bytes
                data, rest = data[:data_bytes], data[data_bytes:]
                self.buffer.appendleft(rest)
            parts.append(data)
            parts_bytes += data_bytes
            self.byte_count -= data_bytes
            if self.line_count:
                self.line_count -= data.count(b'\n')
        return b''.join(parts)

    @tulip.coroutine
    def readexactly(self, n):
        if n <= 0:
            return b''
        while self.byte_count < n and not self.eof:
            assert not self.waiter
            self.waiter = tulip.Future()
            yield from self.waiter
        return (yield from self.read(n))


class HttpClientProtocol:
    """This Protocol class is also used to initiate the connection.

    Usage:
      p = HttpClientProtocol(url, ...)
      f = p.connect()  # Returns a Future
      ...now what?...
    """

    def __init__(self, host, port=None, *,
                 path='/', method='GET', headers=None, ssl=None,
                 make_body=None, encoding='utf-8', version='1.1',
                 chunked=False):
        host = self.validate(host, 'host')
        if ':' in host:
            assert port is None
            host, port_s = host.split(':', 1)
            port = int(port_s)
        self.host = host
        if port is None:
            if ssl:
                port = 443
            else:
                port = 80
        assert isinstance(port, int)
        self.port = port
        self.path = self.validate(path, 'path')
        self.method = self.validate(method, 'method')
        self.headers = email.message.Message()
        if headers:
            for key, value in headers.items():
                self.validate(key, 'header key')
                self.validate(value, 'header value', True)
                self.headers[key] = value
        self.encoding = self.validate(encoding, 'encoding')
        self.version = self.validate(version, 'version')
        self.make_body = make_body
        self.chunked = chunked
        self.ssl = ssl
        if 'content-length' not in self.headers:
            if self.make_body is None:
                self.headers['Content-Length'] = '0'
            else:
                self.chunked = True
        if self.chunked:
            if 'Transfer-Encoding' not in self.headers:
                self.headers['Transfer-Encoding'] = 'chunked'
            else:
                assert self.headers['Transfer-Encoding'].lower() == 'chunked'
        if 'host' not in self.headers:
            self.headers['Host'] = self.host
        self.event_loop = tulip.get_event_loop()
        self.transport = None

    def validate(self, value, name, embedded_spaces_okay=False):
        # Must be a string. If embedded_spaces_okay is False, no
        # whitespace is allowed; otherwise, internal single spaces are
        # allowed (but no other whitespace).
        assert isinstance(value, str), \
            '{} should be str, not {}'.format(name, type(value))
        parts = value.split()
        assert parts, '{} should not be empty'.format(name)
        if embedded_spaces_okay:
            assert ' '.join(parts) == value, \
                '{} can only contain embedded single spaces ({!r})'.format(
                    name, value)
        else:
            assert parts == [value], \
                '{} cannot contain whitespace ({!r})'.format(name, value)
        return value

    @tulip.coroutine
    def connect(self):
        yield from self.event_loop.create_connection(lambda: self,
                                                     self.host,
                                                     self.port,
                                                     ssl=self.ssl)
        # TODO: A better mechanism to return all info from the
        # status line, all headers, and the buffer, without having
        # an N-tuple return value.
        status_line = yield from self.stream.readline()
        m = re.match(rb'HTTP/(\d\.\d)\s+(\d\d\d)\s+([^\r\n]+)\r?\n\Z',
                     status_line)
        if not m:
            raise 'Invalid HTTP status line ({!r})'.format(status_line)
        version, status, message = m.groups()
        raw_headers = []
        while True:
            header = yield from self.stream.readline()
            if not header.strip():
                break
            raw_headers.append(header)
        parser = email.parser.BytesHeaderParser()
        headers = parser.parsebytes(b''.join(raw_headers))
        content_length = headers.get('content-length')
        if content_length:
            content_length = int(content_length)  # May raise.
        if content_length is None:
            stream = self.stream
        else:
            # TODO: A wrapping stream that limits how much it can read
            # without reading it all into memory at once.
            body = yield from self.stream.readexactly(content_length)
            stream = StreamReader()
            stream.feed_data(body)
            stream.feed_eof()
        sts = '{} {}'.format(self.decode(status), self.decode(message))
        return (sts, headers, stream)

    def encode(self, s):
        if isinstance(s, bytes):
            return s
        return s.encode(self.encoding)

    def decode(self, s):
        if isinstance(s, str):
            return s
        return s.decode(self.encoding)

    def write_str(self, s):
        self.transport.write(self.encode(s))

    def write_chunked(self, s):
        if not s:
            return
        data = self.encode(s)
        self.write_str('{:x}\r\n'.format(len(data)))
        self.transport.write(data)
        self.transport.write(b'\r\n')

    def write_chunked_eof(self):
        self.transport.write(b'0\r\n\r\n')

    def connection_made(self, transport):
        self.transport = transport
        line = '{} {} HTTP/{}\r\n'.format(self.method,
                                          self.path,
                                          self.version)
        self.write_str(line)
        for key, value in self.headers.items():
            self.write_str('{}: {}\r\n'.format(key, value))
        self.transport.write(b'\r\n')
        self.stream = StreamReader()
        if self.make_body is not None:
            if self.chunked:
                self.make_body(self.write_chunked, self.write_chunked_eof)
            else:
                self.make_body(self.write_str, self.transport.write_eof)

    def data_received(self, data):
        self.stream.feed_data(data)

    def eof_received(self):
        self.stream.feed_eof()

    def connection_lost(self, exc):
        pass
