"""Test client that connects and sends infinite data."""

import sys

from tulip import *

def dprint(*args):
    print('source:', *args, file=sys.stderr)

class Client(Protocol):

    data = b'x'*16*1024

    def connection_made(self, tr):
        dprint('connecting to', tr.get_extra_info('socket').getpeername())
        dprint('my socket is', tr.get_extra_info('socket').getsockname())
        self.tr = tr
        self.lost = False
        self.loop = get_event_loop()
        self.waiter = Future()
        if '--stop' in sys.argv[1:]:
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
def start(loop):
    tr, pr = yield from loop.create_connection(Client, '127.0.0.1', 1111)
    dprint('tr =', tr)
    dprint('pr =', pr)
    res = yield from pr.waiter
    return res

def main():
    if '--iocp' in sys.argv:
        from tulip.windows_events import ProactorEventLoop
        loop = ProactorEventLoop()
        set_event_loop(loop)
    loop = get_event_loop()
    loop.run_until_complete(start(loop))
    loop.close()

if __name__ == '__main__':
    main()
