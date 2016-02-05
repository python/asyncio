.. _libraries:

++++++++++++++++++
Trollius Libraries
++++++++++++++++++

.. warning::
   :ref:`The Trollius project is now deprecated! <deprecated>`

Libraries compatible with asyncio and trollius
==============================================

* `aioeventlet <https://aioeventlet.readthedocs.org/>`_: asyncio API
  implemented on top of eventlet
* `aiogevent <https://pypi.python.org/pypi/aiogevent>`_: asyncio API
  implemented on top of gevent
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

Specific Ports
==============

* `trollius-redis <https://github.com/benjolitz/trollius-redis>`_:
  A port of `asyncio-redis <http://asyncio-redis.readthedocs.org/>`_ to
  trollius
