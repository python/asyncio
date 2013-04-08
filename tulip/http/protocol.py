"""Http related helper utils."""

__all__ = ['HttpStreamReader',
           'HttpMessage', 'Request', 'Response',
           'RawHttpMessage', 'RequestLine', 'ResponseStatus']

import collections
import functools
import http.server
import itertools
import re
import sys
import time
import zlib
from wsgiref.handlers import format_date_time

import tulip
from . import errors

METHRE = re.compile('[A-Z0-9$-_.]+')
VERSRE = re.compile('HTTP/(\d+).(\d+)')
HDRRE = re.compile(b"[\x00-\x1F\x7F()<>@,;:\[\]={} \t\\\\\"]")
CONTINUATION = (b' ', b'\t')
RESPONSES = http.server.BaseHTTPRequestHandler.responses

RequestLine = collections.namedtuple(
    'RequestLine', ['method', 'uri', 'version'])


ResponseStatus = collections.namedtuple(
    'ResponseStatus', ['version', 'code', 'reason'])


RawHttpMessage = collections.namedtuple(
    'RawHttpMessage', ['headers', 'payload', 'should_close', 'compression'])


class HttpStreamReader(tulip.StreamReader):

    MAX_HEADERS = 32768
    MAX_HEADERFIELD_SIZE = 8190

    # if _parser is set, feed_data and feed_eof sends data into
    # _parser instead of self. is it being used as stream redirection for
    # _parse_chunked_payload, _parse_length_payload and _parse_eof_payload
    _parser = None

    def feed_data(self, data):
        """_parser is a generator, if _parser is set, feed_data sends
        incoming data into the generator untile generator stops."""
        if self._parser:
            try:
                self._parser.send(data)
            except StopIteration as exc:
                self._parser = None
                if exc.value:
                    self.feed_data(exc.value)
        else:
            super().feed_data(data)

    def feed_eof(self):
        """_parser is a generator, if _parser is set feed_eof throws
        StreamEofException into this generator."""
        if self._parser:
            try:
                self._parser.throw(StreamEofException())
            except StopIteration:
                self._parser = None

        super().feed_eof()

    @tulip.coroutine
    def read_request_line(self):
        """Read request status line. Exception errors.BadStatusLine
        could be raised in case of any errors in status line.
        Returns three values (method, uri, version)

        Example:

            GET /path HTTP/1.1

            >> yield from reader.read_request_line()
            ('GET', '/path', (1, 1))

        """
        bline = yield from self.readline()
        try:
            line = bline.decode('ascii').rstrip()
        except UnicodeDecodeError:
            raise errors.BadStatusLine(bline) from None

        try:
            method, uri, version = line.split(None, 2)
        except ValueError:
            raise errors.BadStatusLine(line) from None

        # method
        method = method.upper()
        if not METHRE.match(method):
            raise errors.BadStatusLine(method)

        # version
        match = VERSRE.match(version)
        if match is None:
            raise errors.BadStatusLine(version)
        version = (int(match.group(1)), int(match.group(2)))

        return RequestLine(method, uri, version)

    @tulip.coroutine
    def read_response_status(self):
        """Read response status line. Exception errors.BadStatusLine
        could be raised in case of any errors in status line.
        Returns three values (version, status_code, reason)

        Example:

            HTTP/1.1 200 Ok

            >> yield from reader.read_response_status()
            ((1, 1), 200, 'Ok')

        """
        bline = yield from self.readline()
        if not bline:
            # Presumably, the server closed the connection before
            # sending a valid response.
            raise errors.BadStatusLine(bline)

        try:
            line = bline.decode('ascii').rstrip()
        except UnicodeDecodeError:
            raise errors.BadStatusLine(bline) from None

        try:
            version, status = line.split(None, 1)
        except ValueError:
            raise errors.BadStatusLine(line) from None
        else:
            try:
                status, reason = status.split(None, 1)
            except ValueError:
                reason = ''

        # version
        match = VERSRE.match(version)
        if match is None:
            raise errors.BadStatusLine(line)
        version = (int(match.group(1)), int(match.group(2)))

        # The status code is a three-digit number
        try:
            status = int(status)
        except ValueError:
            raise errors.BadStatusLine(line) from None

        if status < 100 or status > 999:
            raise errors.BadStatusLine(line)

        return ResponseStatus(version, status, reason.strip())

    @tulip.coroutine
    def read_headers(self):
        """Read and parses RFC2822 headers from a stream.

        Line continuations are supported. Returns list of header name
        and value pairs. Header name is in upper case.
        """
        size = 0
        headers = []

        line = yield from self.readline()

        while line not in (b'\r\n', b'\n'):
            header_length = len(line)

            # Parse initial header name : value pair.
            sep_pos = line.find(b':')
            if sep_pos < 0:
                raise ValueError('Invalid header {}'.format(line.strip()))

            name, value = line[:sep_pos], line[sep_pos+1:]
            name = name.rstrip(b' \t').upper()
            if HDRRE.search(name):
                raise ValueError('Invalid header name {}'.format(name))

            name = name.strip().decode('ascii', 'surrogateescape')
            value = [value.lstrip()]

            # next line
            line = yield from self.readline()

            # consume continuation lines
            continuation = line.startswith(CONTINUATION)

            if continuation:
                while continuation:
                    header_length += len(line)
                    if header_length > self.MAX_HEADERFIELD_SIZE:
                        raise errors.LineTooLong(
                            'limit request headers fields size')
                    value.append(line)

                    line = yield from self.readline()
                    continuation = line.startswith(CONTINUATION)
            else:
                if header_length > self.MAX_HEADERFIELD_SIZE:
                    raise errors.LineTooLong(
                        'limit request headers fields size')

            # total headers size
            size += header_length
            if size >= self.MAX_HEADERS:
                raise errors.LineTooLong('limit request headers fields')

            headers.append(
                (name,
                 b''.join(value).rstrip().decode('ascii', 'surrogateescape')))

        return headers

    def _parse_chunked_payload(self):
        """Chunked transfer encoding parser."""
        stream = yield

        try:
            data = bytearray()

            while True:
                # read line
                if b'\n' not in data:
                    data.extend((yield))
                    continue

                line, data = data.split(b'\n', 1)

                # Read the next chunk size from the file
                i = line.find(b';')
                if i >= 0:
                    line = line[:i]  # strip chunk-extensions
                try:
                    size = int(line, 16)
                except ValueError:
                    raise errors.IncompleteRead(b'') from None

                if size == 0:
                    break

                # read chunk
                while len(data) < size:
                    data.extend((yield))

                # feed stream
                stream.feed_data(data[:size])

                data = data[size:]

                # toss the CRLF at the end of the chunk
                while len(data) < 2:
                    data.extend((yield))

                data = data[2:]

            # read and discard trailer up to the CRLF terminator
            while True:
                if b'\n' in data:
                    line, data = data.split(b'\n', 1)
                    if line in (b'\r', b''):
                        break
                else:
                    data.extend((yield))

            # stream eof
            stream.feed_eof()
            return data

        except StreamEofException:
            stream.set_exception(errors.IncompleteRead(b''))
        except errors.IncompleteRead as exc:
            stream.set_exception(exc)

    def _parse_length_payload(self, length):
        """Read specified amount of bytes."""
        stream = yield

        try:
            data = bytearray()
            while length:
                data.extend((yield))

                data_len = len(data)
                if data_len <= length:
                    stream.feed_data(data)
                    data = bytearray()
                    length -= data_len
                else:
                    stream.feed_data(data[:length])
                    data = data[length:]
                    length = 0

            stream.feed_eof()
            return data
        except StreamEofException:
            stream.set_exception(errors.IncompleteRead(b''))

    def _parse_eof_payload(self):
        """Read all bytes untile eof."""
        stream = yield

        try:
            while True:
                stream.feed_data((yield))
        except StreamEofException:
            stream.feed_eof()

    @tulip.coroutine
    def read_message(self, version=(1, 1),
                     length=None, compression=True, readall=False):
        """Read RFC2822 headers and message payload from a stream.

        read_message() automatically decompress gzip and deflate content
        encoding. To prevent decompression pass compression=False.

        Returns tuple of headers, payload stream, should close flag,
        compression type.
        """
        # load headers
        headers = yield from self.read_headers()

        # payload params
        chunked = False
        encoding = None
        close_conn = None

        for name, value in headers:
            if name == 'CONTENT-LENGTH':
                length = value
            elif name == 'TRANSFER-ENCODING':
                chunked = value.lower() == 'chunked'
            elif name == 'SEC-WEBSOCKET-KEY1':
                length = 8
            elif name == 'CONNECTION':
                v = value.lower()
                if v == 'close':
                    close_conn = True
                elif v == 'keep-alive':
                    close_conn = False
            elif compression and name == 'CONTENT-ENCODING':
                enc = value.lower()
                if enc in ('gzip', 'deflate'):
                    encoding = enc

        if close_conn is None:
            close_conn = version <= (1, 0)

        # payload parser
        if chunked:
            parser = self._parse_chunked_payload()

        elif length is not None:
            try:
                length = int(length)
            except ValueError:
                raise errors.InvalidHeader('CONTENT-LENGTH') from None

            if length < 0:
                raise errors.InvalidHeader('CONTENT-LENGTH')

            parser = self._parse_length_payload(length)
        else:
            if readall:
                parser = self._parse_eof_payload()
            else:
                parser = self._parse_length_payload(0)

        next(parser)

        payload = stream = tulip.StreamReader()

        # payload decompression wrapper
        if encoding is not None:
            stream = DeflateStream(stream, encoding)

        try:
            # initialize payload parser with stream, stream is being
            # used by parser as destination stream
            parser.send(stream)
        except StopIteration:
            pass
        else:
            # feed existing buffer to payload parser
            self.byte_count = 0
            while self.buffer:
                try:
                    parser.send(self.buffer.popleft())
                except StopIteration as exc:
                    parser = None

                    # parser is done
                    buf = b''.join(self.buffer)
                    self.buffer.clear()

                    # re-add remaining data back to buffer
                    if exc.value:
                        self.feed_data(exc.value)

                    if buf:
                        self.feed_data(buf)

                    break

            # parser still require more data
            if parser is not None:
                if self.eof:
                    try:
                        parser.throw(StreamEofException())
                    except StopIteration as exc:
                        pass
                else:
                    self._parser = parser

        return RawHttpMessage(headers, payload, close_conn, encoding)


