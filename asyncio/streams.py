"""Stream-related things."""

__all__ = ['StreamReader', 'StreamWriter', 'StreamReaderProtocol',
           'open_connection', 'start_server', 'IncompleteReadError',
           'connect_read_pipe', 'connect_write_pipe',
           ]

import collections

from . import events
from . import futures
from . import protocols
from . import tasks


_DEFAULT_LIMIT = 2**16

class IncompleteReadError(EOFError):
    """
    Incomplete read error. Attributes:

    - partial: read bytes string before the end of stream was reached
    - expected: total number of expected bytes
    """
    def __init__(self, partial, expected):
        EOFError.__init__(self, "%s bytes read on a total of %s expected bytes"
                                % (len(partial), expected))
        self.partial = partial
        self.expected = expected


@tasks.coroutine
def open_connection(host=None, port=None, *,
                    loop=None, limit=_DEFAULT_LIMIT, **kwds):
    """A wrapper for create_connection() returning a (reader, writer) pair.

    The reader returned is a StreamReader instance; the writer is a
    StreamWriter instance.

    The arguments are all the usual arguments to create_connection()
    except protocol_factory; most common are positional host and port,
    with various optional keyword arguments following.

    Additional optional keyword arguments are loop (to set the event loop
    instance to use) and limit (to set the buffer limit passed to the
    StreamReader).

    (If you want to customize the StreamReader and/or
    StreamReaderProtocol classes, just copy the code -- there's
    really nothing special here except some convenience.)
    """
    if loop is None:
        loop = events.get_event_loop()
    reader = StreamReader(limit=limit, loop=loop)
    protocol = StreamReaderProtocol(reader, loop=loop)
    transport, _ = yield from loop.create_connection(
        lambda: protocol, host, port, **kwds)
    writer = StreamWriter(transport, protocol, reader, loop)
    return reader, writer


@tasks.coroutine
def start_server(client_connected_cb, host=None, port=None, *,
                 loop=None, limit=_DEFAULT_LIMIT, **kwds):
    """Start a socket server, call back for each client connected.

    The first parameter, `client_connected_cb`, takes two parameters:
    client_reader, client_writer.  client_reader is a StreamReader
    object, while client_writer is a StreamWriter object.  This
    parameter can either be a plain callback function or a coroutine;
    if it is a coroutine, it will be automatically converted into a
    Task.

    The rest of the arguments are all the usual arguments to
    loop.create_server() except protocol_factory; most common are
    positional host and port, with various optional keyword arguments
    following.  The return value is the same as loop.create_server().

    Additional optional keyword arguments are loop (to set the event loop
    instance to use) and limit (to set the buffer limit passed to the
    StreamReader).

    The return value is the same as loop.create_server(), i.e. a
    Server object which can be used to stop the service.
    """
    if loop is None:
        loop = events.get_event_loop()

    def factory():
        reader = StreamReader(limit=limit, loop=loop)
        protocol = StreamReaderProtocol(reader, client_connected_cb,
                                        loop=loop)
        return protocol

    return (yield from loop.create_server(factory, host, port, **kwds))


class StreamReaderProtocol(protocols.Protocol):
    """Trivial helper class to adapt between Protocol and StreamReader.

    (This is a helper class instead of making StreamReader itself a
    Protocol subclass, because the StreamReader has other potential
    uses, and to prevent the user of the StreamReader to accidentally
    call inappropriate methods of the protocol.)
    """

    def __init__(self, stream_reader, client_connected_cb=None, loop=None):
        self._stream_reader = stream_reader
        self._stream_writer = None
        self._drain_waiter = None
        self._paused = False
        self._client_connected_cb = client_connected_cb
        self._loop = loop  # May be None; we may never need it.

    def connection_made(self, transport):
        self._stream_reader.set_transport(transport)
        if self._client_connected_cb is not None:
            self._stream_writer = StreamWriter(transport, self,
                                               self._stream_reader,
                                               self._loop)
            res = self._client_connected_cb(self._stream_reader,
                                            self._stream_writer)
            if tasks.iscoroutine(res):
                tasks.Task(res, loop=self._loop)

    def connection_lost(self, exc):
        if exc is None:
            self._stream_reader.feed_eof()
        else:
            self._stream_reader.set_exception(exc)
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

    def data_received(self, data):
        self._stream_reader.feed_data(data)

    def eof_received(self):
        self._stream_reader.feed_eof()

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

