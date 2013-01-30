"""UNIX event loop and related classes.

The event loop can be broken up into a selector (the part responsible
for telling us when file descriptors are ready) and the event loop
proper, which wraps a selector with functionality for scheduling
callbacks, immediately or at a given time in the future.

Whenever a public API takes a callback, subsequent positional
arguments will be passed to the callback if/when it is called.  This
avoids the proliferation of trivial lambdas implementing closures.
Keyword arguments for the callback are not supported; this is a
conscious design decision, leaving the door open for keyword arguments
to modify the meaning of the API call itself.
"""

import errno
import logging
import socket
try:
    import ssl
except ImportError:
    ssl = None
import sys

from . import base_events
from . import events
from . import futures
from . import selectors
from . import transports

try:
    from socket import socketpair
except ImportError:
    assert sys.platform == 'win32'
    from .winsocketpair import socketpair

# Errno values indicating the connection was disconnected.
_DISCONNECTED = frozenset((errno.ECONNRESET,
                           errno.ENOTCONN,
                           errno.ESHUTDOWN,
                           errno.ECONNABORTED,
                           errno.EPIPE,
                           errno.EBADF,
                           ))

# Errno values indicating the socket isn't ready for I/O just yet.
_TRYAGAIN = frozenset((errno.EAGAIN, errno.EWOULDBLOCK, errno.EINPROGRESS))
if sys.platform == 'win32':
    _TRYAGAIN = frozenset(list(_TRYAGAIN) + [errno.WSAEWOULDBLOCK])


