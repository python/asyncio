"""Tests for http/protocol.py"""

import http.client
import unittest
import unittest.mock

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
