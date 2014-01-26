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
        self.writer = WritePipeStream(None, self, None)

    def connection_made(self, transport):
        super().connection_made(transport)
        self.writer._transport = transport
        self.writer._loop = transport._loop

    def connection_lost(self, exc):
        # FIXME: call super().connection_lost(exc)
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


class WritePipeStream:
    """Wraps a Transport.

    This exposes write(), writelines(), [can_]write_eof(),
    get_extra_info() and close().  It adds drain() which returns an
    optional Future on which you can wait for flow control.  It also
    adds a transport property which references the Transport
    directly.
    """

    def __init__(self, transport, protocol, loop):
        self._transport = transport
        self._protocol = protocol
        self._loop = loop

    @property
    def transport(self):
        return self._transport

    def write(self, data):
        self._transport.write(data)

    def writelines(self, data):
        self._transport.writelines(data)

    def write_eof(self):
        return self._transport.write_eof()

    def can_write_eof(self):
        return self._transport.can_write_eof()

    def close(self):
        return self._transport.close()

    def get_extra_info(self, name, default=None):
        return self._transport.get_extra_info(name, default)

    def drain(self):
        """This method has an unusual return value.

        The intended use is to write

          w.write(data)
          yield from w.drain()

        When there's nothing to wait for, drain() returns (), and the
        yield-from continues immediately.  When the transport buffer
        is full (the protocol is paused), drain() creates and returns
        a Future and the yield-from will block until that Future is
        completed, which will happen when the buffer is (partially)
        drained and the protocol is resumed.
        """
        if self._transport._conn_lost:  # Uses private variable.
            raise ConnectionResetError('Connection lost')
        if not self._protocol._paused:
            return ()
        waiter = self._protocol._drain_waiter
        assert waiter is None or waiter.cancelled()
        waiter = futures.Future(loop=self._loop)
        self._protocol._drain_waiter = waiter
        return waiter

class ReadSubprocessPipeStreamProto(base_subprocess.ReadSubprocessPipeProto):
    def __init__(self, proc, fd, limit=streams._DEFAULT_LIMIT):
        super().__init__(proc, fd)
        self._stream_reader = streams.StreamReader(limit=limit)

    def connection_made(self, transport):
        super().connection_made(transport)
        self._stream_reader.set_transport(transport)

    def connection_lost(self, exc):
        # FIXME: call super().connection_lost(exc)
        if exc is None:
            self._stream_reader.feed_eof()
        else:
            self._stream_reader.set_exception(exc)

    def data_received(self, data):
        self._stream_reader.feed_data(data)

    def eof_received(self):
        self._stream_reader.feed_eof()


class SubprocessStreamProtocol(protocols.SubprocessProtocol):
    write_pipe_protocol = WriteSubprocessPipeStreamProto

    def __init__(self, limit=streams._DEFAULT_LIMIT):
        self._pipes = {}
        self.limit = limit
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self._waiters = []
        self._transport = None

    def read_pipe_protocol(self, transport, fd):
        protocol = ReadSubprocessPipeStreamProto(transport, fd, self.limit)
        self.pipe_connection_made(fd, protocol)
        return protocol

    def connection_made(self, transport):
        self._transport = transport

    def pipe_data_received(self, fd, data):
        pipe = self._pipes[fd]
        pipe.data_received(data)

    def pipe_connection_lost(self, fd, exc):
        pipe = self._pipes[fd]
        pipe.connection_lost(exc)

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

    def pipe_connection_made(self, fd, pipe):
        self._pipes[fd] = pipe
        if fd == 0:
            self.stdin = pipe.writer
        elif fd == 1:
            self.stdout = pipe._stream_reader
        elif fd == 2:
            self.stderr = pipe._stream_reader
