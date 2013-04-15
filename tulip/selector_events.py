"""Event loop using a selector and related classes.

A selector is a "notify-when-ready" multiplexer.  For a subclass which
also includes support for signal handling, see the unix_events sub-module.
"""

import collections
import errno
import socket
try:
    import ssl
except ImportError:  # pragma: no cover
    ssl = None

from . import base_events
from . import constants
from . import events
from . import futures
from . import selectors
from . import transports
from .log import tulip_log


# Errno values indicating the connection was disconnected.
# Comment out _DISCONNECTED as never used
# TODO: make sure that errors has processed properly
# for now we have no exception clsses for ENOTCONN and EBADF
# _DISCONNECTED = frozenset((errno.ECONNRESET,
#                            errno.ENOTCONN,
#                            errno.ESHUTDOWN,
#                            errno.ECONNABORTED,
#                            errno.EPIPE,
#                            errno.EBADF,
#                            ))


class BaseSelectorEventLoop(base_events.BaseEventLoop):
    """Selector event loop.

    See events.EventLoop for API specification.
    """

    def __init__(self, selector=None):
        super().__init__()

        if selector is None:
            selector = selectors.Selector()
        tulip_log.debug('Using selector: %s', selector.__class__.__name__)
        self._selector = selector
        self._make_self_pipe()

    def _make_socket_transport(self, sock, protocol, waiter=None, *,
                               extra=None):
        return _SelectorSocketTransport(self, sock, protocol, waiter, extra)

    def _make_ssl_transport(self, rawsock, protocol, sslcontext, waiter, *,
                            server_side=False, extra=None):
        return _SelectorSslTransport(
            self, rawsock, protocol, sslcontext, waiter, server_side, extra)

    def _make_datagram_transport(self, sock, protocol,
                                 address=None, extra=None):
        return _SelectorDatagramTransport(self, sock, protocol, address, extra)

    def close(self):
        if self._selector is not None:
            self._close_self_pipe()
            self._selector.close()
            self._selector = None

    def _socketpair(self):
        raise NotImplementedError

    def _close_self_pipe(self):
        self.remove_reader(self._ssock.fileno())
        self._ssock.close()
        self._ssock = None
        self._csock.close()
        self._csock = None
        self._internal_fds -= 1

    def _make_self_pipe(self):
        # A self-socket, really. :-)
        self._ssock, self._csock = self._socketpair()
        self._ssock.setblocking(False)
        self._csock.setblocking(False)
        self._internal_fds += 1
        self.add_reader(self._ssock.fileno(), self._read_from_self)

    def _read_from_self(self):
        try:
            self._ssock.recv(1)
        except (BlockingIOError, InterruptedError):
            pass

    def _write_to_self(self):
        try:
            self._csock.send(b'x')
        except (BlockingIOError, InterruptedError):
            pass

    def _start_serving(self, protocol_factory, sock, ssl=False):
        self.add_reader(sock.fileno(), self._accept_connection,
                        protocol_factory, sock, ssl)

    def _accept_connection(self, protocol_factory, sock, ssl=False):
        try:
            conn, addr = sock.accept()
            conn.setblocking(False)
        except (BlockingIOError, InterruptedError):
            pass  # False alarm.
        except:
            # Bad error. Stop serving.
            self.remove_reader(sock.fileno())
            sock.close()
            # There's nowhere to send the error, so just log it.
            # TODO: Someone will want an error handler for this.
            tulip_log.exception('Accept failed')
        else:
            if ssl:
                sslcontext = None if isinstance(ssl, bool) else ssl
                self._make_ssl_transport(
                    conn, protocol_factory(), sslcontext, None,
                    server_side=True, extra={'addr': addr})
            else:
                self._make_socket_transport(
                    conn, protocol_factory(), extra={'addr': addr})
        # It's now up to the protocol to handle the connection.

    def add_reader(self, fd, callback, *args):
        """Add a reader callback.  Return a Handle instance."""
        handle = events.make_handle(callback, args)
        try:
            mask, (reader, writer) = self._selector.get_info(fd)
        except KeyError:
            self._selector.register(fd, selectors.EVENT_READ,
                                    (handle, None))
        else:
            self._selector.modify(fd, mask | selectors.EVENT_READ,
                                  (handle, writer))
            if reader is not None:
                reader.cancel()

        return handle

    def remove_reader(self, fd):
        """Remove a reader callback."""
        try:
            mask, (reader, writer) = self._selector.get_info(fd)
        except KeyError:
            return False
        else:
            mask &= ~selectors.EVENT_READ
            if not mask:
                self._selector.unregister(fd)
            else:
                self._selector.modify(fd, mask, (None, writer))

            if reader is not None:
                reader.cancel()
                return True
            else:
                return False

    def add_writer(self, fd, callback, *args):
        """Add a writer callback.  Return a Handle instance."""
        handle = events.make_handle(callback, args)
        try:
            mask, (reader, writer) = self._selector.get_info(fd)
        except KeyError:
            self._selector.register(fd, selectors.EVENT_WRITE,
                                    (None, handle))
        else:
            self._selector.modify(fd, mask | selectors.EVENT_WRITE,
                                  (reader, handle))
            if writer is not None:
                writer.cancel()

        return handle

    def remove_writer(self, fd):
        """Remove a writer callback."""
        try:
            mask, (reader, writer) = self._selector.get_info(fd)
        except KeyError:
            return False
        else:
            # Remove both writer and connector.
            mask &= ~selectors.EVENT_WRITE
            if not mask:
                self._selector.unregister(fd)
            else:
                self._selector.modify(fd, mask, (reader, None))

            if writer is not None:
                writer.cancel()
                return True
            else:
                return False

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
        except (BlockingIOError, InterruptedError):
            self.add_reader(fd, self._sock_recv, fut, True, sock, n)
        except Exception as exc:
            fut.set_exception(exc)

    def sock_sendall(self, sock, data):
        """XXX"""
        fut = futures.Future()
        if data:
            self._sock_sendall(fut, False, sock, data)
        else:
            fut.set_result(None)
        return fut

    def _sock_sendall(self, fut, registered, sock, data):
        fd = sock.fileno()

        if registered:
            self.remove_writer(fd)
        if fut.cancelled():
            return

        try:
            n = sock.send(data)
        except (BlockingIOError, InterruptedError):
            n = 0
        except Exception as exc:
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
            self.remove_writer(fd)
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
        except (BlockingIOError, InterruptedError):
            self.add_writer(fd, self._sock_connect, fut, True, sock, address)
        except Exception as exc:
            fut.set_exception(exc)

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
        except (BlockingIOError, InterruptedError):
            self.add_reader(fd, self._sock_accept, fut, True, sock)
        except Exception as exc:
            fut.set_exception(exc)

    def _process_events(self, event_list):
        for fileobj, mask, (reader, writer) in event_list:
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

    def stop_serving(self, sock):
        self.remove_reader(sock.fileno())
        sock.close()


