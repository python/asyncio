"""Tests for http/protocol.py"""

import http.client
import unittest
import unittest.mock
import zlib

import tulip
from tulip.http import protocol
from tulip.test_utils import LogTrackingTestCase


class HttpStreamReaderTests(LogTrackingTestCase):

    def setUp(self):
        super().setUp()
        self.suppress_log_errors()

        self.loop = tulip.new_event_loop()
        tulip.set_event_loop(self.loop)

        self.transport = unittest.mock.Mock()
        self.stream = protocol.HttpStreamReader()

    def tearDown(self):
        self.loop.close()
        super().tearDown()

    def test_request_line(self):
        self.stream.feed_data(b'get /path HTTP/1.1\r\n')
        self.assertEqual(
            ('GET', '/path', (1, 1)),
            self.loop.run_until_complete(
                tulip.Task(self.stream.read_request_line())))

    def test_request_line_two_slashes(self):
        self.stream.feed_data(b'get //path HTTP/1.1\r\n')
        self.assertEqual(
            ('GET', '//path', (1, 1)),
            self.loop.run_until_complete(
                tulip.Task(self.stream.read_request_line())))

    def test_request_line_non_ascii(self):
        self.stream.feed_data(b'get /path\xd0\xb0 HTTP/1.1\r\n')

        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.loop.run_until_complete(
                tulip.Task(self.stream.read_request_line()))

        self.assertEqual(
            b'get /path\xd0\xb0 HTTP/1.1\r\n', cm.exception.args[0])

    def test_request_line_bad_status_line(self):
        self.stream.feed_data(b'\r\n')
        self.assertRaises(
            http.client.BadStatusLine,
            self.loop.run_until_complete,
            tulip.Task(self.stream.read_request_line()))

    def test_request_line_bad_method(self):
        self.stream.feed_data(b'!12%()+=~$ /get HTTP/1.1\r\n')
        self.assertRaises(
            http.client.BadStatusLine,
            self.loop.run_until_complete,
            tulip.Task(self.stream.read_request_line()))

    def test_request_line_bad_version(self):
        self.stream.feed_data(b'GET //get HT/11\r\n')
        self.assertRaises(
            http.client.BadStatusLine,
            self.loop.run_until_complete,
            tulip.Task(self.stream.read_request_line()))

    def test_response_status_bad_status_line(self):
        self.stream.feed_data(b'\r\n')
        self.assertRaises(
            http.client.BadStatusLine,
            self.loop.run_until_complete,
            tulip.Task(self.stream.read_response_status()))

    def test_response_status_bad_status_line_eof(self):
        self.stream.feed_eof()
        self.assertRaises(
            http.client.BadStatusLine,
            self.loop.run_until_complete,
            tulip.Task(self.stream.read_response_status()))

    def test_response_status_bad_status_non_ascii(self):
        self.stream.feed_data(b'HTTP/1.1 200 \xd0\xb0\r\n')
        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.loop.run_until_complete(
                tulip.Task(self.stream.read_response_status()))

        self.assertEqual(b'HTTP/1.1 200 \xd0\xb0\r\n', cm.exception.args[0])

    def test_response_status_bad_version(self):
        self.stream.feed_data(b'HT/11 200 Ok\r\n')
        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.loop.run_until_complete(
                tulip.Task(self.stream.read_response_status()))

        self.assertEqual('HT/11 200 Ok', cm.exception.args[0])

    def test_response_status_no_reason(self):
        self.stream.feed_data(b'HTTP/1.1 200\r\n')

        v, s, r = self.loop.run_until_complete(
            tulip.Task(self.stream.read_response_status()))
        self.assertEqual(v, (1, 1))
        self.assertEqual(s, 200)
        self.assertEqual(r, '')

    def test_response_status_bad(self):
        self.stream.feed_data(b'HTT/1\r\n')
        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.loop.run_until_complete(
                tulip.Task(self.stream.read_response_status()))

        self.assertIn('HTT/1', str(cm.exception))

    def test_response_status_bad_code_under_100(self):
        self.stream.feed_data(b'HTTP/1.1 99 test\r\n')
        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.loop.run_until_complete(
                tulip.Task(self.stream.read_response_status()))

        self.assertIn('HTTP/1.1 99 test', str(cm.exception))

    def test_response_status_bad_code_above_999(self):
        self.stream.feed_data(b'HTTP/1.1 9999 test\r\n')
        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.loop.run_until_complete(
                tulip.Task(self.stream.read_response_status()))

        self.assertIn('HTTP/1.1 9999 test', str(cm.exception))

    def test_response_status_bad_code_not_int(self):
        self.stream.feed_data(b'HTTP/1.1 ttt test\r\n')
        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.loop.run_until_complete(
                tulip.Task(self.stream.read_response_status()))

        self.assertIn('HTTP/1.1 ttt test', str(cm.exception))

    def test_read_headers(self):
        self.stream.feed_data(b'test: line\r\n'
                              b' continue\r\n'
                              b'test2: data\r\n'
                              b'\r\n')

        headers = self.loop.run_until_complete(
            tulip.Task(self.stream.read_headers()))
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
            tulip.Task(self.stream.read_headers()))

    def test_read_headers_invalid_header(self):
        self.stream.feed_data(b'test line\r\n')

        with self.assertRaises(ValueError) as cm:
            self.loop.run_until_complete(
                tulip.Task(self.stream.read_headers()))

        self.assertIn("Invalid header b'test line'", str(cm.exception))

    def test_read_headers_invalid_name(self):
        self.stream.feed_data(b'test[]: line\r\n')

        with self.assertRaises(ValueError) as cm:
            self.loop.run_until_complete(
                tulip.Task(self.stream.read_headers()))

        self.assertIn("Invalid header name b'TEST[]'", str(cm.exception))

    def test_read_headers_headers_size(self):
        self.stream.MAX_HEADERFIELD_SIZE = 5
        self.stream.feed_data(b'test: line data data\r\ndata\r\n')

        with self.assertRaises(http.client.LineTooLong) as cm:
            self.loop.run_until_complete(
                tulip.Task(self.stream.read_headers()))

        self.assertIn("limit request headers fields size", str(cm.exception))

    def test_read_headers_continuation_headers_size(self):
        self.stream.MAX_HEADERFIELD_SIZE = 5
        self.stream.feed_data(b'test: line\r\n test\r\n')

        with self.assertRaises(http.client.LineTooLong) as cm:
            self.loop.run_until_complete(
                tulip.Task(self.stream.read_headers()))

        self.assertIn("limit request headers fields size", str(cm.exception))

    def test_read_payload_unknown_encoding(self):
        self.assertRaises(
            ValueError, self.stream.read_length_payload, encoding='unknown')

    def test_read_payload(self):
        self.stream.feed_data(b'da')
        self.stream.feed_data(b't')
        self.stream.feed_data(b'ali')
        self.stream.feed_data(b'ne')

        stream = self.stream.read_length_payload(4)
        self.assertIsInstance(stream, tulip.StreamReader)

        data = self.loop.run_until_complete(tulip.Task(stream.read()))
        self.assertEqual(b'data', data)
        self.assertEqual(b'line', b''.join(self.stream.buffer))

    def test_read_payload_eof(self):
        self.stream.feed_data(b'da')
        self.stream.feed_eof()
        stream = self.stream.read_length_payload(4)

        self.assertRaises(
            http.client.IncompleteRead,
            self.loop.run_until_complete, tulip.Task(stream.read()))

    def test_read_payload_eof_exc(self):
        self.stream.feed_data(b'da')
        stream = self.stream.read_length_payload(4)

        def eof():
            yield from []
            self.stream.feed_eof()

        t1 = tulip.Task(stream.read())
        t2 = tulip.Task(eof())

        self.loop.run_until_complete(tulip.Task(tulip.wait([t1, t2])))
        self.assertRaises(http.client.IncompleteRead, t1.result)
        self.assertIsNone(self.stream._reader)

    def test_read_payload_deflate(self):
        comp = zlib.compressobj(wbits=-zlib.MAX_WBITS)

        data = b''.join([comp.compress(b'data'), comp.flush()])
        stream = self.stream.read_length_payload(len(data), encoding='deflate')

        self.stream.feed_data(data)

        data = self.loop.run_until_complete(tulip.Task(stream.read()))
        self.assertEqual(b'data', data)

    def _test_read_payload_compress_error(self):
        data = b'123123123datadatadata'
        reader = protocol.length_reader(4)
        self.stream.feed_data(data)
        stream = self.stream.read_payload(reader, 'deflate')

        self.assertRaises(
            http.client.IncompleteRead,
            self.loop.run_until_complete, tulip.Task(stream.read()))

    def test_read_chunked_payload(self):
        stream = self.stream.read_chunked_payload()
        self.stream.feed_data(b'4\r\ndata\r\n4\r\nline\r\n0\r\ntest\r\n\r\n')

        data = self.loop.run_until_complete(tulip.Task(stream.read()))
        self.assertEqual(b'dataline', data)

    def test_read_chunked_payload_chunks(self):
        stream = self.stream.read_chunked_payload()

        self.stream.feed_data(b'4\r\ndata\r')
        self.stream.feed_data(b'\n4')
        self.stream.feed_data(b'\r')
        self.stream.feed_data(b'\n')
        self.stream.feed_data(b'line\r\n0\r\n')
        self.stream.feed_data(b'test\r\n\r\n')

        data = self.loop.run_until_complete(tulip.Task(stream.read()))
        self.assertEqual(b'dataline', data)

    def test_read_chunked_payload_incomplete(self):
        stream = self.stream.read_chunked_payload()

        self.stream.feed_data(b'4\r\ndata\r\n')
        self.stream.feed_eof()

        self.assertRaises(
            http.client.IncompleteRead,
            self.loop.run_until_complete, tulip.Task(stream.read()))

    def test_read_chunked_payload_extension(self):
        stream = self.stream.read_chunked_payload()

        self.stream.feed_data(
            b'4;test\r\ndata\r\n4\r\nline\r\n0\r\ntest\r\n\r\n')

        data = self.loop.run_until_complete(tulip.Task(stream.read()))
        self.assertEqual(b'dataline', data)

    def test_read_chunked_payload_size_error(self):
        stream = self.stream.read_chunked_payload()

        self.stream.feed_data(b'blah\r\n')
        self.assertRaises(
            http.client.IncompleteRead,
            self.loop.run_until_complete, tulip.Task(stream.read()))

    def test_read_length_payload(self):
        stream = self.stream.read_length_payload(8)

        self.stream.feed_data(b'data')
        self.stream.feed_data(b'data')

        data = self.loop.run_until_complete(tulip.Task(stream.read()))
        self.assertEqual(b'datadata', data)

    def test_read_length_payload_zero(self):
        stream = self.stream.read_length_payload(0)

        self.stream.feed_data(b'data')

        data = self.loop.run_until_complete(tulip.Task(stream.read()))
        self.assertEqual(b'', data)

    def test_read_length_payload_incomplete(self):
        stream = self.stream.read_length_payload(8)

        self.stream.feed_data(b'data')
        self.stream.feed_eof()

        self.assertRaises(
            http.client.IncompleteRead,
            self.loop.run_until_complete, tulip.Task(stream.read()))

    def test_read_eof_payload(self):
        stream = self.stream.read_eof_payload()

        self.stream.feed_data(b'data')
        self.stream.feed_eof()

        data = self.loop.run_until_complete(tulip.Task(stream.read()))
        self.assertEqual(b'data', data)

    def test_read_message_should_close(self):
        self.stream.feed_data(
            b'Host: example.com\r\nConnection: close\r\n\r\n')

        msg = self.loop.run_until_complete(
            tulip.Task(self.stream.read_message()))
        self.assertTrue(msg.should_close)

    def test_read_message_should_close_http11(self):
        self.stream.feed_data(
            b'Host: example.com\r\n\r\n')

        msg = self.loop.run_until_complete(
            tulip.Task(self.stream.read_message(version=(1, 1))))
        self.assertFalse(msg.should_close)

    def test_read_message_should_close_http10(self):
        self.stream.feed_data(
            b'Host: example.com\r\n\r\n')

        msg = self.loop.run_until_complete(
            tulip.Task(self.stream.read_message(version=(1, 0))))
        self.assertTrue(msg.should_close)

    def test_read_message_should_close_keep_alive(self):
        self.stream.feed_data(
            b'Host: example.com\r\nConnection: keep-alive\r\n\r\n')

        msg = self.loop.run_until_complete(
            tulip.Task(self.stream.read_message()))
        self.assertFalse(msg.should_close)

    def test_read_message_content_length_broken(self):
        self.stream.feed_data(
            b'Host: example.com\r\nContent-Length: qwe\r\n\r\n')

        self.assertRaises(
            http.client.HTTPException,
            self.loop.run_until_complete,
            tulip.Task(self.stream.read_message()))

    def test_read_message_content_length_wrong(self):
        self.stream.feed_data(
            b'Host: example.com\r\nContent-Length: -1\r\n\r\n')

        self.assertRaises(
            http.client.HTTPException,
            self.loop.run_until_complete,
            tulip.Task(self.stream.read_message()))

    def test_read_message_content_length(self):
        self.stream.feed_data(
            b'Host: example.com\r\nContent-Length: 2\r\n\r\n12')

        msg = self.loop.run_until_complete(
            tulip.Task(self.stream.read_message()))

        payload = self.loop.run_until_complete(tulip.Task(msg.payload.read()))
        self.assertEqual(b'12', payload)

    def test_read_message_content_length_no_val(self):
        self.stream.feed_data(b'Host: example.com\r\n\r\n12')

        msg = self.loop.run_until_complete(
            tulip.Task(self.stream.read_message(readall=False)))

        payload = self.loop.run_until_complete(tulip.Task(msg.payload.read()))
        self.assertEqual(b'', payload)

    _comp = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    _COMPRESSED = b''.join([_comp.compress(b'data'), _comp.flush()])

    def test_read_message_deflate(self):
        self.stream.feed_data(
            ('Host: example.com\r\nContent-Length: %s\r\n'
             'Content-Encoding: deflate\r\n\r\n' %
             len(self._COMPRESSED)).encode())
        self.stream.feed_data(self._COMPRESSED)

        msg = self.loop.run_until_complete(
            tulip.Task(self.stream.read_message()))
        payload = self.loop.run_until_complete(tulip.Task(msg.payload.read()))
        self.assertEqual(b'data', payload)

    def test_read_message_deflate_disabled(self):
        self.stream.feed_data(
            ('Host: example.com\r\nContent-Encoding: deflate\r\n'
             'Content-Length: %s\r\n\r\n' %
             len(self._COMPRESSED)).encode())
        self.stream.feed_data(self._COMPRESSED)

        msg = self.loop.run_until_complete(
            tulip.Task(self.stream.read_message(compression=False)))
        payload = self.loop.run_until_complete(tulip.Task(msg.payload.read()))
        self.assertEqual(self._COMPRESSED, payload)

    def test_read_message_deflate_unknown(self):
        self.stream.feed_data(
            ('Host: example.com\r\nContent-Encoding: compress\r\n'
             'Content-Length: %s\r\n\r\n' % len(self._COMPRESSED)).encode())
        self.stream.feed_data(self._COMPRESSED)

        msg = self.loop.run_until_complete(
            tulip.Task(self.stream.read_message(compression=False)))
        payload = self.loop.run_until_complete(tulip.Task(msg.payload.read()))
        self.assertEqual(self._COMPRESSED, payload)

    def test_read_message_websocket(self):
        self.stream.feed_data(
            b'Host: example.com\r\nSec-Websocket-Key1: 13\r\n\r\n1234567890')

        msg = self.loop.run_until_complete(
            tulip.Task(self.stream.read_message()))

        payload = self.loop.run_until_complete(tulip.Task(msg.payload.read()))
        self.assertEqual(b'12345678', payload)

    def test_read_message_chunked(self):
        self.stream.feed_data(
            b'Host: example.com\r\nTransfer-Encoding: chunked\r\n\r\n')
        self.stream.feed_data(
            b'4;test\r\ndata\r\n4\r\nline\r\n0\r\ntest\r\n\r\n')

        msg = self.loop.run_until_complete(
            tulip.Task(self.stream.read_message()))

        payload = self.loop.run_until_complete(tulip.Task(msg.payload.read()))
        self.assertEqual(b'dataline', payload)

    def test_read_message_readall(self):
        self.stream.feed_data(
            b'Host: example.com\r\n\r\n')
        self.stream.feed_data(b'data')
        self.stream.feed_data(b'line')
        self.stream.feed_eof()

        msg = self.loop.run_until_complete(
            tulip.Task(self.stream.read_message(readall=True)))

        payload = self.loop.run_until_complete(tulip.Task(msg.payload.read()))
        self.assertEqual(b'dataline', payload)
