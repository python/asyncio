import collections
import subprocess

from . import protocols
from . import tasks
from . import streams
from . import transports


STDIN = 0
STDOUT = 1
STDERR = 2


class BaseSubprocessTransport(transports.SubprocessTransport):

    def __init__(self, loop, protocol, args, shell,
                 stdin, stdout, stderr, bufsize,
                 extra=None, **kwargs):
        super().__init__(extra)
        self._protocol = protocol
        self._loop = loop

        self._pipes = {}
        if stdin == subprocess.PIPE:
            self._pipes[STDIN] = None
        if stdout == subprocess.PIPE:
            self._pipes[STDOUT] = None
        if stderr == subprocess.PIPE:
            self._pipes[STDERR] = None
        self._pending_calls = collections.deque()
        self._finished = False
        self._returncode = None
        self._start(args=args, shell=shell, stdin=stdin, stdout=stdout,
                    stderr=stderr, bufsize=bufsize, **kwargs)
        self._extra['subprocess'] = self._proc

    def _start(self, args, shell, stdin, stdout, stderr, bufsize, **kwargs):
        raise NotImplementedError

    def _make_write_subprocess_pipe_proto(self, fd):
        raise NotImplementedError

    def _make_read_subprocess_pipe_proto(self, fd):
        raise NotImplementedError

    def close(self):
        for proto in self._pipes.values():
            proto.pipe.close()
        if self._returncode is None:
            self.terminate()

    def get_pid(self):
        return self._proc.pid

    def get_returncode(self):
        return self._returncode

    def get_pipe_transport(self, fd):
        if fd in self._pipes:
            return self._pipes[fd].pipe
        else:
            return None

    def send_signal(self, signal):
        self._proc.send_signal(signal)

    def terminate(self):
        self._proc.terminate()

    def kill(self):
        self._proc.kill()

    @tasks.coroutine
    def _post_init(self):
        proc = self._proc
        loop = self._loop
        if proc.stdin is not None:
            transp, proto = yield from self._protocol.connect_write_pipe(self._loop, self, STDIN, proc.stdin)
        if proc.stdout is not None:
            transp, proto = yield from self._protocol.connect_read_pipe(self._loop, self, STDOUT, proc.stdout)
        if proc.stderr is not None:
            transp, proto = yield from self._protocol.connect_read_pipe(self._loop, self, STDERR, proc.stderr)
        if not self._pipes:
            self._try_connected()

    def _call(self, cb, *data):
        if self._pending_calls is not None:
            self._pending_calls.append((cb, data))
        else:
            self._loop.call_soon(cb, *data)

    def _try_connected(self):
        assert self._pending_calls is not None
        if all(p is not None and p.connected for p in self._pipes.values()):
            self._loop.call_soon(self._protocol.connection_made, self)
            for callback, data in self._pending_calls:
                self._loop.call_soon(callback, *data)
            self._pending_calls = None

    def _pipe_connection_lost(self, fd, exc):
        self._call(self._protocol.pipe_connection_lost, fd, exc)
        self._try_finish()

    def _pipe_data_received(self, fd, data):
        self._call(self._protocol.pipe_data_received, fd, data)

    def _process_exited(self, returncode):
        assert returncode is not None, returncode
        assert self._returncode is None, self._returncode
        self._returncode = returncode
        self._loop._subprocess_closed(self)
        self._call(self._protocol.process_exited, returncode)
        self._try_finish()

    def _try_finish(self):
        assert not self._finished
        if self._returncode is None:
            return
        if all(p is not None and p.disconnected
               for p in self._pipes.values()):
            self._finished = True
            self._loop.call_soon(self._call_connection_lost, None)

    def _call_connection_lost(self, exc):
        try:
            self._protocol.connection_lost(exc)
        finally:
            self._proc = None
            self._protocol = None
            self._loop = None


class WriteSubprocessPipeProto(protocols.BaseProtocol):
    pipe = None

    def __init__(self, proc, fd):
        self.proc = proc
        self.fd = fd
        self.connected = False
        self.disconnected = False
        proc._pipes[fd] = self

    def connection_made(self, transport):
        self.connected = True
        self.pipe = transport
        self.proc._try_connected()

    def connection_lost(self, exc):
        self.disconnected = True
        self.proc._pipe_connection_lost(self.fd, exc)

    def eof_received(self):
        pass


class ReadSubprocessPipeProto(WriteSubprocessPipeProto,
                              protocols.Protocol):

    def data_received(self, data):
        self.proc._pipe_data_received(self.fd, data)


class WriteSubprocessPipeStreamProto(WriteSubprocessPipeProto):
    def __init__(self, process_transport, fd, loop=None):
        WriteSubprocessPipeProto.__init__(self, process_transport, fd)
        self._drain_waiter = None
        self._paused = False
        self._loop = loop  # May be None; we may never need it.

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
        self._pipes = {}   # file descriptor (int) => StreamReaderProtocol
        self.limit = limit
        self.stdin = None   # TODO: _UnixWritePipeTransport, but should be StreamWriter
        self.stdout = None  # StreamReader
        self.stderr = None  # StreamReader
        self._waiters = []  # list of Future waiting for the exit of the process,
                            # the result is the returncode of the process
        self._returncode = None
        self._loop = None

    def connection_made(self, transport):
        self._loop = transport._loop
        proc = transport._proc
        #if proc.stdin is not None:
        #    # FIXME: implement StreamWriter for stdin
        #    # stdin_transport = transport.get_pipe_transport(0) # _UnixWritePipeTransport
        #    # stdin_protocol = stdin_transport._protocol # WriteSubprocessPipeProto
        #    # class FakeReader:
        #    #     pass
        #    # stdin_reader = FakeReader() # ???
        #    # stdin_reader._exception = None
        #    # self.stdin = asyncio.StreamWriter(stdin_transport, stdin_protocol, stdin_reader, loop=self._loop)
        #    self.stdin = transport.get_pipe_transport(0)
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

    # FIXME: remove loop
    @tasks.coroutine
    def connect_read_pipe(self, loop, transport, fd, pipe):
        return (yield from loop.connect_read_pipe(
            lambda: ReadSubprocessPipeProto(transport, fd),
            pipe))

    @tasks.coroutine
    def connect_write_pipe(self, loop, process_transport, fd, pipe):
        transport, protocol =  yield from loop.connect_write_pipe(lambda: WriteSubprocessPipeStreamProto(process_transport, fd, loop), pipe)
        writer = WritePipeStream(transport, protocol, loop)
        if fd == 0:
            self.stdin = writer
        return transport, writer