class _SelectorSocketTransport(transports.Transport):

    def __init__(self, event_loop, sock, protocol, waiter=None, extra=None):
        super().__init__(extra)
        self._extra['socket'] = sock
        self._event_loop = event_loop
        self._sock = sock
        self._protocol = protocol
        self._buffer = []
        self._conn_lost = 0
        self._closing = False  # Set when close() called.
        self._event_loop.add_reader(self._sock.fileno(), self._read_ready)
        self._event_loop.call_soon(self._protocol.connection_made, self)
        if waiter is not None:
            self._event_loop.call_soon(waiter.set_result, None)

    def _read_ready(self):
        try:
            data = self._sock.recv(16*1024)
        except (BlockingIOError, InterruptedError):
            pass
        except Exception as exc:
            self._fatal_error(exc)
        else:
            if data:
                self._protocol.data_received(data)
            else:
                self._event_loop.remove_reader(self._sock.fileno())
                self._protocol.eof_received()

    def write(self, data):
        assert isinstance(data, (bytes, bytearray)), repr(data)
        assert not self._closing
        if not data:
            return

        if self._conn_lost:
            if self._conn_lost >= constants.LOG_THRESHOLD_FOR_CONNLOST_WRITES:
                tulip_log.warning('socket.send() raised exception.')
            self._conn_lost += 1
            return

        if not self._buffer:
            # Attempt to send it right away first.
            try:
                n = self._sock.send(data)
            except (BlockingIOError, InterruptedError):
                n = 0
            except socket.error as exc:
                self._conn_lost += 1
                self._fatal_error(exc)
                return

            if n == len(data):
                return
            elif n:
                data = data[n:]
            self._event_loop.add_writer(self._sock.fileno(), self._write_ready)

        self._buffer.append(data)

    def _write_ready(self):
        data = b''.join(self._buffer)
        assert data, "Data should not be empty"

        self._buffer.clear()
        try:
            n = self._sock.send(data)
        except (BlockingIOError, InterruptedError):
            self._buffer.append(data)
        except Exception as exc:
            self._conn_lost += 1
            self._fatal_error(exc)
        else:
            if n == len(data):
                self._event_loop.remove_writer(self._sock.fileno())
                if self._closing:
                    self._call_connection_lost(None)
                return
            elif n:
                data = data[n:]

            self._buffer.append(data)  # Try again later.

    # TODO: write_eof(), can_write_eof().

    def abort(self):
        self._close(None)

    def close(self):
        self._closing = True
        self._event_loop.remove_reader(self._sock.fileno())
        if not self._buffer:
            self._event_loop.call_soon(self._call_connection_lost, None)

    def _fatal_error(self, exc):
        # should be called from exception handler only
        tulip_log.exception('Fatal error for %s', self)
        self._close(exc)

    def _close(self, exc):
        self._event_loop.remove_writer(self._sock.fileno())
        self._event_loop.remove_reader(self._sock.fileno())
        self._buffer.clear()
        self._event_loop.call_soon(self._call_connection_lost, exc)

    def _call_connection_lost(self, exc):
        try:
            self._protocol.connection_lost(exc)
        finally:
            self._sock.close()