class StreamEofException(Exception):
    """Internal exception: eof received."""


class DeflateStream:
    """DeflateStream decomress stream and feed data into specified stream."""

    def __init__(self, stream, encoding):
        self.stream = stream
        zlib_mode = (16 + zlib.MAX_WBITS
                     if encoding == 'gzip' else -zlib.MAX_WBITS)

        self.zlib = zlib.decompressobj(wbits=zlib_mode)

    def set_exception(self, exc):
        self.stream.set_exception(exc)

    def feed_data(self, chunk):
        try:
            chunk = self.zlib.decompress(chunk)
        except:
            self.stream.set_exception(errors.IncompleteRead(b''))

        if chunk:
            self.stream.feed_data(chunk)

    def feed_eof(self):
        self.stream.feed_data(self.zlib.flush())
        if not self.zlib.eof:
            self.stream.set_exception(errors.IncompleteRead(b''))

        self.stream.feed_eof()


EOF_MARKER = object()
EOL_MARKER = object()


def wrap_payload_filter(func):
    """Wraps payload filter and piped filters.

    Filter is a generatator that accepts arbitrary chunks of data,
    modify data and emit new stream of data.

    For example we have stream of chunks: ['1', '2', '3', '4', '5'],
    we can apply chunking filter to this stream:

    ['1', '2', '3', '4', '5']
      |
    response.add_chunking_filter(2)
      |
    ['12', '34', '5']

    It is possible to use different filters at the same time.

    For a example to compress incoming stream with 'deflate' encoding
    and then split data and emit chunks of 8196 bytes size chunks:

      >> response.add_compression_filter('deflate')
      >> response.add_chunking_filter(8196)

    Filters do not alter transfer encoding.

    Filter can receive types types of data, bytes object or EOF_MARKER.

      1. If filter receives bytes object, it should process data
         and yield processed data then yield EOL_MARKER object.
      2. If Filter recevied EOF_MARKER, it should yield remaining
         data (buffered) and then yield EOF_MARKER.
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kw):
        new_filter = func(self, *args, **kw)

        filter = self.filter
        if filter is not None:
            next(new_filter)
            self.filter = filter_pipe(filter, new_filter)
        else:
            self.filter = new_filter

        next(self.filter)

    return wrapper


def filter_pipe(filter, filter2):
    """Creates pipe between two filters.

    filter_pipe() feeds first filter with incoming data and then
    send yielded from first filter data into filter2, results of
    filter2 are being emitted.

      1. If filter_pipe receives bytes object, it sends it to the first filter.
      2. Reads yielded values from the first filter until it receives
         EOF_MARKER or EOL_MARKER.
      3. Each of this values is being send to second filter.
      4. Reads yielded values from second filter until it recives EOF_MARKER or
         EOL_MARKER. Each of this values yields to writer.
    """
    chunk = yield

    while True:
        eof = chunk is EOF_MARKER
        chunk = filter.send(chunk)

        while chunk is not EOL_MARKER:
            chunk = filter2.send(chunk)

            while chunk not in (EOF_MARKER, EOL_MARKER):
                yield chunk
                chunk = next(filter2)

            if chunk is not EOF_MARKER:
                if eof:
                    chunk = EOF_MARKER
                else:
                    chunk = next(filter)
            else:
                break

        chunk = yield EOL_MARKER


class HttpMessage:
    """HttpMessage allows to write headers and payload to a stream.

    For example, lets say we want to read file then compress it with deflate
    compression and then send it with chunked transfer encoding, code may look
    like this:

       >> response = tulip.http.Response(transport, 200)

    We have to use deflate compression first:

      >> response.add_compression_filter('deflate')

    Then we want to split output stream into chunks of 1024 bytes size:

      >> response.add_chunking_filter(1024)

    We can add headers to response with add_headers() method. add_headers()
    does not send data to transport, send_headers() sends request/response
    line and then sends headers:

      >> response.add_headers(
      ..     ('Content-Disposition', 'attachment; filename="..."'))
      >> response.send_headers()

    Now we can use chunked writer to write stream to a network stream.
    First call to write() method sends response status line and headers,
    add_header() and add_headers() method unavailble at this stage:

    >> with open('...', 'rb') as f:
    ..     chunk = fp.read(8196)
    ..     while chunk:
    ..         response.write(chunk)
    ..         chunk = fp.read(8196)

    >> response.write_eof()
    """

    writer = None

    # 'filter' is being used for altering write() bahaviour,
    # add_chunking_filter adds deflate/gzip compression and
    # add_compression_filter splits incoming data into a chunks.
    filter = None

    HOP_HEADERS = None  # Must be set by subclass.

    SERVER_SOFTWARE = 'Python/{0[0]}.{0[1]} tulip/0.0'.format(sys.version_info)

    status = None
    status_line = b''

    # subclass can enable auto sending headers with write() call,
    # this is useful for wsgi's start_response implementation.
    _send_headers = False

    def __init__(self, transport, version, close):
        self.transport = transport
        self.version = version
        self.closing = close
        self.keepalive = False

        self.chunked = False
        self.length = None
        self.upgrade = False
        self.headers = []
        self.headers_sent = False

    def force_close(self):
        self.closing = True

    def force_chunked(self):
        self.chunked = True

    def keep_alive(self):
        return self.keepalive and not self.closing

    def is_headers_sent(self):
        return self.headers_sent

    def add_header(self, name, value):
        """Analyze headers. Calculate content length,
        removes hop headers, etc."""
        assert not self.headers_sent, 'headers have been sent already'
        assert isinstance(name, str), '{!r} is not a string'.format(name)

        name = name.strip().upper()

        if name == 'CONTENT-LENGTH':
            self.length = int(value)

        if name == 'CONNECTION':
            val = value.lower().strip()
            # handle websocket
            if val == 'upgrade':
                self.upgrade = True
            # connection keep-alive
            elif val == 'close':
                self.keepalive = False
            elif val == 'keep-alive':
                self.keepalive = True

        elif name == 'UPGRADE':
            if 'websocket' in value.lower():
                self.headers.append((name, value))

        elif name == 'TRANSFER-ENCODING' and not self.chunked:
            self.chunked = value.lower().strip() == 'chunked'

        elif name not in self.HOP_HEADERS:
            # ignore hopbyhop headers
            self.headers.append((name, value))

    def add_headers(self, *headers):
        """Adds headers to a http message."""
        for name, value in headers:
            self.add_header(name, value)

    def send_headers(self):
        """Writes headers to a stream. Constructs payload writer."""
        # Chunked response is only for HTTP/1.1 clients or newer
        # and there is no Content-Length header is set.
        # Do not use chunked responses when the response is guaranteed to
        # not have a response body (304, 204).
        assert not self.headers_sent, 'headers have been sent already'
        self.headers_sent = True

        if (self.chunked is True) or (
                self.length is None and
                self.version >= (1, 1) and
                self.status not in (304, 204)):
            self.chunked = True
            self.writer = self._write_chunked_payload()

        elif self.length is not None:
            self.writer = self._write_length_payload(self.length)

        else:
            self.writer = self._write_eof_payload()

        next(self.writer)

        # status line
        self.transport.write(self.status_line.encode('ascii'))

        # send headers
        self.transport.write(
            ('{}\r\n\r\n'.format('\r\n'.join(
                ('{}: {}'.format(k, v) for k, v in
                 itertools.chain(self._default_headers(), self.headers))))
             ).encode('ascii'))

    def _default_headers(self):
        # set the connection header
        if self.upgrade:
            connection = 'upgrade'
        elif self.keep_alive():
            connection = 'keep-alive'
        else:
            connection = 'close'

        headers = [('CONNECTION', connection)]

        if self.chunked:
            headers.append(('TRANSFER-ENCODING', 'chunked'))

        return headers

    def write(self, chunk):
        """write() writes chunk of data to a steram by using different writers.
        writer uses filter to modify chunk of data. write_eof() indicates
        end of stream. writer can't be used after write_eof() method
        being called."""
        assert (isinstance(chunk, (bytes, bytearray)) or
                chunk is EOF_MARKER), chunk

        if self._send_headers and not self.headers_sent:
            self.send_headers()

        assert self.writer is not None, 'send_headers() is not called.'

        if self.filter:
            chunk = self.filter.send(chunk)
            while chunk not in (EOF_MARKER, EOL_MARKER):
                self.writer.send(chunk)
                chunk = next(self.filter)
        else:
            if chunk is not EOF_MARKER:
                self.writer.send(chunk)

    def write_eof(self):
        self.write(EOF_MARKER)
        try:
            self.writer.throw(StreamEofException())
        except StopIteration:
            pass

    def _write_chunked_payload(self):
        """Write data in chunked transfer encoding."""
        while True:
            try:
                chunk = yield
            except StreamEofException:
                self.transport.write(b'0\r\n\r\n')
                break

            self.transport.write('{:x}\r\n'.format(len(chunk)).encode('ascii'))
            self.transport.write(bytes(chunk))
            self.transport.write(b'\r\n')

    def _write_length_payload(self, length):
        """Write specified number of bytes to a stream."""
        while True:
            try:
                chunk = yield
            except StreamEofException:
                break

            if length:
                l = len(chunk)
                if length >= l:
                    self.transport.write(chunk)
                else:
                    self.transport.write(chunk[:length])

                length = max(0, length-l)

    def _write_eof_payload(self):
        while True:
            try:
                chunk = yield
            except StreamEofException:
                break

            self.transport.write(chunk)

    @wrap_payload_filter
    def add_chunking_filter(self, chunk_size=16*1024):
        """Split incoming stream into chunks."""
        buf = bytearray()
        chunk = yield

        while True:
            if chunk is EOF_MARKER:
                if buf:
                    yield buf

                yield EOF_MARKER

            else:
                buf.extend(chunk)

                while len(buf) >= chunk_size:
                    chunk = bytes(buf[:chunk_size])
                    del buf[:chunk_size]
                    yield chunk

                chunk = yield EOL_MARKER

    @wrap_payload_filter
    def add_compression_filter(self, encoding='deflate'):
        """Compress incoming stream with deflate or gzip encoding."""
        zlib_mode = (16 + zlib.MAX_WBITS
                     if encoding == 'gzip' else -zlib.MAX_WBITS)
        zcomp = zlib.compressobj(wbits=zlib_mode)

        chunk = yield
        while True:
            if chunk is EOF_MARKER:
                yield zcomp.flush()
                chunk = yield EOF_MARKER

            else:
                yield zcomp.compress(chunk)
                chunk = yield EOL_MARKER


class Response(HttpMessage):
    """Create http response message.

    Transport is a socket stream transport. status is a response status code,
    status has to be integer value. http_version is a tuple that represents
    http version, (1, 0) stands for HTTP/1.0 and (1, 1) is for HTTP/1.1
    """

    HOP_HEADERS = {
        'CONNECTION',
        'KEEP-ALIVE',
        'PROXY-AUTHENTICATE',
        'PROXY-AUTHORIZATION',
        'TE',
        'TRAILERS',
        'TRANSFER-ENCODING',
        'UPGRADE',
        'SERVER',
        'DATE',
    }

    def __init__(self, transport, status, http_version=(1, 1), close=False):
        super().__init__(transport, http_version, close)

        self.status = status
        self.status_line = 'HTTP/{0[0]}.{0[1]} {1} {2}\r\n'.format(
            http_version, status, RESPONSES[status][0])

    def _default_headers(self):
        headers = super()._default_headers()
        headers.extend((('DATE', format_date_time(time.time())),
                        ('SERVER', self.SERVER_SOFTWARE)))

        return headers


class Request(HttpMessage):

    HOP_HEADERS = ()

    def __init__(self, transport, method, uri,
                 http_version=(1, 1), close=False):
        super().__init__(transport, http_version, close)

        self.method = method
        self.uri = uri
        self.status_line = '{0} {1} HTTP/{2[0]}.{2[1]}\r\n'.format(
            method, uri, http_version)

    def _default_headers(self):
        headers = super()._default_headers()
        headers.append(('USER-AGENT', self.SERVER_SOFTWARE))

        return headers
