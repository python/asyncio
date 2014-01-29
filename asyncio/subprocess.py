__all__ = ['create_subprocess_exec', 'create_subprocess_shell']

import collections
import subprocess

from . import events
from . import futures
from . import protocols
from . import streams
from . import tasks


PIPE = subprocess.PIPE
STDOUT = subprocess.STDOUT
DEVNULL = subprocess.DEVNULL


class SubprocessStreamProtocol(streams.FlowControlMixin,
                               protocols.SubprocessProtocol):
    """Like StreamReaderProtocol, but for a subprocess."""

    def __init__(self, limit):
        super().__init__()
        self._limit = limit
        self.stdin = self.stdout = self.stderr = None
        self.waiter = futures.Future()
        self._waiters = collections.deque()
        self._transport = None

    def connection_made(self, transport):
        self._transport = transport
        if transport.get_pipe_transport(1):
            self.stdout = streams.StreamReader(limit=self._limit)
        if transport.get_pipe_transport(2):
            self.stderr = streams.StreamReader(limit=self._limit)
        stdin = transport.get_pipe_transport(0)
        if stdin is not None:
            self.stdin = streams.StreamWriter(stdin,
                                              protocol=self,
                                              reader=None,
                                              loop=transport._loop)
        self.waiter.set_result(None)

    def pipe_data_received(self, fd, data):
        if fd == 1:
            reader = self.stdout
        elif fd == 2:
            reader = self.stderr
        else:
            reader = None
        if reader is not None:
            reader.feed_data(data)

    def pipe_connection_lost(self, fd, exc):
        if fd == 0:
            pipe = self.stdin
            if pipe is not None:
                pipe.close()
            self.connection_lost(exc)
            return
        if fd == 1:
            reader = self.stdout
        elif fd == 2:
            reader = self.stderr
        else:
            reader = None
        if reader != None:
            if exc is None:
                reader.feed_eof()
            else:
                reader.set_exception(exc)

    def process_exited(self):

        # wake up futures waiting for wait()
        returncode = self._transport.get_returncode()
        while self._waiters:
            waiter = self._waiters.popleft()
            waiter.set_result(returncode)


@tasks.coroutine
def _noop():
    return None

@tasks.coroutine
def _read_stream(transport, stream):
    output = yield from stream.read()
    transport.close()
    return output


class Popen:
    def __init__(self, transport, protocol):
        self._transport = transport
        self._protocol = protocol
        self.stdin = protocol.stdin
        self.stdout = protocol.stdout
        self.stderr = protocol.stderr
        self.pid = transport.get_pid()
        self.returncode = transport.get_returncode()
        # FIXME: is it possible that returncode is already known?
        if self.returncode is None:
            waiter = futures.Future()
            self._protocol._waiters.append(waiter)
            waiter.add_done_callback(self._set_returncode)
            self._dead = False
        else:
            self._dead = False

    def _set_returncode(self, fut):
        self.returncode = fut.result()
        # operations on the processing will now fail with ProcessLookupError
        self._dead = True

    @tasks.coroutine
    def wait(self):
        """Wait until the process exit and return the process return code."""
        if self.returncode is not None:
            return self.returncode

        waiter = futures.Future()
        self._protocol._waiters.append(waiter)
        yield from waiter
        return waiter.result()

    def _check_alive(self):
        if self._dead:
            raise ProcessLookupError()

    def get_subprocess(self):
        # FIXME: allow getting the dead subprocess.Popen object?
        self._protocol._check_alive()
        return self._transport.get_extra_info('subprocess')

    def send_signal(self, signal):
        self._check_alive()
        self._transport.send_signal(signal)

    def terminate(self):
        self._check_alive()
        self._transport.terminate()

    def kill(self):
        self._check_alive()
        self._transport.kill()

    @tasks.coroutine
    def _feed_stdin(self, input):
        self.stdin.write(input)
        yield from self.stdin.drain()
        self.stdin.close()

    @tasks.coroutine
    def communicate(self, input=None):
        loop = self._transport._loop
        if input:
            stdin = self._feed_stdin(input)
        else:
            stdin = _noop()
        if self.stdout is not None:
            stdout = _read_stream(self._transport.get_pipe_transport(1),
                                  self.stdout)
        else:
            stdout = _noop()
        if self.stderr is not None:
            stderr = _read_stream(self._transport.get_pipe_transport(2),
                                  self.stderr)
        else:
            stderr = _noop()
        stdin, stdout, stderr = yield from tasks.gather(stdin, stdout, stderr,
                                                        loop=loop)
        yield from self.wait()
        return (stdout, stderr)

    # FIXME: remove close()?
    def close(self):
        self._transport.close()

    # FIXME: add context manager



@tasks.coroutine
def create_subprocess_shell(cmd, stdin=None, stdout=None, stderr=None,
                            loop=None, limit=streams._DEFAULT_LIMIT, **kwds):
    if loop is None:
        loop = events.get_event_loop()
    transport, protocol = yield from loop.subprocess_shell(
                                     lambda: SubprocessStreamProtocol(limit),
                                     cmd, stdin=stdin, stdout=stdout,
                                     stderr=stderr, **kwds)
    yield from protocol.waiter
    return Popen(transport, protocol)

@tasks.coroutine
def create_subprocess_exec(*args, stdin=None, stdout=None, stderr=None,
                           loop=None, limit=streams._DEFAULT_LIMIT, **kwds):
    if loop is None:
        loop = events.get_event_loop()
    transport, protocol = yield from loop.subprocess_exec(
                                     lambda: SubprocessStreamProtocol(limit),
                                     *args, stdin=stdin, stdout=stdout,
                                     stderr=stderr, **kwds)
    yield from protocol.waiter
    return Popen(transport, protocol)


@tasks.coroutine
def call(*popenargs, timeout=None, **kwargs):
    """Run command with arguments.  Wait for command to complete or
    timeout, then return the returncode attribute.

    The arguments are the same as for the Popen constructor.  Example:

    retcode = call(["ls", "-l"])
    """
    # FIXME: raise an error if stdin, stdout or sterr is a pipe?
    proc = yield from create_subprocess_exec(*popenargs, **kwargs)
    try:
        try:
            return (yield from tasks.wait_for(proc.wait(), timeout))
        except:
            proc.kill()
            # FIXME: should we call wait? yield from proc.wait()
            raise
    finally:
        proc.close()


