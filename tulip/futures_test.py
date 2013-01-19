"""Tests for futures.py."""

import unittest

from . import futures


class FutureTests(unittest.TestCase):

    def testInitialState(self):
        f = futures.Future()
        self.assertFalse(f.cancelled())
        self.assertFalse(f.running())
        self.assertFalse(f.done())

    def testInitEventLoopPositional(self):
        # Make sure Future does't accept a positional argument
        self.assertRaises(TypeError, futures.Future, 42)

    def testCancel(self):
        f = futures.Future()
        self.assertTrue(f.cancel())
        self.assertTrue(f.cancelled())
        self.assertFalse(f.running())
        self.assertTrue(f.done())
        self.assertRaises(futures.CancelledError, f.result)
        self.assertRaises(futures.CancelledError, f.exception)
        self.assertRaises(futures.InvalidStateError, f.set_result, None)
        self.assertRaises(futures.InvalidStateError, f.set_exception, None)
        self.assertFalse(f.cancel())

    def testResult(self):
        f = futures.Future()
        f.set_result(42)
        self.assertFalse(f.cancelled())
        self.assertFalse(f.running())
        self.assertTrue(f.done())
        self.assertEqual(f.result(), 42)
        self.assertEqual(f.exception(), None)
        self.assertRaises(futures.InvalidStateError, f.set_result, None)
        self.assertRaises(futures.InvalidStateError, f.set_exception, None)
        self.assertFalse(f.cancel())

    def testException(self):
        exc = RuntimeError()
        f = futures.Future()
        f.set_exception(exc)
        self.assertFalse(f.cancelled())
        self.assertFalse(f.running())
        self.assertTrue(f.done())
        self.assertRaises(RuntimeError, f.result)
        self.assertEqual(f.exception(), exc)
        self.assertRaises(futures.InvalidStateError, f.set_result, None)
        self.assertRaises(futures.InvalidStateError, f.set_exception, None)
        self.assertFalse(f.cancel())

    def testDoneCallbacks(self):
        class MyEventLoop:
            def call_soon(self, fn, future):
                fn(future)

        bag = []
        def make_callback(num):
            def bag_appender(future):
                bag.append(num)
            return bag_appender

        f = futures.Future(event_loop=MyEventLoop())
        f.add_done_callback(make_callback(42))
        f.add_done_callback(make_callback(17))

        self.assertEquals(bag, [])
        f.set_result('foo')
        self.assertEqual(bag, [42, 17])
        self.assertEqual(f.result(), 'foo')

    def testYieldFromTwice(self):
        f = futures.Future()
        def fixture():
            yield 'A'
            x = yield from f
            yield 'B', x
            y = yield from f
            yield 'C', y
        g = fixture()
        self.assertEqual(next(g), 'A')  # yield 'A'.
        self.assertEqual(next(g), f)  # First yield from f.
        f.set_result(42)
        self.assertEqual(next(g), ('B', 42))  # yield 'B', x.
        # The second "yield from f" does not yield f.
        self.assertEqual(next(g), ('C', 42))  # yield 'C', y.


if __name__ == '__main__':
    unittest.main()
