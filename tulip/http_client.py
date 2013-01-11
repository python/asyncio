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

import urllib.parse  # For urlparse().

import tulip
from . import events


class HttpClientProtocol:
    """This Protocol class is also used to initiate the connection.

    Usage:
      p = HttpClientProtocol(url, ...)
      f = p.connect()  # Returns a Future
      ...now what?...
    """

    def __init__(self, url, method='GET', headers=None, make_body=None,
                 encoding='utf-8', version='1.1', chunked=False):
        self.url = self.validate(url, 'url')
        self.method = self.validate(method, 'method')
        self.headers = {}
        if headers:
            for key, value in headers.items():
                self.validate(key, 'header key')
                self.validate(value, 'header value', True)
                assert key not in self.headers, \
                       '{} header is a duplicate'.format(key)
                self.headers[key.lower()] = value
        self.encoding = self.validate(encoding, 'encoding')
        self.version = self.validate(version, 'version')
        self.make_body = make_body
        self.chunked = chunked
        self.split_url = urllib.parse.urlsplit(url)
        (self.scheme, self.netloc, self.path,
         self.query, self.fragment) = self.split_url
        if not self.path:
            self.path = '/'
        self.ssl = self.scheme == 'https'
        if 'content-length' not in self.headers:
            if self.make_body is None:
                self.headers['content-length'] = '0'
            else:
                self.chunked = True
        if self.chunked:
            if 'transfer-encoding' not in self.headers:
                self.headers['transfer-encoding'] = 'chunked'
            else:
                assert self.headers['transfer-encoding'] == 'chunked'
        if ':' in self.netloc:
            self.host, port_str = self.netloc.split(':', 1)
            self.port = int(port_str)
        else:
            self.host = self.netloc
            if self.ssl:
                self.port = 443
            else:
                self.port = 80
        if 'host' not in self.headers:
            self.headers['host'] = self.host
        self.event_loop = events.get_event_loop()
        self.transport = None

    def validate(self, value, name, embedded_spaces_okay=False):
        # Must be a string.  If embedded_spaces_okay is False, no
        # whitespace is allowed; otherwise, internal single spaces are
        # allowed (but no other whitespace).
        assert isinstance(value, str), \
               '{} should be str, not {}'.format(name, type(value))
        parts = value.split()
        assert parts, '{} should not be empty'.format(name)
        if embedded_spaces_okay:
            assert ' '.join(parts) == value, \
                   '{} can only contain embedded single spaces'.format(name)
        else:
            assert parts == [value], \
                   '{} cannot contain whitespace'.format(name)
        return value

    @tulip.coroutine
    def connect(self):
        t, p = yield from self.event_loop.create_transport(lambda: self,
                                                           self.host,
                                                           self.port,
                                                           ssl=self.ssl)
        return t  # Since p is self.

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
        if self.make_body is not None:
            if self.chunked:
                self.make_body(self.write_chunked, self.write_chunked_eof)
            else:
                self.make_body(self.write_str, self.transport.write_eof)
        self.lines_received = []
        self.incomplete_line = b''
        self.body_bytes_received = None

    def data_received(self, data):
        if self.body_bytes_received is not None:  # State: reading body.
            print('body data received:', data)
            self.body_bytes_received.append(data)
            self.body_byte_count += len(data)
            return
        self.incomplete_line += data
        parts = self.incomplete_line.splitlines(True)
        self.incomplete_line = b''
        done = False
        for part in parts:
            if not done:
                if not part.endswith(b'\n'):
                    self.incomplete_line = part
                    break
                self.lines_received.append(part)
                if part in (b'\r\n', b'\n'):
                    done = True
                    self.body_bytes_received = []
                    self.body_byte_count = 0
            else:
                self.body_bytes_received.append(part)
                self.body_byte_count += len(part)
        if done:
            print('headers received:', str(self.lines_received).replace(', ', ',\n '))
            for data in self.body_bytes_received:
                print('more data received:', data)

    def eof_received(self):
        print('received EOF')

    def connection_lost(self, exc):
        print('connection lost:', exc)
