"""Tests for http/server.py"""

import unittest
import unittest.mock

import tulip
from tulip.http import server
from tulip.http import errors
from tulip.test_utils import LogTrackingTestCase


class HttpServerProtocolTests(LogTrackingTestCase):

    def setUp(self):
        super().setUp()
        self.suppress_log_errors()

        self.loop = tulip.new_event_loop()
        tulip.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()
        super().tearDown()

    def test_http_status_exception(self):
        exc = errors.HttpStatusException(500, message='Internal error')
        self.assertEqual(exc.code, 500)
        self.assertEqual(exc.message, 'Internal error')

    def test_handle_request(self):
        transport = unittest.mock.Mock()

        srv = server.ServerHttpProtocol()
        srv.connection_made(transport)

        rline = unittest.mock.Mock()
        rline.version = (1, 1)
        message = unittest.mock.Mock()
        srv.handle_request(rline, message)

        content = b''.join([c[1][0] for c in list(transport.write.mock_calls)])
        self.assertTrue(content.startswith(b'HTTP/1.1 404 Not Found\r\n'))

    def test_connection_made(self):
        srv = server.ServerHttpProtocol()
        self.assertIsNone(srv._request_handle)

        srv.connection_made(unittest.mock.Mock())
        self.assertIsNotNone(srv._request_handle)

    def test_data_received(self):
        srv = server.ServerHttpProtocol()
        srv.connection_made(unittest.mock.Mock())

        srv.data_received(b'123')
        self.assertEqual(b'123', b''.join(srv.stream.buffer))

        srv.data_received(b'456')
        self.assertEqual(b'123456', b''.join(srv.stream.buffer))

    def test_eof_received(self):
        srv = server.ServerHttpProtocol()
        srv.connection_made(unittest.mock.Mock())
        srv.eof_received()
        self.assertTrue(srv.stream.eof)

    def test_connection_lost(self):
        srv = server.ServerHttpProtocol()
        srv.connection_made(unittest.mock.Mock())
        srv.data_received(b'123')

        handle = srv._request_handle
        srv.connection_lost(None)

        self.assertIsNone(srv._request_handle)
        self.assertTrue(handle.cancelled())

        srv.connection_lost(None)
        self.assertIsNone(srv._request_handle)

    def test_close(self):
        srv = server.ServerHttpProtocol()
        self.assertFalse(srv._closing)

        srv.close()
        self.assertTrue(srv._closing)

    def test_handle_error(self):
        transport = unittest.mock.Mock()
        srv = server.ServerHttpProtocol()
        srv.connection_made(transport)

        srv.handle_error(404)
        content = b''.join([c[1][0] for c in list(transport.write.mock_calls)])
        self.assertIn(b'HTTP/1.1 404 Not Found', content)

    @unittest.mock.patch('tulip.http.server.traceback')
    def test_handle_error_traceback_exc(self, m_trace):
        transport = unittest.mock.Mock()
        srv = server.ServerHttpProtocol(debug=True)
        srv.connection_made(transport)

        m_trace.format_exc.side_effect = ValueError

        srv.handle_error(500, exc=object())
        content = b''.join([c[1][0] for c in list(transport.write.mock_calls)])
        self.assertTrue(
            content.startswith(b'HTTP/1.1 500 Internal Server Error'))

    def test_handle_error_debug(self):
        transport = unittest.mock.Mock()
        srv = server.ServerHttpProtocol()
        srv.debug = True
        srv.connection_made(transport)

        try:
            raise ValueError()
        except Exception as exc:
            srv.handle_error(999, exc=exc)

        content = b''.join([c[1][0] for c in list(transport.write.mock_calls)])

        self.assertIn(b'HTTP/1.1 500 Internal', content)
        self.assertIn(b'Traceback (most recent call last):', content)

    def test_handle_error_500(self):
        log = unittest.mock.Mock()
        transport = unittest.mock.Mock()

        srv = server.ServerHttpProtocol(log=log)
        srv.connection_made(transport)

        srv.handle_error(500)
        self.assertTrue(log.exception.called)

    def test_handle(self):
        transport = unittest.mock.Mock()
        srv = server.ServerHttpProtocol()
        srv.connection_made(transport)

        handle = srv.handle_request = unittest.mock.Mock()

        srv.stream.feed_data(
            b'GET / HTTP/1.0\r\n'
            b'Host: example.com\r\n\r\n')
        srv.close()

        self.loop.run_until_complete(srv._request_handle)
        self.assertTrue(handle.called)
        self.assertIsNone(srv._request_handle)

    def test_handle_coro(self):
        transport = unittest.mock.Mock()
        srv = server.ServerHttpProtocol()
        srv.connection_made(transport)

        called = False

        @tulip.coroutine
        def coro(rline, message):
            nonlocal called
            called = True
            yield from []
            srv.eof_received()

        srv.handle_request = coro

        srv.stream.feed_data(
            b'GET / HTTP/1.0\r\n'
            b'Host: example.com\r\n\r\n')
        self.loop.run_until_complete(srv._request_handle)
        self.assertTrue(called)

    def test_handle_close(self):
        transport = unittest.mock.Mock()
        srv = server.ServerHttpProtocol()
        srv.connection_made(transport)

        handle = srv.handle_request = unittest.mock.Mock()

        srv.stream.feed_data(
            b'GET / HTTP/1.0\r\n'
            b'Host: example.com\r\n\r\n')
        srv.close()
        self.loop.run_until_complete(srv._request_handle)

        self.assertTrue(handle.called)
        self.assertTrue(transport.close.called)

    def test_handle_cancel(self):
        log = unittest.mock.Mock()
        transport = unittest.mock.Mock()

        srv = server.ServerHttpProtocol(log=log, debug=True)
        srv.connection_made(transport)

        srv.handle_request = unittest.mock.Mock()

        @tulip.task
        def cancel():
            yield from []
            srv._request_handle.cancel()

        srv.close()
        self.loop.run_until_complete(
            tulip.wait([srv._request_handle, cancel()]))
        self.assertTrue(log.debug.called)

    def test_handle_400(self):
        transport = unittest.mock.Mock()
        srv = server.ServerHttpProtocol()
        srv.connection_made(transport)
        srv.handle_error = unittest.mock.Mock()

        def side_effect(*args):
            srv.close()
        srv.handle_error.side_effect = side_effect

        srv.stream.feed_data(b'GET / HT/asd\r\n')

        self.loop.run_until_complete(srv._request_handle)
        self.assertTrue(srv.handle_error.called)
        self.assertTrue(400, srv.handle_error.call_args[0][0])
        self.assertTrue(transport.close.called)

    def test_handle_500(self):
        transport = unittest.mock.Mock()
        srv = server.ServerHttpProtocol()
        srv.connection_made(transport)

        handle = srv.handle_request = unittest.mock.Mock()
        handle.side_effect = ValueError
        srv.handle_error = unittest.mock.Mock()
        srv.close()

        srv.stream.feed_data(
            b'GET / HTTP/1.0\r\n'
            b'Host: example.com\r\n\r\n')
        self.loop.run_until_complete(srv._request_handle)

        self.assertTrue(srv.handle_error.called)
        self.assertTrue(500, srv.handle_error.call_args[0][0])
