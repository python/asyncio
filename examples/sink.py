"""Test service that accepts connections and reads all data off them."""

import argparse
import sys

from tulip import *

ARGS = argparse.ArgumentParser(description="TCP data sink example.")
ARGS.add_argument(
    '--iocp', action='store_true', dest='iocp',
    default=False, help='Use IOCP event loop (Windows only)')
ARGS.add_argument(
    '--host', action='store', dest='host',
    default='127.0.0.1', help='Host name')
ARGS.add_argument(
    '--port', action='store', dest='port',
    default=1111, type=int, help='Port number')

server = None


def dprint(*args):
    print('sink:', *args, file=sys.stderr)


class Service(Protocol):

    def connection_made(self, tr):
        dprint('connection from', tr.get_extra_info('peername'))
        dprint('my socket is', tr.get_extra_info('sockname'))
        self.tr = tr
        self.total = 0

    def data_received(self, data):
        if data == b'stop':
            dprint('stopping server')
            server.close()
            self.tr.close()
            return
        self.total += len(data)
        dprint('received', len(data), 'bytes; total', self.total)
        if self.total > 1e6:
            dprint('closing due to too much data')
            self.tr.close()

    def connection_lost(self, how):
        dprint('closed', repr(how))


@coroutine
def start(loop, host, port):
    global server
    server = yield from loop.create_server(Service, host, port)
    dprint('serving', [s.getsockname() for s in server.sockets])
    yield from server.wait_closed()


def main():
    args = ARGS.parse_args()
    if args.iocp:
        from tulip.windows_events import ProactorEventLoop
        loop = ProactorEventLoop()
        set_event_loop(loop)
    else:
        loop = get_event_loop()
    loop.run_until_complete(start(loop, args.host, args.port))
    loop.close()


if __name__ == '__main__':
    main()
