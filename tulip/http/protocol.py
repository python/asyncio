"""Http related helper utils."""

__all__ = ['HttpStreamReader', 'HttpMessage', 'RequestLine', 'ResponseStatus']

import collections
import functools
import http.client
import re
import zlib

import tulip

METHRE = re.compile('[A-Z0-9$-_.]+')
VERSRE = re.compile('HTTP/(\d+).(\d+)')
HDRRE = re.compile(b"[\x00-\x1F\x7F()<>@,;:\[\]={} \t\\\\\"]")
CONTINUATION = (b' ', b'\t')


RequestLine = collections.namedtuple(
    'RequestLine', ['method', 'uri', 'version'])


ResponseStatus = collections.namedtuple(
    'ResponseStatus', ['version', 'code', 'reason'])


HttpMessage = collections.namedtuple(
    'HttpMessage', ['headers', 'payload', 'should_close', 'compression'])


class StreamEofException(http.client.HTTPException):
    """eof received"""


def wrap_payload_reader(f):
    """wrap_payload_reader wraps payload readers and redirect stream.
    payload readers are generator functions, read_chunked_payload,
    read_length_payload, read_eof_payload.
    payload reader allows to modify data stream and feed data into stream.

    StreamReader instance should be send to generator as first parameter.
    This steam is used as destination stream for processed data.
    To send data to reader use generator's send() method.

    To indicate eof stream, throw StreamEofException exception into the reader.
    In case of errors in incoming stream reader sets exception to
    destination stream with StreamReader.set_exception() method.

    Before exit, reader generator returns unprocessed data.
    """

    @functools.wraps(f)
    def wrapper(self, *args, **kw):
        assert self._reader is None

        rstream = stream = tulip.StreamReader()

        encoding = kw.pop('encoding', None)
        if encoding is not None:
            if encoding not in ('gzip', 'deflate'):
                raise ValueError(
                    'Content-Encoding %r is not supported' % encoding)

            stream = DeflateStream(stream, encoding)

        reader = f(self, *args, **kw)
        next(reader)
        try:
            reader.send(stream)
        except StopIteration:
            pass
        else:
            # feed buffer
            self.line_count = 0
            self.byte_count = 0
            while self.buffer:
                try:
                    reader.send(self.buffer.popleft())
                except StopIteration as exc:
                    buf = b''.join(self.buffer)
                    self.buffer.clear()
                    reader = None
                    if exc.value:
                        self.feed_data(exc.value)

                    if buf:
                        self.feed_data(buf)

                    break

            if reader is not None:
                if self.eof:
                    try:
                        reader.throw(StreamEofException())
                    except StopIteration as exc:
                        pass
                else:
                    self._reader = reader

        return rstream

    return wrapper


class DeflateStream:
    """DeflateStream decomress stream and feed data into specified steram."""

    def __init__(self, stream, encoding):
        self.stream = stream
        zlib_mode = (16 + zlib.MAX_WBITS
                     if encoding == 'gzip' else -zlib.MAX_WBITS)

        self.zlib = zlib.decompressobj(wbits=zlib_mode)

    def feed_data(self, chunk):
        try:
            chunk = self.zlib.decompress(chunk)
        except:
            self.stream.set_exception(http.client.IncompleteRead(b''))

        if chunk:
            self.stream.feed_data(chunk)

    def feed_eof(self):
        self.stream.feed_data(self.zlib.flush())
        if not self.zlib.eof:
            self.stream.set_exception(http.client.IncompleteRead(b''))

        self.stream.feed_eof()


