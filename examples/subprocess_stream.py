import asyncio

@asyncio.coroutine
def cat(loop):
    transport, protocol = yield from loop.subprocess_shell(asyncio.SubprocessStreamProtocol, "cat")
    print("pid: %s" % transport.get_pid())
    stdin = protocol.stdin
    stdout = protocol.stdout

    message = "Hello World!"
    print("cat write: %r" % message)
    stdin.write(message.encode('ascii'))
    yield from stdin.drain()

    stdin.close()
    read = yield from stdout.read()
    print("cat read: %r" % read.decode('ascii'))
    transport.close()

@asyncio.coroutine
def ls(loop):
    transport, protocol = yield from loop.subprocess_exec(asyncio.SubprocessStreamProtocol, "ls", stdin=None)
    while True:
        line = yield from protocol.stdout.readline()
        if not line:
            break
        print("ls>>", line.decode('ascii').rstrip())
    transport.close()

loop = asyncio.get_event_loop()
loop.run_until_complete(cat(loop))
loop.run_until_complete(ls(loop))
