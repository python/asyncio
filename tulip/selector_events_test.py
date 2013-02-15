"""Tests for selector_events.py"""

import errno
import socket
import unittest
import unittest.mock
try:
    import ssl
except ImportError:
    ssl = None

from . import futures
from . import selectors
from .selector_events import BaseSelectorEventLoop
from .selector_events import _SelectorSslTransport
from .selector_events import _SelectorSocketTransport


class TestBaseSelectorEventLoop(BaseSelectorEventLoop):

    def _make_self_pipe(self):
        self._ssock = unittest.mock.Mock()
        self._csock = unittest.mock.Mock()


class BaseSelectorEventLoopTests(unittest.TestCase):

    def setUp(self):
        self.event_loop = TestBaseSelectorEventLoop(unittest.mock.Mock())

    def test_make_socket_transport(self):
        m = unittest.mock.Mock()
        self.event_loop.add_reader = unittest.mock.Mock()
        self.assertIsInstance(
            self.event_loop._make_socket_transport(m, m),
            _SelectorSocketTransport)

    def test_make_ssl_transport(self):
        m = unittest.mock.Mock()
        self.event_loop.add_reader = unittest.mock.Mock()
        self.event_loop.add_writer = unittest.mock.Mock()
        self.event_loop.remove_reader = unittest.mock.Mock()
        self.event_loop.remove_writer = unittest.mock.Mock()
        self.assertIsInstance(
            self.event_loop._make_ssl_transport(m, m, m, m),
            _SelectorSslTransport)

    def test_close(self):
        self.event_loop._selector.close()
        self.event_loop._selector = selector = unittest.mock.Mock()
        self.event_loop.close()
        self.assertIsNone(self.event_loop._selector)
        self.assertTrue(selector.close.called)
        self.assertTrue(self.event_loop._ssock.close.called)
        self.assertTrue(self.event_loop._csock.close.called)

    def test_close_no_selector(self):
        self.event_loop._selector.close()
        self.event_loop._selector = None
        self.event_loop.close()
        self.assertIsNone(self.event_loop._selector)
        self.assertTrue(self.event_loop._ssock.close.called)
        self.assertTrue(self.event_loop._csock.close.called)

    def test_socketpair(self):
        self.assertRaises(NotImplementedError, self.event_loop._socketpair)

    def test_read_from_self_tryagain(self):
        class Err(socket.error):
            errno = errno.EAGAIN

        self.event_loop._ssock.recv.side_effect = Err
        self.assertIsNone(self.event_loop._read_from_self())

    def test_read_from_self_exception(self):
        class Err(socket.error):
            pass

        self.event_loop._ssock.recv.side_effect = Err
        self.assertRaises(Err, self.event_loop._read_from_self)

    def test_write_to_self_tryagain(self):
        class Err(socket.error):
            errno = errno.EAGAIN

        self.event_loop._csock.send.side_effect = Err
        self.assertIsNone(self.event_loop._write_to_self())

    def test_write_to_self_exception(self):
        class Err(socket.error):
            pass

        self.event_loop._csock.send.side_effect = Err
        self.assertRaises(Err, self.event_loop._write_to_self)

    def test_sock_recv(self):
        sock = unittest.mock.Mock()
        self.event_loop._sock_recv = unittest.mock.Mock()

        f = self.event_loop.sock_recv(sock, 1024)
        self.assertIsInstance(f, futures.Future)
        self.assertEqual(
            (f, False, sock, 1024),
            self.event_loop._sock_recv.call_args[0])

    def test__sock_recv_canceled_fut(self):
        sock = unittest.mock.Mock()

        f = futures.Future()
        f.cancel()

        self.event_loop._sock_recv(f, False, sock, 1024)
        self.assertFalse(sock.recv.called)

    def test__sock_recv_unregister(self):
        sock = unittest.mock.Mock()
        sock.fileno.return_value = 10

        f = futures.Future()
        f.cancel()

        self.event_loop.remove_reader = unittest.mock.Mock()
        self.event_loop._sock_recv(f, True, sock, 1024)
        self.assertEqual((10,), self.event_loop.remove_reader.call_args[0])

    def test__sock_recv_tryagain(self):
        class Err(socket.error):
            errno = errno.EAGAIN

        f = futures.Future()
        sock = unittest.mock.Mock()
        sock.fileno.return_value = 10
        sock.recv.side_effect = Err

        self.event_loop.add_reader = unittest.mock.Mock()
        self.event_loop._sock_recv(f, False, sock, 1024)
        self.assertEqual((10, self.event_loop._sock_recv, f, True, sock, 1024),
                         self.event_loop.add_reader.call_args[0])

    def test__sock_recv_exception(self):
        class Err(socket.error):
            pass

        f = futures.Future()
        sock = unittest.mock.Mock()
        sock.fileno.return_value = 10
        sock.recv.side_effect = Err

        self.event_loop._sock_recv(f, False, sock, 1024)
        self.assertIsInstance(f.exception(), Err)

    def test_sock_sendall(self):
        sock = unittest.mock.Mock()
        self.event_loop._sock_sendall = unittest.mock.Mock()

        f = self.event_loop.sock_sendall(sock, b'data')
        self.assertIsInstance(f, futures.Future)
        self.assertEqual(
            (f, False, sock, b'data'),
            self.event_loop._sock_sendall.call_args[0])

    def test_sock_sendall_nodata(self):
        sock = unittest.mock.Mock()
        self.event_loop._sock_sendall = unittest.mock.Mock()

        f = self.event_loop.sock_sendall(sock, b'')
        self.assertIsInstance(f, futures.Future)
        self.assertTrue(f.done())
        self.assertFalse(self.event_loop._sock_sendall.called)

    def test__sock_sendall_canceled_fut(self):
        sock = unittest.mock.Mock()

        f = futures.Future()
        f.cancel()

        self.event_loop._sock_sendall(f, False, sock, b'data')
        self.assertFalse(sock.send.called)

    def test__sock_sendall_unregister(self):
        sock = unittest.mock.Mock()
        sock.fileno.return_value = 10

        f = futures.Future()
        f.cancel()

        self.event_loop.remove_writer = unittest.mock.Mock()
        self.event_loop._sock_sendall(f, True, sock, b'data')
        self.assertEqual((10,), self.event_loop.remove_writer.call_args[0])

    def test__sock_sendall_tryagain(self):
        class Err(socket.error):
            errno = errno.EAGAIN

        f = futures.Future()
        sock = unittest.mock.Mock()
        sock.fileno.return_value = 10
        sock.send.side_effect = Err

        self.event_loop.add_writer = unittest.mock.Mock()
        self.event_loop._sock_sendall(f, False, sock, b'data')
        self.assertEqual(
            (10, self.event_loop._sock_sendall, f, True, sock, b'data'),
            self.event_loop.add_writer.call_args[0])

    def test__sock_sendall_exception(self):
        class Err(socket.error):
            pass

        f = futures.Future()
        sock = unittest.mock.Mock()
        sock.fileno.return_value = 10
        sock.send.side_effect = Err

        self.event_loop._sock_sendall(f, False, sock, b'data')
        self.assertIsInstance(f.exception(), Err)

    def test__sock_sendall(self):
        sock = unittest.mock.Mock()

        f = futures.Future()
        sock.fileno.return_value = 10
        sock.send.return_value = 4

        self.event_loop._sock_sendall(f, False, sock, b'data')
        self.assertTrue(f.done())

    def test__sock_sendall_partial(self):
        sock = unittest.mock.Mock()

        f = futures.Future()
        sock.fileno.return_value = 10
        sock.send.return_value = 2

        self.event_loop.add_writer = unittest.mock.Mock()
        self.event_loop._sock_sendall(f, False, sock, b'data')
        self.assertFalse(f.done())
        self.assertEqual(
            (10, self.event_loop._sock_sendall, f, True, sock, b'ta'),
            self.event_loop.add_writer.call_args[0])

    def test__sock_sendall_none(self):
        sock = unittest.mock.Mock()

        f = futures.Future()
        sock.fileno.return_value = 10
        sock.send.return_value = 0

        self.event_loop.add_writer = unittest.mock.Mock()
        self.event_loop._sock_sendall(f, False, sock, b'data')
        self.assertFalse(f.done())
        self.assertEqual(
            (10, self.event_loop._sock_sendall, f, True, sock, b'data'),
            self.event_loop.add_writer.call_args[0])

    def test_sock_connect(self):
        sock = unittest.mock.Mock()
        self.event_loop._sock_connect = unittest.mock.Mock()

        f = self.event_loop.sock_connect(sock, ('127.0.0.1',8080))
        self.assertIsInstance(f, futures.Future)
        self.assertEqual(
            (f, False, sock, ('127.0.0.1',8080)),
            self.event_loop._sock_connect.call_args[0])

    def test__sock_connect(self):
        f = futures.Future()

        sock = unittest.mock.Mock()
        sock.fileno.return_value = 10

        self.event_loop._sock_connect(f, False, sock, ('127.0.0.1',8080))
        self.assertTrue(f.done())
        self.assertTrue(sock.connect.called)

    def test__sock_connect_canceled_fut(self):
        sock = unittest.mock.Mock()

        f = futures.Future()
        f.cancel()

        self.event_loop._sock_connect(f, False, sock, ('127.0.0.1',8080))
        self.assertFalse(sock.connect.called)

    def test__sock_connect_unregister(self):
        sock = unittest.mock.Mock()
        sock.fileno.return_value = 10

        f = futures.Future()
        f.cancel()

        self.event_loop.remove_writer = unittest.mock.Mock()
        self.event_loop._sock_connect(f, True, sock, ('127.0.0.1',8080))
        self.assertEqual((10,), self.event_loop.remove_writer.call_args[0])

    def test__sock_connect_tryagain(self):
        f = futures.Future()
        sock = unittest.mock.Mock()
        sock.fileno.return_value = 10
        sock.getsockopt.return_value = errno.EAGAIN

        self.event_loop.add_writer = unittest.mock.Mock()
        self.event_loop.remove_writer = unittest.mock.Mock()

        self.event_loop._sock_connect(f, True, sock, ('127.0.0.1',8080))
        self.assertEqual(
            (10, self.event_loop._sock_connect, f,
             True, sock, ('127.0.0.1',8080)),
            self.event_loop.add_writer.call_args[0])

    def test__sock_connect_exception(self):
        f = futures.Future()
        sock = unittest.mock.Mock()
        sock.fileno.return_value = 10
        sock.getsockopt.return_value = errno.ENOTCONN

        self.event_loop.remove_writer = unittest.mock.Mock()
        self.event_loop._sock_connect(f, True, sock, ('127.0.0.1',8080))
        self.assertIsInstance(f.exception(), socket.error)

    def test_sock_accept(self):
        sock = unittest.mock.Mock()
        self.event_loop._sock_accept = unittest.mock.Mock()

        f = self.event_loop.sock_accept(sock)
        self.assertIsInstance(f, futures.Future)
        self.assertEqual(
            (f, False, sock), self.event_loop._sock_accept.call_args[0])

    def test__sock_accept(self):
        f = futures.Future()

        conn = unittest.mock.Mock()

        sock = unittest.mock.Mock()
        sock.fileno.return_value = 10
        sock.accept.return_value = conn, ('127.0.0.1', 1000)

        self.event_loop._sock_accept(f, False, sock)
        self.assertTrue(f.done())
        self.assertEqual((conn, ('127.0.0.1', 1000)), f.result())
        self.assertEqual((False,), conn.setblocking.call_args[0])

    def test__sock_accept_canceled_fut(self):
        sock = unittest.mock.Mock()

        f = futures.Future()
        f.cancel()

        self.event_loop._sock_accept(f, False, sock)
        self.assertFalse(sock.accept.called)

    def test__sock_accept_unregister(self):
        sock = unittest.mock.Mock()
        sock.fileno.return_value = 10

        f = futures.Future()
        f.cancel()

        self.event_loop.remove_reader = unittest.mock.Mock()
        self.event_loop._sock_accept(f, True, sock)
        self.assertEqual((10,), self.event_loop.remove_reader.call_args[0])

    def test__sock_accept_tryagain(self):
        class Err(socket.error):
            errno = errno.EAGAIN

        f = futures.Future()
        sock = unittest.mock.Mock()
        sock.fileno.return_value = 10
        sock.accept.side_effect = Err

        self.event_loop.add_reader = unittest.mock.Mock()
        self.event_loop._sock_accept(f, False, sock)
        self.assertEqual(
            (10, self.event_loop._sock_accept, f, True, sock),
            self.event_loop.add_reader.call_args[0])

    def test__sock_accept_exception(self):
        class Err(socket.error):
            pass

        f = futures.Future()
        sock = unittest.mock.Mock()
        sock.fileno.return_value = 10
        sock.accept.side_effect = Err

        self.event_loop._sock_accept(f, False, sock)
        self.assertIsInstance(f.exception(), Err)

    def test_add_reader(self):
        self.event_loop._selector.get_info.side_effect = KeyError
        h = self.event_loop.add_reader(1, lambda: True)

        self.assertTrue(self.event_loop._selector.register.called)
        self.assertEqual(
            (1, selectors.EVENT_READ, (h, None)),
            self.event_loop._selector.register.call_args[0])

    def test_add_reader_existing(self):
        reader = unittest.mock.Mock()
        writer = unittest.mock.Mock()
        self.event_loop._selector.get_info.return_value = (
            selectors.EVENT_WRITE, (reader, writer))
        h = self.event_loop.add_reader(1, lambda: True)

        self.assertTrue(reader.cancel.called)
        self.assertFalse(self.event_loop._selector.register.called)
        self.assertTrue(self.event_loop._selector.modify.called)
        self.assertEqual(
            (1, selectors.EVENT_WRITE | selectors.EVENT_READ, (h, writer)),
            self.event_loop._selector.modify.call_args[0])

    def test_add_reader_existing_writer(self):
        writer = unittest.mock.Mock()
        self.event_loop._selector.get_info.return_value = (
            selectors.EVENT_WRITE, (None, writer))
        h = self.event_loop.add_reader(1, lambda: True)

        self.assertFalse(self.event_loop._selector.register.called)
        self.assertTrue(self.event_loop._selector.modify.called)
        self.assertEqual(
            (1, selectors.EVENT_WRITE | selectors.EVENT_READ, (h, writer)),
            self.event_loop._selector.modify.call_args[0])

    def test_remove_reader(self):
        self.event_loop._selector.get_info.return_value = (
            selectors.EVENT_READ, (None, None))
        self.assertFalse(self.event_loop.remove_reader(1))

        self.assertTrue(self.event_loop._selector.unregister.called)

    def test_remove_reader_read_write(self):
        reader = unittest.mock.Mock()
        writer = unittest.mock.Mock()
        self.event_loop._selector.get_info.return_value = (
            selectors.EVENT_READ | selectors.EVENT_WRITE, (reader, writer))
        self.assertTrue(
            self.event_loop.remove_reader(1))

        self.assertFalse(self.event_loop._selector.unregister.called)
        self.assertEqual(
            (1, selectors.EVENT_WRITE, (None, writer)),
            self.event_loop._selector.modify.call_args[0])

    def test_remove_reader_unknown(self):
        self.event_loop._selector.get_info.side_effect = KeyError
        self.assertFalse(
            self.event_loop.remove_reader(1))

    def test_add_writer(self):
        self.event_loop._selector.get_info.side_effect = KeyError
        h = self.event_loop.add_writer(1, lambda: True)

        self.assertTrue(self.event_loop._selector.register.called)
        self.assertEqual(
            (1, selectors.EVENT_WRITE, (None, h)),
            self.event_loop._selector.register.call_args[0])

    def test_add_writer_existing(self):
        reader = unittest.mock.Mock()
        writer = unittest.mock.Mock()
        self.event_loop._selector.get_info.return_value = (
            selectors.EVENT_READ, (reader, writer))
        h = self.event_loop.add_writer(1, lambda: True)

        self.assertTrue(writer.cancel.called)
        self.assertFalse(self.event_loop._selector.register.called)
        self.assertTrue(self.event_loop._selector.modify.called)
        self.assertEqual(
            (1, selectors.EVENT_WRITE | selectors.EVENT_READ, (reader, h)),
            self.event_loop._selector.modify.call_args[0])

    def test_remove_writer(self):
        self.event_loop._selector.get_info.return_value = (
            selectors.EVENT_WRITE, (None, None))
        self.assertFalse(self.event_loop.remove_writer(1))

        self.assertTrue(self.event_loop._selector.unregister.called)

    def test_remove_writer_read_write(self):
        reader = unittest.mock.Mock()
        writer = unittest.mock.Mock()
        self.event_loop._selector.get_info.return_value = (
            selectors.EVENT_READ | selectors.EVENT_WRITE, (reader, writer))
        self.assertTrue(
            self.event_loop.remove_writer(1))

        self.assertFalse(self.event_loop._selector.unregister.called)
        self.assertEqual(
            (1, selectors.EVENT_READ, (reader, None)),
            self.event_loop._selector.modify.call_args[0])

    def test_remove_writer_unknown(self):
        self.event_loop._selector.get_info.side_effect = KeyError
        self.assertFalse(
            self.event_loop.remove_writer(1))

    def test_process_events_read(self):
        reader = unittest.mock.Mock()
        reader.cancelled = False

        self.event_loop._add_callback = unittest.mock.Mock()
        self.event_loop._process_events(
            ((1, selectors.EVENT_READ, (reader, None)),))
        self.assertTrue(self.event_loop._add_callback.called)
        self.assertEqual((reader,), self.event_loop._add_callback.call_args[0])

    def test_process_events_read_cancelled(self):
        reader = unittest.mock.Mock()
        reader.cancelled = True

        self.event_loop.remove_reader = unittest.mock.Mock()
        self.event_loop._process_events(
            ((1, selectors.EVENT_READ, (reader, None)),))
        self.assertTrue(self.event_loop.remove_reader.called)
        self.assertEqual((1,), self.event_loop.remove_reader.call_args[0])

    def test_process_events_write(self):
        writer = unittest.mock.Mock()
        writer.cancelled = False

        self.event_loop._add_callback = unittest.mock.Mock()
        self.event_loop._process_events(
            ((1, selectors.EVENT_WRITE, (None, writer)),))
        self.assertTrue(self.event_loop._add_callback.called)
        self.assertEqual((writer,), self.event_loop._add_callback.call_args[0])

    def test_process_events_write_cancelled(self):
        writer = unittest.mock.Mock()
        writer.cancelled = True

        self.event_loop.remove_writer = unittest.mock.Mock()
        self.event_loop._process_events(
            ((1, selectors.EVENT_WRITE, (None, writer)),))
        self.assertTrue(self.event_loop.remove_writer.called)
        self.assertEqual((1,), self.event_loop.remove_writer.call_args[0])


