"""Tests for http/protocol.py"""

import http.client
import unittest
import unittest.mock
import zlib

import tulip
from tulip.http import protocol


class HttpStreamReaderTests(unittest.TestCase):

    def setUp(self):
        self.loop = tulip.new_event_loop()
        tulip.set_event_loop(self.loop)

        self.transport = unittest.mock.Mock()
        self.stream = protocol.HttpStreamReader()

    def tearDown(self):
        self.loop.close()

    def test_request_line(self):
        self.stream.feed_data(b'get /path HTTP/1.1\r\n')
        self.assertEqual(
            ('GET', '/path', (1, 1)),
            self.loop.run_until_complete(self.stream.read_request_line()))

    def test_request_line_two_slashes(self):
        self.stream.feed_data(b'get //path HTTP/1.1\r\n')
        self.assertEqual(
            ('GET', '//path', (1, 1)),
            self.loop.run_until_complete(self.stream.read_request_line()))

    def test_request_line_non_ascii(self):
        self.stream.feed_data(b'get /path\xd0\xb0 HTTP/1.1\r\n')

        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.loop.run_until_complete(self.stream.read_request_line())

        self.assertEqual(
            b'get /path\xd0\xb0 HTTP/1.1\r\n', cm.exception.args[0])

    def test_request_line_bad_status_line(self):
        self.stream.feed_data(b'\r\n')
        self.assertRaises(
            http.client.BadStatusLine,
            self.loop.run_until_complete,
            self.stream.read_request_line())

    def test_request_line_bad_method(self):
        self.stream.feed_data(b'!12%()+=~$ /get HTTP/1.1\r\n')
        self.assertRaises(
            http.client.BadStatusLine,
            self.loop.run_until_complete,
            self.stream.read_request_line())

    def test_request_line_bad_version(self):
        self.stream.feed_data(b'GET //get HT/11\r\n')
        self.assertRaises(
            http.client.BadStatusLine,
            self.loop.run_until_complete,
            self.stream.read_request_line())

    def test_response_status_bad_status_line(self):
        self.stream.feed_data(b'\r\n')
        self.assertRaises(
            http.client.BadStatusLine,
            self.loop.run_until_complete,
            self.stream.read_response_status())

    def test_response_status_bad_status_line_eof(self):
        self.stream.feed_eof()
        self.assertRaises(
            http.client.BadStatusLine,
            self.loop.run_until_complete,
            self.stream.read_response_status())

    def test_response_status_bad_status_non_ascii(self):
        self.stream.feed_data(b'HTTP/1.1 200 \xd0\xb0\r\n')
        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.loop.run_until_complete(self.stream.read_response_status())

        self.assertEqual(b'HTTP/1.1 200 \xd0\xb0\r\n', cm.exception.args[0])

    def test_response_status_bad_version(self):
        self.stream.feed_data(b'HT/11 200 Ok\r\n')
        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.loop.run_until_complete(self.stream.read_response_status())

        self.assertEqual('HT/11 200 Ok', cm.exception.args[0])

    def test_response_status_no_reason(self):
        self.stream.feed_data(b'HTTP/1.1 200\r\n')

        v, s, r = self.loop.run_until_complete(
            self.stream.read_response_status())
        self.assertEqual(v, (1, 1))
        self.assertEqual(s, 200)
        self.assertEqual(r, '')

    def test_response_status_bad(self):
        self.stream.feed_data(b'HTT/1\r\n')
        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.loop.run_until_complete(
                self.stream.read_response_status())

        self.assertIn('HTT/1', str(cm.exception))

    def test_response_status_bad_code_under_100(self):
        self.stream.feed_data(b'HTTP/1.1 99 test\r\n')
        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.loop.run_until_complete(
                self.stream.read_response_status())

        self.assertIn('HTTP/1.1 99 test', str(cm.exception))

    def test_response_status_bad_code_above_999(self):
        self.stream.feed_data(b'HTTP/1.1 9999 test\r\n')
        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.loop.run_until_complete(
                self.stream.read_response_status())

        self.assertIn('HTTP/1.1 9999 test', str(cm.exception))

    def test_response_status_bad_code_not_int(self):
        self.stream.feed_data(b'HTTP/1.1 ttt test\r\n')
        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.loop.run_until_complete(
                self.stream.read_response_status())

        self.assertIn('HTTP/1.1 ttt test', str(cm.exception))

    def test_read_headers(self):
        self.stream.feed_data(b'test: line\r\n'
                              b' continue\r\n'
                              b'test2: data\r\n'
                              b'\r\n')

        headers = self.loop.run_until_complete(self.stream.read_headers())
        self.assertEqual(headers,
                         [('TEST', 'line\r\n continue'), ('TEST2', 'data')])

    def test_read_headers_size(self):
        self.stream.feed_data(b'test: line\r\n')
        self.stream.feed_data(b' continue\r\n')
        self.stream.feed_data(b'test2: data\r\n')
        self.stream.feed_data(b'\r\n')

        self.stream.MAX_HEADERS = 5
        self.assertRaises(
            http.client.LineTooLong,
            self.loop.run_until_complete,
            self.stream.read_headers())

    def test_read_headers_invalid_header(self):
        self.stream.feed_data(b'test line\r\n')

        with self.assertRaises(ValueError) as cm:
            self.loop.run_until_complete(self.stream.read_headers())

        self.assertIn("Invalid header b'test line'", str(cm.exception))

    def test_read_headers_invalid_name(self):
        self.stream.feed_data(b'test[]: line\r\n')

        with self.assertRaises(ValueError) as cm:
            self.loop.run_until_complete(self.stream.read_headers())

        self.assertIn("Invalid header name b'TEST[]'", str(cm.exception))

    def test_read_headers_headers_size(self):
        self.stream.MAX_HEADERFIELD_SIZE = 5
        self.stream.feed_data(b'test: line data data\r\ndata\r\n')

        with self.assertRaises(http.client.LineTooLong) as cm:
            self.loop.run_until_complete(self.stream.read_headers())

        self.assertIn("limit request headers fields size", str(cm.exception))

    def test_read_headers_continuation_headers_size(self):
        self.stream.MAX_HEADERFIELD_SIZE = 5
        self.stream.feed_data(b'test: line\r\n test\r\n')

        with self.assertRaises(http.client.LineTooLong) as cm:
            self.loop.run_until_complete(self.stream.read_headers())

        self.assertIn("limit request headers fields size", str(cm.exception))

    def test_read_message_should_close(self):
        self.stream.feed_data(
            b'Host: example.com\r\nConnection: close\r\n\r\n')

        msg = self.loop.run_until_complete(self.stream.read_message())
        self.assertTrue(msg.should_close)

    def test_read_message_should_close_http11(self):
        self.stream.feed_data(
            b'Host: example.com\r\n\r\n')

        msg = self.loop.run_until_complete(
            self.stream.read_message(version=(1, 1)))
        self.assertFalse(msg.should_close)

    def test_read_message_should_close_http10(self):
        self.stream.feed_data(
            b'Host: example.com\r\n\r\n')

        msg = self.loop.run_until_complete(
            self.stream.read_message(version=(1, 0)))
        self.assertTrue(msg.should_close)

    def test_read_message_should_close_keep_alive(self):
        self.stream.feed_data(
            b'Host: example.com\r\nConnection: keep-alive\r\n\r\n')

        msg = self.loop.run_until_complete(self.stream.read_message())
        self.assertFalse(msg.should_close)

    def test_read_message_content_length_broken(self):
        self.stream.feed_data(
            b'Host: example.com\r\nContent-Length: qwe\r\n\r\n')

        self.assertRaises(
            http.client.HTTPException,
            self.loop.run_until_complete,
            self.stream.read_message())

    def test_read_message_content_length_wrong(self):
        self.stream.feed_data(
            b'Host: example.com\r\nContent-Length: -1\r\n\r\n')

        self.assertRaises(
            http.client.HTTPException,
            self.loop.run_until_complete,
            self.stream.read_message())

    def test_read_message_content_length(self):
        self.stream.feed_data(
            b'Host: example.com\r\nContent-Length: 2\r\n\r\n12')

        msg = self.loop.run_until_complete(self.stream.read_message())

        payload = self.loop.run_until_complete(msg.payload.read())
        self.assertEqual(b'12', payload)

    def test_read_message_content_length_no_val(self):
        self.stream.feed_data(b'Host: example.com\r\n\r\n12')

        msg = self.loop.run_until_complete(
            self.stream.read_message(readall=False))

        payload = self.loop.run_until_complete(msg.payload.read())
        self.assertEqual(b'', payload)

    _comp = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    _COMPRESSED = b''.join([_comp.compress(b'data'), _comp.flush()])

    def test_read_message_deflate(self):
        self.stream.feed_data(
            ('Host: example.com\r\nContent-Length: {}\r\n'
             'Content-Encoding: deflate\r\n\r\n'.format(
             len(self._COMPRESSED)).encode()))
        self.stream.feed_data(self._COMPRESSED)

        msg = self.loop.run_until_complete(self.stream.read_message())
        payload = self.loop.run_until_complete(msg.payload.read())
        self.assertEqual(b'data', payload)

    def test_read_message_deflate_disabled(self):
        self.stream.feed_data(
            ('Host: example.com\r\nContent-Encoding: deflate\r\n'
             'Content-Length: {}\r\n\r\n'.format(
             len(self._COMPRESSED)).encode()))
        self.stream.feed_data(self._COMPRESSED)

        msg = self.loop.run_until_complete(
            self.stream.read_message(compression=False))
        payload = self.loop.run_until_complete(msg.payload.read())
        self.assertEqual(self._COMPRESSED, payload)

    def test_read_message_deflate_unknown(self):
        self.stream.feed_data(
            ('Host: example.com\r\nContent-Encoding: compress\r\n'
             'Content-Length: {}\r\n\r\n'.format(
             len(self._COMPRESSED)).encode()))
        self.stream.feed_data(self._COMPRESSED)

        msg = self.loop.run_until_complete(
            self.stream.read_message(compression=False))
        payload = self.loop.run_until_complete(msg.payload.read())
        self.assertEqual(self._COMPRESSED, payload)

    def test_read_message_websocket(self):
        self.stream.feed_data(
            b'Host: example.com\r\nSec-Websocket-Key1: 13\r\n\r\n1234567890')

        msg = self.loop.run_until_complete(self.stream.read_message())

        payload = self.loop.run_until_complete(msg.payload.read())
        self.assertEqual(b'12345678', payload)

    def test_read_message_chunked(self):
        self.stream.feed_data(
            b'Host: example.com\r\nTransfer-Encoding: chunked\r\n\r\n')
        self.stream.feed_data(
            b'4;test\r\ndata\r\n4\r\nline\r\n0\r\ntest\r\n\r\n')

        msg = self.loop.run_until_complete(self.stream.read_message())

        payload = self.loop.run_until_complete(msg.payload.read())
        self.assertEqual(b'dataline', payload)

    def test_read_message_readall_eof(self):
        self.stream.feed_data(
            b'Host: example.com\r\n\r\n')
        self.stream.feed_data(b'data')
        self.stream.feed_data(b'line')
        self.stream.feed_eof()

        msg = self.loop.run_until_complete(
            self.stream.read_message(readall=True))

        payload = self.loop.run_until_complete(msg.payload.read())
        self.assertEqual(b'dataline', payload)

    def test_read_message_payload(self):
        self.stream.feed_data(
            b'Host: example.com\r\n'
            b'Content-Length: 8\r\n\r\n')
        self.stream.feed_data(b'data')
        self.stream.feed_data(b'data')

        msg = self.loop.run_until_complete(
            self.stream.read_message(readall=True))

        data = self.loop.run_until_complete(msg.payload.read())
        self.assertEqual(b'datadata', data)

    def test_read_message_payload_eof(self):
        self.stream.feed_data(
            b'Host: example.com\r\n'
            b'Content-Length: 4\r\n\r\n')
        self.stream.feed_data(b'da')
        self.stream.feed_eof()

        msg = self.loop.run_until_complete(
            self.stream.read_message(readall=True))

        self.assertRaises(
            http.client.IncompleteRead,
            self.loop.run_until_complete, msg.payload.read())

    def test_read_message_length_payload_zero(self):
        self.stream.feed_data(
            b'Host: example.com\r\n'
            b'Content-Length: 0\r\n\r\n')
        self.stream.feed_data(b'data')

        msg = self.loop.run_until_complete(self.stream.read_message())
        data = self.loop.run_until_complete(msg.payload.read())
        self.assertEqual(b'', data)

    def test_read_message_length_payload_incomplete(self):
        self.stream.feed_data(
            b'Host: example.com\r\n'
            b'Content-Length: 8\r\n\r\n')

        msg = self.loop.run_until_complete(self.stream.read_message())

        @tulip.coroutine
        def coro():
            self.stream.feed_data(b'data')
            self.stream.feed_eof()
            return (yield from msg.payload.read())

        self.assertRaises(
            http.client.IncompleteRead,
            self.loop.run_until_complete, coro())

    def test_read_message_eof_payload(self):
        self.stream.feed_data(b'Host: example.com\r\n\r\n')

        msg = self.loop.run_until_complete(
            self.stream.read_message(readall=True))

        @tulip.coroutine
        def coro():
            self.stream.feed_data(b'data')
            self.stream.feed_eof()
            return (yield from msg.payload.read())

        data = self.loop.run_until_complete(coro())
        self.assertEqual(b'data', data)

    def test_read_message_length_payload(self):
        self.stream.feed_data(
            b'Host: example.com\r\n'
            b'Content-Length: 4\r\n\r\n')
        self.stream.feed_data(b'da')
        self.stream.feed_data(b't')
        self.stream.feed_data(b'ali')
        self.stream.feed_data(b'ne')

        msg = self.loop.run_until_complete(
            self.stream.read_message(readall=True))

        self.assertIsInstance(msg.payload, tulip.StreamReader)

        data = self.loop.run_until_complete(msg.payload.read())
        self.assertEqual(b'data', data)
        self.assertEqual(b'line', b''.join(self.stream.buffer))

    def test_read_message_length_payload_extra(self):
        self.stream.feed_data(
            b'Host: example.com\r\n'
            b'Content-Length: 4\r\n\r\n')

        msg = self.loop.run_until_complete(self.stream.read_message())

        @tulip.coroutine
        def coro():
            self.stream.feed_data(b'da')
            self.stream.feed_data(b't')
            self.stream.feed_data(b'ali')
            self.stream.feed_data(b'ne')
            return (yield from msg.payload.read())

        data = self.loop.run_until_complete(coro())
        self.assertEqual(b'data', data)
        self.assertEqual(b'line', b''.join(self.stream.buffer))

    def test_parse_length_payload_eof_exc(self):
        parser = self.stream._parse_length_payload(4)
        next(parser)

        stream = tulip.StreamReader()
        parser.send(stream)
        self.stream._parser = parser
        self.stream.feed_data(b'da')

        @tulip.coroutine
        def eof():
            self.stream.feed_eof()

        t1 = tulip.Task(stream.read())
        t2 = tulip.Task(eof())

        self.loop.run_until_complete(tulip.wait([t1, t2]))
        self.assertRaises(http.client.IncompleteRead, t1.result)
        self.assertIsNone(self.stream._parser)

    def test_read_message_deflate_payload(self):
        comp = zlib.compressobj(wbits=-zlib.MAX_WBITS)

        data = b''.join([comp.compress(b'data'), comp.flush()])

        self.stream.feed_data(
            b'Host: example.com\r\n'
            b'Content-Encoding: deflate\r\n' +
            ('Content-Length: {}\r\n\r\n'.format(len(data)).encode()))

        msg = self.loop.run_until_complete(
            self.stream.read_message(readall=True))

        @tulip.coroutine
        def coro():
            self.stream.feed_data(data)
            return (yield from msg.payload.read())

        data = self.loop.run_until_complete(coro())
        self.assertEqual(b'data', data)

    def test_read_message_chunked_payload(self):
        self.stream.feed_data(
            b'Host: example.com\r\n'
            b'Transfer-Encoding: chunked\r\n\r\n')

        msg = self.loop.run_until_complete(self.stream.read_message())

        @tulip.coroutine
        def coro():
            self.stream.feed_data(
                b'4\r\ndata\r\n4\r\nline\r\n0\r\ntest\r\n\r\n')
            return (yield from msg.payload.read())

        data = self.loop.run_until_complete(coro())
        self.assertEqual(b'dataline', data)

    def test_read_message_chunked_payload_chunks(self):
        self.stream.feed_data(
            b'Host: example.com\r\n'
            b'Transfer-Encoding: chunked\r\n\r\n')

        msg = self.loop.run_until_complete(self.stream.read_message())

        @tulip.coroutine
        def coro():
            self.stream.feed_data(b'4\r\ndata\r')
            self.stream.feed_data(b'\n4')
            self.stream.feed_data(b'\r')
            self.stream.feed_data(b'\n')
            self.stream.feed_data(b'line\r\n0\r\n')
            self.stream.feed_data(b'test\r\n\r\n')
            return (yield from msg.payload.read())

        data = self.loop.run_until_complete(coro())
        self.assertEqual(b'dataline', data)

    def test_read_message_chunked_payload_incomplete(self):
        self.stream.feed_data(
            b'Host: example.com\r\n'
            b'Transfer-Encoding: chunked\r\n\r\n')

        msg = self.loop.run_until_complete(self.stream.read_message())

        @tulip.coroutine
        def coro():
            self.stream.feed_data(b'4\r\ndata\r\n')
            self.stream.feed_eof()
            return (yield from msg.payload.read())

        self.assertRaises(
            http.client.IncompleteRead,
            self.loop.run_until_complete, coro())

    def test_read_message_chunked_payload_extension(self):
        self.stream.feed_data(
            b'Host: example.com\r\n'
            b'Transfer-Encoding: chunked\r\n\r\n')

        msg = self.loop.run_until_complete(self.stream.read_message())

        @tulip.coroutine
        def coro():
            self.stream.feed_data(
                b'4;test\r\ndata\r\n4\r\nline\r\n0\r\ntest\r\n\r\n')
            return (yield from msg.payload.read())

        data = self.loop.run_until_complete(coro())
        self.assertEqual(b'dataline', data)

    def test_read_message_chunked_payload_size_error(self):
        self.stream.feed_data(
            b'Host: example.com\r\n'
            b'Transfer-Encoding: chunked\r\n\r\n')

        msg = self.loop.run_until_complete(self.stream.read_message())

        @tulip.coroutine
        def coro():
            self.stream.feed_data(b'blah\r\n')
            return (yield from msg.payload.read())

        self.assertRaises(
            http.client.IncompleteRead,
            self.loop.run_until_complete, coro())

    def test_deflate_stream_set_exception(self):
        stream = tulip.StreamReader()
        dstream = protocol.DeflateStream(stream, 'deflate')

        exc = ValueError()
        dstream.set_exception(exc)
        self.assertIs(exc, stream.exception())

    def test_deflate_stream_feed_data(self):
        stream = tulip.StreamReader()
        dstream = protocol.DeflateStream(stream, 'deflate')

        dstream.zlib = unittest.mock.Mock()
        dstream.zlib.decompress.return_value = b'line'

        dstream.feed_data(b'data')
        self.assertEqual([b'line'], list(stream.buffer))

    def test_deflate_stream_feed_data_err(self):
        stream = tulip.StreamReader()
        dstream = protocol.DeflateStream(stream, 'deflate')

        exc = ValueError()
        dstream.zlib = unittest.mock.Mock()
        dstream.zlib.decompress.side_effect = exc

        dstream.feed_data(b'data')
        self.assertIsInstance(stream.exception(), http.client.IncompleteRead)

    def test_deflate_stream_feed_eof(self):
        stream = tulip.StreamReader()
        dstream = protocol.DeflateStream(stream, 'deflate')

        dstream.zlib = unittest.mock.Mock()
        dstream.zlib.flush.return_value = b'line'

        dstream.feed_eof()
        self.assertEqual([b'line'], list(stream.buffer))
        self.assertTrue(stream.eof)

    def test_deflate_stream_feed_eof_err(self):
        stream = tulip.StreamReader()
        dstream = protocol.DeflateStream(stream, 'deflate')

        dstream.zlib = unittest.mock.Mock()
        dstream.zlib.flush.return_value = b'line'
        dstream.zlib.eof = False

        dstream.feed_eof()
        self.assertIsInstance(stream.exception(), http.client.IncompleteRead)


