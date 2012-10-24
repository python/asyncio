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
import logging


class Scheduler:

    def __init__(self, eventloop, threadrunner):
        self.eventloop = eventloop  # polling.EventLoop instance.
        self.threadrunner = threadrunner  # polling.Threadrunner instance.
        self.current = None  # Current generator.
        self.current_name = None  # Current generator's name.

    def run(self):
        self.eventloop.run()

    def start(self, task, name=None):
        if name is None:
            name = task.__name__  # If it doesn't have one, pass one.
        self.eventloop.call_soon(self.run_task, task, name)

    def run_task(self, task, name):
        try:
            self.current = task
            self.current_name = name
            next(self.current)
        except StopIteration:
            pass
        except Exception:
            logging.exception('Exception in task %r', name)
        else:
            if self.current is not None:
                self.start(task, name)
        finally:
            self.current = None
            self.current_name = None

    def block_r(self, fd):
        self.block_io(fd, 'r')

    def block_w(self, fd):
        self.block_io(fd, 'w')

    def block_io(self, fd, flag):
        assert isinstance(fd, int), repr(fd)
        assert flag in ('r', 'w'), repr(flag)
        task, name = self.block()
        if flag == 'r':
            method = self.eventloop.add_reader
            callback = self.unblock_r
        else:
            method = self.eventloop.add_writer
            callback = self.unblock_w
        method(fd, callback, fd, task, name)

    def block(self):
        assert self.current
        task = self.current
        self.current = None
        return task, self.current_name

    def unblock_r(self, fd, task, name):
        self.eventloop.remove_reader(fd)
        self.start(task, name)

    def unblock_w(self, fd, task, name):
        self.eventloop.remove_writer(fd)
        self.start(task, name)

    def call_in_thread(self, func, *args):
        # TODO: Prove there is no race condition here.
        task, name = self.block()
        future = self.threadrunner.submit(func, *args)
        future.add_done_callback(lambda _: self.start(task, name))
        yield
        assert future.done()
        return future.result()

    def sleep(self, secs):
        task, name = self.block()
        self.eventloop.call_later(secs, self.start, task, name)
        yield
