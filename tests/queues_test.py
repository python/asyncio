"""Tests for queues.py"""

import unittest
import queue

from tulip import events
from tulip import locks
from tulip import queues
from tulip import tasks


class _QueueTestBase(unittest.TestCase):

    def setUp(self):
        self.event_loop = events.new_event_loop()
        events.set_event_loop(self.event_loop)

    def tearDown(self):
        self.event_loop.close()


class QueueBasicTests(_QueueTestBase):

    def _test_repr_or_str(self, fn, expect_id):
        """Test Queue's repr or str.

        fn is repr or str. expect_id is True if we expect the Queue's id to
        appear in fn(Queue()).
        """
        q = queues.Queue()
        self.assertTrue(fn(q).startswith('<Queue'))
        id_is_present = hex(id(q)) in fn(q)
        self.assertEqual(expect_id, id_is_present)

        @tasks.coroutine
        def add_getter():
            q = queues.Queue()
            # Start a task that waits to get.
            tasks.Task(q.get())
            # Let it start waiting.
            yield from tasks.sleep(0.1)
            self.assertTrue('_getters[1]' in fn(q))

        self.event_loop.run_until_complete(add_getter())

        @tasks.coroutine
        def add_putter():
            q = queues.Queue(maxsize=1)
            q.put_nowait(1)
            # Start a task that waits to put.
            tasks.Task(q.put(2))
            # Let it start waiting.
            yield from tasks.sleep(0.1)
            self.assertTrue('_putters[1]' in fn(q))

        self.event_loop.run_until_complete(add_putter())

        q = queues.Queue()
        q.put_nowait(1)
        self.assertTrue('_queue=[1]' in fn(q))

    def test_repr(self):
        self._test_repr_or_str(repr, True)

    def test_str(self):
        self._test_repr_or_str(str, False)

    def test_empty(self):
        q = queues.Queue()
        self.assertTrue(q.empty())
        q.put_nowait(1)
        self.assertFalse(q.empty())
        self.assertEqual(1, q.get_nowait())
        self.assertTrue(q.empty())

    def test_full(self):
        q = queues.Queue()
        self.assertFalse(q.full())

        q = queues.Queue(maxsize=1)
        q.put_nowait(1)
        self.assertTrue(q.full())

    def test_order(self):
        q = queues.Queue()
        for i in [1, 3, 2]:
            q.put_nowait(i)

        items = [q.get_nowait() for _ in range(3)]
        self.assertEqual([1, 3, 2], items)

    def test_maxsize(self):
        q = queues.Queue(maxsize=2)
        self.assertEqual(2, q.maxsize)
        have_been_put = []

        @tasks.coroutine
        def putter():
            for i in range(3):
                yield from q.put(i)
                have_been_put.append(i)

        @tasks.coroutine
        def test():
            tasks.Task(putter())
            yield from tasks.sleep(0.01)

            # The putter is blocked after putting two items.
            self.assertEqual([0, 1], have_been_put)
            self.assertEqual(0, q.get_nowait())

            # Let the putter resume and put last item.
            yield from tasks.sleep(0.01)
            self.assertEqual([0, 1, 2], have_been_put)
            self.assertEqual(1, q.get_nowait())
            self.assertEqual(2, q.get_nowait())

        self.event_loop.run_until_complete(test())


class QueueGetTests(_QueueTestBase):

    def test_blocking_get(self):
        q = queues.Queue()
        q.put_nowait(1)

        @tasks.coroutine
        def queue_get():
            return (yield from q.get())

        res = self.event_loop.run_until_complete(queue_get())
        self.assertEqual(1, res)

    def test_blocking_get_wait(self):
        q = queues.Queue()
        started = locks.EventWaiter()
        finished = False

        @tasks.coroutine
        def queue_get():
            nonlocal finished
            started.set()
            res = yield from q.get()
            finished = True
            return res

        @tasks.coroutine
        def queue_put():
            self.event_loop.call_later(0.01, q.put_nowait, 1)
            queue_get_task = tasks.Task(queue_get())
            yield from started.wait()
            self.assertFalse(finished)
            res = yield from queue_get_task
            self.assertTrue(finished)
            return res

        res = self.event_loop.run_until_complete(queue_put())
        self.assertEqual(1, res)

    def test_nonblocking_get(self):
        q = queues.Queue()
        q.put_nowait(1)
        self.assertEqual(1, q.get_nowait())

    def test_nonblocking_get_exception(self):
        q = queues.Queue()
        self.assertRaises(queue.Empty, q.get_nowait)

    def test_get_timeout(self):
        q = queues.Queue()

        @tasks.coroutine
        def queue_get():
            with self.assertRaises(queue.Empty):
                return (yield from q.get(timeout=0.01))

            # Get works after timeout, with blocking and non-blocking put.
            q.put_nowait(1)
            self.assertEqual(1, (yield from q.get()))

            tasks.Task(q.put(2))
            self.assertEqual(2, (yield from q.get()))

        self.event_loop.run_until_complete(queue_get())

    def test_get_timeout_cancelled(self):
        q = queues.Queue()

        @tasks.coroutine
        def queue_get():
            return (yield from q.get(timeout=0.05))

        @tasks.coroutine
        def test():
            get_task = tasks.Task(queue_get())
            yield from tasks.sleep(0.01)  # let the task start
            q.put_nowait(1)
            return (yield from get_task)

        self.assertEqual(1, self.event_loop.run_until_complete(test()))