class SelectorEventLoop(base_events.BaseEventLoop):
    """Selector event loop.

    See events.EventLoop for API specification.
    """

    @staticmethod
    def SocketTransport(event_loop, sock, protocol, waiter=None):
        return _SelectorSocketTransport(event_loop, sock, protocol, waiter)

    @staticmethod
    def SslTransport(event_loop, rawsock, protocol, sslcontext, waiter):
        return _SelectorSslTransport(event_loop, rawsock, protocol,
                                     sslcontext, waiter)

    def __init__(self, selector=None):
        super().__init__()
        if selector is None:
            # pick the best selector class for the platform
            selector = selectors.Selector()
            logging.debug('Using selector: %s', selector.__class__.__name__)
        self._selector = selector
        self._make_self_pipe()

    def close(self):
        if self._selector is not None:
            self._selector.close()
            self._selector = None
        self._ssock.close()
        self._csock.close()

    def _make_self_pipe(self):
        # A self-socket, really. :-)
        self._ssock, self._csock = socketpair()
        self._ssock.setblocking(False)
        self._csock.setblocking(False)
        self.add_reader(self._ssock.fileno(), self._read_from_self)

    def _read_from_self(self):
        try:
            self._ssock.recv(1)
        except socket.error as exc:
            if exc.errno in _TRYAGAIN:
                return
            raise  # Halp!

    def _write_to_self(self):
        try:
            self._csock.send(b'x')
        except socket.error as exc:
            if exc.errno in _TRYAGAIN:
                return
            raise  # Halp!

    def _start_serving(self, protocol_factory, sock):
        self.add_reader(sock.fileno(), self._accept_connection,
                        protocol_factory, sock)

    def _accept_connection(self, protocol_factory, sock):
        try:
            conn, addr = sock.accept()
        except socket.error as exc:
            if exc.errno in _TRYAGAIN:
                return  # False alarm.
            # Bad error.  Stop serving.
            self.remove_reader(sock.fileno())
            sock.close()
            # There's nowhere to send the error, so just log it.
            # TODO: Someone will want an error handler for this.
            logging.exception('Accept failed')
            return
        protocol = protocol_factory()
        transport = self.SocketTransport(self, conn, protocol)
        # It's now up to the protocol to handle the connection.

    def add_reader(self, fd, callback, *args):
        """Add a reader callback.  Return a Handler instance."""
        handler = events.make_handler(None, callback, args)
        try:
            mask, (reader, writer, connector) = self._selector.get_info(fd)
        except KeyError:
            self._selector.register(fd, selectors.EVENT_READ,
                                    (handler, None, None))
        else:
            self._selector.modify(fd, mask | selectors.EVENT_READ,
                                  (handler, writer, connector))

        return handler

    def remove_reader(self, fd):
        """Remove a reader callback."""
        try:
            mask, (reader, writer, connector) = self._selector.get_info(fd)
        except KeyError:
            return False
        else:
            mask &= ~selectors.EVENT_READ
            if not mask:
                self._selector.unregister(fd)
            else:
                self._selector.modify(fd, mask, (None, writer, connector))
            if reader is not None:
                reader.cancel()
            return True

    def add_writer(self, fd, callback, *args):
        """Add a writer callback.  Return a Handler instance."""
        handler = events.make_handler(None, callback, args)
        try:
            mask, (reader, writer, connector) = self._selector.get_info(fd)
        except KeyError:
            self._selector.register(fd, selectors.EVENT_WRITE,
                                    (None, handler, None))
        else:
            # Remove connector.
            mask &= ~selectors.EVENT_CONNECT
            self._selector.modify(fd, mask | selectors.EVENT_WRITE,
                                  (reader, handler, None))
        return handler

    def remove_writer(self, fd):
        """Remove a writer callback."""
        try:
            mask, (reader, writer, connector) = self._selector.get_info(fd)
        except KeyError:
            return False
        else:
            # Remove both writer and connector.
            mask &= ~(selectors.EVENT_WRITE | selectors.EVENT_CONNECT)
            if not mask:
                self._selector.unregister(fd)
            else:
                self._selector.modify(fd, mask, (reader, None, None))
            if writer is not None:
                writer.cancel()
            if connector is not None:
                connector.cancel()
            return True

    # NOTE: add_connector() and add_writer() are mutually exclusive.
    # While you can independently manipulate readers and writers,
    # adding a connector for a particular FD automatically removes the
    # writer for that FD, and vice versa, and removing a writer or a
    # connector actually removes both writer and connector.  This is
    # because in most cases writers and connectors use the same mode
    # for the platform polling function; the distinction is only
    # important for PollSelector() on Windows.

    def add_connector(self, fd, callback, *args):
        """Add a connector callback.  Return a Handler instance."""
        handler = events.make_handler(None, callback, args)
        try:
            mask, (reader, writer, connector) = self._selector.get_info(fd)
        except KeyError:
            self._selector.register(fd, selectors.EVENT_CONNECT,
                                    (None, None, handler))
        else:
            # Remove writer.
            mask &= ~selectors.EVENT_WRITE
            self._selector.modify(fd, mask | selectors.EVENT_CONNECT,
                                  (reader, None, handler))
        return handler

    def remove_connector(self, fd):
        """Remove a connector callback."""
        try:
            mask, (reader, writer, connector) = self._selector.get_info(fd)
        except KeyError:
            return False
        else:
            # Remove both writer and connector.
            mask &= ~(selectors.EVENT_WRITE | selectors.EVENT_CONNECT)
            if not mask:
                self._selector.unregister(fd)
            else:
                self._selector.modify(fd, mask, (reader, None, None))
            if writer is not None:
                writer.cancel()
            if connector is not None:
                connector.cancel()
            return True

    def sock_recv(self, sock, n):
        """XXX"""
        fut = futures.Future()
        self._sock_recv(fut, False, sock, n)
        return fut

    def _sock_recv(self, fut, registered, sock, n):
        fd = sock.fileno()
        if registered:
            # Remove the callback early.  It should be rare that the
            # selector says the fd is ready but the call still returns
            # EAGAIN, and I am willing to take a hit in that case in
            # order to simplify the common case.
            self.remove_reader(fd)
        if fut.cancelled():
            return
        try:
            data = sock.recv(n)
            fut.set_result(data)
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                fut.set_exception(exc)
            else:
                self.add_reader(fd, self._sock_recv, fut, True, sock, n)

    def sock_sendall(self, sock, data):
        """XXX"""
        fut = futures.Future()
        self._sock_sendall(fut, False, sock, data)
        return fut

    def _sock_sendall(self, fut, registered, sock, data):
        fd = sock.fileno()
        if registered:
            self.remove_writer(fd)
        if fut.cancelled():
            return
        n = 0
        try:
            if data:
                n = sock.send(data)
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                fut.set_exception(exc)
                return
        if n == len(data):
            fut.set_result(None)
        else:
            if n:
                data = data[n:]
            self.add_writer(fd, self._sock_sendall, fut, True, sock, data)

    def sock_connect(self, sock, address):
        """XXX"""
        # That address better not require a lookup!  We're not calling
        # self.getaddrinfo() for you here.  But verifying this is
        # complicated; the socket module doesn't have a pattern for
        # IPv6 addresses (there are too many forms, apparently).
        fut = futures.Future()
        self._sock_connect(fut, False, sock, address)
        return fut

    def _sock_connect(self, fut, registered, sock, address):
        fd = sock.fileno()
        if registered:
            self.remove_connector(fd)
        if fut.cancelled():
            return
        try:
            if not registered:
                # First time around.
                sock.connect(address)
            else:
                err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if err != 0:
                    # Jump to the except clause below.
                    raise socket.error(err, 'Connect call failed')
            fut.set_result(None)
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                fut.set_exception(exc)
            else:
                self.add_connector(fd, self._sock_connect,
                                   fut, True, sock, address)

    def sock_accept(self, sock):
        """XXX"""
        fut = futures.Future()
        self._sock_accept(fut, False, sock)
        return fut

    def _sock_accept(self, fut, registered, sock):
        fd = sock.fileno()
        if registered:
            self.remove_reader(fd)
        if fut.cancelled():
            return
        try:
            conn, address = sock.accept()
            conn.setblocking(False)
            fut.set_result((conn, address))
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                fut.set_exception(exc)
            else:
                self.add_reader(fd, self._sock_accept, fut, True, sock)

    def _process_events(self, event_list):
        for fileobj, mask, (reader, writer, connector) in event_list:
            if mask & selectors.EVENT_READ and reader is not None:
                if reader.cancelled:
                    self.remove_reader(fileobj)
                else:
                    self._add_callback(reader)
            if mask & selectors.EVENT_WRITE and writer is not None:
                if writer.cancelled:
                    self.remove_writer(fileobj)
                else:
                    self._add_callback(writer)
            elif mask & selectors.EVENT_CONNECT and connector is not None:
                if connector.cancelled:
                    self.remove_connector(fileobj)
                else:
                    self._add_callback(connector)


