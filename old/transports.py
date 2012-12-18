"""Transports and Protocols, actually.

Inspired by Twisted, PEP 3153 and github.com/lvh/async-pep.

THIS IS NOT REAL CODE!  IT IS JUST AN EXPERIMENT.
"""

# Stdlib imports.
import collections
import errno
import logging
import socket
import ssl
import sys
import time

# Local imports.
import polling
import scheduling
import sockets

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

    The implementation here raises NotImplemented for every method
    except writelines(), which calls write() in a loop.
    """

    def write(self, data):
        """Write some data (bytes) to the transport.

        This does not block; it buffers the data and arranges for it
        to be sent out asynchronously.
        """
        raise NotImplementedError

    def writelines(self, list_of_data):
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
        raise NotImplementedError

    def abort(self):
        """Closes the transport immediately.

        Buffered data will be lost.  No more data will be received.
        The protocol's connection_lost() method is called with None as
        its argument.
        """
        raise NotImplementedError

    def half_close(self):
        """Closes the write end after flushing buffered data.

        Data may still be received.

        TODO: What's the use case for this?  How to implement it?
        Should it call shutdown(SHUT_WR) after all the data is flushed?
        Is there no use case for closing the other half first?
        """
        raise NotImplementedError

    def pause(self):
        """Pause the receiving end.

        No data will be received until resume() is called.
        """
        raise NotImplementedError

    def resume(self):
        """Resume the receiving end.

        Cancels a pause() call, resumes receiving data.
        """
        raise NotImplementedError


class Protocol:
    """ABC representing a protocol.

    The user should implement this interface.  They can inherit from
    this class but don't need to.  The implementations here do
    nothing.

    When the user wants to requests a transport, they pass a protocol
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

        Also called when we fail to make a connection at all (in that
        case connection_made() will not be called).

        The argument is an exception object or None (the latter
        meaning a regular EOF is received or the connection was
        aborted or closed).
        """


# TODO: The rest is platform specific and should move elsewhere.

class UnixSocketTransport(Transport):

    def __init__(self, eventloop, protocol, sock):
        self._eventloop = eventloop
        self._protocol = protocol
        self._sock = sock
        self._buffer = collections.deque()  # For write().
        self._write_closed = False

    def _on_readable(self):
        try:
            data = self._sock.recv(8192)
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                self._bad_error(exc)
        else:
            if not data:
                self._eventloop.remove_reader(self._sock.fileno())
                self._sock.close()
                self._protocol.connection_lost(None)
            else:
                self._protocol.data_received(data)  # XXX call_soon()?

    def write(self, data):
        assert isinstance(data, bytes)
        assert not self._write_closed
        if not data:
            # Silly, but it happens.
            return
        if self._buffer:
            # We've already registered a callback, just buffer the data.
            self._buffer.append(data)
            # Consider pausing if the total length of the buffer is
            # truly huge.
            return

        # TODO: Refactor so there's more sharing between this and
        # _on_writable().

        # There's no callback registered yet.  It's quite possible
        # that the kernel has buffer space for our data, so try to
        # write now.  Since the socket is non-blocking it will
        # give us an error in _TRYAGAIN if it doesn't have enough
        # space for even one more byte; it will return the number
        # of bytes written if it can write at least one byte.
        try:
            n = self._sock.send(data)
        except socket.error as exc:
            # An error.
            if exc.errno not in _TRYAGAIN:
                self._bad_error(exc)
                return
            # The kernel doesn't have room for more data right now.
            n = 0
        else:
            # Wrote at least one byte.
            if n == len(data):
                # Wrote it all.  Done!
                if self._write_closed:
                    self._sock.shutdown(socket.SHUT_WR)
                return
            # Throw away the data that was already written.
            # TODO: Do this without copying the data?
            data = data[n:]
        self._buffer.append(data)
        self._eventloop.add_writer(self._sock.fileno(), self._on_writable)

    def _on_writable(self):
        while self._buffer:
            data = self._buffer[0]
            # TODO: Join small amounts of data?
            try:
                n = self._sock.send(data)
            except socket.error as exc:
                # Error handling is the same as in write().
                if exc.errno not in _TRYAGAIN:
                    self._bad_error(exc)
                return
            if n < len(data):
                self._buffer[0] = data[n:]
                return
            self._buffer.popleft()
        self._eventloop.remove_writer(self._sock.fileno())
        if self._write_closed:
            self._sock.shutdown(socket.SHUT_WR)

    def abort(self):
        self._bad_error(None)

    def _bad_error(self, exc):
        # A serious error.  Close the socket etc.
        fd = self._sock.fileno()
        # TODO: Record whether we have a writer and/or reader registered.
        try:
            self._eventloop.remove_writer(fd)
        except Exception:
            pass
        try:
            self._eventloop.remove_reader(fd)
        except Exception:
            pass
        self._sock.close()
        self._protocol.connection_lost(exc)  # XXX call_soon()?

    def half_close(self):
        self._write_closed = True


class UnixSslTransport(Transport):

    # TODO: Refactor Socket and Ssl transport to share some code.
    # (E.g. buffering.)

    # TODO: Consider using coroutines instead of callbacks, it seems
    # much easier that way.

    def __init__(self, eventloop, protocol, rawsock, sslcontext=None):
        self._eventloop = eventloop
        self._protocol = protocol
        self._rawsock = rawsock
        self._sslcontext = sslcontext or ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        self._sslsock = self._sslcontext.wrap_socket(
            self._rawsock, do_handshake_on_connect=False)

        self._buffer = collections.deque()  # For write().
        self._write_closed = False

        # Try the handshake now.  Likely it will raise EAGAIN, then it
        # will take care of registering the appropriate callback.
        self._on_handshake()

    def _bad_error(self, exc):
        # A serious error.  Close the socket etc.
        fd = self._sslsock.fileno()
        # TODO: Record whether we have a writer and/or reader registered.
        try:
            self._eventloop.remove_writer(fd)
        except Exception:
            pass
        try:
            self._eventloop.remove_reader(fd)
        except Exception:
            pass
        self._sslsock.close()
        self._protocol.connection_lost(exc)  # XXX call_soon()?

    def _on_handshake(self):
        fd = self._sslsock.fileno()
        try:
            self._sslsock.do_handshake()
        except ssl.SSLWantReadError:
            self._eventloop.add_reader(fd, self._on_handshake)
            return
        except ssl.SSLWantWriteError:
            self._eventloop.add_writable(fd, self._on_handshake)
            return
        # TODO: What if it raises another error?
        try:
            self._eventloop.remove_reader(fd)
        except Exception:
            pass
        try:
            self._eventloop.remove_writer(fd)
        except Exception:
            pass
        self._protocol.connection_made(self)
        self._eventloop.add_reader(fd, self._on_ready)
        self._eventloop.add_writer(fd, self._on_ready)

    def _on_ready(self):
        # Because of renegotiations (?), there's no difference between
        # readable and writable.  We just try both.  XXX This may be
        # incorrect; we probably need to keep state about what we
        # should do next.

        # Maybe we're already closed...
        fd = self._sslsock.fileno()
        if fd < 0:
            return

        # First try reading.
        try:
            data = self._sslsock.recv(8192)
        except ssl.SSLWantReadError:
            pass
        except ssl.SSLWantWriteError:
            pass
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                self._bad_error(exc)
                return
        else:
            if data:
                self._protocol.data_received(data)
            else:
                # TODO: Don't close when self._buffer is non-empty.
                assert not self._buffer
                self._eventloop.remove_reader(fd)
                self._eventloop.remove_writer(fd)
                self._sslsock.close()
                self._protocol.connection_lost(None)
                return

        # Now try writing, if there's anything to write.
        if not self._buffer:
            return

        data = self._buffer[0]
        try:
            n = self._sslsock.send(data)
        except ssl.SSLWantReadError:
            pass
        except ssl.SSLWantWriteError:
            pass
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                self._bad_error(exc)
                return
        else:
            if n == len(data):
                self._buffer.popleft()
                # Could try again, but let's just have the next callback do it.
            else:
                self._buffer[0] = data[n:]

    def write(self, data):
        assert isinstance(data, bytes)
        assert not self._write_closed
        if not data:
            return
        self._buffer.append(data)
        # We could optimize, but the callback can do this for now.

    def half_close(self):
        self._write_closed = True
        # Just set the flag.  Calling shutdown() on the ssl socket
        # breaks something, causing recv() to return binary data.


def make_connection(protocol, host, port=None, af=0, socktype=0, proto=0,
                    use_ssl=None):
    # TODO: Pass in a protocol factory, not a protocol.
    # What should be the exact sequence of events?
    #   - socket
    #   - transport
    #   - protocol
    #   - tell transport about protocol
    #   - tell protocol about transport
    # Or should the latter two be reversed?  Does it matter?
    if port is None:
        port = 443 if use_ssl else 80
    if use_ssl is None:
        use_ssl = (port == 443)
    if not socktype:
        socktype = socket.SOCK_STREAM
    eventloop = polling.context.eventloop

    def on_socket_connected(task):
        assert not task.alive
        if task.exception is not None:
            # TODO: Call some callback.
            raise task.exception
        sock = task.result
        assert sock is not None
        logging.debug('on_socket_connected')
        if use_ssl:
            # You can pass an ssl.SSLContext object as use_ssl,
            # or a bool.
            if isinstance(use_ssl, bool):
                sslcontext = None
            else:
                sslcontext = use_ssl
            transport = UnixSslTransport(eventloop, protocol, sock, sslcontext)
        else:
            transport = UnixSocketTransport(eventloop, protocol, sock)
            # TODO: Should the ransport make the following calls?
            protocol.connection_made(transport)  # XXX call_soon()?
            # Don't do this before connection_made() is called.
            eventloop.add_reader(sock.fileno(), transport._on_readable)

    coro = sockets.create_connection(host, port, af, socktype, proto)
    task = scheduling.Task(coro)
    task.add_done_callback(on_socket_connected)


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

    host = 'xkcd.com'
    if sys.argv[1:] and '.' in sys.argv[-1]:
        host = sys.argv[-1]

    t0 = time.time()

    class TestProtocol(Protocol):
        def connection_made(self, transport):
            logging.info('Connection made at %.3f secs', time.time() - t0)
            self.transport = transport
            self.transport.write(b'GET / HTTP/1.0\r\nHost: ' +
                                 host.encode('ascii') +
                                 b'\r\n\r\n')
            self.transport.half_close()
        def data_received(self, data):
            logging.info('Received %d bytes at t=%.3f',
                         len(data), time.time() - t0)
            logging.debug('Received %r', data)
        def connection_lost(self, exc):
            logging.debug('Connection lost: %r', exc)
            self.t1 = time.time()
            logging.info('Total time %.3f secs', self.t1 - t0)

    tp = TestProtocol()
    logging.debug('tp = %r', tp)
    make_connection(tp, host, use_ssl=('-S' in sys.argv))
    logging.info('Running...')
    polling.context.eventloop.run()
    logging.info('Done.')


if __name__ == '__main__':
    main()
