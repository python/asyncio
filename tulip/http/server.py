"""simple http server."""

__all__ = ['ServerHttpProtocol']

import http.server
import inspect
import logging
import traceback

import tulip
import tulip.http

from . import errors

RESPONSES = http.server.BaseHTTPRequestHandler.responses
DEFAULT_ERROR_MESSAGE = """
<html>
  <head>
    <title>{status} {reason}</title>
  </head>
  <body>
    <h1>{status} {reason}</h1>
    {message}
  </body>
</html>"""


class ServerHttpProtocol(tulip.Protocol):
    """Simple http protocol implementation.

    ServerHttpProtocol handles incoming http request. It reads request line,
    request headers and request payload and calls handler_request() method.
    By default it always returns with 404 respose.

    ServerHttpProtocol handles errors in incoming request, like bad
    status line, bad headers or incomplete payload. If any error occurs,
    connection gets closed.
    """
    _closing = False
    _request_count = 0
    _request_handle = None

    def __init__(self, *, log=logging, debug=False):
        self.log = log
        self.debug = debug

    def connection_made(self, transport):
        self.transport = transport
        self.stream = tulip.http.HttpStreamReader()
        self._request_handle = self.start()

    def data_received(self, data):
        self.stream.feed_data(data)

    def connection_lost(self, exc):
        if self._request_handle is not None:
            self._request_handle.cancel()
            self._request_handle = None

    def eof_received(self):
        self.stream.feed_eof()

    def close(self):
        self._closing = True

    def log_access(self, status, info, message, *args, **kw):
        pass

    def log_debug(self, *args, **kw):
        if self.debug:
            self.log.debug(*args, **kw)

    def log_exception(self, *args, **kw):
        self.log.exception(*args, **kw)

    @tulip.task
    def start(self):
        """Start processing of incoming requests.
        It reads request line, request headers and request payload, then
        calls handle_request() method. Subclass has to override
        handle_request(). start() handles various excetions in request
        or response handling. In case of any error connection is being closed.
        """

        while True:
            info = None
            message = None
            self._request_count += 1

            try:
                info = yield from self.stream.read_request_line()
                message = yield from self.stream.read_message(info.version)

                handler = self.handle_request(info, message)
                if (inspect.isgenerator(handler) or
                    isinstance(handler, tulip.Future)):
                    yield from handler

            except tulip.CancelledError:
                self.log_debug('Ignored premature client disconnection.')
                break
            except errors.HttpException as exc:
                self.handle_error(exc.code, info, message, exc, exc.headers)
            except Exception as exc:
                self.handle_error(500, info, message, exc)
            finally:
                if self._closing:
                    self.transport.close()
                    break

        self._request_handle = None

    def handle_error(self, status=500, info=None,
                     message=None, exc=None, headers=None):
        """Handle errors.

        Returns http response with specific status code. Logs additional
        information. It always closes current connection."""

        if status == 500:
            self.log_exception("Error handling request")

        try:
            reason, msg = RESPONSES[status]
        except KeyError:
            status = 500
            reason, msg = '???', ''

        if self.debug and exc is not None:
            try:
                tb = traceback.format_exc()
                msg += '<br><h2>Traceback:</h2>\n<pre>{}</pre>'.format(tb)
            except:
                pass

        self.log_access(status, info, message)

        html = DEFAULT_ERROR_MESSAGE.format(
            status=status, reason=reason, message=msg)

        response = tulip.http.Response(self.transport, status, close=True)
        response.add_headers(
            ('Content-Type', 'text/html'),
            ('Content-Length', str(len(html))))
        if headers is not None:
            response.add_headers(*headers)
        response.send_headers()

        response.write(html.encode('ascii'))
        response.write_eof()

        self.close()

    def handle_request(self, info, message):
        """Handle a single http request.

        Subclass should override this method. By default it always
        returns 404 response.

        info: tulip.http.RequestLine instance
        message: tulip.http.RawHttpMessage instance
        """
        response = tulip.http.Response(
            self.transport, 404, http_version=info.version, close=True)

        body = b'Page Not Found!'

        response.add_headers(
            ('Content-Type', 'text/plain'),
            ('Content-Length', str(len(body))))
        response.send_headers()
        response.write(body)
        response.write_eof()

        self.close()
        self.log_access(404, info, message)
