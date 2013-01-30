"""Tests for events.py."""

import concurrent.futures
import gc
import os
import select
import signal
import socket
try:
    import ssl
except ImportError:
    ssl = None
import sys
import threading
import time
import unittest

from . import events
from . import transports
from . import protocols
from . import selectors
from . import selector_events


class MyProto(protocols.Protocol):

    def __init__(self):
        self.state = 'INITIAL'
        self.nbytes = 0

    def connection_made(self, transport):
        self.transport = transport
        assert self.state == 'INITIAL', self.state
        self.state = 'CONNECTED'
        transport.write(b'GET / HTTP/1.0\r\nHost: xkcd.com\r\n\r\n')

    def data_received(self, data):
        assert self.state == 'CONNECTED', self.state
        self.nbytes += len(data)

    def eof_received(self):
        assert self.state == 'CONNECTED', self.state
        self.state = 'EOF'
        self.transport.close()

    def connection_lost(self, exc):
        assert self.state in ('CONNECTED', 'EOF'), self.state
        self.state = 'CLOSED'


class EventLoopTestsMixin:

    def setUp(self):
        self.event_loop = self.create_event_loop()
        events.set_event_loop(self.event_loop)

    def tearDown(self):
        self.event_loop.close()
        gc.collect()

    def test_run(self):
        el = events.get_event_loop()
        el.run()  # Returns immediately.

    def test_call_later(self):
        el = events.get_event_loop()
        results = []
        def callback(arg):
            results.append(arg)
        el.call_later(0.1, callback, 'hello world')
        t0 = time.monotonic()
        el.run()
        t1 = time.monotonic()
        self.assertEqual(results, ['hello world'])
        self.assertTrue(t1-t0 >= 0.09)

    def test_call_repeatedly(self):
        el = events.get_event_loop()
        results = []
        def callback(arg):
            results.append(arg)
        el.call_repeatedly(0.03, callback, 'ho')
        el.call_later(0.1, el.stop)
        el.run()
        self.assertEqual(results, ['ho', 'ho', 'ho'])

    def test_call_soon(self):
        el = events.get_event_loop()
        results = []
        def callback(arg1, arg2):
            results.append((arg1, arg2))
        el.call_soon(callback, 'hello', 'world')
        el.run()
        self.assertEqual(results, [('hello', 'world')])

    def test_call_soon_with_handler(self):
        el = events.get_event_loop()
        results = []
        def callback():
            results.append('yeah')
        handler = events.Handler(None, callback, ())
        self.assertEqual(el.call_soon(handler), handler)
        el.run()
        self.assertEqual(results, ['yeah'])

    def test_call_soon_threadsafe(self):
        el = events.get_event_loop()
        results = []
        def callback(arg):
            results.append(arg)
        def run():
            el.call_soon_threadsafe(callback, 'hello')
        t = threading.Thread(target=run)
        el.call_later(0.1, callback, 'world')
        t0 = time.monotonic()
        t.start()
        el.run()
        t1 = time.monotonic()
        t.join()
        self.assertEqual(results, ['hello', 'world'])
        self.assertTrue(t1-t0 >= 0.09)

    def test_call_soon_threadsafe_with_handler(self):
        el = events.get_event_loop()
        results = []
        def callback(arg):
            results.append(arg)
        handler = events.Handler(None, callback, ('hello',))
        def run():
            self.assertEqual(el.call_soon_threadsafe(handler), handler)
        t = threading.Thread(target=run)
        el.call_later(0.1, callback, 'world')
        t0 = time.monotonic()
        t.start()
        el.run()
        t1 = time.monotonic()
        t.join()
        self.assertEqual(results, ['hello', 'world'])
        self.assertTrue(t1-t0 >= 0.09)

    def test_wrap_future(self):
        el = events.get_event_loop()
        def run(arg):
            time.sleep(0.1)
            return arg
        ex = concurrent.futures.ThreadPoolExecutor(1)
        f1 = ex.submit(run, 'oi')
        f2 = el.wrap_future(f1)
        res = el.run_until_complete(f2)
        self.assertEqual(res, 'oi')

    def test_run_in_executor(self):
        el = events.get_event_loop()
        def run(arg):
            time.sleep(0.1)
            return arg
        f2 = el.run_in_executor(None, run, 'yo')
        res = el.run_until_complete(f2)
        self.assertEqual(res, 'yo')

    def test_run_in_executor_with_handler(self):
        el = events.get_event_loop()
        def run(arg):
            time.sleep(0.1)
            return arg
        handler = events.Handler(None, run, ('yo',))
        f2 = el.run_in_executor(None, handler)
        res = el.run_until_complete(f2)
        self.assertEqual(res, 'yo')

    def test_reader_callback(self):
        el = events.get_event_loop()
        r, w = el._socketpair()
        bytes_read = []
        def reader():
            try:
                data = r.recv(1024)
            except BlockingIOError:
                # Spurious readiness notifications are possible
                # at least on Linux -- see man select.
                return
            if data:
                bytes_read.append(data)
            else:
                self.assertTrue(el.remove_reader(r.fileno()))
                r.close()
        el.add_reader(r.fileno(), reader)
        el.call_later(0.05, w.send, b'abc')
        el.call_later(0.1, w.send, b'def')
        el.call_later(0.15, w.close)
        el.run()
        self.assertEqual(b''.join(bytes_read), b'abcdef')

    def test_reader_callback_with_handler(self):
        el = events.get_event_loop()
        r, w = el._socketpair()
        bytes_read = []
        def reader():
            try:
                data = r.recv(1024)
            except BlockingIOError:
                # Spurious readiness notifications are possible
                # at least on Linux -- see man select.
                return
            if data:
                bytes_read.append(data)
            else:
                self.assertTrue(el.remove_reader(r.fileno()))
                r.close()
        handler = events.Handler(None, reader, ())
        self.assertEqual(el.add_reader(r.fileno(), handler), handler)
        el.call_later(0.05, w.send, b'abc')
        el.call_later(0.1, w.send, b'def')
        el.call_later(0.15, w.close)
        el.run()
        self.assertEqual(b''.join(bytes_read), b'abcdef')

    def test_reader_callback_cancel(self):
        el = events.get_event_loop()
        r, w = el._socketpair()
        bytes_read = []
        def reader():
            try:
                data = r.recv(1024)
            except BlockingIOError:
                return
            if data:
                bytes_read.append(data)
            if sum(len(b) for b in bytes_read) >= 6:
                handler.cancel()
            if not data:
                r.close()
        handler = el.add_reader(r.fileno(), reader)
        el.call_later(0.05, w.send, b'abc')
        el.call_later(0.1, w.send, b'def')
        el.call_later(0.15, w.close)
        el.run()
        self.assertEqual(b''.join(bytes_read), b'abcdef')

    def test_writer_callback(self):
        el = events.get_event_loop()
        r, w = el._socketpair()
        w.setblocking(False)
        el.add_writer(w.fileno(), w.send, b'x'*(256*1024))
        def remove_writer():
            self.assertTrue(el.remove_writer(w.fileno()))
        el.call_later(0.1, remove_writer)
        el.run()
        w.close()
        data = r.recv(256*1024)
        r.close()
        self.assertTrue(len(data) >= 200)

    def test_writer_callback_with_handler(self):
        el = events.get_event_loop()
        r, w = el._socketpair()
        w.setblocking(False)
        handler = events.Handler(None, w.send, (b'x'*(256*1024),))
        self.assertEqual(el.add_writer(w.fileno(), handler), handler)
        def remove_writer():
            self.assertTrue(el.remove_writer(w.fileno()))
        el.call_later(0.1, remove_writer)
        el.run()
        w.close()
        data = r.recv(256*1024)
        r.close()
        self.assertTrue(len(data) >= 200)

    def test_writer_callback_cancel(self):
        el = events.get_event_loop()
        r, w = el._socketpair()
        w.setblocking(False)
        def sender():
            w.send(b'x'*256)
            handler.cancel()
        handler = el.add_writer(w.fileno(), sender)
        el.run()
        w.close()
        data = r.recv(1024)
        r.close()
        self.assertTrue(data == b'x'*256)

    def test_sock_client_ops(self):
        el = events.get_event_loop()
        sock = socket.socket()
        sock.setblocking(False)
        # TODO: This depends on python.org behavior!
        address = socket.getaddrinfo('python.org', 80, socket.AF_INET)[0][4]
        el.run_until_complete(el.sock_connect(sock, address))
        el.run_until_complete(el.sock_sendall(sock, b'GET / HTTP/1.0\r\n\r\n'))
        data = el.run_until_complete(el.sock_recv(sock, 1024))
        sock.close()
        self.assertTrue(data.startswith(b'HTTP/1.1 302 Found\r\n'))

    def test_sock_client_fail(self):
        el = events.get_event_loop()
        sock = socket.socket()
        sock.setblocking(False)
        # TODO: This depends on python.org behavior!
        address = socket.getaddrinfo('python.org', 12345, socket.AF_INET)[0][4]
        with self.assertRaises(ConnectionRefusedError):
            el.run_until_complete(el.sock_connect(sock, address))
        sock.close()

    def test_sock_accept(self):
        el = events.get_event_loop()
        listener = socket.socket()
        listener.setblocking(False)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        client = socket.socket()
        client.connect(listener.getsockname())
        f = el.sock_accept(listener)
        conn, addr = el.run_until_complete(f)
        self.assertEqual(conn.gettimeout(), 0)
        self.assertEqual(addr, client.getsockname())
        self.assertEqual(client.getpeername(), listener.getsockname())
        client.close()
        conn.close()
        listener.close()

    @unittest.skipUnless(hasattr(signal, 'SIGKILL'), 'No SIGKILL')
    def test_add_signal_handler(self):
        caught = 0
        def my_handler():
            nonlocal caught
            caught += 1
        el = events.get_event_loop()
        # Check error behavior first.
        self.assertRaises(TypeError, el.add_signal_handler, 'boom', my_handler)
        self.assertRaises(TypeError, el.remove_signal_handler, 'boom')
        self.assertRaises(ValueError, el.add_signal_handler, signal.NSIG+1,
                          my_handler)
        self.assertRaises(ValueError, el.remove_signal_handler, signal.NSIG+1)
        self.assertRaises(ValueError, el.add_signal_handler, 0, my_handler)
        self.assertRaises(ValueError, el.remove_signal_handler, 0)
        self.assertRaises(ValueError, el.add_signal_handler, -1, my_handler)
        self.assertRaises(ValueError, el.remove_signal_handler, -1)
        self.assertRaises(RuntimeError, el.add_signal_handler, signal.SIGKILL,
                          my_handler)
        # Removing SIGKILL doesn't raise, since we don't call signal().
        self.assertFalse(el.remove_signal_handler(signal.SIGKILL))
        # Now set a handler and handle it.
        el.add_signal_handler(signal.SIGINT, my_handler)
        el.run_once()
        os.kill(os.getpid(), signal.SIGINT)
        el.run_once()
        self.assertEqual(caught, 1)
        # Removing it should restore the default handler.
        self.assertTrue(el.remove_signal_handler(signal.SIGINT))
        self.assertEqual(signal.getsignal(signal.SIGINT),
                         signal.default_int_handler)
        # Removing again returns False.
        self.assertFalse(el.remove_signal_handler(signal.SIGINT))

    @unittest.skipIf(sys.platform == 'win32', 'Unix only')
    def test_cancel_signal_handler(self):
        # Cancelling the handler should remove it (eventually).
        caught = 0
        def my_handler():
            nonlocal caught
            caught += 1
        el = events.get_event_loop()
        handler = el.add_signal_handler(signal.SIGINT, my_handler)
        handler.cancel()
        os.kill(os.getpid(), signal.SIGINT)
        el.run_once()
        self.assertEqual(caught, 0)

    @unittest.skipUnless(hasattr(signal, 'SIGALRM'), 'No SIGALRM')
    def test_signal_handling_while_selecting(self):
        # Test with a signal actually arriving during a select() call.
        caught = 0
        def my_handler():
            nonlocal caught
            caught += 1
        el = events.get_event_loop()
        handler = el.add_signal_handler(signal.SIGALRM, my_handler)
        signal.setitimer(signal.ITIMER_REAL, 0.1, 0)  # Send SIGALRM once.
        el.call_later(0.15, el.stop)
        el.run_forever()
        self.assertEqual(caught, 1)

    def test_create_transport(self):
        el = events.get_event_loop()
        # TODO: This depends on xkcd.com behavior!
        f = el.create_connection(MyProto, 'xkcd.com', 80)
        tr, pr = el.run_until_complete(f)
        self.assertTrue(isinstance(tr, transports.Transport))
        self.assertTrue(isinstance(pr, protocols.Protocol))
        el.run()
        self.assertTrue(pr.nbytes > 0)

    @unittest.skipIf(ssl is None, 'No ssl module')
    def test_create_ssl_transport(self):
        el = events.get_event_loop()
        # TODO: This depends on xkcd.com behavior!
        f = el.create_connection(MyProto, 'xkcd.com', 443, ssl=True)
        tr, pr = el.run_until_complete(f)
        self.assertTrue(isinstance(tr, transports.Transport))
        self.assertTrue(isinstance(pr, protocols.Protocol))
        self.assertTrue('ssl' in tr.__class__.__name__.lower())
        el.run()
        self.assertTrue(pr.nbytes > 0)

    def test_start_serving(self):
        el = events.get_event_loop()
        f = el.start_serving(MyProto, '0.0.0.0', 0)
        sock = el.run_until_complete(f)
        host, port = sock.getsockname()
        self.assertEqual(host, '0.0.0.0')
        client = socket.socket()
        client.connect(('127.0.0.1', port))
        client.send(b'xxx')
        el.run_once()  # This is quite mysterious, but necessary.
        el.run_once()
        el.run_once()
        sock.close()
        # the client socket must be closed after to avoid ECONNRESET upon
        # recv()/send() on the serving socket
        client.close()


