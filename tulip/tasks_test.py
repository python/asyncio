"""Tests for tasks.py."""

import concurrent.futures
import time
import unittest
import unittest.mock

from . import events
from . import futures
from . import tasks
from . import test_utils


class Dummy:
    def __repr__(self):
        return 'Dummy()'
    def __call__(self, *args):
        pass


class TaskTests(test_utils.LogTrackingTestCase):

    def setUp(self):
        super().setUp()
        self.event_loop = events.new_event_loop()
        events.set_event_loop(self.event_loop)

    def tearDown(self):
        self.event_loop.close()
        super().tearDown()

    def test_task_class(self):
        @tasks.coroutine
        def notmuch():
            yield from []
            return 'ok'
        t = tasks.Task(notmuch())
        self.event_loop.run()
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'ok')
        self.assertIs(t._event_loop, self.event_loop)

        event_loop = events.new_event_loop()
        t = tasks.Task(notmuch(), event_loop=event_loop)
        self.assertIs(t._event_loop, event_loop)

    def test_task_decorator(self):
        @tasks.task
        def notmuch():
            yield from []
            return 'ko'
        t = notmuch()
        self.event_loop.run()
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'ko')

    def test_task_repr(self):
        @tasks.task
        def notmuch():
            yield from []
            return 'abc'
        t = notmuch()
        t.add_done_callback(Dummy())
        self.assertEqual(repr(t), 'Task(<notmuch>)<PENDING, [Dummy()]>')
        t.cancel()  # Does not take immediate effect!
        self.assertEqual(repr(t), 'Task(<notmuch>)<CANCELLING, [Dummy()]>')
        self.assertRaises(futures.CancelledError,
                          self.event_loop.run_until_complete, t)
        self.assertEqual(repr(t), 'Task(<notmuch>)<CANCELLED>')
        t = notmuch()
        self.event_loop.run_until_complete(t)
        self.assertEqual(repr(t), "Task(<notmuch>)<result='abc'>")

    def test_task_repr_custom(self):
        def coro():
            yield from []

        class T(futures.Future):
            def __repr__(self):
                return 'T[]'

        class MyTask(tasks.Task, T):
            def __repr__(self):
                return super().__repr__()

        t = MyTask(coro())
        self.assertEqual(repr(t), 'T[](<coro>)')

    def test_task_basics(self):
        @tasks.task
        def outer():
            a = yield from inner1()
            b = yield from inner2()
            return a+b
        @tasks.task
        def inner1():
            yield from []
            return 42
        @tasks.task
        def inner2():
            yield from []
            return 1000
        t = outer()
        self.assertEqual(self.event_loop.run_until_complete(t), 1042)

    def test_cancel(self):
        @tasks.task
        def task():
            yield from tasks.sleep(10.0)
            return 12

        t = task()
        self.event_loop.call_soon(t.cancel)
        self.assertRaises(
            futures.CancelledError,
            self.event_loop.run_until_complete, t)
        self.assertTrue(t.done())
        self.assertFalse(t.cancel())

    def test_cancel_in_coro(self):
        @tasks.coroutine
        def task():
            t.cancel()
            yield from []
            return 12

        t = tasks.Task(task())
        self.assertRaises(
            futures.CancelledError,
            self.event_loop.run_until_complete, t)
        self.assertTrue(t.done())
        self.assertFalse(t.cancel())

    def test_stop_while_run_in_complete(self):
        x = 0
        @tasks.coroutine
        def task():
            nonlocal x
            while x < 10:
                yield from tasks.sleep(0.1)
                x += 1
                if x == 2:
                    self.event_loop.stop()

        t = tasks.Task(task())
        t0 = time.monotonic()
        self.assertRaises(
            futures.InvalidStateError,
            self.event_loop.run_until_complete, t)
        t1 = time.monotonic()
        self.assertFalse(t.done())
        self.assertTrue(0.18 <= t1-t0 <= 0.22)
        self.assertEqual(x, 2)

    def test_timeout(self):
        @tasks.task
        def task():
            yield from tasks.sleep(10.0)
            return 42

        t = task()
        t0 = time.monotonic()
        self.assertRaises(
            futures.TimeoutError,
            self.event_loop.run_until_complete, t, 0.1)
        t1 = time.monotonic()
        self.assertFalse(t.done())
        self.assertTrue(0.08 <= t1-t0 <= 0.12)

    def test_timeout_not(self):
        @tasks.task
        def task():
            yield from tasks.sleep(0.1)
            return 42

        t = task()
        t0 = time.monotonic()
        r = self.event_loop.run_until_complete(t, 10.0)
        t1 = time.monotonic()
        self.assertTrue(t.done())
        self.assertEqual(r, 42)
        self.assertTrue(0.08 <= t1-t0 <= 0.12)

    def test_wait(self):
        a = tasks.sleep(0.1)
        b = tasks.sleep(0.15)
        @tasks.coroutine
        def foo():
            done, pending = yield from tasks.wait([b, a])
            self.assertEqual(done, set([a, b]))
            self.assertEqual(pending, set())
            return 42
        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.14)
        self.assertEqual(res, 42)
        # Doing it again should take no time and exercise a different path.
        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 <= 0.01)
        # TODO: Test different return_when values.

    def test_wait_first_completed(self):
        a = tasks.sleep(10.0)
        b = tasks.sleep(0.1)
        task = tasks.Task(tasks.wait(
                [b, a], return_when=tasks.FIRST_COMPLETED))

        done, pending = self.event_loop.run_until_complete(task)
        self.assertEqual({b}, done)
        self.assertEqual({a}, pending)

    def test_wait_really_done(self):
        self.suppress_log_errors()
        # there is possibility that some tasks in the pending list
        # became done but their callbacks haven't all been called yet

        @tasks.coroutine
        def coro1():
            yield from [None]
        @tasks.coroutine
        def coro2():
            yield from [None, None]

        a = tasks.Task(coro1())
        b = tasks.Task(coro2())
        task = tasks.Task(tasks.wait([b, a], return_when=tasks.FIRST_COMPLETED))

        done, pending = self.event_loop.run_until_complete(task)
        self.assertEqual({a, b}, done)

    def test_wait_first_exception(self):
        self.suppress_log_errors()

        a = tasks.sleep(10.0)
        @tasks.coroutine
        def exc():
            yield from []
            raise ZeroDivisionError('err')

        b = tasks.Task(exc())
        task = tasks.Task(tasks.wait(
                [b, a], return_when=tasks.FIRST_EXCEPTION))

        done, pending = self.event_loop.run_until_complete(task)
        self.assertEqual({b}, done)
        self.assertEqual({a}, pending)

    def test_wait_with_exception(self):
        self.suppress_log_errors()
        a = tasks.sleep(0.1)
        @tasks.coroutine
        def sleeper():
            yield from tasks.sleep(0.15)
            raise ZeroDivisionError('really')
        b = tasks.Task(sleeper())
        @tasks.coroutine
        def foo():
            done, pending = yield from tasks.wait([b, a])
            self.assertEqual(len(done), 2)
            self.assertEqual(pending, set())
            errors = set(f for f in done if f.exception() is not None)
            self.assertEqual(len(errors), 1)
        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.14)
        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 <= 0.01)

    def test_wait_with_timeout(self):
        a = tasks.sleep(0.1)
        b = tasks.sleep(0.15)
        @tasks.coroutine
        def foo():
            done, pending = yield from tasks.wait([b, a], timeout=0.11)
            self.assertEqual(done, set([a]))
            self.assertEqual(pending, set([b]))
        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.1)
        self.assertTrue(t1-t0 <= 0.13)

    def test_as_completed(self):
        @tasks.coroutine
        def sleeper(dt, x):
            yield from tasks.sleep(dt)
            return x
        a = sleeper(0.1, 'a')
        b = sleeper(0.1, 'b')
        c = sleeper(0.15, 'c')
        @tasks.coroutine
        def foo():
            values = []
            for f in tasks.as_completed([b, c, a]):
                values.append((yield from f))
            return values
        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.14)
        self.assertTrue('a' in res[:2])
        self.assertTrue('b' in res[:2])
        self.assertEqual(res[2], 'c')
        # Doing it again should take no time and exercise a different path.
        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 <= 0.01)

    def test_as_completed_with_timeout(self):
        self.suppress_log_errors()
        a = tasks.sleep(0.1, 'a')
        b = tasks.sleep(0.15, 'b')
        @tasks.coroutine
        def foo():
            values = []
            for f in tasks.as_completed([a, b], timeout=0.12):
                try:
                    v = yield from f
                    values.append((1, v))
                except futures.TimeoutError as exc:
                    values.append((2, exc))
            return values
        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.11)
        self.assertEqual(len(res), 2, res)
        self.assertEqual(res[0], (1, 'a'))
        self.assertEqual(res[1][0], 2)
        self.assertTrue(isinstance(res[1][1], futures.TimeoutError))

    def test_sleep(self):
        @tasks.coroutine
        def sleeper(dt, arg):
            yield from tasks.sleep(dt/2)
            res = yield from tasks.sleep(dt/2, arg)
            return res
        t = tasks.Task(sleeper(0.1, 'yeah'))
        t0 = time.monotonic()
        self.event_loop.run()
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.09)
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'yeah')

    def test_task_cancel_sleeping_task(self):
        sleepfut = None
        @tasks.task
        def sleep(dt):
            nonlocal sleepfut
            sleepfut = tasks.sleep(dt)
            try:
                t0 = time.monotonic()
                yield from sleepfut
            finally:
                t1 = time.monotonic()
        @tasks.task
        def doit():
            sleeper = sleep(5000)
            self.event_loop.call_later(0.1, sleeper.cancel)
            try:
                t0 = time.monotonic()
                yield from sleeper
            except futures.CancelledError:
                t1 = time.monotonic()
                return 'cancelled'
            else:
                return 'slept in'
        t0 = time.monotonic()
        doer = doit()
        self.assertEqual(self.event_loop.run_until_complete(doer), 'cancelled')
        t1 = time.monotonic()
        self.assertTrue(0.09 <= t1-t0 <= 0.13, (t1-t0, sleepfut, doer))

    @unittest.mock.patch('tulip.tasks.logging')
    def test_step_in_completed_task(self, m_logging):
        @tasks.coroutine
        def notmuch():
            yield from []
            return 'ko'

        task = tasks.Task(notmuch())
        task.set_result('ok')

        task._step()
        self.assertTrue(m_logging.warn.called)
        self.assertTrue(m_logging.warn.call_args[0][0].startswith(
            '_step(): already done: '))

    @unittest.mock.patch('tulip.tasks.logging')
    def test_step_result(self, m_logging):
        @tasks.coroutine
        def notmuch():
            yield from [None, 1]
            return 'ko'

        task = tasks.Task(notmuch())
        task._step()
        self.assertFalse(m_logging.warn.called)

        task._step()
        self.assertTrue(m_logging.warn.called)
        self.assertEqual(
            '_step(): bad yield: %r',
            m_logging.warn.call_args[0][0])
        self.assertEqual(1, m_logging.warn.call_args[0][1])

    def test_step_result_future(self):
        """If coroutine returns future, task waits on this future."""
        self.suppress_log_warnings()

        class Fut(futures.Future):
            def __init__(self, *args):
                self.cb_added = False
                super().__init__(*args)
            def add_done_callback(self, fn):
                self.cb_added = True
                super().add_done_callback(fn)

        fut = Fut()
        result = None

        @tasks.task
        def wait_for_future():
            nonlocal result
            result = yield from fut

        task = wait_for_future()
        self.event_loop.run_once()
        self.assertTrue(fut.cb_added)

        res = object()
        fut.set_result(res)
        self.event_loop.run_once()
        self.assertIs(res, result)

    def test_step_result_concurrent_future(self):
        # Coroutine returns concurrent.futures.Future
        self.suppress_log_warnings()

        class Fut(concurrent.futures.Future):
            def __init__(self):
                self.cb_added = False
                super().__init__()
            def add_done_callback(self, fn):
                self.cb_added = True
                super().add_done_callback(fn)

        c_fut = Fut()

        @tasks.coroutine
        def notmuch():
            yield from [c_fut]
            return (yield)

        task = tasks.Task(notmuch())
        task._step()
        self.assertTrue(c_fut.cb_added)

        res = object()
        c_fut.set_result(res)
        self.event_loop.run()
        self.assertIs(res, task.result())

    def test_step_with_baseexception(self):
        self.suppress_log_errors()

        @tasks.coroutine
        def notmutch():
            yield from []
            raise BaseException()

        task = tasks.Task(notmutch())
        self.assertRaises(BaseException, task._step)

        self.assertTrue(task.done())
        self.assertIsInstance(task.exception(), BaseException)

    def test_baseexception_during_cancel(self):
        self.suppress_log_errors()

        @tasks.coroutine
        def sleeper():
            yield from tasks.sleep(10)

        @tasks.coroutine
        def notmutch():
            try:
                yield from sleeper()
            except futures.CancelledError:
                raise BaseException()

        task = tasks.Task(notmutch())
        self.event_loop.run_once()

        task.cancel()
        self.assertFalse(task.done())

        self.assertRaises(BaseException, self.event_loop.run_once)

        self.assertTrue(task.done())
        self.assertTrue(task.cancelled())

    def test_iscoroutinefunction(self):
        def fn():
            pass

        self.assertFalse(tasks.iscoroutinefunction(fn))

        def fn1():
            yield
        self.assertFalse(tasks.iscoroutinefunction(fn1))

        @tasks.coroutine
        def fn2():
            yield
        self.assertTrue(tasks.iscoroutinefunction(fn2))

    def test_yield_vs_yield_from(self):
        fut = futures.Future()

        @tasks.task
        def wait_for_future():
            yield fut

        task = wait_for_future()
        self.assertRaises(
            RuntimeError,
            self.event_loop.run_until_complete, task)

    def test_yield_vs_yield_from_generator(self):
        fut = futures.Future()

        @tasks.coroutine
        def coro():
            yield from fut

        @tasks.task
        def wait_for_future():
            yield coro()

        task = wait_for_future()
        self.assertRaises(
            RuntimeError,
            self.event_loop.run_until_complete, task)


if __name__ == '__main__':
    unittest.main()
