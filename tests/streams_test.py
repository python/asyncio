"""Tests for streams.py."""

from unittest import mock
import unittest

from tulip import events
from tulip import streams
from tulip import tasks
from tulip import test_utils


class StreamReaderTests(unittest.TestCase):

    DATA = b'line1\nline2\nline3\n'

    def setUp(self):
        self.loop = events.new_event_loop()
        events.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_open_connection(self):
        with test_utils.run_test_server(self.loop) as httpd:
            f = streams.open_connection(*httpd.address, loop=self.loop)
            reader, writer = self.loop.run_until_complete(f)
            writer.write(b'GET / HTTP/1.0\r\n\r\n')
            f = reader.readline()
            data = self.loop.run_until_complete(f)
            self.assertEqual(data, b'HTTP/1.0 200 OK\r\n')
            f = reader.read()
            data = self.loop.run_until_complete(f)
            self.assertTrue(data.endswith(b'\r\n\r\nTest message'))

    def test_open_connection_no_loop_ssl(self):
        with test_utils.run_test_server(self.loop, use_ssl=True) as httpd:
            f = streams.open_connection(*httpd.address, ssl=True)
            reader, writer = self.loop.run_until_complete(f)
            writer.write(b'GET / HTTP/1.0\r\n\r\n')
            f = reader.read()
            data = self.loop.run_until_complete(f)
            self.assertTrue(data.endswith(b'\r\n\r\nTest message'))

    def test_open_connection_error(self):
        with test_utils.run_test_server(self.loop) as httpd:
            f = streams.open_connection(*httpd.address)
            reader, writer = self.loop.run_until_complete(f)
            writer._protocol.connection_lost(ZeroDivisionError())
            f = reader.read()
            with self.assertRaises(ZeroDivisionError):
                self.loop.run_until_complete(f)

    def test_feed_empty_data(self):
        stream = streams.StreamReader()

        stream.feed_data(b'')
        self.assertEqual(0, stream.byte_count)

    def test_feed_data_byte_count(self):
        stream = streams.StreamReader()

        stream.feed_data(self.DATA)
        self.assertEqual(len(self.DATA), stream.byte_count)

    def test_read_zero(self):
        # Read zero bytes.
        stream = streams.StreamReader()
        stream.feed_data(self.DATA)

        data = self.loop.run_until_complete(stream.read(0))
        self.assertEqual(b'', data)
        self.assertEqual(len(self.DATA), stream.byte_count)

    def test_read(self):
        # Read bytes.
        stream = streams.StreamReader()
        read_task = tasks.Task(stream.read(30))

        def cb():
            stream.feed_data(self.DATA)
        self.loop.call_soon(cb)

        data = self.loop.run_until_complete(read_task)
        self.assertEqual(self.DATA, data)
        self.assertFalse(stream.byte_count)

    def test_read_line_breaks(self):
        # Read bytes without line breaks.
        stream = streams.StreamReader()
        stream.feed_data(b'line1')
        stream.feed_data(b'line2')

        data = self.loop.run_until_complete(stream.read(5))

        self.assertEqual(b'line1', data)
        self.assertEqual(5, stream.byte_count)

    def test_read_eof(self):
        # Read bytes, stop at eof.
        stream = streams.StreamReader()
        read_task = tasks.Task(stream.read(1024))

        def cb():
            stream.feed_eof()
        self.loop.call_soon(cb)

        data = self.loop.run_until_complete(read_task)
        self.assertEqual(b'', data)
        self.assertFalse(stream.byte_count)

    def test_read_until_eof(self):
        # Read all bytes until eof.
        stream = streams.StreamReader()
        read_task = tasks.Task(stream.read(-1))

        def cb():
            stream.feed_data(b'chunk1\n')
            stream.feed_data(b'chunk2')
            stream.feed_eof()
        self.loop.call_soon(cb)

        data = self.loop.run_until_complete(read_task)

        self.assertEqual(b'chunk1\nchunk2', data)
        self.assertFalse(stream.byte_count)

    def test_read_exception(self):
        stream = streams.StreamReader()
        stream.feed_data(b'line\n')

        data = self.loop.run_until_complete(stream.read(2))
        self.assertEqual(b'li', data)

        stream.set_exception(ValueError())
        self.assertRaises(
            ValueError, self.loop.run_until_complete, stream.read(2))

    def test_readline(self):
        # Read one line.
        stream = streams.StreamReader()
        stream.feed_data(b'chunk1 ')
        read_task = tasks.Task(stream.readline())

        def cb():
            stream.feed_data(b'chunk2 ')
            stream.feed_data(b'chunk3 ')
            stream.feed_data(b'\n chunk4')
        self.loop.call_soon(cb)

        line = self.loop.run_until_complete(read_task)
        self.assertEqual(b'chunk1 chunk2 chunk3 \n', line)
        self.assertEqual(len(b'\n chunk4')-1, stream.byte_count)

    def test_readline_limit_with_existing_data(self):
        stream = streams.StreamReader(3)
        stream.feed_data(b'li')
        stream.feed_data(b'ne1\nline2\n')

        self.assertRaises(
            ValueError, self.loop.run_until_complete, stream.readline())
        self.assertEqual([b'line2\n'], list(stream.buffer))

        stream = streams.StreamReader(3)
        stream.feed_data(b'li')
        stream.feed_data(b'ne1')
        stream.feed_data(b'li')

        self.assertRaises(
            ValueError, self.loop.run_until_complete, stream.readline())
        self.assertEqual([b'li'], list(stream.buffer))
        self.assertEqual(2, stream.byte_count)

    def test_readline_limit(self):
        stream = streams.StreamReader(7)

        def cb():
            stream.feed_data(b'chunk1')
            stream.feed_data(b'chunk2')
            stream.feed_data(b'chunk3\n')
            stream.feed_eof()
        self.loop.call_soon(cb)

        self.assertRaises(
            ValueError, self.loop.run_until_complete, stream.readline())
        self.assertEqual([b'chunk3\n'], list(stream.buffer))
        self.assertEqual(7, stream.byte_count)

    def test_readline_line_byte_count(self):
        stream = streams.StreamReader()
        stream.feed_data(self.DATA[:6])
        stream.feed_data(self.DATA[6:])

        line = self.loop.run_until_complete(stream.readline())

        self.assertEqual(b'line1\n', line)
        self.assertEqual(len(self.DATA) - len(b'line1\n'), stream.byte_count)

    def test_readline_eof(self):
        stream = streams.StreamReader()
        stream.feed_data(b'some data')
        stream.feed_eof()

        line = self.loop.run_until_complete(stream.readline())
        self.assertEqual(b'some data', line)

    def test_readline_empty_eof(self):
        stream = streams.StreamReader()
        stream.feed_eof()

        line = self.loop.run_until_complete(stream.readline())
        self.assertEqual(b'', line)

    def test_readline_read_byte_count(self):
        stream = streams.StreamReader()
        stream.feed_data(self.DATA)

        self.loop.run_until_complete(stream.readline())

        data = self.loop.run_until_complete(stream.read(7))

        self.assertEqual(b'line2\nl', data)
        self.assertEqual(
            len(self.DATA) - len(b'line1\n') - len(b'line2\nl'),
            stream.byte_count)

    def test_readline_exception(self):
        stream = streams.StreamReader()
        stream.feed_data(b'line\n')

        data = self.loop.run_until_complete(stream.readline())
        self.assertEqual(b'line\n', data)

        stream.set_exception(ValueError())
        self.assertRaises(
            ValueError, self.loop.run_until_complete, stream.readline())

    def test_readexactly_zero_or_less(self):
        # Read exact number of bytes (zero or less).
        stream = streams.StreamReader()
        stream.feed_data(self.DATA)

        data = self.loop.run_until_complete(stream.readexactly(0))
        self.assertEqual(b'', data)
        self.assertEqual(len(self.DATA), stream.byte_count)

        data = self.loop.run_until_complete(stream.readexactly(-1))
        self.assertEqual(b'', data)
        self.assertEqual(len(self.DATA), stream.byte_count)

    def test_readexactly(self):
        # Read exact number of bytes.
        stream = streams.StreamReader()

        n = 2 * len(self.DATA)
        read_task = tasks.Task(stream.readexactly(n))

        def cb():
            stream.feed_data(self.DATA)
            stream.feed_data(self.DATA)
            stream.feed_data(self.DATA)
        self.loop.call_soon(cb)

        data = self.loop.run_until_complete(read_task)
        self.assertEqual(self.DATA + self.DATA, data)
        self.assertEqual(len(self.DATA), stream.byte_count)

    def test_readexactly_eof(self):
        # Read exact number of bytes (eof).
        stream = streams.StreamReader()
        n = 2 * len(self.DATA)
        read_task = tasks.Task(stream.readexactly(n))

        def cb():
            stream.feed_data(self.DATA)
            stream.feed_eof()
        self.loop.call_soon(cb)

        data = self.loop.run_until_complete(read_task)
        self.assertEqual(self.DATA, data)
        self.assertFalse(stream.byte_count)

    def test_readexactly_exception(self):
        stream = streams.StreamReader()
        stream.feed_data(b'line\n')

        data = self.loop.run_until_complete(stream.readexactly(2))
        self.assertEqual(b'li', data)

        stream.set_exception(ValueError())
        self.assertRaises(
            ValueError, self.loop.run_until_complete, stream.readexactly(2))

    def test_exception(self):
        stream = streams.StreamReader()
        self.assertIsNone(stream.exception())

        exc = ValueError()
        stream.set_exception(exc)
        self.assertIs(stream.exception(), exc)

    def test_exception_waiter(self):
        stream = streams.StreamReader()

        @tasks.coroutine
        def set_err():
            stream.set_exception(ValueError())

        @tasks.coroutine
        def readline():
            yield from stream.readline()

        t1 = tasks.Task(stream.readline())
        t2 = tasks.Task(set_err())

        self.loop.run_until_complete(tasks.wait([t1, t2]))

        self.assertRaises(ValueError, t1.result)


if __name__ == '__main__':
    unittest.main()