class QueuePutTests(_QueueTestBase):

    def test_blocking_put(self):
        q = queues.Queue()

        @tasks.coroutine
        def queue_put():
            # No maxsize, won't block.
            yield from q.put(1)

        self.event_loop.run_until_complete(queue_put())

    def test_blocking_put_wait(self):
        q = queues.Queue(maxsize=1)
        started = locks.EventWaiter()
        finished = False

        @tasks.coroutine
        def queue_put():
            nonlocal finished
            started.set()
            yield from q.put(1)
            yield from q.put(2)
            finished = True

        @tasks.coroutine
        def queue_get():
            self.event_loop.call_later(0.01, q.get_nowait)
            queue_put_task = tasks.Task(queue_put())
            yield from started.wait()
            self.assertFalse(finished)
            yield from queue_put_task
            self.assertTrue(finished)

        self.event_loop.run_until_complete(queue_get())

    def test_nonblocking_put(self):
        q = queues.Queue()
        q.put_nowait(1)
        self.assertEqual(1, q.get_nowait())

    def test_nonblocking_put_exception(self):
        q = queues.Queue(maxsize=1)
        q.put_nowait(1)
        self.assertRaises(queue.Full, q.put_nowait, 2)

    def test_put_timeout(self):
        q = queues.Queue(1)
        q.put_nowait(0)

        @tasks.coroutine
        def queue_put():
            with self.assertRaises(queue.Full):
                return (yield from q.put(1, timeout=0.01))

            self.assertEqual(0, q.get_nowait())

            # Put works after timeout, with blocking and non-blocking get.
            get_task = tasks.Task(q.get())
            # Let the get start waiting.
            yield from tasks.sleep(0.01)
            q.put_nowait(2)
            self.assertEqual(2, (yield from get_task))

            q.put_nowait(3)
            self.assertEqual(3, q.get_nowait())

        self.event_loop.run_until_complete(queue_put())

    def test_put_timeout_cancelled(self):
        q = queues.Queue()

        @tasks.coroutine
        def queue_put():
            yield from q.put(1, timeout=0.01)

        @tasks.coroutine
        def test():
            tasks.Task(queue_put())
            return (yield from q.get())

        self.assertEqual(1, self.event_loop.run_until_complete(test()))


class LifoQueueTests(_QueueTestBase):

    def test_order(self):
        q = queues.LifoQueue()
        for i in [1, 3, 2]:
            q.put_nowait(i)

        items = [q.get_nowait() for _ in range(3)]
        self.assertEqual([2, 3, 1], items)


class PriorityQueueTests(_QueueTestBase):

    def test_order(self):
        q = queues.PriorityQueue()
        for i in [1, 3, 2]:
            q.put_nowait(i)

        items = [q.get_nowait() for _ in range(3)]
        self.assertEqual([1, 2, 3], items)


class JoinableQueueTests(_QueueTestBase):

    def test_task_done_underflow(self):
        q = queues.JoinableQueue()
        self.assertRaises(q.task_done)

    def test_task_done(self):
        q = queues.JoinableQueue()
        for i in range(100):
            q.put_nowait(i)

        accumulator = 0

        # Two workers get items from the queue and call task_done after each.
        # Join the queue and assert all items have been processed.

        @tasks.coroutine
        def worker():
            nonlocal accumulator

            while True:
                item = yield from q.get()
                accumulator += item
                q.task_done()

        @tasks.coroutine
        def test():
            for _ in range(2):
                tasks.Task(worker())

            yield from q.join()

        self.event_loop.run_until_complete(test())
        self.assertEqual(sum(range(100)), accumulator)

    def test_join_empty_queue(self):
        q = queues.JoinableQueue()

        # Test that a queue join()s successfully, and before anything else
        # (done twice for insurance).

        @tasks.coroutine
        def join():
            yield from q.join()
            yield from q.join()

        self.event_loop.run_until_complete(join())

    def test_join_timeout(self):
        q = queues.JoinableQueue()
        q.put_nowait(1)

        @tasks.coroutine
        def join():
            yield from q.join(0.1)

        # Join completes in ~ 0.1 seconds, although no one calls task_done().
        self.event_loop.run_until_complete(join())


if __name__ == '__main__':
    unittest.main()
