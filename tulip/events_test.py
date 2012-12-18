"""Tests for events.py."""

import threading
import time
import unittest

from . import events


class EventLoopTests(unittest.TestCase):

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
        self.assertTrue(t1-t0 >= 0.1)

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
        self.assertTrue(t1-t0 >= 0.1)


class DelayedCallTests(unittest.TestCase):

    def testDelayedCall(self):
        pass


class PolicyTests(unittest.TestCase):

    def testPolicy(self):
        pass


if __name__ == '__main__':
    unittest.main()
