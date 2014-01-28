__all__ = ['subprocess_exec', 'subprocess_shell']

from . import events
from . import futures
from . import protocols
from . import streams
from . import tasks

class SubprocessStreamProtocol(streams.FlowControlMixin,
                               protocols.SubprocessProtocol):
    """Like StreamReaderProtocol, but for a subprocess."""

    def __init__(self):
        super().__init__()
        self.stdin = self.stdout = self.stderr = None
        self.waiter = futures.Future()
        self._waiters = []
        self._transport = None

    def connection_made(self, transport):
        self._transport = transport
        if transport.get_pipe_transport(1):
            self.stdout = streams.StreamReader()
        if transport.get_pipe_transport(2):
            self.stderr = streams.StreamReader()
        stdin = transport.get_pipe_transport(0)
        if stdin is not None:
            self.stdin = streams.StreamWriter(stdin,
                                              protocol=self,
                                              reader=self.stdout,
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
            self.stdin = None
            if pipe is not None:
                pipe.close()
            self._wakeup_drain_waiter(exc)
            return
        if fd == 1:
            reader = self.stdout
            self.stdout = None
        elif fd == 2:
            reader = self.stderr
            self.stderr = None
        else:
            reader = None
        if reader != None:
            if exc is None:
                reader.feed_eof()
            else:
                reader.set_exception(exc)

    def process_exited(self):
        returncode = self._transport.get_returncode()
        for waiter in self._waiters:
            waiter.set_result(returncode)
        self._waiters.clear()

    @tasks.coroutine
    def wait(self):
        """
        Wait until the process exit and return the process return code.
        """
        if self._transport is not None:
            returncode = self._transport.get_returncode()
            if returncode is not None:
                return returncode

        waiter = futures.Future()
        self._waiters.append(waiter)
        yield from waiter
        return waiter.result()


@tasks.coroutine
def subprocess_shell(cmd, **kwds):
    loop = kwds.pop('loop', None)
    if loop is None:
        loop = events.get_event_loop()
    transport, protocol = yield from loop.subprocess_shell(
                                            SubprocessStreamProtocol,
                                            cmd, **kwds)
    yield from protocol.waiter
    return transport, protocol

@tasks.coroutine
def subprocess_exec(*args, **kwds):
    loop = kwds.pop('loop', None)
    if loop is None:
        loop = events.get_event_loop()
    transport, protocol = yield from loop.subprocess_exec(
                                            SubprocessStreamProtocol,
                                            *args, **kwds)
    yield from protocol.waiter
    return transport, protocol
