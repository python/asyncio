"""Tests for lock.py"""

import time
import unittest
import unittest.mock

from tulip import events
from tulip import futures
from tulip import locks
from tulip import tasks


class LockTests(unittest.TestCase):

    def setUp(self):
        self.event_loop = events.new_event_loop()
        events.set_event_loop(self.event_loop)

    def tearDown(self):
        self.event_loop.close()

    def test_repr(self):
        lock = locks.Lock()
        self.assertTrue(repr(lock).endswith('[unlocked]>'))

        @tasks.coroutine
        def acquire_lock():
            yield from lock

        self.event_loop.run_until_complete(acquire_lock())
        self.assertTrue(repr(lock).endswith('[locked]>'))

    def test_lock(self):
        lock = locks.Lock()

        @tasks.coroutine
        def acquire_lock():
            return (yield from lock)

        res = self.event_loop.run_until_complete(acquire_lock())

        self.assertTrue(res)
        self.assertTrue(lock.locked())

        lock.release()
        self.assertFalse(lock.locked())

    def test_acquire(self):
        lock = locks.Lock()
        result = []

        self.assertTrue(
            self.event_loop.run_until_complete(lock.acquire()))

        @tasks.coroutine
        def c1(result):
            if (yield from lock.acquire()):
                result.append(1)
            return True

        @tasks.coroutine
        def c2(result):
            if (yield from lock.acquire()):
                result.append(2)
            return True

        @tasks.coroutine
        def c3(result):
            if (yield from lock.acquire()):
                result.append(3)
            return True

        t1 = tasks.Task(c1(result))
        t2 = tasks.Task(c2(result))

        self.event_loop.run_once()
        self.assertEqual([], result)

        lock.release()
        self.event_loop.run_once()
        self.assertEqual([1], result)

        self.event_loop.run_once()
        self.assertEqual([1], result)

        t3 = tasks.Task(c3(result))

        lock.release()
        self.event_loop.run_once()
        self.assertEqual([1, 2], result)

        lock.release()
        self.event_loop.run_once()
        self.assertEqual([1, 2, 3], result)

        self.assertTrue(t1.done())
        self.assertTrue(t1.result())
        self.assertTrue(t2.done())
        self.assertTrue(t2.result())
        self.assertTrue(t3.done())
        self.assertTrue(t3.result())

    def test_acquire_timeout(self):
        lock = locks.Lock()
        self.assertTrue(
            self.event_loop.run_until_complete(lock.acquire()))

        t0 = time.monotonic()
        acquired = self.event_loop.run_until_complete(
            lock.acquire(timeout=0.1))
        self.assertFalse(acquired)

        total_time = (time.monotonic() - t0)
        self.assertTrue(0.08 < total_time < 0.12)

        lock = locks.Lock()
        self.event_loop.run_until_complete(lock.acquire())

        self.event_loop.call_later(0.01, lock.release)
        acquired = self.event_loop.run_until_complete(lock.acquire(10.1))
        self.assertTrue(acquired)

    def test_acquire_timeout_mixed(self):
        lock = locks.Lock()
        self.event_loop.run_until_complete(lock.acquire())
        tasks.Task(lock.acquire())
        tasks.Task(lock.acquire())
        acquire_task = tasks.Task(lock.acquire(0.01))
        tasks.Task(lock.acquire())

        acquired = self.event_loop.run_until_complete(acquire_task)
        self.assertFalse(acquired)

        self.assertEqual(3, len(lock._waiters))

    def test_acquire_cancel(self):
        lock = locks.Lock()
        self.assertTrue(
            self.event_loop.run_until_complete(lock.acquire()))

        task = tasks.Task(lock.acquire())
        self.event_loop.call_soon(task.cancel)
        self.assertRaises(
            futures.CancelledError,
            self.event_loop.run_until_complete, task)
        self.assertFalse(lock._waiters)

    def test_release_not_acquired(self):
        lock = locks.Lock()

        self.assertRaises(RuntimeError, lock.release)

    def test_release_no_waiters(self):
        lock = locks.Lock()
        self.event_loop.run_until_complete(lock.acquire())
        self.assertTrue(lock.locked())

        lock.release()
        self.assertFalse(lock.locked())

    def test_context_manager(self):
        lock = locks.Lock()

        @tasks.task
        def acquire_lock():
            return (yield from lock)

        with self.event_loop.run_until_complete(acquire_lock()):
            self.assertTrue(lock.locked())

        self.assertFalse(lock.locked())

    def test_context_manager_no_yield(self):
        lock = locks.Lock()

        try:
            with lock:
                self.fail('RuntimeError is not raised in with expression')
        except RuntimeError as err:
            self.assertEqual(
                str(err),
                '"yield from" should be used as context manager expression')


