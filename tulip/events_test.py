"""Tests for events.py."""

import concurrent.futures
import os
import threading
import time
import unittest

from . import events


def run_while_future(event_loop, future):
    r, w = os.pipe()
    def cleanup():
        event_loop.remove_reader(r)
        os.close(w)
        os.close(r)
    event_loop.add_reader(r, cleanup)
    # Closing the write end makes the read end readable.
    future.add_done_callback(lambda _: os.write(w, b'x'))
    event_loop.run()


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


class DelayedCallTests(unittest.TestCase):

    def testDelayedCall(self):
        pass


class PolicyTests(unittest.TestCase):

    def testPolicy(self):
        pass


if __name__ == '__main__':
    unittest.main()
