__all__ = ['subprocess_shell', 'subprocess_exec']

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


class SubprocessStreamProtocol(protocols.SubprocessProtocol):
    def __init__(self, limit=streams._DEFAULT_LIMIT):
        self._pipes = {}
        self.limit = limit
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self._waiters = []
        self._returncode = None
        self._loop = None

    def connection_made(self, transport):
        self._loop = transport._loop
        proc = transport._proc
        if proc.stdout is not None:
            self.stdout = self._get_protocol(1)._stream_reader
        if proc.stderr is not None:
            self.stderr = self._get_protocol(2)._stream_reader

    def get_pipe_reader(self, fd):
        if fd in self._pipes:
            return self._pipes[fd]._stream_reader
        else:
            return None

    def _get_protocol(self, fd):
        try:
            return self._pipes[fd]
        except KeyError:
            reader = streams.StreamReader(limit=self.limit)
            protocol = streams.StreamReaderProtocol(reader, loop=self._loop)
            self._pipes[fd] = protocol
            return protocol

    def pipe_data_received(self, fd, data):
        protocol = self._get_protocol(fd)
        protocol.data_received(data)

    def pipe_connection_lost(self, fd, exc):
        protocol = self._get_protocol(fd)
        protocol.connection_lost(exc)

    @tasks.coroutine
    def wait(self):
        """
        Wait until the process exit and return the process return code.
        """
        if self._returncode:
            return self._returncode

        fut = tasks.Future()
        self._waiters.append(fut)
        yield from fut
        return fut.result()

    def process_exited(self, returncode):
        self._returncode = returncode
        # FIXME: not thread safe
        waiters = self._waiters.copy()
        self._waiters.clear()
        for waiter in waiters:
            waiter.set_result(returncode)

    def pipe_connection_made(self, fd, pipe):
        if fd == 0:
            self.stdin = pipe.writer

@tasks.coroutine
def subprocess_exec(*args, **kwargs):
    loop = kwargs.pop('loop', None)
    if loop is None:
        loop = events.get_event_loop()
    kwargs['write_pipe_proto_factory'] = WriteSubprocessPipeStreamProto
    yield from loop.subprocess_exec(SubprocessStreamProtocol, *args, **kwargs)


@tasks.coroutine
def subprocess_shell(*args, **kwargs):
    loop = kwargs.pop('loop', None)
    if loop is None:
        loop = events.get_event_loop()
    kwargs['write_pipe_protocol_factory'] = WriteSubprocessPipeStreamProto
    return (yield from loop.subprocess_shell(SubprocessStreamProtocol, *args, **kwargs))

