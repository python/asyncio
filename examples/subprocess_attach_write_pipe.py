#!/usr/bin/env python3
"""Example showing how to attach a write pipe to a subprocess."""
import asyncio
import os, sys
from asyncio import subprocess

code = """
import os, sys
fd = int(sys.argv[1])
data = os.read(fd, 1024)
sys.stdout.buffer.write(data)
"""

loop = asyncio.get_event_loop()

@asyncio.coroutine
def task():
    rfd, wfd = os.pipe()
    args = [sys.executable, '-c', code, str(rfd)]
    proc = yield from asyncio.create_subprocess_exec(
                          *args,
                          pass_fds={rfd},
                          stdout=subprocess.PIPE)

    pipe = open(wfd, 'wb', 0)
    transport, _ = yield from loop.connect_write_pipe(asyncio.Protocol,
                                                      pipe)
    transport.write(b'data')

    stdout, stderr = yield from proc.communicate()
    print("stdout = %r" % stdout.decode())
    transport.close()

loop.run_until_complete(task())
loop.close()
