"""Transports and Protocols, actually.

Inspired by Twisted, PEP 3153 and github.com/lvh/async-pep.
"""

# Stdlib imports.
import errno
import logging
import socket
import ssl
import sys

# Local imports.
import polling

# Errno values indicating the connection was disconnected.
_DISCONNECTED = frozenset((errno.ECONNRESET,
                           errno.ENOTCONN,
                           errno.ESHUTDOWN,
                           errno.ECONNABORTED,
                           errno.EPIPE,
                           errno.EBADF,
                           ))

# Errno values indicating the socket isn't ready for I/O just yet.
_TRYAGAIN = frozenset((errno.EAGAIN, errno.EWOULDBLOCK))


class Transport:

    def write(self, data):
        XXX

    def writelines(self, list_of_data):  # Not just for lines.
        XXX

    def close(self):
        XXX

    def abort(self):
        XXX

    def half_close(self):  # Closes the write end after flushing.
        XXX

    def pause(self):
        XXX

    def resume(self):
        XXX


class Protocol:

    def connection_made(self, transport):
        XXX

    def data_received(self, data):
        XXX

    def connection_lost(self, exc):  # Also when connect() failed.
        XXX


# XXX The rest is platform specific and should move elsewhere.

class SocketTransport(Transport):

    def __init__(self, eventloop, protocol, sock):
        self._eventloop = eventloop
        self._protocol = protocol
        self._sock = sock

    def _on_readable(self):
        try:
            data = self._sock.recv(8192)
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                self._eventloop.remove_reader(sock.fileno())
                sock.close()
                self._protocol.connection_lost(exc)  # XXX calL_soon()?
        else:
            if not data:
                self._eventloop.remove_reader(self._sock.fileno())
                self._sock.close()
                self._protocol.connection_lost(None)
            else:
                self._protocol.data_received(data)  # XXX call_soon()?

    def write(self, data):
        # XXX implement write buffering.
        self._sock.sendall(data)


def make_connection(protocol, host, port=None, af=0, socktype=0, proto=0,
                    use_ssl=None):
    if port is None:
        port = 443 if use_ssl else 80
    if use_ssl is None:
        use_ssl = (port == 443)
    eventloop = polling.context.eventloop
    threadrunner = polling.context.threadrunner

    # XXX Move all this to private methods on SocketTransport.

    def on_addrinfo(infos, exc):
        logging.debug('on_addrinfo(<list of %d>, %r)', len(infos), exc)
        # XXX Make infos into an iterator, to avoid pop()?
        if not infos:
            if exc is not None:
                protocol.connection_lost(exc)
                return
            protocol.connection_lost(IOError(0, 'No more infos to try'))
            return

        af, socktype, proto, cname, address = infos.pop(0)
        sock = socket.socket(af, socktype, proto)
        sock.setblocking(False)

        try:
            sock.connect(address)
        except socket.error as exc:
            if exc.errno != errno.EINPROGRESS:
                sock.close()
                on_addrinfo(infos, exc)  # XXX Use eventloop.call_soon()?
                return

        def on_writable():
            eventloop.remove_writer(sock.fileno())
            err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if err != 0:
                sock.close()
                # Try the next getaddrinfo() return value.
                on_addrinfo(infos, None)
                return

            if use_ssl:
                XXX
            else:
                transport = SocketTransport(eventloop, protocol, sock)
                protocol.connection_made(transport)  # XXX call_soon()?
                eventloop.add_reader(sock.fileno(), transport._on_readable)

        eventloop.add_writer(sock.fileno(), on_writable)

    future = threadrunner.submit(socket.getaddrinfo,
                                 host, port, af, socktype, proto)
    def on_future_done(fut):
        logging.debug('Future done.')
        exc = fut.exception()
        if exc is None:
            infos = fut.result()
        else:
            infos = None
        eventloop.call_soon(on_addrinfo, infos, exc)
    future.add_done_callback(lambda fut: eventloop.call_soon(on_future_done, fut))


def main():  # Testing...

    # Initialize logging.
    if '-d' in sys.argv:
        level = logging.DEBUG
    elif '-v' in sys.argv:
        level = logging.INFO
    elif '-q' in sys.argv:
        level = logging.ERROR
    else:
        level = logging.WARN
    logging.basicConfig(level=level)

    class TestProtocol(Protocol):
        def connection_made(self, transport):
            logging.debug('Connection made.')
            self.transport = transport
            self.transport.write(b'GET / HTTP/1.0\r\n\r\n')
            ## self.transport.half_close()
        def data_received(self, data):
            logging.info('Received %d bytes: %r', len(data), data)
        def connection_lost(self, exc):
            logging.debug('Connection lost: %r', exc)

    tp = TestProtocol()
    logging.info('tp = %r', tp)
    make_connection(tp, 'python.org')
    logging.info('Running...')
    polling.context.eventloop.run()
    logging.info('Done.')


if __name__ == '__main__':
    main()
