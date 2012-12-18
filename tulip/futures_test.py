"""Tests for futures.py."""

import unittest

from . import futures


class FutureTests(unittest.TestCase):

    def testInitialState(self):
        f = futures.Future()
        self.assertFalse(f.cancelled())
        self.assertFalse(f.running())
        self.assertFalse(f.done())

    def testCancel(self):
        f = futures.Future()
        f.cancel()
        self.assertTrue(f.cancelled())
        self.assertFalse(f.running())
        self.assertTrue(f.done())
        self.assertRaises(futures.CancelledError, f.result)
        self.assertRaises(futures.CancelledError, f.exception)

    def testResult(self):
        f = futures.Future()
        f.set_result(42)
        self.assertFalse(f.cancelled())
        self.assertFalse(f.running())
        self.assertTrue(f.done())
        self.assertEqual(f.result(), 42)
        self.assertEqual(f.exception(), None)

    def testException(self):
        exc = RuntimeError()
        f = futures.Future()
        f.set_exception(exc)
        self.assertFalse(f.cancelled())
        self.assertFalse(f.running())
        self.assertTrue(f.done())
        self.assertRaises(RuntimeError, f.result)
        self.assertEqual(f.exception(), exc)


if __name__ == '__main__':
    unittest.main()