class _SelectorSslTransport(transports.Transport):

    def __init__(self, event_loop, rawsock, protocol, sslcontext, waiter=None,
                 server_side=False, extra=None):
        super().__init__(extra)

        self._event_loop = event_loop
        self._rawsock = rawsock
        self._protocol = protocol
        sslcontext = sslcontext or ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        self._sslcontext = sslcontext
        self._waiter = waiter
        sslsock = sslcontext.wrap_socket(rawsock, server_side=server_side,
                                         do_handshake_on_connect=False)
        self._sslsock = sslsock
        self._buffer = []
        self._conn_lost = 0
        self._closing = False  # Set when close() called.
        self._extra['socket'] = sslsock

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
            if self._waiter is not None:
                self._waiter.set_exception(exc)
            return
        except BaseException as exc:
            self._sslsock.close()
            if self._waiter is not None:
                self._waiter.set_exception(exc)
            raise
        self._event_loop.remove_reader(fd)
        self._event_loop.remove_writer(fd)
        self._event_loop.add_reader(fd, self._on_ready)
        self._event_loop.add_writer(fd, self._on_ready)
        self._event_loop.call_soon(self._protocol.connection_made, self)
        if self._waiter is not None:
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
        except (BlockingIOError, InterruptedError):
            pass
        except Exception as exc:
            self._fatal_error(exc)
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
            n = 0
        except ssl.SSLWantWriteError:
            n = 0
        except (BlockingIOError, InterruptedError):
            n = 0
        except Exception as exc:
            self._conn_lost += 1
            self._fatal_error(exc)
            return

        if n < len(data):
            self._buffer.append(data[n:])
        elif self._closing:
            self._event_loop.remove_writer(self._sslsock.fileno())
            self._sslsock.close()
            self._protocol.connection_lost(None)

    def write(self, data):
        assert isinstance(data, bytes), repr(data)
        assert not self._closing
        if not data:
            return

        if self._conn_lost:
            if self._conn_lost >= constants.LOG_THRESHOLD_FOR_CONNLOST_WRITES:
                tulip_log.warning('socket.send() raised exception.')
            self._conn_lost += 1
            return

        self._buffer.append(data)
        # We could optimize, but the callback can do this for now.

    # TODO: write_eof(), can_write_eof().

    def abort(self):
        self._close(None)

    def close(self):
        self._closing = True
        self._event_loop.remove_reader(self._sslsock.fileno())
        if not self._buffer:
            self._event_loop.call_soon(self._protocol.connection_lost, None)

    def _fatal_error(self, exc):
        tulip_log.exception('Fatal error for %s', self)
        self._close(exc)

    def _close(self, exc):
        self._event_loop.remove_writer(self._sslsock.fileno())
        self._event_loop.remove_reader(self._sslsock.fileno())
        self._buffer = []
        self._event_loop.call_soon(self._protocol.connection_lost, exc)


