#!/usr/bin/env python3
"""Example showing how to attach a read pipe to a subprocess."""
import trollius as asyncio
import os, sys
from trollius import From

code = """
import os, sys
fd = int(sys.argv[1])
os.write(fd, b'data')
os.close(fd)
"""

loop = asyncio.get_event_loop()

@asyncio.coroutine
def task():
    rfd, wfd = os.pipe()
    args = [sys.executable, '-c', code, str(wfd)]

    pipe = os.fdopen(rfd, 'rb', 0)
    reader = asyncio.StreamReader(loop=loop)
    protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
    transport, _ = yield From(loop.connect_read_pipe(lambda: protocol, pipe))

    kwds = {}
    if sys.version_info >= (3, 2):
        kwds['pass_fds'] = (wfd,)
    proc = yield From(asyncio.create_subprocess_exec(*args, **kwds))
    yield From(proc.wait())

    os.close(wfd)
    data = yield From(reader.read())
    print("read = %r" % data.decode())

loop.run_until_complete(task())
loop.close()
