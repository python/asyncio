"""
Example of a simple TCP server that is written in (mostly) coroutine
style and uses asyncio.streams.start_server() and
asyncio.streams.open_connection().

Note that running this example starts both the TCP server and client
in the same process.  It listens on port 12345 on 127.0.0.1, so it will
fail if this port is currently in use.
"""

import sys
import asyncio
import asyncio.streams


class MyServer:
    """
    This is just an example of how a TCP server might be potentially
    structured.  This class has basically 3 methods: start the server,
    handle a client, and stop the server.

    Note that you don't have to follow this structure, it is really
    just an example or possible starting point.
    """

    def __init__(self):
        self.server = None # encapsulates the server sockets

        # this keeps track of all the clients that connected to our
        # server.  It can be useful in some cases, for instance to
        # kill client connections or to broadcast some data to all
        # clients...
        self.clients = {} # task -> (reader, writer)

    def _accept_client(self, client_reader, client_writer):
        """
        This method accepts a new client connection and creates a Task
        to handle this client.  self.clients is updated to keep track
        of the new client.
        """

        # start a new Task to handle this specific client connection
        task = asyncio.Task(self._handle_client(client_reader, client_writer))
        self.clients[task] = (client_reader, client_writer)

        def client_done(task):
            print("client task done:", task, file=sys.stderr)
            del self.clients[task]

        task.add_done_callback(client_done)

    @asyncio.coroutine
    def _handle_client(self, client_reader, client_writer):
        """
        This method actually does the work to handle the requests for
        a specific client.  The protocol is line oriented, so there is
        a main loop that reads a line with a request and then sends
        out one or more lines back to the client with the result.
        """
        while True:
            data = (yield from client_reader.readline()).decode("utf-8")
            if not data: # an empty string means the client disconnected
                break
            cmd, *args = data.rstrip().split(' ')
            if cmd == 'add':
                arg1 = float(args[0])
                arg2 = float(args[1])
                retval = arg1 + arg2
                client_writer.write("{!r}\n".format(retval).encode("utf-8"))
            elif cmd == 'repeat':
                times = int(args[0])
                msg = args[1]
                client_writer.write("begin\n".encode("utf-8"))
                for idx in range(times):
                    client_writer.write("{}. {}\n".format(idx+1, msg)
                                        .encode("utf-8"))
                client_writer.write("end\n".encode("utf-8"))
            else:
                print("Bad command {!r}".format(data), file=sys.stderr)

            # This enables us to have flow control in our connection.
            yield from client_writer.drain()

    def start(self, loop):
        """
        Starts the TCP server, so that it listens on port 12345.

        For each client that connects, the accept_client method gets
        called.  This method runs the loop until the server sockets
        are ready to accept connections.
        """
        self.server = loop.run_until_complete(
            asyncio.streams.start_server(self._accept_client,
                                         '127.0.0.1', 12345,
                                         loop=loop))

    def stop(self, loop):
        """
        Stops the TCP server, i.e. closes the listening socket(s).

        This method runs the loop until the server sockets are closed.
        """
        if self.server is not None:
            self.server.close()
            loop.run_until_complete(self.server.wait_closed())
            self.server = None


def main():
    loop = asyncio.get_event_loop()

    # creates a server and starts listening to TCP connections
    server = MyServer()
    server.start(loop)

    @asyncio.coroutine
    def client():
        reader, writer = yield from asyncio.streams.open_connection(
            '127.0.0.1', 12345, loop=loop)

        def send(msg):
            print("> " + msg)
            writer.write((msg + '\n').encode("utf-8"))

        def recv():
            msgback = (yield from reader.readline()).decode("utf-8").rstrip()
            print("< " + msgback)
            return msgback

        # send a line
        send("add 1 2")
        msg = yield from recv()

        send("repeat 5 hello")
        msg = yield from recv()
        assert msg == 'begin'
        while True:
            msg = yield from recv()
            if msg == 'end':
                break

        writer.close()
        yield from asyncio.sleep(0.5)

    # creates a client and connects to our server
    try:
        loop.run_until_complete(client())
        server.stop(loop)
    finally:
        loop.close()


if __name__ == '__main__':
    main()
