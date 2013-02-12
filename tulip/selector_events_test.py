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
from .selector_events import BaseSelectorEventLoop
from .selector_events import _SelectorSslTransport
from .selector_events import _SelectorSocketTransport


class TestBaseSelectorEventLoop(BaseSelectorEventLoop):

    def _make_self_pipe(self):
        pass


class BaseSelectorEventLoopTests(unittest.TestCase):

    def setUp(self):
        self.event_loop = TestBaseSelectorEventLoop()

    def test_make_socket_transport(self):
        m = unittest.mock.Mock()
        self.assertIsInstance(
            self.event_loop._make_socket_transport(m, m, m),
            _SelectorSocketTransport)

    def test_make_ssl_transport(self):
        m = unittest.mock.Mock()
        self.assertIsInstance(
            self.event_loop._make_ssl_transport(m, m, m, m, m),
            _SelectorSslTransport)


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