"""Simple server written using an event loop."""

import email.message
import email.parser
import gc
import re

import tulip
from tulip.http_client import StreamReader


class HttpServer(tulip.Protocol):

    def __init__(self):
        super().__init__()
        self.transport = None
        self.reader = None
        self.handler = None

    @tulip.task
    def handle_request(self):
        line = yield from self.reader.readline()
        print('request line', line)
        match = re.match(rb'GET (\S+) HTTP/(1.\d)\r?\n\Z', line)
        if not match:
            self.transport.close()
            return
        lines = []
        while True:
            line = yield from self.reader.readline()
            print('header line', line)
            if not line.strip(b' \t\r\n'):
                break
            lines.append(line)
            if line == b'\r\n':
                break
        parser = email.parser.BytesHeaderParser()
        headers = parser.parsebytes(b''.join(lines))
        self.transport.write(b'HTTP/1.0 200 Ok\r\n'
                             b'Content-type: text/plain\r\n'
                             b'\r\n'
                             b'Hello world.\r\n')
        self.transport.close()

    def connection_made(self, transport):
        self.transport = transport
        print('connection made', transport, transport._sock)
        self.reader = StreamReader()
        self.handler = self.handle_request()

    def data_received(self, data):
        print('data received', data)
        self.reader.feed_data(data)

    def eof_received(self):
        print('eof received')
        self.reader.feed_eof()

    def connection_lost(self, exc):
        print('connection lost', exc)
        if (self.handler.done() and
            not self.handler.cancelled() and
            self.handler.exception() is not None):
            print('handler exception:', self.handler.exception())


def main():
    loop = tulip.get_event_loop()
    f = loop.start_serving(HttpServer, '127.0.0.1', 8080)
    x = loop.run_until_complete(f)
    print('serving on', x.getsockname())
    loop.run_forever()


if __name__ == '__main__':
    main()
