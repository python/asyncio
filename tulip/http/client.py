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

__all__ = ['HttpClientProtocol']


import email.message
import email.parser

import tulip

from . import protocol


class HttpClientProtocol:
    """This Protocol class is also used to initiate the connection.

    Usage:
      p = HttpClientProtocol(url, ...)
      sts, headers, stream = yield from p.connect()

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
        self.headers['Accept-Encoding'] = 'gzip, deflate'
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
        yield from self.event_loop.create_connection(
            lambda: self, self.host, self.port, ssl=self.ssl)

        # read response status
        version, status, reason = yield from self.stream.read_response_status()

        message = yield from self.stream.read_message(version)

        # headers
        headers = email.message.Message()
        for hdr, val in message.headers:
            headers.add_header(hdr, val)

        sts = '{} {}'.format(status, reason)
        return (sts, headers, message.payload)

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
        self.stream = protocol.HttpStreamReader()
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
