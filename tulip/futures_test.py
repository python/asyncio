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


# A fake event loop for tests. All it does is implement a call_soon method
# that immediately invokes the given function.
class _FakeEventLoop:
    def call_soon(self, fn, future):
        fn(future)


class FutureDoneCallbackTests(unittest.TestCase):

    def _make_callback(self, bag, thing):
        # Create a callback function that appends thing to bag.
        def bag_appender(future):
            bag.append(thing)
        return bag_appender

    def _new_future(self):
        return futures.Future(event_loop=_FakeEventLoop())

    def testCallbacksInvokedOnSetResult(self):
        bag = []
        f = self._new_future()
        f.add_done_callback(self._make_callback(bag, 42))
        f.add_done_callback(self._make_callback(bag, 17))

        self.assertEqual(bag, [])
        f.set_result('foo')
        self.assertEqual(bag, [42, 17])
        self.assertEqual(f.result(), 'foo')

    def testCallbacksInvokedOnSetException(self):
        bag = []
        f = self._new_future()
        f.add_done_callback(self._make_callback(bag, 100))

        self.assertEqual(bag, [])
        exc = RuntimeError()
        f.set_exception(exc)
        self.assertEqual(bag, [100])
        self.assertEqual(f.exception(), exc)

    def testRemoveDoneCallback(self):
        bag = []
        f = self._new_future()
        cb1 = self._make_callback(bag, 1)
        cb2 = self._make_callback(bag, 2)
        cb3 = self._make_callback(bag, 3)

        # Add one cb1 and one cb2.
        f.add_done_callback(cb1)
        f.add_done_callback(cb2)

        # One instance of cb2 removed. Now there's only one cb1.
        self.assertEqual(f.remove_done_callback(cb2), 1)

        # Never had any cb3 in there.
        self.assertEqual(f.remove_done_callback(cb3), 0)

        # After this there will be 6 instances of cb1 and one of cb2.
        f.add_done_callback(cb2)
        for i in range(5):
            f.add_done_callback(cb1)

        # Remove all instances of cb1. One cb2 remains.
        self.assertEqual(f.remove_done_callback(cb1), 6)

        self.assertEqual(bag, [])
        f.set_result('foo')
        self.assertEqual(bag, [2])
        self.assertEqual(f.result(), 'foo')


if __name__ == '__main__':
    unittest.main()
