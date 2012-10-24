#!/usr/bin/env python3.3
"""Example coroutine scheduler, PEP-380-style ('yield from <generator>').

Requires Python 3.3.

There are likely micro-optimizations possible here, but that's not the point.

TODO:
- Docstrings.
- Unittests.

PATTERNS TO TRY:
- Wait for all, collate results.
- Wait for first N that are ready.
- Wait until some predicate becomes true.
- Run with timeout.

"""

__author__ = 'Guido van Rossum <guido@python.org>'

# Standard library imports (keep in alphabetic order).
from concurrent.futures import TimeoutError
import logging
import time


class Task:
    """Lightweight wrapper around a generator."""

    def __init__(self, sched, gen, name=None, *, timeout=None):
        self.sched = sched
        self.gen = gen
        self.name = name or gen.__name__
        if timeout is not None and timeout < 1000000:
            timeout += time.time()
        self.timeout = timeout
        self.alive = True

    def run(self):
        if not self.alive:
            return
        self.sched.current = self
        try:
            if self.timeout is not None and self.timeout < time.time():
                self.gen.throw(TimeoutError)
            else:
                next(self.gen)
        except StopIteration:
            self.alive = False
        except Exception:
            self.alive = False
            logging.exception('Uncaught exception in task %r', self.name)
        except BaseException:
            self.alive = False
            raise
        else:
            if self.sched.current is not None:
                self.start()
        finally:
            self.sched.current = None

    def start(self):
        self.sched.eventloop.call_soon(self.run)


class Scheduler:

    def __init__(self, eventloop, threadrunner):
        self.eventloop = eventloop  # polling.EventLoop instance.
        self.threadrunner = threadrunner  # polling.Threadrunner instance.
        self.current = None  # Current Task.

    def run(self):
        self.eventloop.run()

    def start(self, gen, name=None, *, timeout=None):
        Task(self, gen, name, timeout=timeout).start()

    def block_r(self, fd):
        self.block_io(fd, 'r')

    def block_w(self, fd):
        self.block_io(fd, 'w')

    def block_io(self, fd, flag):
        assert isinstance(fd, int), repr(fd)
        assert flag in ('r', 'w'), repr(flag)
        task = self.block()
        if flag == 'r':
            self.eventloop.add_reader(fd, self.unblock_io, fd, flag, task)
        else:
            self.eventloop.add_writer(fd, self.unblock_io, fd, flag, task)
        if task.timeout:
            self.eventloop.call_later(task.timeout,
                                      self.unblock_timeout, fd, flag, task)

    def block(self):
        assert self.current
        task = self.current
        self.current = None
        return task

    def unblock_io(self, fd, flag, task):
        if flag == 'r':
            self.eventloop.remove_reader(fd)
        else:
            self.eventloop.remove_writer(fd)
        task.start()

    def unblock_timeout(self, fd, flag, task):
        if not task.alive:
            return
        if flag == 'r':
            if fd in self.eventloop.readers:
                self.eventloop.remove_reader(fd)
        else:
            if fd in self.eventloop.writers:
                self.eventloop.remove_writer(fd)
        if task.alive:
            task.timeout = 0  # Force it to cancel
            task.start()

    def call_in_thread(self, func, *args):
        # TODO: Prove there is no race condition here.
        task = self.block()
        future = self.threadrunner.submit(func, *args)
        future.add_done_callback(lambda _: task.start())
        try:
            yield
        except TimeoutError:
            future.cancel()
            raise
        assert future.done()
        return future.result()

    def sleep(self, secs):
        task = self.block()
        self.eventloop.call_later(secs, task.start)
        yield
