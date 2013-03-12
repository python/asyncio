"""Simple server written using an event loop."""

import http.client
import email.message
import email.parser
import os

import tulip
import tulip.http


class HttpServer(tulip.Protocol):

    def __init__(self):
        super().__init__()
        self.transport = None
        self.reader = None
        self.handler = None

    @tulip.task
    def handle_request(self):
        try:
            method, path, version = yield from self.reader.read_request_line()
        except http.client.BadStatusLine:
            self.transport.close()
            return

        print('method = {!r}; path = {!r}; version = {!r}'.format(
            method, path, version))

        if (not (path.isprintable() and path.startswith('/')) or '/.' in path):
            print('bad path', repr(path))
            path = None
        else:
            path = '.' + path
            if not os.path.exists(path):
                print('no file', repr(path))
                path = None
            else:
                isdir = os.path.isdir(path)

        if not path:
            self.transport.write(b'HTTP/1.0 404 Not found\r\n\r\n')
            self.transport.close()
            return

        headers = email.message.Message()
        for hdr, val in (yield from self.reader.read_headers()):
            print(hdr, val)
            headers.add_header(hdr, val)

        write = self.transport.write
        if isdir and not path.endswith('/'):
            bpath = path.encode('ascii')
            write(b'HTTP/1.0 302 Redirected\r\n'
                  b'URI: ' + bpath + b'/\r\n'
                  b'Location: ' + bpath + b'/\r\n'
                  b'\r\n')
            return
        write(b'HTTP/1.0 200 Ok\r\n')
        if isdir:
            write(b'Content-type: text/html\r\n')
        else:
            write(b'Content-type: text/plain\r\n')
        write(b'\r\n')
        if isdir:
            write(b'<ul>\r\n')
            for name in sorted(os.listdir(path)):
                if name.isprintable() and not name.startswith('.'):
                    try:
                        bname = name.encode('ascii')
                    except UnicodeError:
                        pass
                    else:
                        if os.path.isdir(os.path.join(path, name)):
                            write(b'<li><a href="' + bname +
                                  b'/">' + bname + b'/</a></li>\r\n')
                        else:
                            write(b'<li><a href="' + bname +
                                  b'">' + bname + b'</a></li>\r\n')
            write(b'</ul>')
        else:
            try:
                with open(path, 'rb') as f:
                    write(f.read())
            except OSError:
                write(b'Cannot open\r\n')
        self.transport.close()

    def connection_made(self, transport):
        self.transport = transport
        print('connection made', transport, transport.get_extra_info('socket'))
        self.reader = tulip.http.HttpStreamReader()
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
