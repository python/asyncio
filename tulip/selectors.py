"""Select module.

This module supports asynchronous I/O on multiple file descriptors.
"""

import sys
from select import *

from .log import tulip_log


# generic events, that must be mapped to implementation-specific ones
# read event
EVENT_READ = (1 << 0)
# write event
EVENT_WRITE = (1 << 1)


def _fileobj_to_fd(fileobj):
    """Return a file descriptor from a file object.

    Parameters:
    fileobj -- file descriptor, or any object with a `fileno()` method

    Returns:
    corresponding file descriptor
    """
    if isinstance(fileobj, int):
        fd = fileobj
    else:
        try:
            fd = int(fileobj.fileno())
        except (ValueError, TypeError):
            raise ValueError("Invalid file object: {!r}".format(fileobj))
    return fd


class SelectorKey:
    """Object used internally to associate a file object to its backing file
    descriptor, selected event mask and attached data."""

    def __init__(self, fileobj, events, data=None):
        self.fileobj = fileobj
        self.fd = _fileobj_to_fd(fileobj)
        self.events = events
        self.data = data

    def __repr__(self):
        return '{}<fileobj={}, fd={}, events={:#x}, data={}>'.format(
            self.__class__.__name__,
            self.fileobj, self.fd, self.events, self.data)


class _BaseSelector:
    """Base selector class.

    A selector supports registering file objects to be monitored for specific
    I/O events.

    A file object is a file descriptor or any object with a `fileno()` method.
    An arbitrary object can be attached to the file object, which can be used
    for example to store context information, a callback, etc.

    A selector can use various implementations (select(), poll(), epoll()...)
    depending on the platform. The default `Selector` class uses the most
    performant implementation on the current platform.
    """

    def __init__(self):
        # this maps file descriptors to keys
        self._fd_to_key = {}
        # this maps file objects to keys - for fast (un)registering
        self._fileobj_to_key = {}

    def register(self, fileobj, events, data=None):
        """Register a file object.

        Parameters:
        fileobj -- file object
        events  -- events to monitor (bitwise mask of EVENT_READ|EVENT_WRITE)
        data    -- attached data

        Returns:
        SelectorKey instance
        """
        if (not events) or (events & ~(EVENT_READ|EVENT_WRITE)):
            raise ValueError("Invalid events: {}".format(events))

        if fileobj in self._fileobj_to_key:
            raise ValueError("{!r} is already registered".format(fileobj))

        key = SelectorKey(fileobj, events, data)
        self._fd_to_key[key.fd] = key
        self._fileobj_to_key[fileobj] = key
        return key

    def unregister(self, fileobj):
        """Unregister a file object.

        Parameters:
        fileobj -- file object

        Returns:
        SelectorKey instance
        """
        try:
            key = self._fileobj_to_key[fileobj]
            del self._fd_to_key[key.fd]
            del self._fileobj_to_key[fileobj]
        except KeyError:
            raise ValueError("{!r} is not registered".format(fileobj))
        return key

    def modify(self, fileobj, events, data=None):
        """Change a registered file object monitored events or attached data.

        Parameters:
        fileobj -- file object
        events  -- events to monitor (bitwise mask of EVENT_READ|EVENT_WRITE)
        data    -- attached data
        """
        # TODO: Subclasses can probably optimize this even further.
        try:
            key = self._fileobj_to_key[fileobj]
        except KeyError:
            raise ValueError("{!r} is not registered".format(fileobj))
        if events != key.events or data != key.data:
            # TODO: If only the data changed, use a shortcut that only
            # updates the data.
            self.unregister(fileobj)
            return self.register(fileobj, events, data)
        else:
            return key

    def select(self, timeout=None):
        """Perform the actual selection, until some monitored file objects are
        ready or a timeout expires.

        Parameters:
        timeout -- if timeout > 0, this specifies the maximum wait time, in
                   seconds
                   if timeout == 0, the select() call won't block, and will
                   report the currently ready file objects
                   if timeout is None, select() will block until a monitored
                   file object becomes ready

        Returns:
        list of (fileobj, events, attached data) for ready file objects
        `events` is a bitwise mask of EVENT_READ|EVENT_WRITE
        """
        raise NotImplementedError()

    def close(self):
        """Close the selector.

        This must be called to make sure that any underlying resource is freed.
        """
        self._fd_to_key.clear()
        self._fileobj_to_key.clear()

    def get_info(self, fileobj):
        """Return information about a registered file object.

        Returns:
        (events, data) associated to this file object

        Raises KeyError if the file object is not registered.
        """
        try:
            key = self._fileobj_to_key[fileobj]
        except KeyError:
            raise KeyError("{} is not registered".format(fileobj))
        return key.events, key.data

    def registered_count(self):
        """Return the number of registered file objects.

        Returns:
        number of currently registered file objects
        """
        return len(self._fd_to_key)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _key_from_fd(self, fd):
        """Return the key associated to a given file descriptor.

        Parameters:
        fd -- file descriptor

        Returns:
        corresponding key
        """
        try:
            return self._fd_to_key[fd]
        except KeyError:
            tulip_log.warning('No key found for fd %r', fd)
            return None