class SelectorSocketTransportTests(unittest.TestCase):

    def setUp(self):
        self.event_loop = unittest.mock.Mock()
        self.sock = unittest.mock.Mock()
        self.protocol = unittest.mock.Mock()

    def test_ctor(self):
        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        self.assertTrue(self.event_loop.add_reader.called)
        self.assertTrue(self.event_loop.call_soon.called)

    def test_ctor_with_waiter(self):
        fut = futures.Future()

        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol, fut)
        self.assertEqual(2, self.event_loop.call_soon.call_count)
        self.assertEqual(fut.set_result,
                         self.event_loop.call_soon.call_args[0][0])

    def test_read_ready(self):
        data_received = unittest.mock.Mock()
        self.protocol.data_received = data_received

        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)

        self.sock.recv.return_value = b'data'
        transport._read_ready()

        self.assertTrue(self.event_loop.call_soon.called)
        self.assertEqual(
            (data_received, b'data'),
            self.event_loop.call_soon.call_args[0])

    def test_read_ready_eof(self):
        eof_received = unittest.mock.Mock()
        self.protocol.eof_received = eof_received

        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)

        self.sock.recv.return_value = b''
        transport._read_ready()

        self.assertTrue(self.event_loop.remove_reader.called)
        self.assertEqual(
            (eof_received,), self.event_loop.call_soon.call_args[0])

    def test_read_ready_tryagain(self):
        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)

        class Err(socket.error):
            errno = errno.EAGAIN

        self.sock.recv.side_effect = Err
        transport._fatal_error = unittest.mock.Mock()
        transport._read_ready()

        self.assertFalse(transport._fatal_error.called)

    def test_read_ready_err(self):
        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)

        class Err(socket.error):
            pass

        self.sock.recv.side_effect = Err
        transport._fatal_error = unittest.mock.Mock()
        transport._read_ready()

        self.assertTrue(transport._fatal_error.called)
        self.assertIsInstance(transport._fatal_error.call_args[0][0], Err)

    def test_abort(self):
        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport._fatal_error = unittest.mock.Mock()

        transport.abort()
        self.assertTrue(transport._fatal_error.called)
        self.assertIsNone(transport._fatal_error.call_args[0][0])

    def test_write(self):
        data = b'data'
        self.sock.send.return_value = len(data)

        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport.write(data)
        self.assertTrue(self.sock.send.called)
        self.assertEqual(self.sock.send.call_args[0], (data,))

    def test_write_no_data(self):
        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport._buffer.append(b'data')
        transport.write(b'')
        self.assertFalse(self.sock.send.called)
        self.assertEqual([b'data'], transport._buffer)

    def test_write_buffer(self):
        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport._buffer.append(b'data1')
        transport.write(b'data2')
        self.assertFalse(self.sock.send.called)
        self.assertEqual([b'data1', b'data2'], transport._buffer)

    def test_write_partial(self):
        data = b'data'
        self.sock.send.return_value = 2

        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport.write(data)

        self.assertTrue(self.event_loop.add_writer.called)
        self.assertEqual(
            transport._write_ready, self.event_loop.add_writer.call_args[0][1])

        self.assertEqual([b'ta'], transport._buffer)

    def test_write_partial_none(self):
        data = b'data'
        self.sock.send.return_value = 0

        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport.write(data)

        self.assertTrue(self.event_loop.add_writer.called)
        self.assertEqual(
            transport._write_ready, self.event_loop.add_writer.call_args[0][1])

        self.assertEqual([b'data'], transport._buffer)

    def test_write_tryagain(self):
        data = b'data'

        class Err(socket.error):
            errno = errno.EAGAIN

        self.sock.send.side_effect = Err

        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport.write(data)

        self.assertTrue(self.event_loop.add_writer.called)
        self.assertEqual(
            transport._write_ready, self.event_loop.add_writer.call_args[0][1])

        self.assertEqual([b'data'], transport._buffer)

    def test_write_exception(self):
        data = b'data'

        class Err(socket.error):
            pass

        self.sock.send.side_effect = Err

        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport._fatal_error = unittest.mock.Mock()
        transport.write(data)

        self.assertTrue(transport._fatal_error.called)
        self.assertIsInstance(transport._fatal_error.call_args[0][0], Err)

    def test_write_str(self):
        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        self.assertRaises(AssertionError, transport.write, 'str')

    def test_write_closing(self):
        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport.close()
        self.assertRaises(AssertionError, transport.write, b'data')

    def test_write_ready(self):
        data = b'data'
        self.sock.send.return_value = len(data)

        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport._buffer.append(data)
        transport._write_ready()
        self.assertTrue(self.sock.send.called)
        self.assertEqual(self.sock.send.call_args[0], (data,))
        self.assertTrue(self.event_loop.remove_writer.called)

    def test_write_ready_closing(self):
        data = b'data'
        self.sock.send.return_value = len(data)

        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport._closing = True
        transport._buffer.append(data)
        transport._write_ready()
        self.assertTrue(self.sock.send.called)
        self.assertEqual(self.sock.send.call_args[0], (data,))
        self.assertTrue(self.event_loop.remove_writer.called)
        self.assertTrue(self.event_loop.call_soon.called)
        self.assertEqual(
            (transport._call_connection_lost, None),
            self.event_loop.call_soon.call_args[0])

    def test_write_ready_no_data(self):
        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport._write_ready()
        self.assertFalse(self.sock.send.called)
        self.assertTrue(self.event_loop.remove_writer.called)

    def test_write_ready_partial(self):
        data = b'data'
        self.sock.send.return_value = 2

        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport._buffer.append(data)
        transport._write_ready()
        self.assertFalse(self.event_loop.remove_writer.called)
        self.assertEqual([b'ta'], transport._buffer)

    def test_write_ready_partial_none(self):
        data = b'data'
        self.sock.send.return_value = 0

        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport._buffer.append(data)
        transport._write_ready()
        self.assertFalse(self.event_loop.remove_writer.called)
        self.assertEqual([b'data'], transport._buffer)

    def test_write_ready_tryagain(self):
        class Err(socket.error):
            errno = errno.EAGAIN

        self.sock.send.side_effect = Err

        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport._buffer = [b'data1', b'data2']
        transport._write_ready()

        self.assertFalse(self.event_loop.remove_writer.called)
        self.assertEqual([b'data1data2'], transport._buffer)

    def test_write_ready_exception(self):
        class Err(socket.error):
            pass

        self.sock.send.side_effect = Err

        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport._fatal_error = unittest.mock.Mock()
        transport._buffer.append(b'data')
        transport._write_ready()

        self.assertTrue(transport._fatal_error.called)
        self.assertIsInstance(transport._fatal_error.call_args[0][0], Err)

    def test_close(self):
        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        transport.close()

        self.assertTrue(transport._closing)
        self.assertTrue(self.event_loop.remove_reader.called)
        self.assertTrue(self.event_loop.call_soon.called)
        self.assertEqual(
            (transport._call_connection_lost, None),
            self.event_loop.call_soon.call_args[0])

    def test_close_write_buffer(self):
        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        self.event_loop.reset_mock()
        transport._buffer.append(b'data')
        transport.close()

        self.assertTrue(self.event_loop.remove_reader.called)
        self.assertFalse(self.event_loop.call_soon.called)

    def test_fatal_error(self):
        exc = object()
        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        self.event_loop.reset_mock()
        transport._buffer.append(b'data')
        transport._fatal_error(exc)

        self.assertEqual([], transport._buffer)
        self.assertTrue(self.event_loop.remove_writer.called)
        self.assertTrue(self.event_loop.remove_reader.called)
        self.assertTrue(self.event_loop.call_soon.called)
        self.assertEqual(
            (transport._call_connection_lost, exc),
            self.event_loop.call_soon.call_args[0])

    def test_connection_lost(self):
        exc = object()
        transport = _SelectorSocketTransport(
            self.event_loop, self.sock, self.protocol)
        self.sock.reset_mock()
        self.protocol.reset_mock()
        transport._call_connection_lost(exc)

        self.assertTrue(self.protocol.connection_lost.called)
        self.assertEqual(
            (exc,), self.protocol.connection_lost.call_args[0])
        self.assertTrue(self.sock.close.called)


