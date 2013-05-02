# NOTE: This is a hack.  Andrew Svetlov is working in a proper
# subprocess management transport for use with
# connect_{read,write}_pipe().

"""Tests for subprocess_transport.py."""

import logging
import unittest

from tulip import events
from tulip import futures
from tulip import protocols
from tulip import subprocess_transport


class MyProto(protocols.Protocol):

    def __init__(self):
        self.state = 'INITIAL'
        self.nbytes = 0
        self.done = futures.Future()

    def connection_made(self, transport):
        self.transport = transport
        assert self.state == 'INITIAL', self.state
        self.state = 'CONNECTED'
        transport.write_eof()

    def data_received(self, data):
        logging.info('received: %r', data)
        assert self.state == 'CONNECTED', self.state
        self.nbytes += len(data)

    def eof_received(self):
        assert self.state == 'CONNECTED', self.state
        self.state = 'EOF'
        self.transport.close()

    def connection_lost(self, exc):
        assert self.state in ('CONNECTED', 'EOF'), self.state
        self.state = 'CLOSED'
        self.done.set_result(None)


class FutureTests(unittest.TestCase):

    def setUp(self):
        self.event_loop = events.new_event_loop()
        events.set_event_loop(self.event_loop)

    def tearDown(self):
        self.event_loop.close()

    def test_unix_subprocess(self):
        p = MyProto()
        subprocess_transport.UnixSubprocessTransport(p, ['/bin/ls', '-lR'])
        self.event_loop.run_until_complete(p.done)


if __name__ == '__main__':
    unittest.main()
