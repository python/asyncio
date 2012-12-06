"""Transports and Protocols, actually.

Inspired by Twisted, PEP 3153 and github.com/lvh/async-pep.
"""

# Stdlib imports.
import errno
import socket
import ssl

# Local imports.
import scheduling

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
        self._eventloop = None
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
            self._protocol.data_received(data)  # XXX call_soon()?

    # XXX implement write buffering.


def make_connection(protocol, host, port=None, af=0, socktype=0, proto=0,
                    use_ssl=None):
    if port is None:
        port = 443 if use_ssl else 80
    if use_ssl is None:
        use_ssl = (port == 443)
    eventloop = scheduling.context.eventloop

    # XXX Move all this to private methods on SocketTransport.

    def on_addrinfo(infos, exc):
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

    # XXX Implement EventLoop.call_in_thread().
    eventloop.call_in_thread(socket.getaddrinfo,
                             host, port, af, socktype, proto,
                             callback=on_addrinfo)
