"""Http related helper utils."""

__all__ = ['HttpStreamReader', 'RequestLine', 'ResponseStatus']

import collections
import http.client
import re

import tulip

METHRE = re.compile('[A-Z0-9$-_.]+')
VERSRE = re.compile('HTTP/(\d+).(\d+)')


RequestLine = collections.namedtuple(
    'RequestLine', ['method', 'uri', 'version'])


ResponseStatus = collections.namedtuple(
    'ResponseStatus', ['version', 'code', 'reason'])


class HttpStreamReader(tulip.StreamReader):

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
