"""Event loop and event loop policy.

Beyond the PEP:
- Only the main thread has a default event loop.
- init_event_loop() (re-)initializes the event loop.
"""

__all__ = ['EventLoopPolicy', 'DefaultEventLoopPolicy',
           'EventLoop', 'DelayedCall',
           'get_event_loop_policy', 'set_event_loop_policy',
           'get_event_loop', 'set_event_loop',
           'init_event_loop',
           ]

import threading


class DelayedCall:
    """Object returned by callback registration methods."""

    def __init__(self, when, callback, args, kwds=None):
        self.when = when
        self.callback = callback
        self.args = args
        self.kwds = kwds
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def __lt__(self, other):
        return self.when < other.when

    def __le__(self, other):
        return self.when <= other.when

    def __eq__(self, other):
        return self.when == other.when


class EventLoop:
    """Abstract event loop."""

    def run(self):
        """Run the event loop.  Block until there is nothing left to do."""
        raise NotImplementedError

    # TODO: stop()?

    # Methods returning DelayedCalls for scheduling callbacks.

    def call_later(self, when, callback, *args):
        raise NotImplementedError

    def call_soon(self, callback, *args):
        return self.call_later(0, callback, *args)

    def call_soon_threadsafe(self, callback, *args):
        raise NotImplementedError

    # Methods returning Futures for interacting with threads.

    def wrap_future(self, future):
        raise NotImplementedError

    def run_in_executor(self, executor, function, *args):
        raise NotImplementedError

    # Network I/O methods returning Futures.

    def getaddrinfo(self, host, port, *, family=0, type=0, proto=0, flags=0):
        raise NotImplementedError

    def getnameinfo(self, sockaddr, flags=0):
        raise NotImplementedError

    def create_transport(self, protocol_factory, host, port, *,
                         family=0, type=0, proto=0, flags=0):
        raise NotImplementedError

    def start_serving(self, protocol_factory, host, port, *,
                      family=0, type=0, proto=0, flags=0):
        raise NotImplementedError

    # Ready-based callback registration methods.
    # The add_*() methods return a DelayedCall.
    # The remove_*() methods return True if something was removed,
    # False if there was nothing to delete.

    def add_reader(self, fd, callback, *args):
        raise NotImplementedError

    def remove_reader(self, fd):
        raise NotImplementedError

    def add_writer(self, fd, callback, *args):
        raise NotImplementedError

    def remove_writer(self, fd):
        raise NotImplementedError

    # Completion based I/O methods returning Futures.

    def sock_recv(self, sock, nbytes):
        raise NotImplementedError

    def sock_sendall(self, sock, data):
        raise NotImplementedError

    def sock_connect(self, sock, address):
        raise NotImplementedError

    def sock_accept(self, sock):
        raise NotImplementedError


class EventLoopPolicy:
    """Abstract policy for accessing the event loop."""

    def get_event_loop(self):
        """XXX"""
        raise NotImplementedError

    def set_event_loop(self, event_loop):
        """XXX"""
        raise NotImplementedError

    def init_event_loop(self):
        """XXX"""
        raise NotImplementedError


class DefaultEventLoopPolicy(threading.local, EventLoopPolicy):
    """Default policy implementation for accessing the event loop.

    In this policy, each thread has its own event loop.  However, we
    only automatically create an event loop by default for the main
    thread; other threads by default have no event loop.

    Other policies may have different rules (e.g. a single global
    event loop, or automatically creating an event loop per thread, or
    using some other notion of context to which an event loop is
    associated).
    """

    _event_loop = None

    def get_event_loop(self):
        """Get the event loop.

        This may be None or an instance of EventLoop.
        """
        if (self._event_loop is None and
            threading.current_thread().name == 'MainThread'):
            self.init_event_loop()
        return self._event_loop

    def set_event_loop(self, event_loop):
        """Set the event loop."""
        assert event_loop is None or isinstance(event_loop, EventLoop)
        self._event_loop = event_loop

    def init_event_loop(self):
        """(Re-)initialize the event loop.

        This is calls set_event_loop() with a freshly created event
        loop suitable for the platform.
        """
        # TODO: Do something else for Windows.
        from . import unix_events
        self.set_event_loop(unix_events.UnixEventLoop())


# Event loop policy.  The policy itself is always global, even if the
# policy's rules say that there is an event loop per thread (or other
# notion of context).  The default policy is installed by the first
# call to get_event_loop_policy().
_event_loop_policy = None


def get_event_loop_policy():
    """XXX"""
    global _event_loop_policy
    if _event_loop_policy is None:
        _event_loop_policy = DefaultEventLoopPolicy()
    return _event_loop_policy


def set_event_loop_policy(policy):
    """XXX"""
    global _event_loop_policy
    assert policy is None or isinstance(policy, EventLoopPolicy)
    _event_loop_policy = policy


def get_event_loop():
    """XXX"""
    return get_event_loop_policy().get_event_loop()


def set_event_loop(event_loop):
    """XXX"""
    get_event_loop_policy().set_event_loop(event_loop)


def init_event_loop():
    """XXX"""
    get_event_loop_policy().init_event_loop()
