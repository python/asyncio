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
- Various synchronization primitives (Lock, RLock, Event, Condition,
  Semaphore, BoundedSemaphore, Barrier).
"""

__author__ = 'Guido van Rossum <guido@python.org>'

# Standard library imports (keep in alphabetic order).
from concurrent.futures import TimeoutError
import logging
import threading
import time

import polling


class Context(threading.local):
    """Thread-local context.

    We use this to avoid having to explicitly pass around an event loop
    or something to hold the current task.

    TODO: Add an API so frameworks can substitute a different notion
    of context more easily.
    """

    def __init__(self, eventloop=None, threadrunner=None):
        # Default event loop and thread runner are lazily constructed
        # when first accessed.
        self._eventloop = eventloop
        self._threadrunner = threadrunner
        self.current_task = None

    @property
    def eventloop(self):
        if self._eventloop is None:
            self._eventloop = polling.EventLoop()
        return self._eventloop

    @property
    def threadrunner(self):
        if self._threadrunner is None:
            self._threadrunner = polling.ThreadRunner(self.eventloop)
        return self._threadrunner

    
context = Context()  # Thread-local!


class Task:
    """Wrapper around a stack of generators.

    This is a bit like a Future, but with a different interface.

    TODO:
    - cancellation.
    - wait for result.
    """

    def __init__(self, gen, name=None, *, timeout=None):
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
        context.current_task = self
        try:
            if self.timeout is not None and self.timeout < time.time():
                self.gen.throw(TimeoutError)
            else:
                next(self.gen)
        except StopIteration as exc:
            self.alive = False
            self.result = exc.value
        except Exception as exc:
            self.alive = False
            self.exception = exc
            logging.exception('Uncaught exception in task %r', self.name)
        except BaseException:
            self.alive = False
            self.exception = exc
            raise
        else:
            if context.current_task is not None:
                self.start()
        finally:
            context.current_task = None

    def start(self):
        if self.alive:
            context.eventloop.call_soon(self.run)


def run():
    context.eventloop.run()


def sleep(secs):
    task = block()
    context.eventloop.call_later(secs, task.start)
    yield


def block_r(fd):
    block_io(fd, 'r')


def block_w(fd):
        block_io(fd, 'w')


def block_io(fd, flag):
    assert isinstance(fd, int), repr(fd)
    assert flag in ('r', 'w'), repr(flag)
    task = block()
    dcall = None
    if task.timeout:
        dcall = context.eventloop.call_later(task.timeout, unblock_timeout,
                                             fd, flag, task)
    if flag == 'r':
        context.eventloop.add_reader(fd, unblock_io, fd, flag, task, dcall)
    else:
        context.eventloop.add_writer(fd, unblock_io, fd, flag, task, dcall)


def block():
    assert context.current_task
    task = context.current_task
    context.current_task = None
    return task


def unblock_io(fd, flag, task, dcall):
    if dcall is not None:
        dcall.cancel()
    if flag == 'r':
        context.eventloop.remove_reader(fd)
    else:
        context.eventloop.remove_writer(fd)
    task.start()


def unblock_timeout(fd, flag, task):
    # NOTE: Due to the call_soon() semantics, we can't guarantee
    # that unblock_timeout() isn't called *after* unblock_io() has
    # already been called.  So we must write this defensively.
    # TODO: Analyse this further for race conditions etc.
    if flag == 'r':
        if fd in context.eventloop.readers:
            context.eventloop.remove_reader(fd)
    else:
        if fd in context.eventloop.writers:
            context.eventloop.remove_writer(fd)
    task.timeout = 0  # Force it to cancel.
    task.start()


def call_in_thread(func, *args, executor=None):
    # TODO: Prove there is no race condition here.
    task = block()
    future = context.threadrunner.submit(func, *args, executor=executor)
    # Don't reference context in the lambda!  It is called in another thread.
    this_eventloop = context.eventloop
    future.add_done_callback(lambda _: this_eventloop.call_soon(task.run))
    try:
        yield
    except TimeoutError:
        future.cancel()
        raise
    assert future.done()
    return future.result()
