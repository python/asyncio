"""Test server that accepts connections and reads all data off them."""

import sys

from tulip import *

def dprint(*args):
    print('sink:', *args, file=sys.stderr)

class Server(Protocol):

    def connection_made(self, tr):
        dprint('connection from', tr.get_extra_info('addr'))
        self.tr = tr
        self.total = 0

    def data_received(self, data):
        self.total += len(data)
        dprint('received', len(data), 'bytes; total', self.total)
        if self.total > 1e6:
            dprint('closing due to too much data')
            self.tr.close()

    def connection_lost(self, how):
        dprint('closed', repr(how))

@coroutine
def start(loop):
    svr = yield from loop.create_server(Server, 'localhost', 1111)
    return svr

def main():
    loop = get_event_loop()
    svr = loop.run_until_complete(start(loop))
    dprint('serving', [s.getsockname() for s in svr.sockets])
    loop.run_forever()

if __name__ == '__main__':
    main()
