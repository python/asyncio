"""Http related helper utils."""

__all__ = ['HttpStreamReader', 'RequestLine', 'ResponseStatus']

import collections
import http.client
import re

import tulip

METHRE = re.compile('[A-Z0-9$-_.]+')
VERSRE = re.compile('HTTP/(\d+).(\d+)')
HDRRE = re.compile(b"[\x00-\x1F\x7F()<>@,;:\[\]={} \t\\\\\"]")
CONTINUATION = (b' ', b'\t')


RequestLine = collections.namedtuple(
    'RequestLine', ['method', 'uri', 'version'])


ResponseStatus = collections.namedtuple(
    'ResponseStatus', ['version', 'code', 'reason'])


class HttpStreamReader(tulip.StreamReader):

    MAX_HEADERS = 32768
    MAX_HEADERFIELD_SIZE = 8190

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
        and value pairs.
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