#
# Return a write-only transport wrapping a writable pipe
#

@tasks.coroutine
def connect_write_pipe(file, loop=None):
    if loop is None:
        loop = events.get_event_loop()
    protocol = protocols.Protocol()
    transport, _ =  yield from loop.connect_write_pipe(protocols.Protocol, file)
    writer = StreamWriter(transport, protocol, None, loop)
    return writer

#
# Wrap a readable pipe in a stream
#

@tasks.coroutine
def connect_read_pipe(file, loop=None):
    if loop is None:
        loop = events.get_event_loop()
    stream_reader = StreamReader(loop=loop)
    def factory():
        return StreamReaderProtocol(stream_reader)
    _transport, _ = yield from loop.connect_read_pipe(factory, file)
    return stream_reader


class StreamWriter:
    """Wraps a Transport.

    This exposes write(), writelines(), [can_]write_eof(),
    get_extra_info() and close().  It adds drain() which returns an
    optional Future on which you can wait for flow control.  It also
    adds a transport property which references the Transport
    directly.
    """

    def __init__(self, transport, protocol, reader, loop):
        self._transport = transport
        self._protocol = protocol
        self._reader = reader
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
        if self._reader is not None and self._reader._exception is not None:
            raise self._reader._exception
        if self._transport._conn_lost:  # Uses private variable.
            raise ConnectionResetError('Connection lost')
        if not self._protocol._paused:
            return ()
        waiter = self._protocol._drain_waiter
        assert waiter is None or waiter.cancelled()
        waiter = futures.Future(loop=self._loop)
        self._protocol._drain_waiter = waiter
        return waiter


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