class HttpMessageTests(unittest.TestCase):

    def setUp(self):
        self.transport = unittest.mock.Mock()

    def test_start_request(self):
        msg = protocol.Request(
            self.transport, 'GET', '/index.html', close=True)

        self.assertIs(msg.transport, self.transport)
        self.assertIsNone(msg.status)
        self.assertTrue(msg.closing)
        self.assertEqual(msg.status_line, 'GET /index.html HTTP/1.1\r\n')

    def test_start_response(self):
        msg = protocol.Response(self.transport, 200, close=True)

        self.assertIs(msg.transport, self.transport)
        self.assertEqual(msg.status, 200)
        self.assertTrue(msg.closing)
        self.assertEqual(msg.status_line, 'HTTP/1.1 200 OK\r\n')

    def test_force_close(self):
        msg = protocol.Response(self.transport, 200)
        self.assertFalse(msg.closing)
        msg.force_close()
        self.assertTrue(msg.closing)

    def test_force_chunked(self):
        msg = protocol.Response(self.transport, 200)
        self.assertFalse(msg.chunked)
        msg.force_chunked()
        self.assertTrue(msg.chunked)

    def test_keep_alive(self):
        msg = protocol.Response(self.transport, 200)
        self.assertFalse(msg.keep_alive())
        msg.keepalive = True
        self.assertTrue(msg.keep_alive())

        msg.force_close()
        self.assertFalse(msg.keep_alive())

    def test_add_header(self):
        msg = protocol.Response(self.transport, 200)
        self.assertEqual([], msg.headers)

        msg.add_header('content-type', 'plain/html')
        self.assertEqual([('CONTENT-TYPE', 'plain/html')], msg.headers)

    def test_add_headers(self):
        msg = protocol.Response(self.transport, 200)
        self.assertEqual([], msg.headers)

        msg.add_headers(('content-type', 'plain/html'))
        self.assertEqual([('CONTENT-TYPE', 'plain/html')], msg.headers)

    def test_add_headers_length(self):
        msg = protocol.Response(self.transport, 200)
        self.assertIsNone(msg.length)

        msg.add_headers(('content-length', '200'))
        self.assertEqual(200, msg.length)

    def test_add_headers_upgrade(self):
        msg = protocol.Response(self.transport, 200)
        self.assertFalse(msg.upgrade)

        msg.add_headers(('connection', 'upgrade'))
        self.assertTrue(msg.upgrade)

    def test_add_headers_upgrade_websocket(self):
        msg = protocol.Response(self.transport, 200)

        msg.add_headers(('upgrade', 'test'))
        self.assertEqual([], msg.headers)

        msg.add_headers(('upgrade', 'websocket'))
        self.assertEqual([('UPGRADE', 'websocket')], msg.headers)

    def test_add_headers_connection_keepalive(self):
        msg = protocol.Response(self.transport, 200)

        msg.add_headers(('connection', 'keep-alive'))
        self.assertEqual([], msg.headers)
        self.assertTrue(msg.keepalive)

        msg.add_headers(('connection', 'close'))
        self.assertFalse(msg.keepalive)

    def test_add_headers_hop_headers(self):
        msg = protocol.Response(self.transport, 200)

        msg.add_headers(('connection', 'test'), ('transfer-encoding', 't'))
        self.assertEqual([], msg.headers)

    def test_default_headers(self):
        msg = protocol.Response(self.transport, 200)

        headers = [r for r, _ in msg._default_headers()]
        self.assertIn('DATE', headers)
        self.assertIn('CONNECTION', headers)

    def test_default_headers_server(self):
        msg = protocol.Response(self.transport, 200)

        headers = [r for r, _ in msg._default_headers()]
        self.assertIn('SERVER', headers)

    def test_default_headers_useragent(self):
        msg = protocol.Request(self.transport, 'GET', '/')

        headers = [r for r, _ in msg._default_headers()]
        self.assertNotIn('SERVER', headers)
        self.assertIn('USER-AGENT', headers)

    def test_default_headers_chunked(self):
        msg = protocol.Response(self.transport, 200)

        headers = [r for r, _ in msg._default_headers()]
        self.assertNotIn('TRANSFER-ENCODING', headers)

        msg.force_chunked()

        headers = [r for r, _ in msg._default_headers()]
        self.assertIn('TRANSFER-ENCODING', headers)

    def test_default_headers_connection_upgrade(self):
        msg = protocol.Response(self.transport, 200)
        msg.upgrade = True

        headers = [r for r in msg._default_headers() if r[0] == 'CONNECTION']
        self.assertEqual([('CONNECTION', 'upgrade')], headers)

    def test_default_headers_connection_close(self):
        msg = protocol.Response(self.transport, 200)
        msg.force_close()

        headers = [r for r in msg._default_headers() if r[0] == 'CONNECTION']
        self.assertEqual([('CONNECTION', 'close')], headers)

    def test_default_headers_connection_keep_alive(self):
        msg = protocol.Response(self.transport, 200)
        msg.keepalive = True

        headers = [r for r in msg._default_headers() if r[0] == 'CONNECTION']
        self.assertEqual([('CONNECTION', 'keep-alive')], headers)

    def test_send_headers(self):
        write = self.transport.write = unittest.mock.Mock()

        msg = protocol.Response(self.transport, 200)
        msg.add_headers(('content-type', 'plain/html'))
        self.assertFalse(msg.is_headers_sent())

        msg.send_headers()

        content = b''.join([arg[1][0] for arg in list(write.mock_calls)])

        self.assertTrue(content.startswith(b'HTTP/1.1 200 OK\r\n'))
        self.assertIn(b'CONTENT-TYPE: plain/html', content)
        self.assertTrue(msg.headers_sent)
        self.assertTrue(msg.is_headers_sent())

    def test_send_headers_nomore_add(self):
        msg = protocol.Response(self.transport, 200)
        msg.add_headers(('content-type', 'plain/html'))
        msg.send_headers()

        self.assertRaises(AssertionError,
                          msg.add_header, 'content-type', 'plain/html')

    def test_prepare_length(self):
        msg = protocol.Response(self.transport, 200)
        length = msg._write_length_payload = unittest.mock.Mock()
        length.return_value = iter([1, 2, 3])

        msg.add_headers(('content-length', '200'))
        msg.send_headers()

        self.assertTrue(length.called)
        self.assertTrue((200,), length.call_args[0])

    def test_prepare_chunked_force(self):
        msg = protocol.Response(self.transport, 200)
        msg.force_chunked()

        chunked = msg._write_chunked_payload = unittest.mock.Mock()
        chunked.return_value = iter([1, 2, 3])

        msg.add_headers(('content-length', '200'))
        msg.send_headers()
        self.assertTrue(chunked.called)

    def test_prepare_chunked_no_length(self):
        msg = protocol.Response(self.transport, 200)

        chunked = msg._write_chunked_payload = unittest.mock.Mock()
        chunked.return_value = iter([1, 2, 3])

        msg.send_headers()
        self.assertTrue(chunked.called)

    def test_prepare_eof(self):
        msg = protocol.Response(self.transport, 200, http_version=(1, 0))

        eof = msg._write_eof_payload = unittest.mock.Mock()
        eof.return_value = iter([1, 2, 3])

        msg.send_headers()
        self.assertTrue(eof.called)

    def test_write_auto_send_headers(self):
        msg = protocol.Response(self.transport, 200, http_version=(1, 0))
        msg._send_headers = True

        msg.write(b'data1')
        self.assertTrue(msg.headers_sent)

    def test_write_payload_eof(self):
        write = self.transport.write = unittest.mock.Mock()
        msg = protocol.Response(self.transport, 200, http_version=(1, 0))
        msg.send_headers()

        msg.write(b'data1')
        self.assertTrue(msg.headers_sent)

        msg.write(b'data2')
        msg.write_eof()

        content = b''.join([c[1][0] for c in list(write.mock_calls)])
        self.assertEqual(
            b'data1data2', content.split(b'\r\n\r\n', 1)[-1])

    def test_write_payload_chunked(self):
        write = self.transport.write = unittest.mock.Mock()

        msg = protocol.Response(self.transport, 200)
        msg.force_chunked()
        msg.send_headers()

        msg.write(b'data')
        msg.write_eof()

        content = b''.join([c[1][0] for c in list(write.mock_calls)])
        self.assertEqual(
            b'4\r\ndata\r\n0\r\n\r\n',
            content.split(b'\r\n\r\n', 1)[-1])

    def test_write_payload_chunked_multiple(self):
        write = self.transport.write = unittest.mock.Mock()

        msg = protocol.Response(self.transport, 200)
        msg.force_chunked()
        msg.send_headers()

        msg.write(b'data1')
        msg.write(b'data2')
        msg.write_eof()

        content = b''.join([c[1][0] for c in list(write.mock_calls)])
        self.assertEqual(
            b'5\r\ndata1\r\n5\r\ndata2\r\n0\r\n\r\n',
            content.split(b'\r\n\r\n', 1)[-1])

    def test_write_payload_length(self):
        write = self.transport.write = unittest.mock.Mock()

        msg = protocol.Response(self.transport, 200)
        msg.add_headers(('content-length', '2'))
        msg.send_headers()

        msg.write(b'd')
        msg.write(b'ata')
        msg.write_eof()

        content = b''.join([c[1][0] for c in list(write.mock_calls)])
        self.assertEqual(
            b'da', content.split(b'\r\n\r\n', 1)[-1])

    def test_write_payload_chunked_filter(self):
        write = self.transport.write = unittest.mock.Mock()

        msg = protocol.Response(self.transport, 200)
        msg.send_headers()

        msg.add_chunking_filter(2)
        msg.write(b'data')
        msg.write_eof()

        content = b''.join([c[1][0] for c in list(write.mock_calls)])
        self.assertTrue(content.endswith(b'2\r\nda\r\n2\r\nta\r\n0\r\n\r\n'))

    def test_write_payload_chunked_filter_mutiple_chunks(self):
        write = self.transport.write = unittest.mock.Mock()
        msg = protocol.Response(self.transport, 200)
        msg.send_headers()

        msg.add_chunking_filter(2)
        msg.write(b'data1')
        msg.write(b'data2')
        msg.write_eof()
        content = b''.join([c[1][0] for c in list(write.mock_calls)])
        self.assertTrue(content.endswith(
            b'2\r\nda\r\n2\r\nta\r\n2\r\n1d\r\n2\r\nat\r\n'
            b'2\r\na2\r\n0\r\n\r\n'))

    def test_write_payload_chunked_large_chunk(self):
        write = self.transport.write = unittest.mock.Mock()
        msg = protocol.Response(self.transport, 200)
        msg.send_headers()

        msg.add_chunking_filter(1024)
        msg.write(b'data')
        msg.write_eof()
        content = b''.join([c[1][0] for c in list(write.mock_calls)])
        self.assertTrue(content.endswith(b'4\r\ndata\r\n0\r\n\r\n'))

    _comp = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    _COMPRESSED = b''.join([_comp.compress(b'data'), _comp.flush()])

    def test_write_payload_deflate_filter(self):
        write = self.transport.write = unittest.mock.Mock()
        msg = protocol.Response(self.transport, 200)
        msg.add_headers(('content-length', '{}'.format(len(self._COMPRESSED))))
        msg.send_headers()

        msg.add_compression_filter('deflate')
        msg.write(b'data')
        msg.write_eof()

        content = b''.join([c[1][0] for c in list(write.mock_calls)])
        self.assertEqual(
            self._COMPRESSED, content.split(b'\r\n\r\n', 1)[-1])

    def test_write_payload_deflate_and_chunked(self):
        write = self.transport.write = unittest.mock.Mock()
        msg = protocol.Response(self.transport, 200)
        msg.send_headers()

        msg.add_compression_filter('deflate')
        msg.add_chunking_filter(2)

        msg.write(b'data')
        msg.write_eof()

        content = b''.join([c[1][0] for c in list(write.mock_calls)])
        self.assertEqual(
            b'2\r\nKI\r\n2\r\n,I\r\n2\r\n\x04\x00\r\n0\r\n\r\n',
            content.split(b'\r\n\r\n', 1)[-1])

    def test_write_payload_chunked_and_deflate(self):
        write = self.transport.write = unittest.mock.Mock()
        msg = protocol.Response(self.transport, 200)
        msg.add_headers(('content-length', '{}'.format(len(self._COMPRESSED))))

        msg.add_chunking_filter(2)
        msg.add_compression_filter('deflate')
        msg.send_headers()

        msg.write(b'data')
        msg.write_eof()

        content = b''.join([c[1][0] for c in list(write.mock_calls)])
        self.assertEqual(
            self._COMPRESSED, content.split(b'\r\n\r\n', 1)[-1])
