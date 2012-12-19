"""Tests for tasks.py."""

import time
import unittest

from . import futures
from . import tasks


class TaskTests(unittest.TestCase):

    def testTask(self):
        @tasks.coroutine
        def notmuch():
            yield from []
            return 'ok'
        t = tasks.Task(notmuch())
        t._event_loop.run()
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'ok')

    def testSleep(self):
        @tasks.coroutine
        def sleeper(dt, arg):
            res = yield from futures.sleep(dt, arg)
            return res
        t = tasks.Task(sleeper(0.1, 'yeah'))
        t0 = time.monotonic()
        t._event_loop.run()
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.09)
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'yeah')


if __name__ == '__main__':
    unittest.main()
