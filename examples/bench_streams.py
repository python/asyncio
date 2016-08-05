#!/usr/bin/env python3.5

import asyncio
import os
import time


async def read_stream_until_end(stream, bufsize):
    while True:
        d = await stream.read(bufsize)
        if not d:
            break


async def write_to_stream(stream, bufsize, count, drain_after_each_buf):
    # unfortunatelly, sometimes writes zeroes to disk are optimized by FS
    buf = b'x' * bufsize
    for i in range(count):
        stream.write(buf)
        if drain_after_each_buf:
            await stream.drain()
    await stream.drain()
    stream.close()


def bench(fun):
    # todo: @wraps(fun)
    async def newfun(bufsize, count, drain_after_each_buf):
        a = time.monotonic()
        await fun(bufsize, count, drain_after_each_buf)
        b = time.monotonic()
        bufsize /= 1024 * 1024
        print('{}: {} buffers by {:.2f} MB {}: {:.2f} seconds ({:.2f} MB/sec)'.format(
            fun.__name__,
            count,
            bufsize,
            '(with intermediate drains)' if drain_after_each_buf else '',
            b - a,
            bufsize * count / (b - a),
        ))
    return newfun


async def connect_write_pipe(fd):
    loop = asyncio.get_event_loop()
    transport, protocol = await  loop.connect_write_pipe(asyncio.streams.FlowControlMixin, open(fd, 'wb'))
    stream_writer = asyncio.StreamWriter(transport, protocol, None, loop)
    return stream_writer, transport


async def connect_read_pipe(fd):
    loop = asyncio.get_event_loop()
    stream_reader = asyncio.StreamReader()
    transport, protocol = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stream_reader), open(fd, 'rb'))
    return stream_reader, transport

@bench
async def bench_pipes(bufsize, count, drain_after_each_buf):
    (r, w) = os.pipe()
    (stream_reader, rtransport) = await connect_read_pipe(r)
    (stream_writer, wtransport) = await connect_write_pipe(w)
    await asyncio.gather(
        write_to_stream(stream_writer, bufsize, count, drain_after_each_buf),
        # by default, pipes can not read more than 65536 bytes.
        # unless fcntl(pipe_fd, F_SETPIPE_SZ, ...) is called
        read_stream_until_end(stream_reader, 65536),
    )
    wtransport.close()
    rtransport.close()

@bench
async def bench_sockets(bufsize, count, drain_after_each_buf):
    handler = lambda rs, ws: write_to_stream(ws, bufsize, count, drain_after_each_buf)
    server = await asyncio.start_server(handler, '127.0.0.1', 0)

    addr = server.sockets[0].getsockname()
    (rs, ws) = await asyncio.open_connection(*addr)
    server.close()
    await server.wait_closed()

    # now writing part is running "in background". TODO: wait it completion somehow
    await read_stream_until_end(rs, 10 * 1024 * 1024)
    ws.close()


async def amain():
    N = 5
    for drain in (False, True):
        await bench_pipes(10 * 1024 * 1024, N, drain)
        await bench_pipes(10 * 1024, N * 1024, drain)

        await bench_sockets(10 * 1024 * 1024, N, drain)
        await bench_sockets(10 * 1024, N * 1024, drain)


def main():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(amain())


if __name__ == '__main__':
    main()
