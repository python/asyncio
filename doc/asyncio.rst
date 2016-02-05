++++++++++++++++++++
Trollius and asyncio
++++++++++++++++++++

.. warning::
   :ref:`The Trollius project is now deprecated! <deprecated>`

Differences between Trollius and asyncio
========================================

Syntax of coroutines
--------------------

The major difference between Trollius and asyncio is the syntax of coroutines:

==================  ======================
asyncio             Trollius
==================  ======================
``yield from ...``  ``yield From(...)``
``yield from []``   ``yield From(None)``
``return``          ``raise Return()``
``return x``        ``raise Return(x)``
``return x, y``     ``raise Return(x, y)``
==================  ======================

Because of this major difference, it was decided to call the module
``trollius`` instead of ``asyncio``. This choice also allows to use Trollius on
Python 3.4 and later. Changing imports is not enough to use Trollius code with
asyncio: the asyncio event loop explicit rejects coroutines using ``yield``
(instead of ``yield from``).

OSError and socket.error exceptions
-----------------------------------

The ``OSError`` exception changed in Python 3.3: there are now subclasses like
``ConnectionResetError`` or ``BlockingIOError``. The exception hierarchy also
changed: ``socket.error`` is now an alias to ``OSError``. The ``asyncio``
module is written for Python 3.3 and newer and so is based on these new
exceptions.

.. seealso::

   `PEP 3151: Reworking the OS and IO exception hierarchy
   <https://www.python.org/dev/peps/pep-3151>`_.

On Python 3.2 and older, Trollius wraps ``OSError``, ``IOError``,
``socket.error`` and ``select.error`` exceptions on operating system and socket
operations to raise more specific exceptions, subclasses of ``OSError``:

* ``trollius.BlockingIOError``
* ``trollius.BrokenPipeError``
* ``trollius.ChildProcessError``
* ``trollius.ConnectionAbortedError``
* ``trollius.ConnectionRefusedError``
* ``trollius.ConnectionResetError``
* ``trollius.FileNotFoundError``
* ``trollius.InterruptedError``
* ``trollius.PermissionError``

On Python 3.3 and newer, these symbols are just aliases to builtin exceptions.

.. note::

   ``ssl.SSLError`` exceptions are not wrapped to ``OSError``, even if
   ``ssl.SSLError`` is a subclass of ``socket.error``.


SSLError
--------

On Python 3.2 and older, Trollius wraps ``ssl.SSLError`` exceptions to raise
more specific exceptions, subclasses of ``ssl.SSLError``, to mimic the Python
3.3:

* ``trollius.SSLEOFError``
* ``trollius.SSLWantReadError``
* ``trollius.SSLWantWriteError``

On Python 3.3 and newer, these symbols are just aliases to exceptions of the
``ssl`` module.

``trollius.BACKPORT_SSL_ERRORS`` constant:

* ``True`` if ``ssl.SSLError`` are wrapped to Trollius exceptions (Python 2
  older than 2.7.9, or Python 3 older than 3.3),
* ``False`` is trollius SSL exceptions are just aliases.


SSLContext
----------

Python 3.3 has a new ``ssl.SSLContext`` class: see the `documentaton of the
ssl.SSLContext class
<https://docs.python.org/3/library/ssl.html#ssl.SSLContext>`_.

On Python 3.2 and older, Trollius has a basic ``trollius.SSLContext`` class to
mimic Python 3.3 API, but it only has a few features:

* ``protocol``, ``certfile`` and ``keyfile`` attributes
* read-only ``verify_mode`` attribute: its value is ``CERT_NONE``
* ``load_cert_chain(certfile, keyfile)`` method
* ``wrap_socket(sock, **kw)`` method: see the ``ssl.wrap_socket()``
  documentation of your Python version for the keyword parameters

Example of missing features:

* no ``options`` attribute
* the ``verify_mode`` attriubte cannot be modified
* no ``set_default_verify_paths()`` method
* no "Server Name Indication" (SNI) support
* etc.

On Python 3.2 and older, the trollius SSL transport does not have the
``'compression'`` extra info.

``trollius.BACKPORT_SSL_CONTEXT`` constant:

* ``True`` if ``trollius.SSLContext`` is the backported class (Python 2 older
  than 2.7.9, or Python 3 older than 3.3),
* ``False`` if ``trollius.SSLContext`` is just an alias to ``ssl.SSLContext``.


Other differences
-----------------

* Trollius uses the ``TROLLIUSDEBUG`` envrionment variable instead of
  the ``PYTHONASYNCIODEBUG`` envrionment variable. ``TROLLIUSDEBUG`` variable
  is used even if the Python command line option ``-E`` is used.
* ``asyncio.subprocess`` has no ``DEVNULL`` constant
* Python 2 does not support keyword-only parameters.
* If the ``concurrent.futures`` module is missing,
  ``BaseEventLoop.run_in_executor()`` uses a synchronous executor instead of a
  pool of threads. It blocks until the function returns. For example, DNS
  resolutions are blocking in this case.
* Trollius has more symbols than asyncio for compatibility with Python older
  than 3.3:

  - ``From``: part of ``yield From(...)`` syntax
  - ``Return``: part of ``raise Return(...)`` syntax


Write code working on Trollius and asyncio
==========================================

Trollius and asyncio are different, especially for coroutines (``yield
From(...)`` vs ``yield from ...``).

To use asyncio or Trollius on Python 2 and Python 3, add the following code at
the top of your file::

    try:
        # Use builtin asyncio on Python 3.4+, or asyncio on Python 3.3
        import asyncio
    except ImportError:
        # Use Trollius on Python <= 3.2
        import trollius as asyncio

It is possible to write code working on both projects using only callbacks.
This option is used by the following projects which work on Trollius and asyncio:

* `AutobahnPython <https://github.com/tavendo/AutobahnPython>`_: WebSocket &
  WAMP for Python, it works on Trollius (Python 2.6 and 2.7), asyncio (Python
  3.3) and Python 3.4 (asyncio), and also on Twisted.
* `Pulsar <http://pythonhosted.org/pulsar/>`_: Event driven concurrent
  framework for Python. With pulsar you can write asynchronous servers
  performing one or several activities in different threads and/or processes.
  Trollius 0.3 requires Pulsar 0.8.2 or later. Pulsar uses the ``asyncio``
  module if available, or import ``trollius``.
* `Tornado <http://www.tornadoweb.org/>`_ supports asyncio and Trollius since
  Tornado 3.2: `tornado.platform.asyncio — Bridge between asyncio and Tornado
  <http://tornado.readthedocs.org/en/latest/asyncio.html>`_. It tries to import
  asyncio or fallback on importing trollius.

Another option is to provide functions returning ``Future`` objects, so the
caller can decide to use callback using ``fut.add_done_callback(callback)`` or
to use coroutines (``yield From(fut)`` for Trollius, or ``yield from fut`` for
asyncio). This option is used by the `aiodns <https://github.com/saghul/aiodns>`_
project for example.

Since Trollius 0.4, it's possible to use asyncio and Trollius coroutines in the
same process. The only limit is that the event loop must be a Trollius event
loop.

.. note::

   The Trollius module was called ``asyncio`` in Trollius version 0.2. The
   module name changed to ``trollius`` to support Python 3.4.

