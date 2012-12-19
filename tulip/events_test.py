"""Tests for events.py."""

import concurrent.futures
import os
import socket
import threading
import time
import unittest

from . import events


def run_while_future(event_loop, future):
    # TODO: Use socketpair().
    r, w = os.pipe()
    def cleanup():
        event_loop.remove_reader(r)
        os.close(r)
    event_loop.add_reader(r, cleanup)
    # Closing the write end makes the read end readable.
    future.add_done_callback(lambda _: os.close(w))
    event_loop.run()
    return future.result()  # May raise.


class EventLoopTests(unittest.TestCase):

    def setUp(self):
        events.init_event_loop()

    def testRun(self):
        el = events.get_event_loop()
        el.run()  # Returns immediately.

    def testCallLater(self):
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

    def testCallSoon(self):
        el = events.get_event_loop()
        results = []
        def callback(arg1, arg2):
            results.append((arg1, arg2))
        el.call_soon(callback, 'hello', 'world')
        el.run()
        self.assertEqual(results, [('hello', 'world')])

    def testCallSoonThreadsafe(self):
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

    def testWrapFuture(self):
        el = events.get_event_loop()
        def run(arg):
            time.sleep(0.1)
            return arg
        ex = concurrent.futures.ThreadPoolExecutor(1)
        f1 = ex.submit(run, 'oi')
        f2 = el.wrap_future(f1)
        run_while_future(el, f2)
        self.assertTrue(f2.done())
        self.assertEqual(f2.result(), 'oi')

    def testRunInExecutor(self):
        el = events.get_event_loop()
        def run(arg):
            time.sleep(0.1)
            return arg
        f2 = el.run_in_executor(None, run, 'yo')
        run_while_future(el, f2)
        self.assertTrue(f2.done())
        self.assertEqual(f2.result(), 'yo')

    def test_reader_callback(self):
        el = events.get_event_loop()
        # TODO: Use socketpair().
        r, w = os.pipe()
        bytes_read = []
        def reader():
            data = os.read(r, 1024)
            if data:
                bytes_read.append(data)
            else:
                el.remove_reader(r)
                os.close(r)
        el.add_reader(r, reader)
        el.call_later(0.05, os.write, w, b'abc')
        el.call_later(0.1, os.write, w, b'def')
        el.call_later(0.15, os.close, w)
        el.run()
        self.assertEqual(b''.join(bytes_read), b'abcdef')

    def test_writer_callback(self):
        el = events.get_event_loop()
        # TODO: Use socketpair().
        r, w = os.pipe()
        el.add_writer(w, os.write, w, b'x'*100)
        el.call_later(0.1, el.remove_writer, w)
        el.run()
        os.close(w)
        data = os.read(r, 32*1024)
        os.close(r)
        self.assertTrue(len(data) >= 200)

    def test_sock_client_ops(self):
        el = events.get_event_loop()
        sock = socket.socket()
        sock.setblocking(False)
        # TODO: This depends on python.org behavior!
        run_while_future(el, el.sock_connect(sock, ('python.org', 80)))
        run_while_future(el, el.sock_sendall(sock, b'GET / HTTP/1.0\r\n\r\n'))
        data = run_while_future(el, el.sock_recv(sock, 1024))
        sock.close()
        self.assertTrue(data.startswith(b'HTTP/1.1 302 Found\r\n'))


class DelayedCallTests(unittest.TestCase):

    def testDelayedCall(self):
        pass


class PolicyTests(unittest.TestCase):

    def testPolicy(self):
        pass


if __name__ == '__main__':
    unittest.main()
