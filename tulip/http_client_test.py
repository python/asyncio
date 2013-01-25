"""Tests for http_client.py."""

import unittest

from . import events
from . import http_client
from . import tasks
from . import test_utils


class StreamReaderTests(unittest.TestCase):

    DATA = b'line1\nline2\nline3\n'

    def setUp(self):
        self.event_loop = events.new_event_loop()
        self.addCleanup(self.event_loop.close)

        events.set_event_loop(self.event_loop)

    def test_feed_empty_data(self):
        stream = http_client.StreamReader()

        stream.feed_data(b'')
        self.assertEqual(0, stream.line_count)
        self.assertEqual(0, stream.byte_count)

    def test_feed_data_line_byte_count(self):
        stream = http_client.StreamReader()

        stream.feed_data(self.DATA)
        self.assertEqual(self.DATA.count(b'\n'), stream.line_count)
        self.assertEqual(len(self.DATA), stream.byte_count)

    @test_utils.sync
    def test_read_zero(self):
        """ Read zero bytes """
        stream = http_client.StreamReader()
        stream.feed_data(self.DATA)

        data = yield from stream.read(0)
        self.assertEqual(b'', data)
        self.assertEqual(len(self.DATA), stream.byte_count)
        self.assertEqual(self.DATA.count(b'\n'), stream.line_count)

    @test_utils.sync
    def test_read(self):
        """ Read bytes """
        stream = http_client.StreamReader()

        res = stream.read(30)

        def cb():
            stream.feed_data(self.DATA)
        self.event_loop.call_soon(cb)

        data = yield from res
        self.assertEqual(self.DATA, data)
        self.assertFalse(stream.byte_count)
        self.assertFalse(stream.line_count)

    @test_utils.sync
    def test_read_eof(self):
        """ Read bytes, stop at eof """
        stream = http_client.StreamReader()

        read = tasks.Task(stream.read(1024))

        def cb():
            stream.feed_eof()
        self.event_loop.call_soon(cb)

        data = yield from read

        self.assertEqual(b'', data)
        self.assertFalse(stream.byte_count)
        self.assertFalse(stream.line_count)

    @test_utils.sync
    def test_read_until_eof(self):
        """ Read all bytes until eof """
        stream = http_client.StreamReader()

        read = tasks.Task(stream.read(-1))

        def cb():
            stream.feed_data(b'chunk1\n')
            stream.feed_data(b'chunk2')
            stream.feed_eof()
        self.event_loop.call_soon(cb)

        data = yield from read

        self.assertEqual(b'chunk1\nchunk2', data)
        self.assertFalse(stream.byte_count)
        self.assertFalse(stream.line_count)

    @test_utils.sync
    def test_readline(self):
        """ Read one line """
        stream = http_client.StreamReader()
        stream.feed_data(b'chunk1 ')

        def cb():
            stream.feed_data(b'chunk2 ')
            stream.feed_data(b'chunk3 ')
            stream.feed_data(b'\n chunk4')
        self.event_loop.call_soon(cb)

        line = yield from stream.readline()

        self.assertEqual(b'chunk1 chunk2 chunk3 \n', line)
        self.assertFalse(stream.line_count)
        self.assertEqual(len(b'\n chunk4')-1, stream.byte_count)

    @test_utils.sync
    def test_readline_line_byte_count(self):
        stream = http_client.StreamReader()
        stream.feed_data(self.DATA)

        line = yield from stream.readline()

        self.assertEqual(b'line1\n', line)
        self.assertEqual(self.DATA.count(b'\n')-1, stream.line_count)
        self.assertEqual(len(self.DATA) - len(b'line1\n'), stream.byte_count)

    @test_utils.sync
    def test_readline_read_byte_count(self):
        stream = http_client.StreamReader()
        stream.feed_data(self.DATA)

        line = yield from stream.readline()
        data = yield from stream.read(7)

        self.assertEqual(b'line2\nl', data)
        self.assertEqual(
            1, stream.line_count)
        self.assertEqual(
            len(self.DATA) - len(b'line1\n') - len(b'line2\nl'),
            stream.byte_count)

    @test_utils.sync
    def test_readexactly_zero_or_less(self):
        """ Read exact number of bytes (zero or less) """
        stream = http_client.StreamReader()
        stream.feed_data(self.DATA)

        data = yield from stream.readexactly(0)
        self.assertEqual(b'', data)
        self.assertEqual(len(self.DATA), stream.byte_count)
        self.assertEqual(self.DATA.count(b'\n'), stream.line_count)

        data = yield from stream.readexactly(-1)
        self.assertEqual(b'', data)
        self.assertEqual(len(self.DATA), stream.byte_count)
        self.assertEqual(self.DATA.count(b'\n'), stream.line_count)

    @test_utils.sync
    def test_readexactly(self):
        """ Read exact number of bytes """
        stream = http_client.StreamReader()

        def cb():
            stream.feed_data(self.DATA)
            stream.feed_data(self.DATA)
            stream.feed_data(self.DATA)
        self.event_loop.call_soon(cb)

        n = 2*len(self.DATA)
        data = yield from stream.readexactly(n)

        self.assertEqual(self.DATA+self.DATA, data)
        self.assertEqual(len(self.DATA), stream.byte_count)
        self.assertEqual(self.DATA.count(b'\n'), stream.line_count)

    @test_utils.sync
    def test_readexactly_eof(self):
        """ Read exact number of bytes (eof) """
        stream = http_client.StreamReader()

        def cb():
            stream.feed_data(self.DATA)
            stream.feed_eof()
        self.event_loop.call_soon(cb)

        n = 2*len(self.DATA)
        data = yield from stream.readexactly(n)

        self.assertEqual(self.DATA, data)
        self.assertFalse(stream.byte_count)
        self.assertFalse(stream.line_count)


if __name__ == '__main__':
    unittest.main()