class EventWaiterTests(unittest.TestCase):

    def setUp(self):
        self.event_loop = events.new_event_loop()
        events.set_event_loop(self.event_loop)

    def tearDown(self):
        self.event_loop.close()

    def test_repr(self):
        ev = locks.EventWaiter()
        self.assertTrue(repr(ev).endswith('[unset]>'))

        ev.set()
        self.assertTrue(repr(ev).endswith('[set]>'))

    def test_wait(self):
        ev = locks.EventWaiter()
        self.assertFalse(ev.is_set())

        result = []

        @tasks.coroutine
        def c1(result):
            if (yield from ev.wait()):
                result.append(1)

        @tasks.coroutine
        def c2(result):
            if (yield from ev.wait()):
                result.append(2)

        @tasks.coroutine
        def c3(result):
            if (yield from ev.wait()):
                result.append(3)

        t1 = tasks.Task(c1(result))
        t2 = tasks.Task(c2(result))

        self.event_loop.run_once()
        self.assertEqual([], result)

        t3 = tasks.Task(c3(result))

        ev.set()
        self.event_loop.run_once()
        self.assertEqual([3, 1, 2], result)

        self.assertTrue(t1.done())
        self.assertIsNone(t1.result())
        self.assertTrue(t2.done())
        self.assertIsNone(t2.result())
        self.assertTrue(t3.done())
        self.assertIsNone(t3.result())

    def test_wait_on_set(self):
        ev = locks.EventWaiter()
        ev.set()

        res = self.event_loop.run_until_complete(ev.wait())
        self.assertTrue(res)

    def test_wait_timeout(self):
        ev = locks.EventWaiter()

        t0 = time.monotonic()
        res = self.event_loop.run_until_complete(ev.wait(0.1))
        self.assertFalse(res)
        total_time = (time.monotonic() - t0)
        self.assertTrue(0.08 < total_time < 0.12)

        ev = locks.EventWaiter()
        self.event_loop.call_later(0.01, ev.set)
        acquired = self.event_loop.run_until_complete(ev.wait(10.1))
        self.assertTrue(acquired)

    def test_wait_timeout_mixed(self):
        ev = locks.EventWaiter()
        tasks.Task(ev.wait())
        tasks.Task(ev.wait())
        acquire_task = tasks.Task(ev.wait(0.1))
        tasks.Task(ev.wait())

        t0 = time.monotonic()
        acquired = self.event_loop.run_until_complete(acquire_task)
        self.assertFalse(acquired)

        total_time = (time.monotonic() - t0)
        self.assertTrue(0.08 < total_time < 0.12)

        self.assertEqual(3, len(ev._waiters))

    def test_wait_cancel(self):
        ev = locks.EventWaiter()

        wait = tasks.Task(ev.wait())
        self.event_loop.call_soon(wait.cancel)
        self.assertRaises(
            futures.CancelledError,
            self.event_loop.run_until_complete, wait)
        self.assertFalse(ev._waiters)

    def test_clear(self):
        ev = locks.EventWaiter()
        self.assertFalse(ev.is_set())

        ev.set()
        self.assertTrue(ev.is_set())

        ev.clear()
        self.assertFalse(ev.is_set())

    def test_clear_with_waiters(self):
        ev = locks.EventWaiter()
        result = []

        @tasks.coroutine
        def c1(result):
            if (yield from ev.wait()):
                result.append(1)
            return True

        t = tasks.Task(c1(result))
        self.event_loop.run_once()
        self.assertEqual([], result)

        ev.set()
        ev.clear()
        self.assertFalse(ev.is_set())

        ev.set()
        ev.set()
        self.assertEqual(1, len(ev._waiters))

        self.event_loop.run_once()
        self.assertEqual([1], result)
        self.assertEqual(0, len(ev._waiters))

        self.assertTrue(t.done())
        self.assertTrue(t.result())


