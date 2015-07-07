"""Like source.py, but uses streams."""

from __future__ import print_function
import argparse
import sys

from trollius import *
from trollius import test_utils

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


class Debug:
    """A clever little class that suppresses repetitive messages."""

    overwriting = False
    label = 'stream1:'

    def print_(self, *args):
        if self.overwriting:
            print(file=sys.stderr)
            self.overwriting = 0
        print(self.label, *args, file=sys.stderr)

    def oprint(self, *args):
        self.overwriting += 1
        end = '\n'
        if self.overwriting >= 3:
            if self.overwriting == 3:
                print(self.label, '[...]', file=sys.stderr)
            end = '\r'
        print(self.label, *args, file=sys.stderr, end=end)
        sys.stdout.flush()


@coroutine
def start(loop, args):
    d = Debug()
    total = 0
    sslctx = None
    if args.tls:
        d.print_('using dummy SSLContext')
        sslctx = test_utils.dummy_ssl_context()
    r, w = yield From(open_connection(args.host, args.port, ssl=sslctx))
    d.print_('r =', r)
    d.print_('w =', w)
    if args.stop:
        w.write(b'stop')
        w.close()
    else:
        size = args.size
        data = b'x'*size
        try:
            while True:
                total += size
                d.oprint('writing', size, 'bytes; total', total)
                w.write(data)
                f = w.drain()
                if f:
                    d.print_('pausing')
                    yield From(f)
        except (ConnectionResetError, BrokenPipeError) as exc:
            d.print_('caught', repr(exc))


def main():
    global args
    args = ARGS.parse_args()
    if args.iocp:
        from trollius.windows_events import ProactorEventLoop
        loop = ProactorEventLoop()
        set_event_loop(loop)
    else:
        loop = get_event_loop()
    try:
        loop.run_until_complete(start(loop, args))
    finally:
        loop.close()


if __name__ == '__main__':
    main()
