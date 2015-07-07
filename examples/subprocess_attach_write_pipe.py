#!/usr/bin/env python3
"""Example showing how to attach a write pipe to a subprocess."""
import trollius as asyncio
from trollius import From
import os, sys
from trollius import subprocess

code = """
import os, sys
fd = int(sys.argv[1])
data = os.read(fd, 1024)
if sys.version_info >= (3,):
    stdout = sys.stdout.buffer
else:
    stdout = sys.stdout
stdout.write(data)
"""

loop = asyncio.get_event_loop()

@asyncio.coroutine
def task():
    rfd, wfd = os.pipe()
    args = [sys.executable, '-c', code, str(rfd)]
    kwargs = {'stdout': subprocess.PIPE}
    if sys.version_info >= (3, 2):
        kwargs['pass_fds'] = (rfd,)
    proc = yield From(asyncio.create_subprocess_exec(*args, **kwargs))

    pipe = os.fdopen(wfd, 'wb', 0)
    transport, _ = yield From(loop.connect_write_pipe(asyncio.Protocol,
                                                      pipe))
    transport.write(b'data')

    stdout, stderr = yield From(proc.communicate())
    print("stdout = %r" % stdout.decode())
    pipe.close()

loop.run_until_complete(task())
loop.close()
