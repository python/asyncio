"""
Example of asynchronous interaction with a subprocess on Windows.

This requires use of overlapped pipe handles and (a modified) iocp proactor.
"""

import itertools
import msvcrt
import os
import subprocess
import sys
import tempfile
import _winapi

try:
    import tulip
except ImportError:
    # tulip is not installed
    sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
    import tulip

from tulip import _overlapped
from tulip import windows_events
from tulip import streams
from tulip import protocols

#
# Constants/globals
#

BUFSIZE = 8192
PIPE = subprocess.PIPE
_mmap_counter=itertools.count()

#
# Replacement for os.pipe() using handles instead of fds
#

def pipe(*, duplex=False, overlapped=(True, True), bufsize=BUFSIZE):
    address = tempfile.mktemp(prefix=r'\\.\pipe\python-pipe-%d-%d-' %
                              (os.getpid(), next(_mmap_counter)))

    if duplex:
        openmode = _winapi.PIPE_ACCESS_DUPLEX
        access = _winapi.GENERIC_READ | _winapi.GENERIC_WRITE
        obsize, ibsize = bufsize, bufsize
    else:
        openmode = _winapi.PIPE_ACCESS_INBOUND
        access = _winapi.GENERIC_WRITE
        obsize, ibsize = 0, bufsize

    openmode |= _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE

    if overlapped[0]:
        openmode |= _winapi.FILE_FLAG_OVERLAPPED

    if overlapped[1]:
        flags_and_attribs = _winapi.FILE_FLAG_OVERLAPPED
    else:
        flags_and_attribs = 0

    h1 = h2 = None
    try:
        h1 = _winapi.CreateNamedPipe(
            address, openmode, _winapi.PIPE_WAIT,
            1, obsize, ibsize, _winapi.NMPWAIT_WAIT_FOREVER, _winapi.NULL)

        h2 = _winapi.CreateFile(
            address, access, 0, _winapi.NULL, _winapi.OPEN_EXISTING,
            flags_and_attribs, _winapi.NULL)

        ov = _winapi.ConnectNamedPipe(h1, overlapped=True)
        ov.GetOverlappedResult(True)
        return h1, h2
    except:
        if h1 is not None:
            _winapi.CloseHandle(h1)
        if h2 is not None:
            _winapi.CloseHandle(h2)
        raise

#
# Wrapper for a pipe handle
#

class PipeHandle:
    def __init__(self, handle):
        self._handle = handle

    @property
    def handle(self):
        return self._handle

    def fileno(self):
        return self._handle

    def close(self, *, CloseHandle=_winapi.CloseHandle):
        if self._handle is not None:
            CloseHandle(self._handle)
            self._handle = None

    __del__ = close

    def __enter__(self):
        return self

    def __exit__(self, t, v, tb):
        self.close()

#
# Replacement for subprocess.Popen using overlapped pipe handles
#

class Popen(subprocess.Popen):
    def __init__(self, args, stdin=None, stdout=None, stderr=None, **kwds):
        stdin_rfd = stdout_wfd = stderr_wfd = None
        stdin_wh = stdout_rh = stderr_rh = None
        if stdin == PIPE:
            stdin_rh, stdin_wh = pipe(overlapped=(False, True))
            stdin_rfd = msvcrt.open_osfhandle(stdin_rh, os.O_RDONLY)
        if stdout == PIPE:
            stdout_rh, stdout_wh = pipe(overlapped=(True, False))
            stdout_wfd = msvcrt.open_osfhandle(stdout_wh, 0)
        if stderr == PIPE:
            stderr_rh, stderr_wh = pipe(overlapped=(True, False))
            stderr_wfd = msvcrt.open_osfhandle(stderr_wh, 0)
        try:
            super().__init__(args, stdin=stdin_rfd, stdout=stdout_wfd,
                             stderr=stderr_wfd, **kwds)
        except:
            for h in (stdin_wh, stdout_rh, stderr_rh):
                _winapi.CloseHandle(h)
            raise
        else:
            if stdin_wh is not None:
                self.stdin = PipeHandle(stdin_wh)
            if stdout_rh is not None:
                self.stdout = PipeHandle(stdout_rh)
            if stderr_rh is not None:
                self.stderr = PipeHandle(stderr_rh)
        finally:
            if stdin == PIPE:
                os.close(stdin_rfd)
            if stdout == PIPE:
                os.close(stdout_wfd)
            if stderr == PIPE:
                os.close(stderr_wfd)

#
# Return a write-only transport wrapping a writable pipe
#

def connect_write_pipe(file):
    loop = tulip.get_event_loop()
    protocol = protocols.Protocol()
    return loop._make_socket_transport(file, protocol, write_only=True)

#
# Wrap a readable pipe in a stream
#

def connect_read_pipe(file):
    loop = tulip.get_event_loop()
    stream_reader = streams.StreamReader()
    protocol = _StreamReaderProtocol(stream_reader)
    transport = loop._make_socket_transport(file, protocol)
    return stream_reader

class _StreamReaderProtocol(protocols.Protocol):
    def __init__(self, stream_reader):
        self.stream_reader = stream_reader
    def connection_lost(self, exc):
        self.stream_reader.set_exception(exc)
    def data_received(self, data):
        self.stream_reader.feed_data(data)
    def eof_received(self):
        self.stream_reader.feed_eof()

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
        while True:
            done, pending = yield from tulip.wait(
                registered, timeout, tulip.FIRST_COMPLETED)
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
    loop = windows_events.ProactorEventLoop()
    tulip.set_event_loop(loop)
    loop.run_until_complete(main(loop))
    loop.close()
