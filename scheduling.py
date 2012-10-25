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
    """Lightweight wrapper around a generator.

    This is a bit like a Future, but with a different interface.

    TODO:
    - cancellation.
    - wait for result.
    """

    def __init__(self, sched, gen, name=None, *, timeout=None):
        self.sched = sched
        self.gen = gen
        self.name = name or gen.__name__
        if timeout is not None and timeout < 1000000:
            timeout += time.time()
        self.timeout = timeout
        self.alive = True
        self.result = None
        self.exception = None

    def __repr__(self):
        return 'Task<%r, timeout=%s>(alive=%r, result=%r, exception=%r)' % (
            self.name, self.timeout, self.alive, self.result, self.exception)

    def run(self):
        if not self.alive:
            return
        self.sched.current = self
        try:
            if self.timeout is not None and self.timeout < time.time():
                self.gen.throw(TimeoutError)
            else:
                next(self.gen)
        except StopIteration as exc:
            self.result = exc.value
            self.alive = False
        except Exception as exc:
            self.exception = exc
            self.alive = False
            logging.exception('Uncaught exception in task %r', self.name)
        except BaseException:
            self.exception = exc
            self.alive = False
            raise
        else:
            if self.sched.current is not None:
                self.start()
        finally:
            self.sched.current = None

    def start(self):
        if self.alive:
            self.sched.eventloop.call_soon(self.run)


class Scheduler:

    def __init__(self, eventloop, threadrunner):
        self.eventloop = eventloop  # polling.EventLoop instance.
        self.threadrunner = threadrunner  # polling.Threadrunner instance.
        self.current = None  # Current Task.

    def run(self):
        self.eventloop.run()

    def newtask(self, gen, name=None, *, timeout=None):
        return Task(self, gen, name, timeout=timeout)

    def start(self, gen, name=None, *, timeout=None):
        task = self.newtask(gen, name, timeout=timeout)
        task.start()
        return task

    def block_r(self, fd):
        self.block_io(fd, 'r')

    def block_w(self, fd):
        self.block_io(fd, 'w')

    def block_io(self, fd, flag):
        assert isinstance(fd, int), repr(fd)
        assert flag in ('r', 'w'), repr(flag)
        task = self.block()
        dcall = None
        if task.timeout:
            dcall = self.eventloop.call_later(task.timeout,
                                              self.unblock_timeout,
                                              fd, flag, task)
        if flag == 'r':
            self.eventloop.add_reader(fd, self.unblock_io,
                                      fd, flag, task, dcall)
        else:
            self.eventloop.add_writer(fd, self.unblock_io,
                                      fd, flag, task, dcall)

    def block(self):
        assert self.current
        task = self.current
        self.current = None
        return task

    def unblock_io(self, fd, flag, task, dcall):
        if dcall is not None:
            dcall.cancel()
        if flag == 'r':
            self.eventloop.remove_reader(fd)
        else:
            self.eventloop.remove_writer(fd)
        task.start()

    def unblock_timeout(self, fd, flag, task):
        # NOTE: Due to the call_soon() semantics, we can't guarantee
        # that unblock_timeout() isn't called *after* unblock_io() has
        # already been called.  So we must write this defensively.
        # TODO: Analyse this further for race conditions etc.
        if flag == 'r':
            if fd in self.eventloop.readers:
                self.eventloop.remove_reader(fd)
        else:
            if fd in self.eventloop.writers:
                self.eventloop.remove_writer(fd)
        task.timeout = 0  # Force it to cancel.
        task.start()

    def call_in_thread(self, func, *args, executor=None):
        # TODO: Prove there is no race condition here.
        task = self.block()
        future = self.threadrunner.submit(func, *args, executor=executor)
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
