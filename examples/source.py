"""Test client that connects and sends infinite data."""

import argparse
import sys

from asyncio import *
from asyncio import test_utils


ARGS = argparse.ArgumentParser(description="TCP data sink example.")
ARGS.add_argument(
    '--tls', action='store_true', dest='tls',
    default=False, help='Use TLS')
ARGS.add_argument(
    '--iocp', action='store_true', dest='iocp',
    default=False, help='Use IOCP event loop (Windows only)')
ARGS.add_argument(
    '--stop', action='store_true', dest='stop',
    default=False, help='Stop the server by sending it b"stop" as data')
ARGS.add_argument(
    '--host', action='store', dest='host',
    default='127.0.0.1', help='Host name')
ARGS.add_argument(
    '--port', action='store', dest='port',
    default=1111, type=int, help='Port number')
ARGS.add_argument(
    '--size', action='store', dest='size',
    default=16*1024, type=int, help='Data size')

args = None


def dprint(*args):
    print('source:', *args, file=sys.stderr)


class Client(Protocol):

    total = 0

    def connection_made(self, tr):
        dprint('connecting to', tr.get_extra_info('peername'))
        dprint('my socket is', tr.get_extra_info('sockname'))
        self.tr = tr
        self.lost = False
        self.loop = get_event_loop()
        self.waiter = Future()
        if args.stop:
            self.tr.write(b'stop')
            self.tr.close()
        else:
            self.data = b'x'*args.size
            self.write_some_data()

    def write_some_data(self):
        if self.lost:
            dprint('lost already')
            return
        data = self.data
        size = len(data)
        self.total += size
        dprint('writing', size, 'bytes; total', self.total)
        self.tr.write(data)
        self.loop.call_soon(self.write_some_data)

    def connection_lost(self, exc):
        dprint('lost connection', repr(exc))
        self.lost = True
        self.waiter.set_result(None)


@coroutine
def start(loop, host, port):
    sslctx = None
    if args.tls:
        sslctx = test_utils.dummy_ssl_context()
    tr, pr = yield from loop.create_connection(Client, host, port,
                                               ssl=sslctx)
    dprint('tr =', tr)
    dprint('pr =', pr)
    yield from pr.waiter


def main():
    global args
    args = ARGS.parse_args()
    if args.iocp:
        from asyncio.windows_events import ProactorEventLoop
        loop = ProactorEventLoop()
        set_event_loop(loop)
    else:
        loop = get_event_loop()
    try:
        loop.run_until_complete(start(loop, args.host, args.port))
    finally:
        loop.close()


if __name__ == '__main__':
    main()
