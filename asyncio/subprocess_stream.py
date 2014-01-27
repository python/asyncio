__all__ = ['SubprocessStreamProtocol']

from . import base_subprocess
from . import events
from . import protocols
from . import streams
from . import tasks

class WriteSubprocessPipeStreamProto(base_subprocess.WriteSubprocessPipeProto):
    def __init__(self, process_transport, fd):
        base_subprocess.WriteSubprocessPipeProto.__init__(self, process_transport, fd)
        self._drain_waiter = None
        self._paused = False
        self.writer = None

    def connection_made(self, transport):
        super().connection_made(transport)
        self.writer = streams.StreamWriter(transport, self, None, transport._loop)

    def connection_lost(self, exc):
        super().connection_lost(exc)
        # Also wake up the writing side.
        if self._paused:
            waiter = self._drain_waiter
            if waiter is not None:
                self._drain_waiter = None
                if not waiter.done():
                    if exc is None:
                        waiter.set_result(None)
                    else:
                        waiter.set_exception(exc)

    def pause_writing(self):
        assert not self._paused
        self._paused = True

    def resume_writing(self):
        assert self._paused
        self._paused = False
        waiter = self._drain_waiter
        if waiter is not None:
            self._drain_waiter = None
            if not waiter.done():
                waiter.set_result(None)


class ReadSubprocessPipeStreamProto(base_subprocess.WriteSubprocessPipeProto,
                                    protocols.Protocol):
    def __init__(self, proc, fd, limit=streams._DEFAULT_LIMIT):
        super().__init__(proc, fd)
        self._stream_reader = streams.StreamReader(limit=limit)

    def connection_made(self, transport):
        super().connection_made(transport)
        self._stream_reader.set_transport(transport)

    def connection_lost(self, exc):
        super().connection_lost(exc)
        if exc is None:
            self._stream_reader.feed_eof()
        else:
            self._stream_reader.set_exception(exc)

    def data_received(self, data):
        self._stream_reader.feed_data(data)

    def eof_received(self):
        self._stream_reader.feed_eof()


class SubprocessStreamProtocol(base_subprocess.SubprocessProtocol):
    def __init__(self, limit=streams._DEFAULT_LIMIT):
        self._pipes = {}
        self.limit = limit
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self._waiters = []
        self._transport = None

    def create_read_pipe_protocol(self, transport, fd):
        pipe = ReadSubprocessPipeStreamProto(transport, fd, self.limit)
        self._pipes[fd] = pipe
        return pipe

    def create_write_pipe_protocol(self, transport, fd):
        pipe = WriteSubprocessPipeStreamProto(transport, fd)
        self._pipes[fd] = pipe
        return pipe

    def connection_made(self, transport):
        self._transport = transport
        if 0 in self._pipes:
            self.stdin = self._pipes[0].writer
        if 1 in self._pipes:
            self.stdout = self._pipes[1]._stream_reader
        if 2 in self._pipes:
            self.stderr = self._pipes[1]._stream_reader

    @tasks.coroutine
    def wait(self):
        """
        Wait until the process exit and return the process return code.
        """
        returncode = self._transport.get_returncode()
        if returncode is not None:
            return returncode

        fut = tasks.Future()
        self._waiters.append(fut)
        yield from fut
        return fut.result()

    def process_exited(self):
        returncode = self._transport.get_returncode()
        # FIXME: not thread safe
        waiters = self._waiters.copy()
        self._waiters.clear()
        for waiter in waiters:
            waiter.set_result(returncode)
