"""Tests for tasks.py."""

import time
import unittest
import unittest.mock

from tulip import events
from tulip import futures
from tulip import tasks
from tulip import test_utils


class Dummy:

    def __repr__(self):
        return 'Dummy()'

    def __call__(self, *args):
        pass


class TaskTests(unittest.TestCase):

    def setUp(self):
        self.loop = events.new_event_loop()
        events.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_task_class(self):
        @tasks.coroutine
        def notmuch():
            return 'ok'
        t = tasks.Task(notmuch())
        self.loop.run_until_complete(t)
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'ok')
        self.assertIs(t._loop, self.loop)

        loop = events.new_event_loop()
        t = tasks.Task(notmuch(), loop=loop)
        self.assertIs(t._loop, loop)
        loop.close()

    def test_task_decorator(self):
        @tasks.task
        def notmuch():
            yield from []
            return 'ko'
        t = notmuch()
        self.loop.run_until_complete(t)
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'ko')

    def test_task_decorator_func(self):
        @tasks.task
        def notmuch():
            return 'ko'
        t = notmuch()
        self.loop.run_until_complete(t)
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'ko')

    def test_task_decorator_fut(self):
        fut = futures.Future(loop=self.loop)
        fut.set_result('ko')

        @tasks.task
        def notmuch():
            return fut
        t = notmuch()
        self.loop.run_until_complete(t)
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'ko')

    def test_async_coroutine(self):
        @tasks.coroutine
        def notmuch():
            return 'ok'
        t = tasks.async(notmuch())
        self.loop.run_until_complete(t)
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'ok')
        self.assertIs(t._loop, self.loop)

        loop = events.new_event_loop()
        t = tasks.async(notmuch(), loop=loop)
        self.assertIs(t._loop, loop)

    def test_async_future(self):
        f_orig = futures.Future(loop=self.loop)
        f_orig.set_result('ko')

        f = tasks.async(f_orig)
        self.loop.run_until_complete(f)
        self.assertTrue(f.done())
        self.assertEqual(f.result(), 'ko')
        self.assertIs(f, f_orig)

        with self.assertRaises(ValueError):
            loop = events.new_event_loop()
            f = tasks.async(f_orig, loop=loop)
        f = tasks.async(f_orig, loop=self.loop)
        self.assertIs(f, f_orig)

    def test_async_task(self):
        @tasks.coroutine
        def notmuch():
            return 'ok'
        t_orig = tasks.Task(notmuch())
        t = tasks.async(t_orig)
        self.loop.run_until_complete(t)
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'ok')
        self.assertIs(t, t_orig)

        with self.assertRaises(ValueError):
            loop = events.new_event_loop()
            t = tasks.async(t_orig, loop=loop)
        t = tasks.async(t_orig, loop=self.loop)
        self.assertIs(t, t_orig)

    def test_async_neither(self):
        with self.assertRaises(TypeError):
            tasks.async('ok')

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
                          self.loop.run_until_complete, t)
        self.assertEqual(repr(t), 'Task(<notmuch>)<CANCELLED>')
        t = notmuch()
        self.loop.run_until_complete(t)
        self.assertEqual(repr(t), "Task(<notmuch>)<result='abc'>")

    def test_task_repr_custom(self):
        @tasks.coroutine
        def coro():
            pass

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
            return 42

        @tasks.task
        def inner2():
            return 1000

        t = outer()
        self.assertEqual(self.loop.run_until_complete(t), 1042)

    def test_cancel(self):
        @tasks.task
        def task():
            yield from tasks.sleep(10.0)
            return 12

        t = task()
        self.loop.call_soon(t.cancel)
        self.assertRaises(
            futures.CancelledError, self.loop.run_until_complete, t)
        self.assertTrue(t.done())
        self.assertFalse(t.cancel())

    def test_cancel_yield(self):
        @tasks.task
        def task():
            yield
            yield
            return 12

        t = task()
        test_utils.run_briefly(self.loop)  # start coro
        t.cancel()
        self.assertRaises(
            futures.CancelledError, self.loop.run_until_complete, t)
        self.assertTrue(t.done())
        self.assertFalse(t.cancel())

    def test_cancel_done_future(self):
        fut1 = futures.Future(loop=self.loop)
        fut2 = futures.Future(loop=self.loop)
        fut3 = futures.Future(loop=self.loop)

        @tasks.task
        def task():
            yield from fut1
            try:
                yield from fut2
            except futures.CancelledError:
                pass
            yield from fut3

        t = task()
        test_utils.run_briefly(self.loop)
        fut1.set_result(None)
        t.cancel()
        test_utils.run_once(self.loop)  # process fut1 result, delay cancel
        self.assertFalse(t.done())
        test_utils.run_once(self.loop)  # cancel fut2, but coro still alive
        self.assertFalse(t.done())
        test_utils.run_briefly(self.loop)  # cancel fut3
        self.assertTrue(t.done())

        self.assertEqual(fut1.result(), None)
        self.assertTrue(fut2.cancelled())
        self.assertTrue(fut3.cancelled())
        self.assertTrue(t.cancelled())

    def test_future_timeout(self):
        @tasks.coroutine
        def coro():
            yield from tasks.sleep(10.0)
            return 12

        t = tasks.Task(coro(), timeout=0.1)

        self.assertRaises(
            futures.CancelledError,
            self.loop.run_until_complete, t)
        self.assertTrue(t.done())
        self.assertFalse(t.cancel())

    def test_future_timeout_catch(self):
        @tasks.coroutine
        def coro():
            yield from tasks.sleep(10.0)
            return 12

        class Cancelled(Exception):
            pass

        @tasks.coroutine
        def coro2():
            try:
                yield from tasks.Task(coro(), timeout=0.1)
            except futures.CancelledError:
                raise Cancelled()

        self.assertRaises(
            Cancelled, self.loop.run_until_complete, coro2())

    def test_cancel_in_coro(self):
        @tasks.coroutine
        def task():
            t.cancel()
            return 12

        t = tasks.Task(task())
        self.assertRaises(
            futures.CancelledError, self.loop.run_until_complete, t)
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
                    self.loop.stop()

        t = tasks.Task(task())
        t0 = time.monotonic()
        self.assertRaises(
            RuntimeError, self.loop.run_until_complete, t)
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
            futures.TimeoutError, self.loop.run_until_complete, t, 0.1)
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
        r = self.loop.run_until_complete(t, 10.0)
        t1 = time.monotonic()
        self.assertTrue(t.done())
        self.assertEqual(r, 42)
        self.assertTrue(0.08 <= t1-t0 <= 0.12)

    def test_wait(self):
        a = tasks.Task(tasks.sleep(0.1))
        b = tasks.Task(tasks.sleep(0.15))

        @tasks.coroutine
        def foo():
            done, pending = yield from tasks.wait([b, a])
            self.assertEqual(done, set([a, b]))
            self.assertEqual(pending, set())
            return 42

        t0 = time.monotonic()
        res = self.loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.14)
        self.assertEqual(res, 42)
        # Doing it again should take no time and exercise a different path.
        t0 = time.monotonic()
        res = self.loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 <= 0.01)
        # TODO: Test different return_when values.

    def test_wait_errors(self):
        self.assertRaises(
            ValueError, self.loop.run_until_complete,
            tasks.wait(set()))

        self.assertRaises(
            ValueError, self.loop.run_until_complete,
            tasks.wait([tasks.sleep(10.0)], return_when=-1))

    def test_wait_first_completed(self):
        a = tasks.Task(tasks.sleep(10.0))
        b = tasks.Task(tasks.sleep(0.1))
        task = tasks.Task(tasks.wait(
            [b, a], return_when=tasks.FIRST_COMPLETED))

        done, pending = self.loop.run_until_complete(task)
        self.assertEqual({b}, done)
        self.assertEqual({a}, pending)
        self.assertFalse(a.done())
        self.assertTrue(b.done())
        self.assertIsNone(b.result())

    def test_wait_really_done(self):
        # there is possibility that some tasks in the pending list
        # became done but their callbacks haven't all been called yet

        @tasks.coroutine
        def coro1():
            yield

        @tasks.coroutine
        def coro2():
            yield
            yield

        a = tasks.Task(coro1())
        b = tasks.Task(coro2())
        task = tasks.Task(
            tasks.wait([b, a], return_when=tasks.FIRST_COMPLETED))

        done, pending = self.loop.run_until_complete(task)
        self.assertEqual({a, b}, done)
        self.assertTrue(a.done())
        self.assertIsNone(a.result())
        self.assertTrue(b.done())
        self.assertIsNone(b.result())

    def test_wait_first_exception(self):
        # first_exception, task already has exception
        a = tasks.Task(tasks.sleep(10.0))

        @tasks.coroutine
        def exc():
            raise ZeroDivisionError('err')

        b = tasks.Task(exc())
        task = tasks.Task(tasks.wait(
            [b, a], return_when=tasks.FIRST_EXCEPTION))

        done, pending = self.loop.run_until_complete(task)
        self.assertEqual({b}, done)
        self.assertEqual({a}, pending)

    def test_wait_first_exception_in_wait(self):
        # first_exception, exception during waiting
        a = tasks.Task(tasks.sleep(10.0))

        @tasks.coroutine
        def exc():
            yield from tasks.sleep(0.01)
            raise ZeroDivisionError('err')

        b = tasks.Task(exc())
        task = tasks.wait([b, a], return_when=tasks.FIRST_EXCEPTION)

        done, pending = self.loop.run_until_complete(task)
        self.assertEqual({b}, done)
        self.assertEqual({a}, pending)

    def test_wait_with_exception(self):
        a = tasks.Task(tasks.sleep(0.1))

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
        self.loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.14)
        t0 = time.monotonic()
        self.loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 <= 0.01)

    def test_wait_with_timeout(self):
        a = tasks.Task(tasks.sleep(0.1))
        b = tasks.Task(tasks.sleep(0.15))

        @tasks.coroutine
        def foo():
            done, pending = yield from tasks.wait([b, a], timeout=0.11)
            self.assertEqual(done, set([a]))
            self.assertEqual(pending, set([b]))

        t0 = time.monotonic()
        self.loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.1)
        self.assertTrue(t1-t0 <= 0.13)

    def test_wait_concurrent_complete(self):
        a = tasks.Task(tasks.sleep(0.1))
        b = tasks.Task(tasks.sleep(0.15))

        done, pending = self.loop.run_until_complete(
            tasks.wait([b, a], timeout=0.1))

        self.assertEqual(done, set([a]))
        self.assertEqual(pending, set([b]))

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
        res = self.loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.14)
        self.assertTrue('a' in res[:2])
        self.assertTrue('b' in res[:2])
        self.assertEqual(res[2], 'c')
        # Doing it again should take no time and exercise a different path.
        t0 = time.monotonic()
        res = self.loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 <= 0.01)

    def test_as_completed_with_timeout(self):
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
        res = self.loop.run_until_complete(tasks.Task(foo()))
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.11)
        self.assertEqual(len(res), 2, res)
        self.assertEqual(res[0], (1, 'a'))
        self.assertEqual(res[1][0], 2)
        self.assertTrue(isinstance(res[1][1], futures.TimeoutError))

    def test_as_completed_reverse_wait(self):
        a = tasks.sleep(0.05, 'a')
        b = tasks.sleep(0.10, 'b')
        fs = {a, b}
        futs = list(tasks.as_completed(fs))
        self.assertEqual(len(futs), 2)
        x = self.loop.run_until_complete(futs[1])
        self.assertEqual(x, 'a')
        y = self.loop.run_until_complete(futs[0])
        self.assertEqual(y, 'b')

    def test_as_completed_concurrent(self):
        a = tasks.sleep(0.05, 'a')
        b = tasks.sleep(0.05, 'b')
        fs = {a, b}
        futs = list(tasks.as_completed(fs))
        self.assertEqual(len(futs), 2)
        waiter = tasks.wait(futs)
        done, pending = self.loop.run_until_complete(waiter)
        self.assertEqual(set(f.result() for f in done), {'a', 'b'})

    def test_sleep(self):
        @tasks.coroutine
        def sleeper(dt, arg):
            yield from tasks.sleep(dt/2)
            res = yield from tasks.sleep(dt/2, arg)
            return res

        t = tasks.Task(sleeper(0.1, 'yeah'))
        t0 = time.monotonic()
        self.loop.run_until_complete(t)
        t1 = time.monotonic()
        self.assertTrue(t1-t0 >= 0.09)
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'yeah')

    def test_sleep_cancel(self):
        t = tasks.Task(tasks.sleep(10.0, 'yeah'))

        handle = None
        orig_call_later = self.loop.call_later

        def call_later(self, delay, callback, *args):
            nonlocal handle
            handle = orig_call_later(self, delay, callback, *args)
            return handle

        self.loop.call_later = call_later
        test_utils.run_briefly(self.loop)

        self.assertFalse(handle._cancelled)

        t.cancel()
        test_utils.run_briefly(self.loop)
        self.assertTrue(handle._cancelled)

    def test_task_cancel_sleeping_task(self):
        sleepfut = None

        @tasks.task
        def sleep(dt):
            nonlocal sleepfut
            sleepfut = tasks.sleep(dt)
            try:
                time.monotonic()
                yield from sleepfut
            finally:
                time.monotonic()

        @tasks.task
        def doit():
            sleeper = sleep(5000)
            self.loop.call_later(0.1, sleeper.cancel)
            try:
                time.monotonic()
                yield from sleeper
            except futures.CancelledError:
                time.monotonic()
                return 'cancelled'
            else:
                return 'slept in'

        t0 = time.monotonic()
        doer = doit()
        self.assertEqual(self.loop.run_until_complete(doer), 'cancelled')
        t1 = time.monotonic()
        self.assertTrue(0.09 <= t1-t0 <= 0.13, (t1-t0, sleepfut, doer))

    def test_task_cancel_waiter_future(self):
        fut = futures.Future(loop=self.loop)

        @tasks.task
        def coro():
            try:
                yield from fut
            except futures.CancelledError:
                pass

        task = coro()
        test_utils.run_briefly(self.loop)
        self.assertIs(task._fut_waiter, fut)

        task.cancel()
        self.assertRaises(
            futures.CancelledError, self.loop.run_until_complete, task)
        self.assertIsNone(task._fut_waiter)
        self.assertTrue(fut.cancelled())

    def test_step_in_completed_task(self):
        @tasks.coroutine
        def notmuch():
            return 'ko'

        task = tasks.Task(notmuch())
        task.set_result('ok')

        self.assertRaises(AssertionError, task._step)

    def test_step_result(self):
        @tasks.coroutine
        def notmuch():
            yield None
            yield 1
            return 'ko'

        self.assertRaises(
            RuntimeError, self.loop.run_until_complete, notmuch())

    def test_step_result_future(self):
        # If coroutine returns future, task waits on this future.

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

        t = wait_for_future()
        test_utils.run_briefly(self.loop)
        self.assertTrue(fut.cb_added)

        res = object()
        fut.set_result(res)
        test_utils.run_briefly(self.loop)
        self.assertIs(res, result)
        self.assertTrue(t.done())
        self.assertIsNone(t.result())

    def test_step_with_baseexception(self):
        @tasks.coroutine
        def notmutch():
            raise BaseException()

        task = tasks.Task(notmutch())
        self.assertRaises(BaseException, task._step)

        self.assertTrue(task.done())
        self.assertIsInstance(task.exception(), BaseException)

    def test_baseexception_during_cancel(self):
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
        test_utils.run_briefly(self.loop)

        task.cancel()
        self.assertFalse(task.done())

        self.assertRaises(BaseException, test_utils.run_briefly, self.loop)

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
        fut = futures.Future(loop=self.loop)

        @tasks.task
        def wait_for_future():
            yield fut

        task = wait_for_future()
        with self.assertRaises(RuntimeError) as cm:
            self.loop.run_until_complete(task)

        self.assertTrue(fut.done())
        self.assertIs(fut.exception(), cm.exception)

    def test_yield_vs_yield_from_generator(self):
        @tasks.coroutine
        def coro():
            yield

        @tasks.task
        def wait_for_future():
            yield coro()

        task = wait_for_future()
        self.assertRaises(
            RuntimeError,
            self.loop.run_until_complete, task)

    def test_coroutine_non_gen_function(self):
        @tasks.coroutine
        def func():
            return 'test'

        self.assertTrue(tasks.iscoroutinefunction(func))

        coro = func()
        self.assertTrue(tasks.iscoroutine(coro))

        res = self.loop.run_until_complete(coro)
        self.assertEqual(res, 'test')

    def test_coroutine_non_gen_function_return_future(self):
        fut = futures.Future(loop=self.loop)

        @tasks.coroutine
        def func():
            return fut

        @tasks.coroutine
        def coro():
            fut.set_result('test')

        t1 = tasks.Task(func())
        t2 = tasks.Task(coro())
        res = self.loop.run_until_complete(t1)
        self.assertEqual(res, 'test')
        self.assertIsNone(t2.result())


if __name__ == '__main__':
    unittest.main()
