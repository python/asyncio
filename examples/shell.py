"""Examples using create_subprocess_exec() and create_subprocess_shell()."""

import asyncio
import signal
from asyncio.subprocess import PIPE

@asyncio.coroutine
def cat(loop):
    proc = yield from asyncio.create_subprocess_shell("cat",
                                                      stdin=PIPE,
                                                      stdout=PIPE)
    print("pid: %s" % proc.pid)

    message = "Hello World!"
    print("cat write: %r" % message)

    stdout, stderr = yield from proc.communicate(message.encode('ascii'))
    print("cat read: %r" % stdout.decode('ascii'))

    exitcode = yield from proc.wait()
    print("(exit code %s)" % exitcode)

@asyncio.coroutine
def ls(loop):
    proc = yield from asyncio.create_subprocess_exec("ls",
                                                     stdout=PIPE)
    while True:
        line = yield from proc.stdout.readline()
        if not line:
            break
        print("ls>>", line.decode('ascii').rstrip())
    try:
        proc.send_signal(signal.SIGINT)
    except ProcessLookupError:
        pass

@asyncio.coroutine
def test_call(*args, timeout=None):
    proc = yield from asyncio.create_subprocess_exec(*args)
    try:
        exitcode = yield from asyncio.wait_for(proc.wait(), timeout)
        print("%s: exit code %s" % (' '.join(args), exitcode))
    except asyncio.TimeoutError:
        print("timeout! (%.1f sec)" % timeout)
        proc.kill()
        yield from proc.wait()

loop = asyncio.get_event_loop()
loop.run_until_complete(cat(loop))
loop.run_until_complete(ls(loop))
loop.run_until_complete(test_call("bash", "-c", "sleep 3", timeout=1.0))
loop.close()
