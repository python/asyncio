++++++++++++++
Using Trollius
++++++++++++++

.. warning::
   :ref:`The Trollius project is now deprecated! <deprecated>`

Documentation of the asyncio module
===================================

The documentation of the asyncio is part of the Python project. It can be read
online: `asyncio - Asynchronous I/O, event loop, coroutines and tasks
<http://docs.python.org/dev/library/asyncio.html>`_.

To adapt asyncio examples for Trollius, "just":

* replace ``asyncio`` with ``trollius``
  (or use ``import trollius as asyncio``)
* replace ``yield from ...`` with ``yield From(...)``
* replace ``yield from []`` with ``yield From(None)``
* in coroutines, replace ``return res`` with ``raise Return(res)``


Trollius Hello World
====================

Print ``Hello World`` every two seconds, using a coroutine::

    import trollius
    from trollius import From

    @trollius.coroutine
    def greet_every_two_seconds():
        while True:
            print('Hello World')
            yield From(trollius.sleep(2))

    loop = trollius.get_event_loop()
    loop.run_until_complete(greet_every_two_seconds())


Debug mode
==========

To enable the debug mode:

* Set ``TROLLIUSDEBUG`` envrironment variable to ``1``
* Configure logging to log at level ``logging.DEBUG``,
  ``logging.basicConfig(level=logging.DEBUG)`` for example

The ``BaseEventLoop.set_debug()`` method can be used to set the debug mode on a
specific event loop. The environment variable enables also the debug mode for
coroutines.

Effect of the debug mode:

* On Python 2, :meth:`Future.set_exception` stores the traceback, so
  ``loop.run_until_complete()`` raises the exception with the original
  traceback.
* Log coroutines defined but never "yielded"
* BaseEventLoop.call_soon() and BaseEventLoop.call_at() methods raise an
  exception if they are called from the wrong thread.
* Log the execution time of the selector
* Log callbacks taking more than 100 ms to be executed. The
  BaseEventLoop.slow_callback_duration attribute is the minimum duration in
  seconds of "slow" callbacks.
* Log most important subprocess events:

  - Log stdin, stdout and stderr transports and protocols
  - Log process identifier (pid)
  - Log connection of pipes
  - Log process exit
  - Log Process.communicate() tasks: feed stdin, read stdout and stderr

* Log most important socket events:

  - Socket connected
  - New client (socket.accept())
  - Connection reset or closed by peer (EOF)
  - Log time elapsed in DNS resolution (getaddrinfo)
  - Log pause/resume reading
  - Log time of SSL handshake
  - Log SSL handshake errors

See `Debug mode of asyncio
<https://docs.python.org/dev/library/asyncio-dev.html#debug-mode-of-asyncio>`_
for more information.

