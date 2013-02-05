"""Event loop and event loop policy.

Beyond the PEP:
- Only the main thread has a default event loop.
"""

__all__ = ['EventLoopPolicy', 'DefaultEventLoopPolicy',
           'AbstractEventLoop', 'Handler', 'make_handler',
           'get_event_loop_policy', 'set_event_loop_policy',
           'get_event_loop', 'set_event_loop', 'new_event_loop',
           ]

import sys
import threading


class Handler:
    """Object returned by callback registration methods."""

    def __init__(self, when, callback, args):
        self._when = when
        self._callback = callback
        self._args = args
        self._cancelled = False

    def __repr__(self):
        res = 'Handler({}, {}, {})'.format(self._when,
                                           self._callback,
                                           self._args)
        if self._cancelled:
            res += '<cancelled>'
        return res

    @property
    def when(self):
        return self._when

    @property
    def callback(self):
        return self._callback

    @property
    def args(self):
        return self._args

    @property
    def cancelled(self):
        return self._cancelled

    def cancel(self):
        self._cancelled = True

    def __lt__(self, other):
        if self._when is None:
            return other._when is not None
        elif other._when is None:
            return False

        return self._when < other._when

    def __le__(self, other):
        if self._when is None:
            return True
        elif other._when is None:
            return False

        return self._when <= other._when

    def __gt__(self, other):
        if self._when is None:
            return False
        elif other._when is None:
            return True

        return self._when > other._when

    def __ge__(self, other):
        if self._when is None:
            return other._when is None
        elif other._when is None:
            return True

        return self._when >= other._when

    def __eq__(self, other):
        return self._when == other._when


def make_handler(when, callback, args):
    if isinstance(callback, Handler):
        assert not args
        assert when is None
        return callback
    return Handler(when, callback, args)


class AbstractEventLoop:
    """Abstract event loop."""

    # TODO: Rename run() -> run_until_idle(), run_forever() -> run().

    def run(self):
        """Run the event loop.  Block until there is nothing left to do."""
        raise NotImplementedError

    def run_forever(self):
        """Run the event loop.  Block until stop() is called."""
        raise NotImplementedError

    def run_once(self, timeout=None):  # NEW!
        """Run one complete cycle of the event loop."""
        raise NotImplementedError

    def run_until_complete(self, future, timeout=None):  # NEW!
        """Run the event loop until a Future is done.

        Return the Future's result, or raise its exception.

        If timeout is not None, run it for at most that long;
        if the Future is still not done, raise TimeoutError
        (but don't cancel the Future).
        """
        raise NotImplementedError

    def stop(self):  # NEW!
        """Stop the event loop as soon as reasonable.

        Exactly how soon that is may depend on the implementation, but
        no more I/O callbacks should be scheduled.
        """
        raise NotImplementedError

    # Methods returning Handlers for scheduling callbacks.

    def call_later(self, delay, callback, *args):
        raise NotImplementedError

    def call_repeatedly(self, interval, callback, *args):  # NEW!
        raise NotImplementedError

    def call_soon(self, callback, *args):
        return self.call_later(0, callback, *args)

    def call_soon_threadsafe(self, callback, *args):
        raise NotImplementedError

    # Methods returning Futures for interacting with threads.

    def wrap_future(self, future):
        raise NotImplementedError

    def run_in_executor(self, executor, callback, *args):
        raise NotImplementedError

    # Network I/O methods returning Futures.

    def getaddrinfo(self, host, port, *, family=0, type=0, proto=0, flags=0):
        raise NotImplementedError

    def getnameinfo(self, sockaddr, flags=0):
        raise NotImplementedError

    def create_connection(self, protocol_factory, host=None, port=None, *,
                          family=0, proto=0, flags=0, sock=None):
        raise NotImplementedError

    def start_serving(self, protocol_factory, host=None, port=None, *,
                      family=0, proto=0, flags=0, sock=None):
        raise NotImplementedError

    # Ready-based callback registration methods.
    # The add_*() methods return a Handler.
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

    def add_connector(self, fd, callback, *args):
        raise NotImplementedError

    def remove_connector(self, fd):
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

    # Signal handling.

    def add_signal_handler(self, sig, callback, *args):
        raise NotImplementedError

    def remove_signal_handler(self, sig):
        raise NotImplementedError


class EventLoopPolicy:
    """Abstract policy for accessing the event loop."""

    def get_event_loop(self):
        """XXX"""
        raise NotImplementedError

    def set_event_loop(self, event_loop):
        """XXX"""
        raise NotImplementedError

    def new_event_loop(self):
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
            self._event_loop = self.new_event_loop()
        return self._event_loop

    def set_event_loop(self, event_loop):
        """Set the event loop."""
        assert event_loop is None or isinstance(event_loop, AbstractEventLoop)
        self._event_loop = event_loop

    def new_event_loop(self):
        """Create a new event loop.

        You must call set_event_loop() to make this the current event
        loop.
        """
        # TODO: Do something else for Windows.
        if sys.platform == 'win32':
            from . import windows_events
            return windows_events.SelectorEventLoop()
        else:
            from . import unix_events
            return unix_events.SelectorEventLoop()


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


def new_event_loop():
    """XXX"""
    return get_event_loop_policy().new_event_loop()
