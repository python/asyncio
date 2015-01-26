"""Client for cache server.

See cachesvr.py for protocol description.
"""

import argparse
import asyncio
from asyncio import test_utils
import json
import logging

ARGS = argparse.ArgumentParser(description='Cache client example.')
ARGS.add_argument(
    '--tls', action='store_true', dest='tls',
    default=False, help='Use TLS')
ARGS.add_argument(
    '--iocp', action='store_true', dest='iocp',
    default=False, help='Use IOCP event loop (Windows only)')
ARGS.add_argument(
    '--host', action='store', dest='host',
    default='localhost', help='Host name')
ARGS.add_argument(
    '--port', action='store', dest='port',
    default=54321, type=int, help='Port number')
ARGS.add_argument(
    '--timeout', action='store', dest='timeout',
    default=5, type=float, help='Timeout')
ARGS.add_argument(
    '--max_backoff', action='store', dest='max_backoff',
    default=5, type=float, help='Max backoff on reconnect')
ARGS.add_argument(
    '--ntasks', action='store', dest='ntasks',
    default=10, type=int, help='Number of tester tasks')
ARGS.add_argument(
    '--ntries', action='store', dest='ntries',
    default=5, type=int, help='Number of request tries before giving up')


args = ARGS.parse_args()


class CacheClient:
    """Multiplexing cache client.

    This wraps a single connection to the cache client.  The
    connection is automatically re-opened when an error occurs.

    Multiple tasks may share this object; the requests will be
    serialized.

    The public API is get(), set(), delete() (all are coroutines).
    """

    def __init__(self, host, port, sslctx=None, loop=None):
        self.host = host
        self.port = port
        self.sslctx = sslctx
        self.loop = loop
        self.todo = set()
        self.initialized = False
        self.task = asyncio.Task(self.activity(), loop=self.loop)

    @asyncio.coroutine
    def get(self, key):
        resp = yield from self.request('get', key)
        if resp is None:
            return None
        return resp.get('value')

    @asyncio.coroutine
    def set(self, key, value):
        resp = yield from self.request('set', key, value)
        if resp is None:
            return False
        return resp.get('status') == 'ok'

    @asyncio.coroutine
    def delete(self, key):
        resp = yield from self.request('delete', key)
        if resp is None:
            return False
        return resp.get('status') == 'ok'

    @asyncio.coroutine
    def request(self, type, key, value=None):
        assert not self.task.done()
        data = {'type': type, 'key': key}
        if value is not None:
            data['value'] = value
        payload = json.dumps(data).encode('utf8')
        waiter = asyncio.Future(loop=self.loop)
        if self.initialized:
            try:
                yield from self.send(payload, waiter)
            except IOError:
                self.todo.add((payload, waiter))
        else:
            self.todo.add((payload, waiter))
        return (yield from waiter)

    @asyncio.coroutine
    def activity(self):
        backoff = 0
        while True:
            try:
                self.reader, self.writer = yield from asyncio.open_connection(
                    self.host, self.port, ssl=self.sslctx, loop=self.loop)
            except Exception as exc:
                backoff = min(args.max_backoff, backoff + (backoff//2) + 1)
                logging.info('Error connecting: %r; sleep %s', exc, backoff)
                yield from asyncio.sleep(backoff, loop=self.loop)
                continue
            backoff = 0
            self.next_id = 0
            self.pending = {}
            self. initialized = True
            try:
                while self.todo:
                    payload, waiter = self.todo.pop()
                    if not waiter.done():
                        yield from self.send(payload, waiter)
                while True:
                    resp_id, resp = yield from self.process()
                    if resp_id in self.pending:
                        payload, waiter = self.pending.pop(resp_id)
                        if not waiter.done():
                            waiter.set_result(resp)
            except Exception as exc:
                self.initialized = False
                self.writer.close()
                while self.pending:
                    req_id, pair = self.pending.popitem()
                    payload, waiter = pair
                    if not waiter.done():
                        self.todo.add(pair)
                logging.info('Error processing: %r', exc)

    @asyncio.coroutine
    def send(self, payload, waiter):
        self.next_id += 1
        req_id = self.next_id
        frame = 'request %d %d\n' % (req_id, len(payload))
        self.writer.write(frame.encode('ascii'))
        self.writer.write(payload)
        self.pending[req_id] = payload, waiter
        yield from self.writer.drain()

    @asyncio.coroutine
    def process(self):
        frame = yield from self.reader.readline()
        if not frame:
            raise EOFError()
        head, tail = frame.split(None, 1)
        if head == b'error':
            raise IOError('OOB error: %r' % tail)
        if head != b'response':
            raise IOError('Bad frame: %r' % frame)
        resp_id, resp_size = map(int, tail.split())
        data = yield from self.reader.readexactly(resp_size)
        if len(data) != resp_size:
            raise EOFError()
        resp = json.loads(data.decode('utf8'))
        return resp_id, resp


def main():
    asyncio.set_event_loop(None)
    if args.iocp:
        from asyncio.windows_events import ProactorEventLoop
        loop = ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    sslctx = None
    if args.tls:
        sslctx = test_utils.dummy_ssl_context()
    cache = CacheClient(args.host, args.port, sslctx=sslctx, loop=loop)
    try:
        loop.run_until_complete(
            asyncio.gather(
                *[testing(i, cache, loop) for i in range(args.ntasks)],
                loop=loop))
    finally:
        loop.close()


@asyncio.coroutine
def testing(label, cache, loop):

    def w(g):
        return asyncio.wait_for(g, args.timeout, loop=loop)

    key = 'foo-%s' % label
    while True:
        logging.info('%s %s', label, '-'*20)
        try:
            ret = yield from w(cache.set(key, 'hello-%s-world' % label))
            logging.info('%s set %s', label, ret)
            ret = yield from w(cache.get(key))
            logging.info('%s get %s', label, ret)
            ret = yield from w(cache.delete(key))
            logging.info('%s del %s', label, ret)
            ret = yield from w(cache.get(key))
            logging.info('%s get2 %s', label, ret)
        except asyncio.TimeoutError:
            logging.warn('%s Timeout', label)
        except Exception as exc:
            logging.exception('%s Client exception: %r', label, exc)
            break


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
