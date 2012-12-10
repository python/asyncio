"""Transports and Protocols, actually.

Inspired by Twisted, PEP 3153 and github.com/lvh/async-pep.

THIS IS NOT REAL CODE!  IT IS JUST AN EXPERIMENT.
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
    """ABC representing a transport.

    There may be many implementations.  The user never instantiates
    this directly; they call some utility function, passing it a
    protocol, and the utility function will call the protocol's
    connection_made() method with a transport (or it will call
    connection_lost() with an exception if it fails to create the
    desired transport).
    """

    def write(self, data):
        """Write some data (bytes) to the transport.

        This does not block; it buffers the data and arranges for it
        to be sent out asynchronously.
        """
        raise NotImplemented

    def writelines(self, list_of_data):  # Not just for lines.
        """Write a list (or any iterable) of data (bytes) to the transport.

        The default implementation just calls write() for each item in
        the list/iterable.
        """
        for data in list_of_data:
            self.write(data)

    def close(self):
        """Closes the transport.

        Buffered data will be flushed asynchronously.  No more data will
        be received.  When all buffered data is flushed, the protocol's
        connection_lost() method is called with None as its argument.
        """

    def abort(self):
        """Closes the transport immediately.

        Buffered data will be lost.  No more data will be received.
        The protocol's connection_lost() method is called with None as
        its argument.
        """

    def half_close(self):  # Closes the write end after flushing.
        """Closes the write end after flushing buffered data.

        Data may still be received.
        """

    def pause(self):
        """Pause the receiving end.
        
        No data will be received until resume end is called.
        """

    def resume(self):
        """Resume the receiving end.

        Cancels a pause() call.
        """


class Protocol:
    """ABC representing a protocol.

    The user should implement this interface.

    When the user requests a transport, they pass it a protocol
    instance to a utility function.

    When the connection is made successfully, connection_made() is
    called with a suitable transport object.  Then data_received()
    will be called 0 or more times with data (bytes) received from the
    transport; finally, connection_list() will be called exactly once
    with either an exception object or None as an argument.

    If the utility function does not succeed in creating a transport,
    it will call connection_lost() with an exception object.

    State machine of calls:

      start -> [CM -> DR*] -> CL -> end
    """

    def connection_made(self, transport):
        """Called when a connection is made.

        The argument is the transport representing the connection.
        To send data, call its write() or writelines() method.
        To receive data, wait for data_received() calls.
        When the connection is closed, connection_lost() is called.
        """

    def data_received(self, data):
        """Called when some data is received.

        The argument is a bytes object.

        TODO: Should we allow it to be a bytesarray or some other
        memory buffer?
        """

    def connection_lost(self, exc):
        """Called when the connection is lost or closed.

        Also called when we fail to make a connection at all.

        The argument is an exception object or None (the latter meaning
        a regular EOF is received or the connection was aborted).
        """


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
                self._eventloop.remove_reader(self._sock.fileno())
                self._sock.close()
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
    if not socktype:
        socktype = socket.SOCK_STREAM
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

    def on_future_done(fut):
        logging.debug('Future done.')
        exc = fut.exception()
        if exc is None:
            infos = fut.result()
        else:
            infos = None
        eventloop.call_soon(on_addrinfo, infos, exc)

    future = threadrunner.submit(socket.getaddrinfo,
                                 host, port, af, socktype, proto,
                                 callback=on_future_done)


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
            self.transport.write(b'GET / HTTP/1.0\r\nHost: python.org\r\n\r\n')
            ## self.transport.half_close()
        def data_received(self, data):
            logging.debug('Received %d bytes: %r', len(data), data)
        def connection_lost(self, exc):
            logging.debug('Connection lost: %r', exc)

    tp = TestProtocol()
    logging.debug('tp = %r', tp)
    make_connection(tp, 'python.org')
    logging.info('Running...')
    polling.context.eventloop.run()
    logging.info('Done.')


if __name__ == '__main__':
    main()
