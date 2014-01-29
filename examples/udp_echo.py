#!/usr/bin/env python3
"""UDP echo example."""
import argparse
import sys
import asyncio
try:
    import signal
except ImportError:
    signal = None


class MyServerUdpEchoProtocol:

    def connection_made(self, transport):
        print('start', transport)
        self.transport = transport

    def datagram_received(self, data, addr):
        print('Data received:', data, addr)
        self.transport.sendto(data, addr)

    def error_received(self, exc):
        print('Error received:', exc)

    def connection_lost(self, exc):
        print('stop', exc)


class MyClientUdpEchoProtocol:

    message = 'This is the message. It will be echoed.'

    def connection_made(self, transport):
        self.transport = transport
        print('sending "{}"'.format(self.message))
        self.transport.sendto(self.message.encode())
        print('waiting to receive')

    def datagram_received(self, data, addr):
        print('received "{}"'.format(data.decode()))
        self.transport.close()

    def error_received(self, exc):
        print('Error received:', exc)

    def connection_lost(self, exc):
        print('closing transport', exc)
        loop = asyncio.get_event_loop()
        loop.stop()


def start_server(loop, addr):
    t = asyncio.Task(loop.create_datagram_endpoint(
        MyServerUdpEchoProtocol, local_addr=addr))
    transport, server = loop.run_until_complete(t)
    return transport


def start_client(loop, addr):
    t = asyncio.Task(loop.create_datagram_endpoint(
        MyClientUdpEchoProtocol, remote_addr=addr))
    loop.run_until_complete(t)


ARGS = argparse.ArgumentParser(description="UDP Echo example.")
ARGS.add_argument(
    '--server', action="store_true", dest='server',
    default=False, help='Run udp server')
ARGS.add_argument(
    '--client', action="store_true", dest='client',
    default=False, help='Run udp client')
ARGS.add_argument(
    '--host', action="store", dest='host',
    default='127.0.0.1', help='Host name')
ARGS.add_argument(
    '--port', action="store", dest='port',
    default=9999, type=int, help='Port number')


if __name__ == '__main__':
    args = ARGS.parse_args()
    if ':' in args.host:
        args.host, port = args.host.split(':', 1)
        args.port = int(port)

    if (not (args.server or args.client)) or (args.server and args.client):
        print('Please specify --server or --client\n')
        ARGS.print_help()
    else:
        loop = asyncio.get_event_loop()
        if signal is not None:
            loop.add_signal_handler(signal.SIGINT, loop.stop)

        if '--server' in sys.argv:
            server = start_server(loop, (args.host, args.port))
        else:
            start_client(loop, (args.host, args.port))

        try:
            loop.run_forever()
        finally:
            if '--server' in sys.argv:
                server.close()
            loop.close()
