"""
Example of asynchronous interaction with a child python process.

This example shows how to attach an existing Popen object and use the low level
transport-protocol API. See shell.py and subprocess_shell.py for higher level
examples.
"""

import os
import sys

try:
    import asyncio
except ImportError:
    # asyncio is not installed
    sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
    import asyncio

if sys.platform == 'win32':
    from asyncio.windows_utils import Popen, PIPE
    from asyncio.windows_events import ProactorEventLoop
else:
    from subprocess import Popen, PIPE

#
# Return a write-only transport wrapping a writable pipe
#

@asyncio.coroutine
def connect_write_pipe(file):
    loop = asyncio.get_event_loop()
    transport, _ =  yield from loop.connect_write_pipe(asyncio.Protocol, file)
    return transport

#
# Wrap a readable pipe in a stream
#

@asyncio.coroutine
def connect_read_pipe(file):
    loop = asyncio.get_event_loop()
    stream_reader = asyncio.StreamReader(loop=loop)
    def factory():
        return asyncio.StreamReaderProtocol(stream_reader)
    transport, _ = yield from loop.connect_read_pipe(factory, file)
    return stream_reader, transport


#
# Example
#

@asyncio.coroutine
def main(loop):
    # program which prints evaluation of each expression from stdin
    code = r'''if 1:
                   import os
                   def writeall(fd, buf):
                       while buf:
                           n = os.write(fd, buf)
                           buf = buf[n:]
                   while True:
                       s = os.read(0, 1024)
                       if not s:
                           break
                       s = s.decode('ascii')
                       s = repr(eval(s)) + '\n'
                       s = s.encode('ascii')
                       writeall(1, s)
                   '''

    # commands to send to input
    commands = iter([b"1+1\n",
                     b"2**16\n",
                     b"1/3\n",
                     b"'x'*50",
                     b"1/0\n"])

    # start subprocess and wrap stdin, stdout, stderr
    p = Popen([sys.executable, '-c', code],
              stdin=PIPE, stdout=PIPE, stderr=PIPE)

    stdin = yield from connect_write_pipe(p.stdin)
    stdout, stdout_transport = yield from connect_read_pipe(p.stdout)
    stderr, stderr_transport = yield from connect_read_pipe(p.stderr)

    # interact with subprocess
    name = {stdout:'OUT', stderr:'ERR'}
    registered = {asyncio.Task(stderr.readline()): stderr,
                  asyncio.Task(stdout.readline()): stdout}
    while registered:
        # write command
        cmd = next(commands, None)
        if cmd is None:
            stdin.close()
        else:
            print('>>>', cmd.decode('ascii').rstrip())
            stdin.write(cmd)

        # get and print lines from stdout, stderr
        timeout = None
        while registered:
            done, pending = yield from asyncio.wait(
                registered, timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED)
            if not done:
                break
            for f in done:
                stream = registered.pop(f)
                res = f.result()
                print(name[stream], res.decode('ascii').rstrip())
                if res != b'':
                    registered[asyncio.Task(stream.readline())] = stream
            timeout = 0.0

    stdout_transport.close()
    stderr_transport.close()

if __name__ == '__main__':
    if sys.platform == 'win32':
        loop = ProactorEventLoop()
        asyncio.set_event_loop(loop)
    else:
        loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main(loop))
    finally:
        loop.close()