class SelectSelector(_BaseSelector):
    """Select-based selector."""

    def __init__(self):
        super().__init__()
        self._readers = set()
        self._writers = set()

    def register(self, fileobj, events, data=None):
        key = super().register(fileobj, events, data)
        if events & EVENT_READ:
            self._readers.add(key.fd)
        if events & EVENT_WRITE:
            self._writers.add(key.fd)
        return key

    def unregister(self, fileobj):
        key = super().unregister(fileobj)
        self._readers.discard(key.fd)
        self._writers.discard(key.fd)
        return key

    def select(self, timeout=None):
        try:
            r, w, _ = self._select(self._readers, self._writers, [], timeout)
        except InterruptedError:
            # A signal arrived.  Don't die, just return no events.
            return []
        r = set(r)
        w = set(w)
        ready = []
        for fd in r | w:
            events = 0
            if fd in r:
                events |= EVENT_READ
            if fd in w:
                events |= EVENT_WRITE

            key = self._key_from_fd(fd)
            if key:
                ready.append((key.fileobj, events & key.events, key.data))
        return ready

    if sys.platform == 'win32':
        def _select(self, r, w, _, timeout=None):
            r, w, x = select(r, w, w, timeout)
            return r, w + x, []
    else:
        from select import select as _select


if 'poll' in globals():

    # TODO: Implement poll() for Windows with workaround for
    # brokenness in WSAPoll() (Richard Oudkerk, see
    # http://bugs.python.org/issue16507).

    class PollSelector(_BaseSelector):
        """Poll-based selector."""

        def __init__(self):
            super().__init__()
            self._poll = poll()

        def register(self, fileobj, events, data=None):
            key = super().register(fileobj, events, data)
            poll_events = 0
            if events & EVENT_READ:
                poll_events |= POLLIN
            if events & EVENT_WRITE:
                poll_events |= POLLOUT
            self._poll.register(key.fd, poll_events)
            return key

        def unregister(self, fileobj):
            key = super().unregister(fileobj)
            self._poll.unregister(key.fd)
            return key

        def select(self, timeout=None):
            timeout = None if timeout is None else int(1000 * timeout)
            ready = []
            try:
                fd_event_list = self._poll.poll(timeout)
            except InterruptedError:
                # A signal arrived.  Don't die, just return no events.
                return []
            for fd, event in fd_event_list:
                events = 0
                if event & ~POLLIN:
                    events |= EVENT_WRITE
                if event & ~POLLOUT:
                    events |= EVENT_READ

                key = self._key_from_fd(fd)
                if key:
                    ready.append((key.fileobj, events & key.events, key.data))
            return ready


if 'epoll' in globals():

    class EpollSelector(_BaseSelector):
        """Epoll-based selector."""

        def __init__(self):
            super().__init__()
            self._epoll = epoll()

        def fileno(self):
            return self._epoll.fileno()

        def register(self, fileobj, events, data=None):
            key = super().register(fileobj, events, data)
            epoll_events = 0
            if events & EVENT_READ:
                epoll_events |= EPOLLIN
            if events & EVENT_WRITE:
                epoll_events |= EPOLLOUT
            self._epoll.register(key.fd, epoll_events)
            return key

        def unregister(self, fileobj):
            key = super().unregister(fileobj)
            self._epoll.unregister(key.fd)
            return key

        def select(self, timeout=None):
            timeout = -1 if timeout is None else timeout
            max_ev = self.registered_count()
            ready = []
            try:
                fd_event_list = self._epoll.poll(timeout, max_ev)
            except InterruptedError:
                # A signal arrived.  Don't die, just return no events.
                return []
            for fd, event in fd_event_list:
                events = 0
                if event & ~EPOLLIN:
                    events |= EVENT_WRITE
                if event & ~EPOLLOUT:
                    events |= EVENT_READ

                key = self._key_from_fd(fd)
                if key:
                    ready.append((key.fileobj, events & key.events, key.data))
            return ready

        def close(self):
            super().close()
            self._epoll.close()


if 'kqueue' in globals():

    class KqueueSelector(_BaseSelector):
        """Kqueue-based selector."""

        def __init__(self):
            super().__init__()
            self._kqueue = kqueue()

        def fileno(self):
            return self._kqueue.fileno()

        def unregister(self, fileobj):
            key = super().unregister(fileobj)
            if key.events & EVENT_READ:
                kev = kevent(key.fd, KQ_FILTER_READ, KQ_EV_DELETE)
                self._kqueue.control([kev], 0, 0)
            if key.events & EVENT_WRITE:
                kev = kevent(key.fd, KQ_FILTER_WRITE, KQ_EV_DELETE)
                self._kqueue.control([kev], 0, 0)
            return key

        def register(self, fileobj, events, data=None):
            key = super().register(fileobj, events, data)
            if events & EVENT_READ:
                kev = kevent(key.fd, KQ_FILTER_READ, KQ_EV_ADD)
                self._kqueue.control([kev], 0, 0)
            if events & EVENT_WRITE:
                kev = kevent(key.fd, KQ_FILTER_WRITE, KQ_EV_ADD)
                self._kqueue.control([kev], 0, 0)
            return key

        def select(self, timeout=None):
            max_ev = self.registered_count()
            ready = []
            try:
                kev_list = self._kqueue.control(None, max_ev, timeout)
            except InterruptedError:
                # A signal arrived.  Don't die, just return no events.
                return []
            for kev in kev_list:
                fd = kev.ident
                flag = kev.filter
                events = 0
                if flag == KQ_FILTER_READ:
                    events |= EVENT_READ
                if flag == KQ_FILTER_WRITE:
                    events |= EVENT_WRITE

                key = self._key_from_fd(fd)
                if key:
                    ready.append((key.fileobj, events & key.events, key.data))
            return ready

        def close(self):
            super().close()
            self._kqueue.close()


# Choose the best implementation: roughly, epoll|kqueue > poll > select.
# select() also can't accept a FD > FD_SETSIZE (usually around 1024)
if 'KqueueSelector' in globals():
    DefaultSelector = KqueueSelector
elif 'EpollSelector' in globals():
    DefaultSelector = EpollSelector
elif 'PollSelector' in globals():
    DefaultSelector = PollSelector
else:
    DefaultSelector = SelectSelector