class ConditionTests(unittest.TestCase):

    def setUp(self):
        self.event_loop = events.new_event_loop()
        events.set_event_loop(self.event_loop)

    def tearDown(self):
        self.event_loop.close()

    def test_wait(self):
        cond = locks.Condition()
        result = []

        @tasks.coroutine
        def c1(result):
            yield from cond.acquire()
            if (yield from cond.wait()):
                result.append(1)
            return True

        @tasks.coroutine
        def c2(result):
            yield from cond.acquire()
            if (yield from cond.wait()):
                result.append(2)
            return True

        @tasks.coroutine
        def c3(result):
            yield from cond.acquire()
            if (yield from cond.wait()):
                result.append(3)
            return True

        t1 = tasks.Task(c1(result))
        t2 = tasks.Task(c2(result))
        t3 = tasks.Task(c3(result))

        self.event_loop.run_once()
        self.assertEqual([], result)
        self.assertFalse(cond.locked())

        self.assertTrue(
            self.event_loop.run_until_complete(cond.acquire()))
        cond.notify()
        self.event_loop.run_once()
        self.assertEqual([], result)
        self.assertTrue(cond.locked())

        cond.release()
        self.event_loop.run_once()
        self.assertEqual([1], result)
        self.assertTrue(cond.locked())

        cond.notify(2)
        self.event_loop.run_once()
        self.assertEqual([1], result)
        self.assertTrue(cond.locked())

        cond.release()
        self.event_loop.run_once()
        self.assertEqual([1, 2], result)
        self.assertTrue(cond.locked())

        cond.release()
        self.event_loop.run_once()
        self.assertEqual([1, 2, 3], result)
        self.assertTrue(cond.locked())

        self.assertTrue(t1.done())
        self.assertTrue(t1.result())
        self.assertTrue(t2.done())
        self.assertTrue(t2.result())
        self.assertTrue(t3.done())
        self.assertTrue(t3.result())

    def test_wait_timeout(self):
        cond = locks.Condition()
        self.event_loop.run_until_complete(cond.acquire())

        t0 = time.monotonic()
        wait = self.event_loop.run_until_complete(cond.wait(0.1))
        self.assertFalse(wait)
        self.assertTrue(cond.locked())

        total_time = (time.monotonic() - t0)
        self.assertTrue(0.08 < total_time < 0.12)

    def test_wait_cancel(self):
        cond = locks.Condition()
        self.event_loop.run_until_complete(cond.acquire())

        wait = tasks.Task(cond.wait())
        self.event_loop.call_soon(wait.cancel)
        self.assertRaises(
            futures.CancelledError,
            self.event_loop.run_until_complete, wait)
        self.assertFalse(cond._condition_waiters)
        self.assertTrue(cond.locked())

    def test_wait_unacquired(self):
        cond = locks.Condition()
        self.assertRaises(
            RuntimeError,
            self.event_loop.run_until_complete, cond.wait())

    def test_wait_for(self):
        cond = locks.Condition()
        presult = False

        def predicate():
            return presult

        result = []

        @tasks.coroutine
        def c1(result):
            yield from cond.acquire()
            if (yield from cond.wait_for(predicate)):
                result.append(1)
                cond.release()
            return True

        t = tasks.Task(c1(result))

        self.event_loop.run_once()
        self.assertEqual([], result)

        self.event_loop.run_until_complete(cond.acquire())
        cond.notify()
        cond.release()
        self.event_loop.run_once()
        self.assertEqual([], result)

        presult = True
        self.event_loop.run_until_complete(cond.acquire())
        cond.notify()
        cond.release()
        self.event_loop.run_once()
        self.assertEqual([1], result)

        self.assertTrue(t.done())
        self.assertTrue(t.result())

    def test_wait_for_timeout(self):
        cond = locks.Condition()

        result = []

        predicate = unittest.mock.Mock()
        predicate.return_value = False

        @tasks.coroutine
        def c1(result):
            yield from cond.acquire()
            if (yield from cond.wait_for(predicate, 0.1)):
                result.append(1)
            else:
                result.append(2)
            cond.release()

        wait_for = tasks.Task(c1(result))

        t0 = time.monotonic()

        self.event_loop.run_once()
        self.assertEqual([], result)

        self.event_loop.run_until_complete(cond.acquire())
        cond.notify()
        cond.release()
        self.event_loop.run_once()
        self.assertEqual([], result)

        self.event_loop.run_until_complete(wait_for)
        self.assertEqual([2], result)
        self.assertEqual(3, predicate.call_count)

        total_time = (time.monotonic() - t0)
        self.assertTrue(0.08 < total_time < 0.12)

    def test_wait_for_unacquired(self):
        cond = locks.Condition()

        # predicate can return true immediately
        res = self.event_loop.run_until_complete(
            cond.wait_for(lambda: [1, 2, 3]))
        self.assertEqual([1, 2, 3], res)

        self.assertRaises(
            RuntimeError,
            self.event_loop.run_until_complete,
            cond.wait_for(lambda: False))

    def test_notify(self):
        cond = locks.Condition()
        result = []

        @tasks.coroutine
        def c1(result):
            yield from cond.acquire()
            if (yield from cond.wait()):
                result.append(1)
                cond.release()
            return True

        @tasks.coroutine
        def c2(result):
            yield from cond.acquire()
            if (yield from cond.wait()):
                result.append(2)
                cond.release()
            return True

        @tasks.coroutine
        def c3(result):
            yield from cond.acquire()
            if (yield from cond.wait()):
                result.append(3)
                cond.release()
            return True

        t1 = tasks.Task(c1(result))
        t2 = tasks.Task(c2(result))
        t3 = tasks.Task(c3(result))

        self.event_loop.run_once()
        self.assertEqual([], result)

        self.event_loop.run_until_complete(cond.acquire())
        cond.notify(1)
        cond.release()
        self.event_loop.run_once()
        self.assertEqual([1], result)

        self.event_loop.run_until_complete(cond.acquire())
        cond.notify(1)
        cond.notify(2048)
        cond.release()
        self.event_loop.run_once()
        self.assertEqual([1, 2, 3], result)

        self.assertTrue(t1.done())
        self.assertTrue(t1.result())
        self.assertTrue(t2.done())
        self.assertTrue(t2.result())
        self.assertTrue(t3.done())
        self.assertTrue(t3.result())

    def test_notify_all(self):
        cond = locks.Condition()

        result = []

        @tasks.coroutine
        def c1(result):
            yield from cond.acquire()
            if (yield from cond.wait()):
                result.append(1)
                cond.release()
            return True

        @tasks.coroutine
        def c2(result):
            yield from cond.acquire()
            if (yield from cond.wait()):
                result.append(2)
                cond.release()
            return True

        t1 = tasks.Task(c1(result))
        t2 = tasks.Task(c2(result))

        self.event_loop.run_once()
        self.assertEqual([], result)

        self.event_loop.run_until_complete(cond.acquire())
        cond.notify_all()
        cond.release()
        self.event_loop.run_once()
        self.assertEqual([1, 2], result)

        self.assertTrue(t1.done())
        self.assertTrue(t1.result())
        self.assertTrue(t2.done())
        self.assertTrue(t2.result())

    def test_notify_unacquired(self):
        cond = locks.Condition()
        self.assertRaises(RuntimeError, cond.notify)

    def test_notify_all_unacquired(self):
        cond = locks.Condition()
        self.assertRaises(RuntimeError, cond.notify_all)


