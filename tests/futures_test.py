"""Tests for futures.py."""

import logging
import unittest
import unittest.mock

from tulip import events
from tulip import futures


def _fakefunc(f):
    return f


class FutureTests(unittest.TestCase):

    def setUp(self):
        self.loop = events.get_event_loop()

    def test_initial_state(self):
        f = futures.Future()
        self.assertFalse(f.cancelled())
        self.assertFalse(f.running())
        self.assertFalse(f.done())
        f.cancel()
        self.assertTrue(f.cancelled())

    def test_init_event_loop_positional(self):
        # Make sure Future does't accept a positional argument
        self.assertRaises(TypeError, futures.Future, 42)

    def test_cancel(self):
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

    def test_result(self):
        f = futures.Future()
        self.assertRaises(futures.InvalidStateError, f.result)
        self.assertRaises(futures.InvalidTimeoutError, f.result, 10)

        f.set_result(42)
        self.assertFalse(f.cancelled())
        self.assertFalse(f.running())
        self.assertTrue(f.done())
        self.assertEqual(f.result(), 42)
        self.assertEqual(f.exception(), None)
        self.assertRaises(futures.InvalidStateError, f.set_result, None)
        self.assertRaises(futures.InvalidStateError, f.set_exception, None)
        self.assertFalse(f.cancel())

    def test_exception(self):
        exc = RuntimeError()
        f = futures.Future()
        self.assertRaises(futures.InvalidStateError, f.exception)
        self.assertRaises(futures.InvalidTimeoutError, f.exception, 10)

        f.set_exception(exc)
        self.assertFalse(f.cancelled())
        self.assertFalse(f.running())
        self.assertTrue(f.done())
        self.assertRaises(RuntimeError, f.result)
        self.assertEqual(f.exception(), exc)
        self.assertRaises(futures.InvalidStateError, f.set_result, None)
        self.assertRaises(futures.InvalidStateError, f.set_exception, None)
        self.assertFalse(f.cancel())

    def test_yield_from_twice(self):
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

    def test_repr(self):
        f_pending = futures.Future()
        self.assertEqual(repr(f_pending), 'Future<PENDING>')
        f_pending.cancel()

        f_cancelled = futures.Future()
        f_cancelled.cancel()
        self.assertEqual(repr(f_cancelled), 'Future<CANCELLED>')

        f_result = futures.Future()
        f_result.set_result(4)
        self.assertEqual(repr(f_result), 'Future<result=4>')
        self.assertEqual(f_result.result(), 4)

        exc = RuntimeError()
        f_exception = futures.Future()
        f_exception.set_exception(exc)
        self.assertEqual(repr(f_exception), 'Future<exception=RuntimeError()>')
        self.assertIs(f_exception.exception(), exc)

        f_few_callbacks = futures.Future()
        f_few_callbacks.add_done_callback(_fakefunc)
        self.assertIn('Future<PENDING, [<function _fakefunc',
                      repr(f_few_callbacks))
        f_few_callbacks.cancel()

        f_many_callbacks = futures.Future()
        for i in range(20):
            f_many_callbacks.add_done_callback(_fakefunc)
        r = repr(f_many_callbacks)
        self.assertIn('Future<PENDING, [<function _fakefunc', r)
        self.assertIn('<18 more>', r)
        f_many_callbacks.cancel()

    def test_copy_state(self):
        # Test the internal _copy_state method since it's being directly
        # invoked in other modules.
        f = futures.Future()
        f.set_result(10)

        newf = futures.Future()
        newf._copy_state(f)
        self.assertTrue(newf.done())
        self.assertEqual(newf.result(), 10)

        f_exception = futures.Future()
        f_exception.set_exception(RuntimeError())

        newf_exception = futures.Future()
        newf_exception._copy_state(f_exception)
        self.assertTrue(newf_exception.done())
        self.assertRaises(RuntimeError, newf_exception.result)

        f_cancelled = futures.Future()
        f_cancelled.cancel()

        newf_cancelled = futures.Future()
        newf_cancelled._copy_state(f_cancelled)
        self.assertTrue(newf_cancelled.cancelled())

    def test_iter(self):
        fut = futures.Future()

        def coro():
            yield from fut

        def test():
            arg1, arg2 = coro()

        self.assertRaises(AssertionError, test)
        fut.cancel()

    @unittest.mock.patch('tulip.futures.tulip_log')
    def test_tb_logger_abandoned(self, m_log):
        fut = futures.Future()
        del fut
        self.assertFalse(m_log.error.called)

    @unittest.mock.patch('tulip.futures.tulip_log')
    def test_tb_logger_result_unretrieved(self, m_log):
        fut = futures.Future()
        fut.set_result(42)
        del fut
        self.assertFalse(m_log.error.called)

    @unittest.mock.patch('tulip.futures.tulip_log')
    def test_tb_logger_result_retrieved(self, m_log):
        fut = futures.Future()
        fut.set_result(42)
        fut.result()
        del fut
        self.assertFalse(m_log.error.called)

    @unittest.mock.patch('tulip.futures.tulip_log')
    def test_tb_logger_exception_unretrieved(self, m_log):
        fut = futures.Future()
        fut.set_exception(RuntimeError('boom'))
        del fut
        self.assertTrue(m_log.error.called)

    @unittest.mock.patch('tulip.futures.tulip_log')
    def test_tb_logger_exception_retrieved(self, m_log):
        fut = futures.Future()
        fut.set_exception(RuntimeError('boom'))
        fut.exception()
        del fut
        self.assertFalse(m_log.error.called)

    @unittest.mock.patch('tulip.futures.tulip_log')
    def test_tb_logger_exception_result_retrieved(self, m_log):
        fut = futures.Future()
        fut.set_exception(RuntimeError('boom'))
        self.assertRaises(RuntimeError, fut.result)
        del fut
        self.assertFalse(m_log.error.called)


# A fake event loop for tests. All it does is implement a call_soon method
# that immediately invokes the given function.
class _FakeEventLoop:
    def call_soon(self, fn, future):
        fn(future)

    def set_log_level(self, val):
        pass

    def get_log_level(self):
        return logging.CRITICAL


class FutureDoneCallbackTests(unittest.TestCase):

    def _make_callback(self, bag, thing):
        # Create a callback function that appends thing to bag.
        def bag_appender(future):
            bag.append(thing)
        return bag_appender

    def _new_future(self):
        return futures.Future(event_loop=_FakeEventLoop())

    def test_callbacks_invoked_on_set_result(self):
        bag = []
        f = self._new_future()
        f.add_done_callback(self._make_callback(bag, 42))
        f.add_done_callback(self._make_callback(bag, 17))

        self.assertEqual(bag, [])
        f.set_result('foo')
        self.assertEqual(bag, [42, 17])
        self.assertEqual(f.result(), 'foo')

    def test_callbacks_invoked_on_set_exception(self):
        bag = []
        f = self._new_future()
        f.add_done_callback(self._make_callback(bag, 100))

        self.assertEqual(bag, [])
        exc = RuntimeError()
        f.set_exception(exc)
        self.assertEqual(bag, [100])
        self.assertEqual(f.exception(), exc)

    def test_remove_done_callback(self):
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
