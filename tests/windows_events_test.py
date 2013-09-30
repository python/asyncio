import unittest

import tulip

from tulip import windows_events
from tulip import protocols
from tulip import streams
from tulip import test_utils


def connect_read_pipe(loop, file):
    stream_reader = streams.StreamReader(loop=loop)
    protocol = _StreamReaderProtocol(stream_reader)
    loop._make_read_pipe_transport(file, protocol)
    return stream_reader


class _StreamReaderProtocol(protocols.Protocol):
    def __init__(self, stream_reader):
        self.stream_reader = stream_reader

    def connection_lost(self, exc):
        self.stream_reader.set_exception(exc)

    def data_received(self, data):
        self.stream_reader.feed_data(data)

    def eof_received(self):
        self.stream_reader.feed_eof()


class ProactorTests(unittest.TestCase):

    def setUp(self):
        self.loop = windows_events.ProactorEventLoop()
        tulip.set_event_loop(None)

    def tearDown(self):
        self.loop.close()
        self.loop = None

    def test_close(self):
        a, b = self.loop._socketpair()
        trans = self.loop._make_socket_transport(a, protocols.Protocol())
        f = tulip.async(self.loop.sock_recv(b, 100))
        trans.close()
        self.loop.run_until_complete(f)
        self.assertEqual(f.result(), b'')