if sys.platform == 'win32':
    from . import windows_events

    class SelectEventLoopTests(EventLoopTestsMixin, unittest.TestCase):
        def create_event_loop(self):
            return windows_events.SelectorEventLoop()

    class ProactorEventLoopTests(EventLoopTestsMixin, unittest.TestCase):
        def create_event_loop(self):
            return windows_events.ProactorEventLoop()
        def test_create_ssl_transport(self):
            raise unittest.SkipTest("IocpEventLoop imcompatible with SSL")
        def test_reader_callback(self):
            raise unittest.SkipTest("IocpEventLoop does not have add_reader()")
        def test_reader_callback_cancel(self):
            raise unittest.SkipTest("IocpEventLoop does not have add_reader()")
        def test_reader_callback_with_handler(self):
            raise unittest.SkipTest("IocpEventLoop does not have add_reader()")
        def test_writer_callback(self):
            raise unittest.SkipTest("IocpEventLoop does not have add_writer()")
        def test_writer_callback_cancel(self):
            raise unittest.SkipTest("IocpEventLoop does not have add_writer()")
        def test_writer_callback_with_handler(self):
            raise unittest.SkipTest("IocpEventLoop does not have add_writer()")

else:
    from . import unix_events

    if hasattr(selectors, 'KqueueSelector'):
        class KqueueEventLoopTests(EventLoopTestsMixin, unittest.TestCase):
            def create_event_loop(self):
                return unix_events.SelectorEventLoop(selectors.KqueueSelector())

    if hasattr(selectors, 'EpollSelector'):
        class EPollEventLoopTests(EventLoopTestsMixin, unittest.TestCase):
            def create_event_loop(self):
                return unix_events.SelectorEventLoop(selectors.EpollSelector())

    if hasattr(selectors, 'PollSelector'):
        class PollEventLoopTests(EventLoopTestsMixin, unittest.TestCase):
            def create_event_loop(self):
                return unix_events.SelectorEventLoop(selectors.PollSelector())

    # Should always exist.
    class SelectEventLoopTests(EventLoopTestsMixin, unittest.TestCase):
        def create_event_loop(self):
            return unix_events.SelectorEventLoop(selectors.SelectSelector())


class HandlerTests(unittest.TestCase):

    def test_handler(self):
        pass

    def test_make_handler(self):
        def callback(*args):
            return args
        h1 = events.Handler(None, callback, ())
        h2 = events.make_handler(None, h1, ())
        self.assertEqual(h1, h2)


class PolicyTests(unittest.TestCase):

    def test_policy(self):
        pass


if __name__ == '__main__':
    unittest.main()
