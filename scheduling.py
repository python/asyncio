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
- Various synchronization primitives (Lock, RLock, Event, Condition,
  Semaphore, BoundedSemaphore, Barrier).
"""

__author__ = 'Guido van Rossum <guido@python.org>'

# Standard library imports (keep in alphabetic order).
from concurrent.futures import CancelledError, TimeoutError
import logging
import threading
import time
import types

# Local imports (keep in alphabetic order).
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
    - wait for result.
    """

    def __init__(self, gen, name=None, *, timeout=None):
        assert isinstance(gen, types.GeneratorType), repr(gen)
        self.gen = gen
        self.name = name or gen.__name__
        self.timeout = timeout
        self.eventloop = context.eventloop
        self.canceleer = None
        if timeout is not None:
            self.canceleer = self.eventloop.call_later(timeout, self.cancel)
        self.blocked = False
        self.unblocker = None
        self.cancelled = False
        self.must_cancel = False
        self.alive = True
        self.result = None
        self.exception = None
        self.done_callbacks = []

    def add_done_callback(self, done_callback):
        # For better or for worse, the callback will always be called
        # with the task as an argument, like concurrent.futures.Future.
        self.done_callbacks.append(done_callback)

    def __repr__(self):
        return 'Task<%r, timeout=%s>(alive=%r, result=%r, exception=%r)' % (
            self.name, self.timeout, self.alive, self.result, self.exception)

    def cancel(self):
        if self.alive:
            self.must_cancel = True
            self.unblock()

    def step(self):
        assert self.alive
        try:
            context.current_task = self
            if self.must_cancel:
                self.must_cancel = False
                self.cancelled = True
                self.gen.throw(CancelledError())
            else:
                next(self.gen)
        except StopIteration as exc:
            self.alive = False
            self.result = exc.value
        except Exception as exc:
            self.alive = False
            self.exception = exc
            logging.debug('Uncaught exception in task %r', self.name,
                          exc_info=True, stack_info=True)
        except BaseException:
            self.alive = False
            self.exception = exc
            raise
        else:
            if not self.blocked:
                self.eventloop.call_soon(self.step)
        finally:
            context.current_task = None
            if not self.alive:
                # Cancel timeout callback if set.
                if self.canceleer is not None:
                    self.canceleer.cancel()
                # Schedule done_callbacks.
                for callback in self.done_callbacks:
                    self.eventloop.call_soon(callback, self)

    def start(self):
        assert self.alive
        self.eventloop.call_soon(self.step)

    def block(self, unblock_callback=None, *unblock_args):
        assert self is context.current_task
        assert self.alive
        assert not self.blocked
        self.blocked = True
        self.unblocker = (unblock_callback, unblock_args)

    def unblock(self, unused=None):
        # Ignore optional argument so we can be a Future's done_callback.
        assert self.alive
        assert self.blocked
        self.blocked = False
        unblock_callback, unblock_args = self.unblocker
        if unblock_callback is not None:
            try:
                unblock_callback(*unblock_args)
            except Exception:
                logging.error('Exception in unblocker in task %r', self.name)
                raise
            finally:
                self.unblocker = None
        self.eventloop.call_soon(self.step)

    def block_io(self, fd, flag):
        assert isinstance(fd, int), repr(fd)
        assert flag in ('r', 'w'), repr(flag)
        if flag == 'r':
            self.block(self.eventloop.remove_reader, fd)
            self.eventloop.add_reader(fd, self.unblock)
        else:
            self.block(self.eventloop.remove_writer, fd)
            self.eventloop.add_writer(fd, self.unblock)

    def wait(self):
        """COROUTINE: Wait until this task is finished."""
        current_task = context.current_task
        assert self is not current_task  # How confusing!
        if not self.alive:
            return
        current_task.block()
        self.add_done_callback(current_task.unblock)
        yield


def run():
    """Run the event loop until it's out of work."""
    context.eventloop.run()


def sleep(secs):
    """COROUTINE: Sleep for some time (a float in seconds)."""
    current_task = context.current_task
    current_task.block()
    context.eventloop.call_later(secs, current_task.unblock)
    yield


def block_r(fd):
    """Helper to call block_io() for reading."""
    context.current_task.block_io(fd, 'r')


def block_w(fd):
    """Helper to call block_io() for writing."""
    context.current_task.block_io(fd, 'w')


def call_in_thread(func, *args, executor=None):
    """COROUTINE: Run a function in a thread."""
    # TODO: Prove there is no race condition here.
    future = context.threadrunner.submit(func, *args, executor=executor)
    task = context.current_task
    task.block(future.cancel)
    future.add_done_callback(task.unblock)
    yield
    assert future.done()
    return future.result()


def wait_for(count, tasks):
    """COROUTINE: Wait for the first N of a set of tasks to complete.

    May return more than N if more than N are immediately ready.

    NOTE: Tasks that were cancelled or raised are also considered ready.
    """
    assert tasks
    tasks = set(tasks)
    assert 1 <= count <= len(tasks)
    current_task = context.current_task
    assert all(task is not current_task for task in tasks)
    todo = set()
    done = set()
    def wait_for_callback(task):
        nonlocal todo, done, current_task, count
        todo.remove(task)
        if len(done) < count:
            done.add(task)
            if len(done) == count:
                current_task.unblock()
    for task in tasks:
        if task.alive:
            todo.add(task)
        else:
            done.add(task)
    if len(done) < count:
        for task in todo:
            task.add_done_callback(wait_for_callback)
        current_task.block()
        yield
    return done


def wait_any(tasks):
    """COROUTINE: Wait for the first of a set of tasks to complete."""
    return wait_for(1, tasks)


def wait_all(tasks):
    """COROUTINE: Wait for all of a set of tasks to complete."""
    return wait_for(len(tasks), tasks)
