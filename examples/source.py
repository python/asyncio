"""Test client that connects and sends infinite data."""

import argparse
import sys

from tulip import *


ARGS = argparse.ArgumentParser(description="TCP data sink example.")
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

args = None


def dprint(*args):
    print('source:', *args, file=sys.stderr)


class Client(Protocol):

    data = b'x'*16*1024

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
            self.write_some_data()

    def write_some_data(self):
        if self.lost:
            dprint('lost already')
            return
        dprint('writing', len(self.data), 'bytes')
        self.tr.write(self.data)
        self.loop.call_soon(self.write_some_data)

    def connection_lost(self, exc):
        dprint('lost connection', repr(exc))
        self.lost = True
        self.waiter.set_result(None)


@coroutine
def start(loop, host, port):
    tr, pr = yield from loop.create_connection(Client, host, port)
    dprint('tr =', tr)
    dprint('pr =', pr)
    res = yield from pr.waiter
    return res


def main():
    global args
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
