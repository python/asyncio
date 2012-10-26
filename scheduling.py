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
            # Cancel timeout callback if set.
            if not self.alive and self.canceleer is not None:
                self.canceleer.cancel()

    def start(self):
        assert self.alive
        self.eventloop.call_soon(self.step)

    def block(self, unblock_callback=None, *unblock_args):
        assert self is context.current_task
        assert self.alive
        assert not self.blocked
        self.blocked = True
        self.unblocker = (unblock_callback, unblock_args)

    def unblock(self):
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


def run():
    context.eventloop.run()


def sleep(secs):
    """COROUTINE: Sleep for some time (a float in seconds)."""
    context.current_task.block()
    context.eventloop.call_later(secs, self.unblock)
    yield


def block_r(fd):
    context.current_task.block_io(fd, 'r')


def block_w(fd):
    context.current_task.block_io(fd, 'w')


def call_in_thread(func, *args, executor=None):
    """COROUTINE: Run a function in a thread."""
    # TODO: Prove there is no race condition here.
    future = context.threadrunner.submit(func, *args, executor=executor)
    task = context.current_task
    task.block(future.cancel)
    future.add_done_callback(lambda _: task.unblock())
    yield
    assert future.done()
    return future.result()
