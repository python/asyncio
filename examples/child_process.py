"""
Example of asynchronous interaction with a child python process.

Note that on Windows we must use the IOCP event loop.
"""

import os
import sys

try:
    import tulip
except ImportError:
    # tulip is not installed
    sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
    import tulip

from tulip import streams
from tulip import protocols

if sys.platform == 'win32':
    from tulip.windows_utils import Popen, PIPE
    from tulip.windows_events import ProactorEventLoop
else:
    from subprocess import Popen, PIPE

#
# Return a write-only transport wrapping a writable pipe
#

def connect_write_pipe(file):
    loop = tulip.get_event_loop()
    protocol = protocols.Protocol()
    return loop._make_write_pipe_transport(file, protocol)

#
# Wrap a readable pipe in a stream
#

def connect_read_pipe(file):
    loop = tulip.get_event_loop()
    stream_reader = streams.StreamReader(loop=loop)
    protocol = streams.StreamReaderProtocol(stream_reader)
    transport = loop._make_read_pipe_transport(file, protocol)
    return stream_reader

#
# Example
#

@tulip.task
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
    stdin = connect_write_pipe(p.stdin)
    stdout = connect_read_pipe(p.stdout)
    stderr = connect_read_pipe(p.stderr)

    # interact with subprocess
    name = {stdout:'OUT', stderr:'ERR'}
    registered = {tulip.Task(stderr.readline()): stderr,
                  tulip.Task(stdout.readline()): stdout}
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
            done, pending = yield from tulip.wait(
                registered, timeout=timeout, return_when=tulip.FIRST_COMPLETED)
            if not done:
                break
            for f in done:
                stream = registered.pop(f)
                res = f.result()
                print(name[stream], res.decode('ascii').rstrip())
                if res != b'':
                    registered[tulip.Task(stream.readline())] = stream
            timeout = 0.0


if __name__ == '__main__':
    if sys.platform == 'win32':
        loop = ProactorEventLoop()
        tulip.set_event_loop(loop)
    else:
        loop = tulip.get_event_loop()
    loop.run_until_complete(main(loop))
    loop.close()
