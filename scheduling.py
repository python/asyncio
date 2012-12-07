#!/usr/bin/env python3.3
"""Example coroutine scheduler, PEP-380-style ('yield from <generator>').

Requires Python 3.3.

There are likely micro-optimizations possible here, but that's not the point.

TODO:
- Docstrings.
- Unittests.

PATTERNS TO TRY:
- Various synchronization primitives (Lock, RLock, Event, Condition,
  Semaphore, BoundedSemaphore, Barrier).
"""

__author__ = 'Guido van Rossum <guido@python.org>'

# Standard library imports (keep in alphabetic order).
from concurrent.futures import CancelledError, TimeoutError
import logging
import time
import types

# Local imports (keep in alphabetic order).
import polling


context = polling.context


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
        # Start the task immediately.
        self.eventloop.call_soon(self.step)

    def add_done_callback(self, done_callback):
        # For better or for worse, the callback will always be called
        # with the task as an argument, like concurrent.futures.Future.
        # TODO: Call it right away if task is no longer alive.
        dcall = polling.DelayedCall(None, done_callback, (self,))
        self.done_callbacks.append(dcall)
        self.done_callbacks = [dc for dc in self.done_callbacks
                               if not dc.cancelled]
        return dcall

    def __repr__(self):
        parts = [self.name]
        is_current = (self is context.current_task)
        if self.blocked:
            parts.append('blocking' if is_current else 'blocked')
        elif self.alive:
            parts.append('running' if is_current else 'runnable')
        if self.must_cancel:
            parts.append('must_cancel')
        if self.cancelled:
            parts.append('cancelled')
        if self.exception is not None:
            parts.append('exception=%r' % self.exception)
        elif not self.alive:
            parts.append('result=%r' % (self.result,))
        if self.timeout is not None:
            parts.append('timeout=%.3f' % self.timeout)
        return 'Task<' + ', '.join(parts) + '>'

    def cancel(self):
        if self.alive:
            if not self.must_cancel and not self.cancelled:
                self.must_cancel = True
                if self.blocked:
                    self.unblock()

    def step(self):
        assert self.alive, self
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
            logging.debug('Uncaught exception in %s', self,
                          exc_info=True, stack_info=True)
        except BaseException as exc:
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
                for dcall in self.done_callbacks:
                    self.eventloop.add_callback(dcall)

    def block(self, unblock_callback=None, *unblock_args):
        assert self is context.current_task, self
        assert self.alive, self
        assert not self.blocked, self
        self.blocked = True
        self.unblocker = (unblock_callback, unblock_args)

    def unblock_if_alive(self, unused=None):
        # Ignore optional argument so we can be a Future's done_callback.
        if self.alive:
            self.unblock()

    def unblock(self, unused=None):
        # Ignore optional argument so we can be a Future's done_callback.
        assert self.alive, self
        assert self.blocked, self
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
        assert self is not current_task, (self, current_task)  # How confusing!
        if not self.alive:
            return
        current_task.block()
        self.add_done_callback(current_task.unblock)
        yield

    def __iter__(self):
        """COROUTINE: Wait, then return result or raise exception.

        This adds a little magic so you can say

          x = yield from Task(gen())

        and it is equivalent to

          x = yield from gen()

        but with the option to add a timeout (and only a tad slower).
        """
        if self.alive:
            yield from self.wait()
            assert not self.alive
        if self.exception is not None:
            raise self.exception
        return self.result


def run(arg=None):
    """Run the event loop until it's out of work.

    If you pass a generator, it will be spawned for you.
    You can also pass a task (already started).
    Returns the task.
    """
    t = None
    if arg is not None:
        if isinstance(arg, Task):
            t = arg
        else:
            t = Task(arg)
    context.eventloop.run()
    if t is not None and t.exception is not None:
            logging.error('Uncaught exception in startup task: %r',
                          t.exception)
    return t


def sleep(secs):
    """COROUTINE: Sleep for some time (a float in seconds)."""
    current_task = context.current_task
    unblocker = context.eventloop.call_later(secs, current_task.unblock)
    current_task.block(unblocker.cancel)
    yield


def block_r(fd):
    """COROUTINE: Block until a file descriptor is ready for reading."""
    context.current_task.block_io(fd, 'r')
    yield


def block_w(fd):
    """COROUTINE: Block until a file descriptor is ready for writing."""
    context.current_task.block_io(fd, 'w')
    yield


def call_in_thread(func, *args, executor=None):
    """COROUTINE: Run a function in a thread."""
    task = context.current_task
    eventloop = context.eventloop
    future = context.threadrunner.submit(func, *args,
                                         executor=executor,
                                         callback=task.unblock_if_alive)
    task.block(future.cancel)
    yield
    assert future.done()
    return future.result()


def wait_for(count, tasks):
    """COROUTINE: Wait for the first N of a set of tasks to complete.

    May return more than N if more than N are immediately ready.

    NOTE: Tasks that were cancelled or raised are also considered ready.
    """
    assert tasks
    assert all(isinstance(task, Task) for task in tasks)
    tasks = set(tasks)
    assert 1 <= count <= len(tasks)
    current_task = context.current_task
    assert all(task is not current_task for task in tasks)
    todo = set()
    done = set()
    dcalls = []
    def wait_for_callback(task):
        nonlocal todo, done, current_task, count, dcalls
        todo.remove(task)
        if len(done) < count:
            done.add(task)
            if len(done) == count:
                for dcall in dcalls:
                    dcall.cancel()
                current_task.unblock()
    for task in tasks:
        if task.alive:
            todo.add(task)
        else:
            done.add(task)
    if len(done) < count:
        for task in todo:
            dcall = task.add_done_callback(wait_for_callback)
            dcalls.append(dcall)
        current_task.block()
        yield
    return done


def wait_any(tasks):
    """COROUTINE: Wait for the first of a set of tasks to complete."""
    return wait_for(1, tasks)


def wait_all(tasks):
    """COROUTINE: Wait for all of a set of tasks to complete."""
    return wait_for(len(tasks), tasks)


def map_over(gen, *args, timeout=None):
    """COROUTINE: map a generator over one or more iterables.

    E.g. map_over(foo, xs, ys) runs

      Task(foo(x, y)) for x, y in zip(xs, ys)

    and returns a list of all results (in that order).  However if any
    task raises an exception, the remaining tasks are cancelled and
    the exception is propagated.
    """
    # gen is a generator function.
    tasks = [Task(gobj, timeout=timeout) for gobj in map(gen, *args)]
    return (yield from par_tasks(tasks))


def par(*args):
    """COROUTINE: Wait for generators, return a list of results.

    Raises as soon as one of the tasks raises an exception (and then
    remaining tasks are cancelled).

    This differs from par_tasks() in two ways:
    - takes *args instead of list of args
    - each arg may be a generator or a task
    """
    tasks = []
    for arg in args:
        if not isinstance(arg, Task):
            # TODO: assert arg is a generator or an iterator?
            arg = Task(arg)
        tasks.append(arg)
    return (yield from par_tasks(tasks))


def par_tasks(tasks):
    """COROUTINE: Wait for a list of tasks, return a list of results.

    Raises as soon as one of the tasks raises an exception (and then
    remaining tasks are cancelled).
    """
    todo = set(tasks)
    while todo:
        ts = yield from wait_any(todo)
        for t in ts:
            assert not t.alive, t
            todo.remove(t)
            if t.exception is not None:
                for other in todo:
                    other.cancel()
                raise t.exception
    return [t.result for t in tasks]
