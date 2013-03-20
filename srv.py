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

        message = yield from self.reader.read_message()

        headers = email.message.Message()
        for hdr, val in message.headers:
            print(hdr, val)
            headers.add_header(hdr, val)

        if isdir and not path.endswith('/'):
            path = path + '/'
            response = tulip.http.Response(self.transport, 302)
            response.add_headers(
                ('URI', path),
                ('Location', path))
            response.send_headers()
            response.write_eof()
            self.transport.close()
            return

        response = tulip.http.Response(self.transport, 200)
        response.add_header('Transfer-Encoding', 'chunked')

        # content encoding
        accept_encoding = headers.get('accept-encoding', '').lower()
        if 'deflate' in accept_encoding:
            response.add_header('Content-Encoding', 'deflate')
            response.add_compression_filter('deflate')
        elif 'gzip' in accept_encoding:
            response.add_header('Content-Encoding', 'gzip')
            response.add_compression_filter('gzip')

        response.add_chunking_filter(1025)

        if isdir:
            response.add_header('Content-type', 'text/html')
            response.send_headers()

            response.write(b'<ul>\r\n')
            for name in sorted(os.listdir(path)):
                if name.isprintable() and not name.startswith('.'):
                    try:
                        bname = name.encode('ascii')
                    except UnicodeError:
                        pass
                    else:
                        if os.path.isdir(os.path.join(path, name)):
                            response.write(b'<li><a href="' + bname +
                                           b'/">' + bname + b'/</a></li>\r\n')
                        else:
                            response.write(b'<li><a href="' + bname +
                                           b'">' + bname + b'</a></li>\r\n')
            response.write(b'</ul>')
        else:
            response.add_header('Content-type', 'text/plain')
            response.send_headers()

            try:
                with open(path, 'rb') as fp:
                    chunk = fp.read(8196)
                    while chunk:
                        if not response.write(chunk):
                            break
                        chunk = fp.read(8196)
            except OSError:
                response.write(b'Cannot open')

        response.write_eof()
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
