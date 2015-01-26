"""A simple memcache-like server.

The basic data structure maintained is a single in-memory dictionary
mapping string keys to string values, with operations get, set and
delete.  (Both keys and values may contain Unicode.)

This is a TCP server listening on port 54321.  There is no
authentication.

Requests provide an operation and return a response.  A connection may
be used for multiple requests.  The connection is closed when a client
sends a bad request.

If a client is idle for over 5 seconds (i.e., it does not send another
request, or fails to read the whole response, within this time), it is
disconnected.

Framing of requests and responses within a connection uses a
line-based protocol.  The first line of a request is the frame header
and contains three whitespace-delimited token followed by LF or CRLF:

- the keyword 'request'
- a decimal request ID; the first request is '1', the second '2', etc.
- a decimal byte count giving the size of the rest of the request

Note that the requests ID *must* be consecutive and start at '1' for
each connection.

Response frames look the same except the keyword is 'response'.  The
response ID matches the request ID.  There should be exactly one
response to each request and responses should be seen in the same
order as the requests.

After the frame, individual requests and responses are JSON encoded.

If the frame header or the JSON request body cannot be parsed, an
unframed error message (always starting with 'error') is written back
and the connection is closed.

JSON-encoded requests can be:

- {"type": "get", "key": <string>}
- {"type": "set", "key": <string>, "value": <string>}
- {"type": "delete", "key": <string>}

Responses are also JSON-encoded:

- {"status": "ok", "value": <string>}  # Successful get request
- {"status": "ok"}  # Successful set or delete request
- {"status": "notfound"}  # Key not found for get or delete request

If the request is valid JSON but cannot be handled (e.g., the type or
key field is absent or invalid), an error response of the following
form is returned, but the connection is not closed:

- {"error": <string>}
"""

import argparse
import asyncio
import json
import logging
import os
import random

ARGS = argparse.ArgumentParser(description='Cache server example.')
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
    '--random_failure_percent', action='store', dest='fail_percent',
    default=0, type=float, help='Fail randomly N percent of the time')
ARGS.add_argument(
    '--random_failure_sleep', action='store', dest='fail_sleep',
    default=0, type=float, help='Sleep time when randomly failing')
ARGS.add_argument(
    '--random_response_sleep', action='store', dest='resp_sleep',
    default=0, type=float, help='Sleep time before responding')

args = ARGS.parse_args()


class Cache:

    def __init__(self, loop):
        self.loop = loop
        self.table = {}

    @asyncio.coroutine
    def handle_client(self, reader, writer):
        # Wrapper to log stuff and close writer (i.e., transport).
        peer = writer.get_extra_info('socket').getpeername()
        logging.info('got a connection from %s', peer)
        try:
            yield from self.frame_parser(reader, writer)
        except Exception as exc:
            logging.error('error %r from %s', exc, peer)
        else:
            logging.info('end connection from %s', peer)
        finally:
            writer.close()

    @asyncio.coroutine
    def frame_parser(self, reader, writer):
        # This takes care of the framing.
        last_request_id = 0
        while True:
            # Read the frame header, parse it, read the data.
            # NOTE: The readline() and readexactly() calls will hang
            # if the client doesn't send enough data but doesn't
            # disconnect either.  We add a timeout to each.  (But the
            # timeout should really be implemented by StreamReader.)
            framing_b = yield from asyncio.wait_for(
                reader.readline(),
                timeout=args.timeout, loop=self.loop)
            if random.random()*100 < args.fail_percent:
                logging.warn('Inserting random failure')
                yield from asyncio.sleep(args.fail_sleep*random.random(),
                                         loop=self.loop)
                writer.write(b'error random failure\r\n')
                break
            logging.debug('framing_b = %r', framing_b)
            if not framing_b:
                break  # Clean close.
            try:
                frame_keyword, request_id_b, byte_count_b = framing_b.split()
            except ValueError:
                writer.write(b'error unparseable frame\r\n')
                break
            if frame_keyword != b'request':
                writer.write(b'error frame does not start with request\r\n')
                break
            try:
                request_id, byte_count = int(request_id_b), int(byte_count_b)
            except ValueError:
                writer.write(b'error unparsable frame parameters\r\n')
                break
            if request_id != last_request_id + 1 or byte_count < 2:
                writer.write(b'error invalid frame parameters\r\n')
                break
            last_request_id = request_id
            request_b = yield from asyncio.wait_for(
                reader.readexactly(byte_count),
                timeout=args.timeout, loop=self.loop)
            try:
                request = json.loads(request_b.decode('utf8'))
            except ValueError:
                writer.write(b'error unparsable json\r\n')
                break
            response = self.handle_request(request)  # Not a coroutine.
            if response is None:
                writer.write(b'error unhandlable request\r\n')
                break
            response_b = json.dumps(response).encode('utf8') + b'\r\n'
            byte_count = len(response_b)
            framing_s = 'response {} {}\r\n'.format(request_id, byte_count)
            writer.write(framing_s.encode('ascii'))
            yield from asyncio.sleep(args.resp_sleep*random.random(),
                                     loop=self.loop)
            writer.write(response_b)

    def handle_request(self, request):
        # This parses one request and farms it out to a specific handler.
        # Return None for all errors.
        if not isinstance(request, dict):
            return {'error': 'request is not a dict'}
        request_type = request.get('type')
        if request_type is None:
            return {'error': 'no type in request'}
        if request_type not in {'get', 'set', 'delete'}:
            return {'error': 'unknown request type'}
        key = request.get('key')
        if not isinstance(key, str):
            return {'error': 'key is not a string'}
        if request_type == 'get':
            return self.handle_get(key)
        if request_type == 'set':
            value = request.get('value')
            if not isinstance(value, str):
                return {'error': 'value is not a string'}
            return self.handle_set(key, value)
        if request_type == 'delete':
            return self.handle_delete(key)
        assert False, 'bad request type'  # Should have been caught above.

    def handle_get(self, key):
        value = self.table.get(key)
        if value is None:
            return {'status': 'notfound'}
        else:
            return {'status': 'ok', 'value': value}

    def handle_set(self, key, value):
        self.table[key] = value
        return {'status': 'ok'}

    def handle_delete(self, key):
        if key not in self.table:
            return {'status': 'notfound'}
        else:
            del self.table[key]
            return {'status': 'ok'}


def main():
    asyncio.set_event_loop(None)
    if args.iocp:
        from asyncio.windows_events import ProactorEventLoop
        loop = ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    sslctx = None
    if args.tls:
        import ssl
        # TODO: take cert/key from args as well.
        here = os.path.join(os.path.dirname(__file__), '..', 'tests')
        sslctx = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        sslctx.options |= ssl.OP_NO_SSLv2
        sslctx.load_cert_chain(
            certfile=os.path.join(here, 'ssl_cert.pem'),
            keyfile=os.path.join(here, 'ssl_key.pem'))
    cache = Cache(loop)
    task = asyncio.streams.start_server(cache.handle_client,
                                        args.host, args.port,
                                        ssl=sslctx, loop=loop)
    svr = loop.run_until_complete(task)
    for sock in svr.sockets:
        logging.info('socket %s', sock.getsockname())
    try:
        loop.run_forever()
    finally:
        loop.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