class _SelectorDatagramTransport(transports.DatagramTransport):

    max_size = 256 * 1024  # max bytes we read in one eventloop iteration

    def __init__(self, event_loop, sock, protocol, address=None, extra=None):
        super().__init__(extra)
        self._extra['socket'] = sock
        self._event_loop = event_loop
        self._sock = sock
        self._fileno = sock.fileno()
        self._protocol = protocol
        self._address = address
        self._buffer = collections.deque()
        self._conn_lost = 0
        self._closing = False  # Set when close() called.
        self._event_loop.add_reader(self._fileno, self._read_ready)
        self._event_loop.call_soon(self._protocol.connection_made, self)

    def _read_ready(self):
        try:
            data, addr = self._sock.recvfrom(self.max_size)
        except (BlockingIOError, InterruptedError):
            pass
        except Exception as exc:
            self._fatal_error(exc)
        else:
            self._protocol.datagram_received(data, addr)

    def sendto(self, data, addr=None):
        assert isinstance(data, bytes)
        assert not self._closing
        if not data:
            return

        if self._address:
            assert addr in (None, self._address)

        if self._conn_lost and self._address:
            if self._conn_lost >= constants.LOG_THRESHOLD_FOR_CONNLOST_WRITES:
                tulip_log.warning('socket.send() raised exception.')
            self._conn_lost += 1
            return

        if not self._buffer:
            # Attempt to send it right away first.
            try:
                if self._address:
                    self._sock.send(data)
                else:
                    self._sock.sendto(data, addr)
                return
            except ConnectionRefusedError as exc:
                if self._address:
                    self._conn_lost += 1
                    self._fatal_error(exc)
                return
            except (BlockingIOError, InterruptedError):
                self._event_loop.add_writer(self._fileno, self._sendto_ready)
            except Exception as exc:
                self._conn_lost += 1
                self._fatal_error(exc)
                return

        self._buffer.append((data, addr))

    def _sendto_ready(self):
        while self._buffer:
            data, addr = self._buffer.popleft()
            try:
                if self._address:
                    self._sock.send(data)
                else:
                    self._sock.sendto(data, addr)
            except ConnectionRefusedError as exc:
                if self._address:
                    self._conn_lost += 1
                    self._fatal_error(exc)
                return
            except (BlockingIOError, InterruptedError):
                self._buffer.appendleft((data, addr))  # Try again later.
                break
            except Exception as exc:
                self._conn_lost += 1
                self._fatal_error(exc)
                return

        if not self._buffer:
            self._event_loop.remove_writer(self._fileno)
            if self._closing:
                self._call_connection_lost(None)

    def abort(self):
        self._close(None)

    def close(self):
        self._closing = True
        self._event_loop.remove_reader(self._fileno)
        if not self._buffer:
            self._event_loop.call_soon(self._call_connection_lost, None)

    def _fatal_error(self, exc):
        tulip_log.exception('Fatal error for %s', self)
        self._close(exc)

    def _close(self, exc):
        self._buffer.clear()
        self._event_loop.remove_writer(self._fileno)
        self._event_loop.remove_reader(self._fileno)
        if self._address and isinstance(exc, ConnectionRefusedError):
            self._protocol.connection_refused(exc)
        self._event_loop.call_soon(self._call_connection_lost, exc)

    def _call_connection_lost(self, exc):
        try:
            self._protocol.connection_lost(exc)
        finally:
            self._sock.close()
