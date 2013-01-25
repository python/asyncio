"""Tests for http_client.py."""

import unittest

from . import events
from . import http_client
from . import tasks


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

    def test_readline_line_byte_count(self):
        stream = http_client.StreamReader()
        stream.feed_data(self.DATA)

        @tasks.coroutine
        def readline():
            line = yield from stream.readline()
            return line

        line = self.event_loop.run_until_complete(tasks.Task(readline()))

        self.assertEqual(b'line1\n', line)
        self.assertEqual(self.DATA.count(b'\n')-1, stream.line_count)
        self.assertEqual(len(self.DATA) - len(b'line1\n'), stream.byte_count)

    def test_readline_read_byte_count(self):
        stream = http_client.StreamReader()
        stream.feed_data(self.DATA)

        @tasks.coroutine
        def readline():
            line = yield from stream.readline()
            return line

        line = self.event_loop.run_until_complete(tasks.Task(readline()))

        @tasks.coroutine
        def read():
            line = yield from stream.read(7)
            return line

        data = self.event_loop.run_until_complete(tasks.Task(read()))

        self.assertEqual(b'line2\nl', data)
        self.assertEqual(
            1, stream.line_count)
        self.assertEqual(
            len(self.DATA) - len(b'line1\n') - len(b'line2\nl'),
            stream.byte_count)


if __name__ == '__main__':
    unittest.main()