@unittest.skipIf(ssl is None, 'No ssl module')
class SelectorSslTransportTests(unittest.TestCase):

    def setUp(self):
        self.event_loop = unittest.mock.Mock()
        self.sock = unittest.mock.Mock()
        self.protocol = unittest.mock.Mock()
        self.sslsock = unittest.mock.Mock()
        self.sslsock.fileno.return_value = 1
        self.sslcontext = unittest.mock.Mock()
        self.sslcontext.wrap_socket.return_value = self.sslsock
        self.waiter = futures.Future()

        self.transport = _SelectorSslTransport(
            self.event_loop, self.sock,
            self.protocol, self.sslcontext, self.waiter)
        self.event_loop.reset_mock()
        self.sock.reset_mock()
        self.protocol.reset_mock()
        self.sslcontext.reset_mock()

    def test_on_handshake(self):
        self.transport._on_handshake()
        self.assertTrue(self.sslsock.do_handshake.called)
        self.assertTrue(self.event_loop.remove_reader.called)
        self.assertTrue(self.event_loop.remove_writer.called)
        self.assertEqual(
            (1, self.transport._on_ready,),
            self.event_loop.add_reader.call_args[0])
        self.assertEqual(
            (1, self.transport._on_ready,),
            self.event_loop.add_writer.call_args[0])

    def test_on_handshake_reader_retry(self):
        self.sslsock.do_handshake.side_effect = ssl.SSLWantReadError
        self.transport._on_handshake()
        self.assertEqual(
            (1, self.transport._on_handshake,),
            self.event_loop.add_reader.call_args[0])

    def test_on_handshake_writer_retry(self):
        self.sslsock.do_handshake.side_effect = ssl.SSLWantWriteError
        self.transport._on_handshake()
        self.assertEqual(
            (1, self.transport._on_handshake,),
            self.event_loop.add_writer.call_args[0])

    def test_on_handshake_exc(self):
        self.sslsock.do_handshake.side_effect = ValueError
        self.transport._on_handshake()
        self.assertTrue(self.sslsock.close.called)

    def test_on_handshake_base_exc(self):
        self.sslsock.do_handshake.side_effect = BaseException
        self.assertRaises(BaseException, self.transport._on_handshake)
        self.assertTrue(self.sslsock.close.called)

    def test_write_no_data(self):
        self.transport._buffer.append(b'data')
        self.transport.write(b'')
        self.assertEqual([b'data'], self.transport._buffer)

    def test_write_str(self):
        self.assertRaises(AssertionError, self.transport.write, 'str')

    def test_write_closing(self):
        self.transport.close()
        self.assertRaises(AssertionError, self.transport.write, b'data')

    def test_abort(self):
        self.transport._fatal_error = unittest.mock.Mock()

        self.transport.abort()
        self.assertTrue(self.transport._fatal_error.called)
        self.assertEqual((None,), self.transport._fatal_error.call_args[0])

    def test_fatal_error(self):
        exc = object()
        self.transport._buffer.append(b'data')
        self.transport._fatal_error(exc)

        self.assertEqual([], self.transport._buffer)
        self.assertTrue(self.event_loop.remove_writer.called)
        self.assertTrue(self.event_loop.remove_reader.called)
        self.assertTrue(self.event_loop.call_soon.called)
        self.assertEqual(
            (self.protocol.connection_lost, exc),
            self.event_loop.call_soon.call_args[0])

    def test_close(self):
        self.transport.close()

        self.assertTrue(self.transport._closing)
        self.assertTrue(self.event_loop.remove_reader.called)
        self.assertTrue(self.event_loop.call_soon.called)
        self.assertEqual(
            (self.protocol.connection_lost, None),
            self.event_loop.call_soon.call_args[0])

    def test_close_write_buffer(self):
        self.transport._buffer.append(b'data')
        self.transport.close()

        self.assertTrue(self.event_loop.remove_reader.called)
        self.assertFalse(self.event_loop.call_soon.called)

    def test_on_ready_closed(self):
        self.sslsock.fileno.return_value = -1
        self.transport._on_ready()
        self.assertFalse(self.sslsock.recv.called)

    def test_on_ready_recv(self):
        self.sslsock.recv.return_value = b'data'
        self.transport._on_ready()
        self.assertTrue(self.sslsock.recv.called)
        self.assertEqual((b'data',), self.protocol.data_received.call_args[0])

    def test_on_ready_recv_eof(self):
        self.sslsock.recv.return_value = b''
        self.transport._on_ready()
        self.assertTrue(self.event_loop.remove_reader.called)
        self.assertTrue(self.event_loop.remove_writer.called)
        self.assertTrue(self.sslsock.close.called)
        self.assertTrue(self.protocol.connection_lost.called)

    def test_on_ready_recv_retry(self):
        self.sslsock.recv.side_effect = ssl.SSLWantReadError
        self.transport._on_ready()
        self.assertTrue(self.sslsock.recv.called)
        self.assertFalse(self.protocol.data_received.called)

        self.sslsock.recv.side_effect = ssl.SSLWantWriteError
        self.transport._on_ready()
        self.assertFalse(self.protocol.data_received.called)

        class Err(socket.error):
            errno = errno.EAGAIN

        self.sslsock.recv.side_effect = Err
        self.transport._on_ready()
        self.assertFalse(self.protocol.data_received.called)

    def test_on_ready_recv_exc(self):
        class Err(socket.error):
            pass

        self.sslsock.recv.side_effect = Err
        self.transport._fatal_error = unittest.mock.Mock()
        self.transport._on_ready()
        self.assertTrue(self.transport._fatal_error.called)

    def test_on_ready_send(self):
        self.sslsock.recv.side_effect = ssl.SSLWantReadError
        self.sslsock.send.return_value = 4
        self.transport._buffer = [b'data']
        self.transport._on_ready()
        self.assertTrue(self.sslsock.send.called)
        self.assertEqual([], self.transport._buffer)

    def test_on_ready_send_none(self):
        self.sslsock.recv.side_effect = ssl.SSLWantReadError
        self.sslsock.send.return_value = 0
        self.transport._buffer = [b'data1', b'data2']
        self.transport._on_ready()
        self.assertTrue(self.sslsock.send.called)
        self.assertEqual([b'data1data2'], self.transport._buffer)

    def test_on_ready_send_partial(self):
        self.sslsock.recv.side_effect = ssl.SSLWantReadError
        self.sslsock.send.return_value = 2
        self.transport._buffer = [b'data1', b'data2']
        self.transport._on_ready()
        self.assertTrue(self.sslsock.send.called)
        self.assertEqual([b'ta1data2'], self.transport._buffer)

    def test_on_ready_send_closing_partial(self):
        self.sslsock.recv.side_effect = ssl.SSLWantReadError
        self.sslsock.send.return_value = 2
        self.transport._buffer = [b'data1', b'data2']
        self.transport._on_ready()
        self.assertTrue(self.sslsock.send.called)
        self.assertFalse(self.sslsock.close.called)

    def test_on_ready_send_closing(self):
        self.sslsock.recv.side_effect = ssl.SSLWantReadError
        self.sslsock.send.return_value = 4
        self.transport.close()
        self.transport._buffer = [b'data']
        self.transport._on_ready()
        self.assertTrue(self.sslsock.close.called)
        self.assertTrue(self.event_loop.remove_writer.called)
        self.assertTrue(self.protocol.connection_lost.called)

    def test_on_ready_send_retry(self):
        self.sslsock.recv.side_effect = ssl.SSLWantReadError

        self.transport._buffer = [b'data']

        self.sslsock.send.side_effect = ssl.SSLWantReadError
        self.transport._on_ready()
        self.assertTrue(self.sslsock.send.called)
        self.assertEqual([b'data'], self.transport._buffer)

        self.sslsock.send.side_effect = ssl.SSLWantWriteError
        self.transport._on_ready()
        self.assertEqual([b'data'], self.transport._buffer)

        class Err(socket.error):
            errno = errno.EAGAIN

        self.sslsock.send.side_effect = Err
        self.transport._on_ready()
        self.assertEqual([b'data'], self.transport._buffer)

    def test_on_ready_send_exc(self):
        self.sslsock.recv.side_effect = ssl.SSLWantReadError

        class Err(socket.error):
            pass

        self.sslsock.send.side_effect = Err
        self.transport._buffer = [b'data']
        self.transport._fatal_error = unittest.mock.Mock()
        self.transport._on_ready()
        self.assertTrue(self.transport._fatal_error.called)
        self.assertEqual([], self.transport._buffer)
