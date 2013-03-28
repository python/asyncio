"""HTTP Client for Tulip.

Most basic usage:

  sts, headers, response = yield from http_client.fetch(url,
       method='GET', headers={}, request=b'')
  assert isinstance(sts, int)
  assert isinstance(headers, dict)
    # sort of; case insensitive (what about multiple values for same header?)
  headers['status'] == '200 Ok'  # or some such
  assert isinstance(response, bytes)

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
                 make_body=None, encoding='utf-8', version=(1, 1),
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
        self.version = version
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

    def connection_made(self, transport):
        self.transport = transport
        self.stream = protocol.HttpStreamReader()

        self.request = protocol.Request(
            transport, self.method, self.path, self.version)

        self.request.add_headers(*self.headers.items())
        self.request.send_headers()

        if self.make_body is not None:
            if self.chunked:
                self.make_body(
                    self.request.write, self.request.eof)
            else:
                self.make_body(
                    self.request.write, self.request.eof)
        else:
            self.request.write_eof()

    def data_received(self, data):
        self.stream.feed_data(data)

    def eof_received(self):
        self.stream.feed_eof()

    def connection_lost(self, exc):
        pass
