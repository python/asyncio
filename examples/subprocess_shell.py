"""Example writing to *and* reading from a subprocess."""

import asyncio
import subprocess


class MyProtocol(asyncio.streams.FlowControlMixin, asyncio.SubprocessProtocol):
    """Like StreamReaderProtocol, but for a subprocess."""

    def __init__(self):
        super().__init__()
        self.loop = asyncio.get_event_loop()
        self.stdin = self.stdout = self.stderr = None
        self.waiter = asyncio.Future()

    def connection_made(self, transport):
        if transport.get_pipe_transport(1):
            self.stdout = asyncio.StreamReader()
        if transport.get_pipe_transport(2):
            self.stderr = asyncio.StreamReader()
        if  transport.get_pipe_transport(0):
            self.stdin = asyncio.StreamWriter(transport=transport.get_pipe_transport(0),
                                              protocol=self,  # Must have _paused, _drain_waiter
                                              reader=self.stdout,
                                              loop=self.loop)
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


@asyncio.coroutine
def shell(cmd, **kwds):
    loop = asyncio.get_event_loop()
    transport, protocol = yield from loop.subprocess_shell(MyProtocol, cmd, **kwds)
    yield from protocol.waiter
    return protocol.stdin, protocol.stdout, protocol.stderr


@asyncio.coroutine
def send_input(writer, input):
    for line in input:
        print('sending', len(line), 'bytes')
        writer.write(line)
        d = writer.drain()
        if d:
            print('pause writing')
            yield from d
            print('resume writing')
    writer.close()


@asyncio.coroutine
def log_errors(reader):
    while True:
        line = yield from reader.readline()
        if not line:
            break
        print('ERROR', repr(line))


@asyncio.coroutine
def start(cmd, input=None, **kwds):
    if input is None and 'stdin' not in kwds:
        kwds['stdin'] = None
    stdin, stdout, stderr = yield from shell(cmd, **kwds)
    if input is not None:
        asyncio.async(send_input(stdin, input))
    else:
        print('No stdin')
    if stderr is not None:
        asyncio.async(log_errors(stderr))
    else:
        print('No stderr')
    if stdout is not None:
        while True:
            line = yield from stdout.readline()
            print('received', repr(line))
            if not line:
                break
    else:
        print('No stdout')


def main():
    loop = asyncio.get_event_loop()
##     print('-'*20)
##     loop.run_until_complete(start('cat', [b'one\n', b'two\n', b'three\n']))
##     print('-'*20)
##     loop.run_until_complete(start('cat 1>&2', [b'three\n', b'four\n']))
##     print('-'*20)
##     loop.run_until_complete(start('echo Foo'))
##     print('-'*20)
##     loop.run_until_complete(start('echo Foo; echo Bar 1>&2'))
##     print('-'*20)
##     loop.run_until_complete(start('echo Foo; echo Bar 1>&2', stderr=subprocess.STDOUT))
##     print('-'*20)
##     loop.run_until_complete(start('echo Foo; echo Bar 1>&2', stderr=None))
##     print('-'*20)
##     loop.run_until_complete(start('echo Foo; echo Bar 1>&2', stdout=None))
    print('-'*20)
    loop.run_until_complete(start('sleep 2; wc', input=[b'foo bar baz\n'*300 for i in range(100)]))


if __name__ == '__main__':
    main()