class _SelectorSocketTransport(transports.Transport):

    def __init__(self, event_loop, sock, protocol, waiter=None):
        self._event_loop = event_loop
        self._sock = sock
        self._protocol = protocol
        self._buffer = []
        self._closing = False  # Set when close() called.
        self._event_loop.add_reader(self._sock.fileno(), self._read_ready)
        self._event_loop.call_soon(self._protocol.connection_made, self)
        if waiter is not None:
            self._event_loop.call_soon(waiter.set_result, None)

    def _read_ready(self):
        try:
            data = self._sock.recv(16*1024)
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                self._fatal_error(exc)
        else:
            if data:
                self._event_loop.call_soon(self._protocol.data_received, data)
            else:
                self._event_loop.remove_reader(self._sock.fileno())
                self._event_loop.call_soon(self._protocol.eof_received)

    def write(self, data):
        assert isinstance(data, bytes)
        assert not self._closing
        if not data:
            return
        if not self._buffer:
            # Attempt to send it right away first.
            try:
                n = self._sock.send(data)
            except socket.error as exc:
                if exc.errno in _TRYAGAIN:
                    n = 0
                else:
                    self._fatal_error(exc)
                    return
            if n == len(data):
                return
            if n:
                data = data[n:]
            self._event_loop.add_writer(self._sock.fileno(), self._write_ready)
        self._buffer.append(data)

    def _write_ready(self):
        data = b''.join(self._buffer)
        self._buffer = []
        try:
            if data:
                n = self._sock.send(data)
            else:
                n = 0
        except socket.error as exc:
            if exc.errno in _TRYAGAIN:
                n = 0
            else:
                self._fatal_error(exc)
                return
        if n == len(data):
            self._event_loop.remove_writer(self._sock.fileno())
            if self._closing:
                self._event_loop.call_soon(self._call_connection_lost, None)
            return
        if n:
            data = data[n:]
        self._buffer.append(data)  # Try again later.

    # TODO: write_eof(), can_write_eof().

    def abort(self):
        self._fatal_error(None)

    def close(self):
        self._closing = True
        self._event_loop.remove_reader(self._sock.fileno())
        if not self._buffer:
            self._event_loop.call_soon(self._call_connection_lost, None)

    def _fatal_error(self, exc):
        logging.exception('Fatal error for %s', self)
        self._event_loop.remove_writer(self._sock.fileno())
        self._event_loop.remove_reader(self._sock.fileno())
        self._buffer = []
        self._event_loop.call_soon(self._call_connection_lost, exc)

    def _call_connection_lost(self, exc):
        try:
            self._protocol.connection_lost(exc)
        finally:
            self._sock.close()