class SemaphoreTests(unittest.TestCase):

    def setUp(self):
        self.event_loop = events.new_event_loop()
        events.set_event_loop(self.event_loop)

    def tearDown(self):
        self.event_loop.close()

    def test_repr(self):
        sem = locks.Semaphore()
        self.assertTrue(repr(sem).endswith('[unlocked,value:1]>'))

        self.event_loop.run_until_complete(sem.acquire())
        self.assertTrue(repr(sem).endswith('[locked]>'))

    def test_semaphore(self):
        sem = locks.Semaphore()
        self.assertEqual(1, sem._value)

        @tasks.task
        def acquire_lock():
            return (yield from sem)

        res = self.event_loop.run_until_complete(acquire_lock())

        self.assertTrue(res)
        self.assertTrue(sem.locked())
        self.assertEqual(0, sem._value)

        sem.release()
        self.assertFalse(sem.locked())
        self.assertEqual(1, sem._value)

    def test_semaphore_value(self):
        self.assertRaises(ValueError, locks.Semaphore, -1)

    def test_acquire(self):
        sem = locks.Semaphore(3)
        result = []

        self.assertTrue(
            self.event_loop.run_until_complete(sem.acquire()))
        self.assertTrue(
            self.event_loop.run_until_complete(sem.acquire()))
        self.assertFalse(sem.locked())

        @tasks.coroutine
        def c1(result):
            yield from sem.acquire()
            result.append(1)
            return True

        @tasks.coroutine
        def c2(result):
            yield from sem.acquire()
            result.append(2)
            return True

        @tasks.coroutine
        def c3(result):
            yield from sem.acquire()
            result.append(3)
            return True

        @tasks.coroutine
        def c4(result):
            yield from sem.acquire()
            result.append(4)
            return True

        t1 = tasks.Task(c1(result))
        t2 = tasks.Task(c2(result))
        t3 = tasks.Task(c3(result))

        self.event_loop.run_once()
        self.assertEqual([1], result)
        self.assertTrue(sem.locked())
        self.assertEqual(2, len(sem._waiters))
        self.assertEqual(0, sem._value)

        t4 = tasks.Task(c4(result))

        sem.release()
        sem.release()
        self.assertEqual(2, sem._value)

        self.event_loop.run_once()
        self.assertEqual(0, sem._value)
        self.assertEqual([1, 2, 3], result)
        self.assertTrue(sem.locked())
        self.assertEqual(1, len(sem._waiters))
        self.assertEqual(0, sem._value)

        self.assertTrue(t1.done())
        self.assertTrue(t1.result())
        self.assertTrue(t2.done())
        self.assertTrue(t2.result())
        self.assertTrue(t3.done())
        self.assertTrue(t3.result())
        self.assertFalse(t4.done())

    def test_acquire_timeout(self):
        sem = locks.Semaphore()
        self.event_loop.run_until_complete(sem.acquire())

        t0 = time.monotonic()
        acquired = self.event_loop.run_until_complete(sem.acquire(0.1))
        self.assertFalse(acquired)

        total_time = (time.monotonic() - t0)
        self.assertTrue(0.08 < total_time < 0.12)

        sem = locks.Semaphore()
        self.event_loop.run_until_complete(sem.acquire())

        self.event_loop.call_later(0.01, sem.release)
        acquired = self.event_loop.run_until_complete(sem.acquire(10.1))
        self.assertTrue(acquired)

    def test_acquire_timeout_mixed(self):
        sem = locks.Semaphore()
        self.event_loop.run_until_complete(sem.acquire())
        tasks.Task(sem.acquire())
        tasks.Task(sem.acquire())
        acquire_task = tasks.Task(sem.acquire(0.1))
        tasks.Task(sem.acquire())

        t0 = time.monotonic()
        acquired = self.event_loop.run_until_complete(acquire_task)
        self.assertFalse(acquired)

        total_time = (time.monotonic() - t0)
        self.assertTrue(0.08 < total_time < 0.12)

        self.assertEqual(3, len(sem._waiters))

    def test_acquire_cancel(self):
        sem = locks.Semaphore()
        self.event_loop.run_until_complete(sem.acquire())

        acquire = tasks.Task(sem.acquire())
        self.event_loop.call_soon(acquire.cancel)
        self.assertRaises(
            futures.CancelledError,
            self.event_loop.run_until_complete, acquire)
        self.assertFalse(sem._waiters)

    def test_release_not_acquired(self):
        sem = locks.Semaphore(bound=True)

        self.assertRaises(ValueError, sem.release)

    def test_release_no_waiters(self):
        sem = locks.Semaphore()
        self.event_loop.run_until_complete(sem.acquire())
        self.assertTrue(sem.locked())

        sem.release()
        self.assertFalse(sem.locked())

    def test_context_manager(self):
        sem = locks.Semaphore(2)

        @tasks.task
        def acquire_lock():
            return (yield from sem)

        with self.event_loop.run_until_complete(acquire_lock()):
            self.assertFalse(sem.locked())
            self.assertEqual(1, sem._value)

            with self.event_loop.run_until_complete(acquire_lock()):
                self.assertTrue(sem.locked())

        self.assertEqual(2, sem._value)


if __name__ == '__main__':
    unittest.main()