class HttpStreamReader(tulip.StreamReader):

    MAX_HEADERS = 32768
    MAX_HEADERFIELD_SIZE = 8190

    # if _reader is set, feed_data and feed_eof sends data into
    # _reader instead of self. is it being used as stream redirection for
    # read_chunked_payload, read_length_payload and read_eof_payload
    _reader = None

    def feed_data(self, data):
        """_reader is a generator, if _reader is set, feed_data sends
        incoming data into this generator untile generates stops."""
        if self._reader:
            try:
                self._reader.send(data)
            except StopIteration as exc:
                self._reader = None
                if exc.value:
                    self.feed_data(exc.value)
        else:
            super().feed_data(data)

    def feed_eof(self):
        """_reader is a generator, if _reader is set feed_eof throws
        StreamEofException into this generator."""
        if self._reader:
            try:
                self._reader.throw(StreamEofException())
            except StopIteration:
                self._reader = None

        super().feed_eof()

    @tulip.coroutine
    def read_request_line(self):
        """Read request status line. Exception http.client.BadStatusLine
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
            raise http.client.BadStatusLine(bline) from None

        try:
            method, uri, version = line.split(None, 2)
        except ValueError:
            raise http.client.BadStatusLine(line) from None

        # method
        method = method.upper()
        if not METHRE.match(method):
            raise http.client.BadStatusLine(method)

        # version
        match = VERSRE.match(version)
        if match is None:
            raise http.client.BadStatusLine(version)
        version = (int(match.group(1)), int(match.group(2)))

        return RequestLine(method, uri, version)

    @tulip.coroutine
    def read_response_status(self):
        """Read response status line. Exception http.client.BadStatusLine
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
            raise http.client.BadStatusLine(bline)

        try:
            line = bline.decode('ascii').rstrip()
        except UnicodeDecodeError:
            raise http.client.BadStatusLine(bline) from None

        try:
            version, status = line.split(None, 1)
        except ValueError:
            raise http.client.BadStatusLine(line) from None
        else:
            try:
                status, reason = status.split(None, 1)
            except ValueError:
                reason = ''

        # version
        match = VERSRE.match(version)
        if match is None:
            raise http.client.BadStatusLine(line)
        version = (int(match.group(1)), int(match.group(2)))

        # The status code is a three-digit number
        try:
            status = int(status)
        except ValueError:
            raise http.client.BadStatusLine(line) from None

        if status < 100 or status > 999:
            raise http.client.BadStatusLine(line)

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
                raise ValueError('Invalid header %s' % line.strip())

            name, value = line[:sep_pos], line[sep_pos+1:]
            name = name.rstrip(b' \t').upper()
            if HDRRE.search(name):
                raise ValueError('Invalid header name %s' % name)

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
                        raise http.client.LineTooLong(
                            'limit request headers fields size')
                    value.append(line)

                    line = yield from self.readline()
                    continuation = line.startswith(CONTINUATION)
            else:
                if header_length > self.MAX_HEADERFIELD_SIZE:
                    raise http.client.LineTooLong(
                        'limit request headers fields size')

            # total headers size
            size += header_length
            if size >= self.MAX_HEADERS:
                raise http.client.LineTooLong('limit request headers fields')

            headers.append(
                (name,
                 b''.join(value).rstrip().decode('ascii', 'surrogateescape')))

        return headers

    @wrap_payload_reader
    def read_chunked_payload(self):
        """Read chunked stream."""
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
                i = line.find(b";")
                if i >= 0:
                    line = line[:i]  # strip chunk-extensions
                try:
                    size = int(line, 16)
                except ValueError:
                    raise http.client.IncompleteRead(b'') from None

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
            stream.set_exception(http.client.IncompleteRead(b''))
        except http.client.IncompleteRead as exc:
            stream.set_exception(exc)

    @wrap_payload_reader
    def read_length_payload(self, length):
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
            stream.set_exception(http.client.IncompleteRead(b''))

    @wrap_payload_reader
    def read_eof_payload(self):
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
            elif name == "CONNECTION":
                v = value.lower()
                if v == "close":
                    close_conn = True
                elif v == "keep-alive":
                    close_conn = False
            elif compression and name == 'CONTENT-ENCODING':
                enc = value.lower()
                if enc in ('gzip', 'deflate'):
                    encoding = enc

        if close_conn is None:
            close_conn = version <= (1, 0)

        # payload stream
        if chunked:
            payload = self.read_chunked_payload(encoding=encoding)

        elif length is not None:
            try:
                length = int(length)
            except ValueError:
                raise http.client.HTTPException('CONTENT-LENGTH') from None

            if length < 0:
                raise http.client.HTTPException('CONTENT-LENGTH')

            payload = self.read_length_payload(length, encoding=encoding)
        else:
            if readall:
                payload = self.read_eof_payload(encoding=encoding)
            else:
                payload = self.read_length_payload(0, encoding=encoding)

        return HttpMessage(headers, payload, close_conn, encoding)