class _SelectorSslTransport(transports.Transport):

    def __init__(self, event_loop, rawsock, protocol, sslcontext, waiter):
        self._event_loop = event_loop
        self._rawsock = rawsock
        self._protocol = protocol
        sslcontext = sslcontext or ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        self._sslcontext = sslcontext
        self._waiter = waiter
        sslsock = sslcontext.wrap_socket(rawsock,
                                         do_handshake_on_connect=False)
        self._sslsock = sslsock
        self._buffer = []
        self._closing = False  # Set when close() called.
        self._on_handshake()

    def _on_handshake(self):
        fd = self._sslsock.fileno()
        try:
            self._sslsock.do_handshake()
        except ssl.SSLWantReadError:
            self._event_loop.add_reader(fd, self._on_handshake)
            return
        except ssl.SSLWantWriteError:
            self._event_loop.add_writer(fd, self._on_handshake)
            return
        except Exception as exc:
            self._sslsock.close()
            self._waiter.set_exception(exc)
            return
        except BaseException as exc:
            self._sslsock.close()
            self._waiter.set_exception(exc)
            raise
        self._event_loop.remove_reader(fd)
        self._event_loop.remove_writer(fd)
        self._event_loop.add_reader(fd, self._on_ready)
        self._event_loop.add_writer(fd, self._on_ready)
        self._event_loop.call_soon(self._protocol.connection_made, self)
        self._event_loop.call_soon(self._waiter.set_result, None)

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
                self._fatal_error(exc)
                return
        else:
            if data:
                self._protocol.data_received(data)
            else:
                # TODO: Don't close when self._buffer is non-empty.
                assert not self._buffer
                self._event_loop.remove_reader(fd)
                self._event_loop.remove_writer(fd)
                self._sslsock.close()
                self._protocol.connection_lost(None)
                return

        # Now try writing, if there's anything to write.
        if not self._buffer:
            return

        data = b''.join(self._buffer)
        self._buffer = []
        try:
            n = self._sslsock.send(data)
        except ssl.SSLWantReadError:
            pass
        except ssl.SSLWantWriteError:
            pass
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                self._fatal_error(exc)
                return
        else:
            if n < len(data):
                self._buffer.append(data[n:])

    def write(self, data):
        assert isinstance(data, bytes)
        assert not self._closing
        if not data:
            return
        self._buffer.append(data)
        # We could optimize, but the callback can do this for now.

    # TODO: write_eof(), can_write_eof().

    def abort(self):
        self._fatal_error(None)

    def close(self):
        self._closing = True
        self._event_loop.remove_reader(self._sslsock.fileno())
        if not self._buffer:
            self._event_loop.call_soon(self._protocol.connection_lost, None)

    def _fatal_error(self, exc):
        logging.exception('Fatal error for %s', self)
        self._event_loop.remove_writer(self._sslsock.fileno())
        self._event_loop.remove_reader(self._sslsock.fileno())
        self._buffer = []
        self._event_loop.call_soon(self._protocol.connection_lost, exc)
