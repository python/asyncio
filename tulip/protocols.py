"""Abstract Protocol class."""

__all__ = ['Protocol', 'DatagramProtocol']


class Protocol:
    """ABC representing a protocol.

    The user should implement this interface.  They can inherit from
    this class but don't need to.  The implementations here do
    nothing (they don't raise exceptions).

    When the user wants to requests a transport, they pass a protocol
    factory to a utility function (e.g., EventLoop.create_connection()).

    When the connection is made successfully, connection_made() is
    called with a suitable transport object.  Then data_received()
    will be called 0 or more times with data (bytes) received from the
    transport; finally, connection_list() will be called exactly once
    with either an exception object or None as an argument.

    State machine of calls:

      start -> CM [-> DR*] [-> ER?] -> CL -> end
    """

    def connection_made(self, transport):
        """Called when a connection is made.

        The argument is the transport representing the connection.
        To send data, call its write() or writelines() method.
        To receive data, wait for data_received() calls.
        When the connection is closed, connection_lost() is called.
        """

    def data_received(self, data):
        """Called when some data is received.

        The argument is a bytes object.
        """

    def eof_received(self):
        """Called when the other end calls write_eof() or equivalent.

        The default implementation does nothing.

        TODO: By default close the transport.  But we don't have the
        transport as an instance variable (connection_made() may not
        set it).
        """

    def connection_lost(self, exc):
        """Called when the connection is lost or closed.

        The argument is an exception object or None (the latter
        meaning a regular EOF is received or the connection was
        aborted or closed).
        """


class DatagramProtocol:
    """ABC representing a datagram protocol."""

    def connection_made(self, transport):
        """Called when a datagram transport is ready."""

    def datagram_received(self, data, addr):
        """Called when some datagram is received."""

    def connection_refused(self, exc):
        """Connection is refused."""

    def connection_lost(self, exc):
        """Called when the connection is lost or closed."""