class StreamReader:

    def __init__(self, limit=_DEFAULT_LIMIT, loop=None):
        # The line length limit is  a security feature;
        # it also doubles as half the buffer limit.
        self._limit = limit
        if loop is None:
            loop = events.get_event_loop()
        self._loop = loop
        # TODO: Use a bytearray for a buffer, like the transport.
        self._buffer = collections.deque()  # Deque of bytes objects.
        self._byte_count = 0  # Bytes in buffer.
        self._eof = False  # Whether we're done.
        self._waiter = None  # A future.
        self._exception = None
        self._transport = None
        self._paused = False

    def exception(self):
        return self._exception

    def set_exception(self, exc):
        self._exception = exc

        waiter = self._waiter
        if waiter is not None:
            self._waiter = None
            if not waiter.cancelled():
                waiter.set_exception(exc)

    def set_transport(self, transport):
        assert self._transport is None, 'Transport already set'
        self._transport = transport

    def _maybe_resume_transport(self):
        if self._paused and self._byte_count <= self._limit:
            self._paused = False
            self._transport.resume_reading()

    def feed_eof(self):
        self._eof = True
        waiter = self._waiter
        if waiter is not None:
            self._waiter = None
            if not waiter.cancelled():
                waiter.set_result(True)

    def feed_data(self, data):
        if not data:
            return

        self._buffer.append(data)
        self._byte_count += len(data)

        waiter = self._waiter
        if waiter is not None:
            self._waiter = None
            if not waiter.cancelled():
                waiter.set_result(False)

        if (self._transport is not None and
            not self._paused and
            self._byte_count > 2*self._limit):
            try:
                self._transport.pause_reading()
            except NotImplementedError:
                # The transport can't be paused.
                # We'll just have to buffer all data.
                # Forget the transport so we don't keep trying.
                self._transport = None
            else:
                self._paused = True

    def _create_waiter(self, func_name):
        # StreamReader uses a future to link the protocol feed_data() method
        # to a read coroutine. Running two read coroutines at the same time
        # would have an unexpected behaviour. It would not possible to know
        # which coroutine would get the next data.
        if self._waiter is not None:
            raise RuntimeError('%s() called while another coroutine is '
                               'already waiting for incoming data' % func_name)
        return futures.Future(loop=self._loop)

    @tasks.coroutine
    def readline(self):
        if self._exception is not None:
            raise self._exception

        parts = []
        parts_size = 0
        not_enough = True

        while not_enough:
            while self._buffer and not_enough:
                data = self._buffer.popleft()
                ichar = data.find(b'\n')
                if ichar < 0:
                    parts.append(data)
                    parts_size += len(data)
                else:
                    ichar += 1
                    head, tail = data[:ichar], data[ichar:]
                    if tail:
                        self._buffer.appendleft(tail)
                    not_enough = False
                    parts.append(head)
                    parts_size += len(head)

                if parts_size > self._limit:
                    self._byte_count -= parts_size
                    self._maybe_resume_transport()
                    raise ValueError('Line is too long')

            if self._eof:
                break

            if not_enough:
                self._waiter = self._create_waiter('readline')
                try:
                    yield from self._waiter
                finally:
                    self._waiter = None

        line = b''.join(parts)
        self._byte_count -= parts_size
        self._maybe_resume_transport()

        return line

    @tasks.coroutine
    def read(self, n=-1):
        if self._exception is not None:
            raise self._exception

        if not n:
            return b''

        if n < 0:
            while not self._eof:
                self._waiter = self._create_waiter('read')
                try:
                    yield from self._waiter
                finally:
                    self._waiter = None
        else:
            if not self._byte_count and not self._eof:
                self._waiter = self._create_waiter('read')
                try:
                    yield from self._waiter
                finally:
                    self._waiter = None

        if n < 0 or self._byte_count <= n:
            data = b''.join(self._buffer)
            self._buffer.clear()
            self._byte_count = 0
            self._maybe_resume_transport()
            return data

        parts = []
        parts_bytes = 0
        while self._buffer and parts_bytes < n:
            data = self._buffer.popleft()
            data_bytes = len(data)
            if n < parts_bytes + data_bytes:
                data_bytes = n - parts_bytes
                data, rest = data[:data_bytes], data[data_bytes:]
                self._buffer.appendleft(rest)

            parts.append(data)
            parts_bytes += data_bytes
            self._byte_count -= data_bytes
            self._maybe_resume_transport()

        return b''.join(parts)

    @tasks.coroutine
    def readexactly(self, n):
        if self._exception is not None:
            raise self._exception

        # There used to be "optimized" code here.  It created its own
        # Future and waited until self._buffer had at least the n
        # bytes, then called read(n).  Unfortunately, this could pause
        # the transport if the argument was larger than the pause
        # limit (which is twice self._limit).  So now we just read()
        # into a local buffer.

        blocks = []
        while n > 0:
            block = yield from self.read(n)
            if not block:
                partial = b''.join(blocks)
                raise IncompleteReadError(partial, len(partial) + n)
            blocks.append(block)
            n -= len(block)

        return b''.join(blocks)

    def close(self):
        return self._transport.close()


class SubprocessStreamProtocol(protocols.SubprocessProtocol):
    def __init__(self, limit=_DEFAULT_LIMIT):
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
            reader = StreamReader(limit=self.limit)
            protocol = StreamReaderProtocol(reader, loop=self._loop)
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
        from . import base_subprocess
        return (yield from loop.connect_read_pipe(
            lambda: base_subprocess.ReadSubprocessPipeProto(transport, fd),
            pipe))

    @tasks.coroutine
    def connect_write_pipe(self, loop, process_transport, fd, pipe):
        from . import base_subprocess

        class WritePipeStreamProtocol(base_subprocess.WriteSubprocessPipeProto):
            def __init__(self, process_transport, fd, loop=None):
                base_subprocess.WriteSubprocessPipeProto.__init__(self, process_transport, fd)
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

        # FIXME: break dependency for transport/protocol
        #transport, protocol =  yield from loop.connect_write_pipe(lambda: base_subprocess.WriteSubprocessPipeProto(process_transport, fd), pipe)
        transport, protocol =  yield from loop.connect_write_pipe(lambda: WritePipeStreamProtocol(process_transport, fd, loop), pipe)
        writer = WritePipeStream(transport, protocol, loop)
        if fd == 0:
            self.stdin = writer
        return transport, writer

        #return (yield from loop.connect_write_pipe(
        #    lambda: base_subprocess.WriteSubprocessPipeProto(transport, fd),
        #    pipe))

