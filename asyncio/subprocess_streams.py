__all__ = ['create_subprocess_exec', 'create_subprocess_shell']

import collections

from . import events
from . import futures
from . import protocols
from . import streams
from . import tasks

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
        self._dead = False
        self.pid = None

    def connection_made(self, transport):
        self._transport = transport
        self.pid = self._transport.get_pid()
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
        self._dead = True
        returncode = self._transport.get_returncode()
        while self._waiters:
            waiter = self._waiters.popleft()
            waiter.set_result(returncode)

    @tasks.coroutine
    def wait(self):
        """Wait until the process exit and return the process return code."""
        returncode = self._transport.get_returncode()
        if returncode is not None:
            return returncode

        waiter = futures.Future()
        self._waiters.append(waiter)
        yield from waiter
        return waiter.result()

    def close(self):
        self._transport.close()

    def _check_alive(self):
        if self._dead:
            raise ProcessLookupError()

    def get_subprocess(self):
        self._check_alive()
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
def create_subprocess_shell(cmd, stdin=None, stdout=None, stderr=None,
                            loop=None, limit=streams._DEFAULT_LIMIT, **kwds):
    if loop is None:
        loop = events.get_event_loop()
    transport, protocol = yield from loop.subprocess_shell(
                                     lambda: SubprocessStreamProtocol(limit),
                                     cmd, stdin=stdin, stdout=stdout,
                                     stderr=stderr, **kwds)
    yield from protocol.waiter
    return protocol

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
    return protocol
