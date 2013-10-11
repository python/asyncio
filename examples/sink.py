"""Test service that accepts connections and reads all data off them."""

import sys

from tulip import *

server = None

def dprint(*args):
    print('sink:', *args, file=sys.stderr)

class Service(Protocol):

    def connection_made(self, tr):
        dprint('connection from', tr.get_extra_info('socket').getpeername())
        dprint('my socket is', tr.get_extra_info('socket').getsockname())
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
def start(loop):
    svr = yield from loop.create_server(Service, 'localhost', 1111)
    return svr

def main():
    if '--iocp' in sys.argv:
        from tulip.windows_events import ProactorEventLoop
        loop = ProactorEventLoop()
        set_event_loop(loop)
    loop = get_event_loop()
    global server
    server = loop.run_until_complete(start(loop))
    dprint('serving', [s.getsockname() for s in server.sockets])
    loop.run_until_complete(server.wait_closed())
    loop.close()

if __name__ == '__main__':
    main()
