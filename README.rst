Trollius provides infrastructure for writing single-threaded concurrent
code using coroutines, multiplexing I/O access over sockets and other
resources, running network clients and servers, and other related primitives.
Here is a more detailed list of the package contents:

* a pluggable event loop with various system-specific implementations;

* transport and protocol abstractions (similar to those in `Twisted
  <http://twistedmatrix.com/>`_);

* concrete support for TCP, UDP, SSL, subprocess pipes, delayed calls, and
  others (some may be system-dependent);

* a ``Future`` class that mimics the one in the ``concurrent.futures`` module,
  but adapted for use with the event loop;

* coroutines and tasks based on generators (``yield``), to help write
  concurrent code in a sequential fashion;

* cancellation support for ``Future``\s and coroutines;

* synchronization primitives for use between coroutines in a single thread,
  mimicking those in the ``threading`` module;

* an interface for passing work off to a threadpool, for times when you
  absolutely, positively have to use a library that makes blocking I/O calls.

Trollius is a portage of the `asyncio project
<https://github.com/python/asyncio>`_ (`PEP 3156
<http://legacy.python.org/dev/peps/pep-3156/>`_) on Python 2. Trollius works on
Python 2.6-3.5. It has been tested on Windows, Linux, Mac OS X, FreeBSD and
OpenIndiana.

* `Asyncio documentation <http://docs.python.org/dev/library/asyncio.html>`_
* `Trollius documentation <http://trollius.readthedocs.org/>`_
* `Trollius project in the Python Cheeseshop (PyPI)
  <https://pypi.python.org/pypi/trollius>`_
* `Trollius project at Github <https://github.com/haypo/trollius>`_
  (bug tracker, source code)
* Copyright/license: Open source, Apache 2.0. Enjoy!

See also the `asyncio project at Github <https://github.com/python/asyncio>`_.
