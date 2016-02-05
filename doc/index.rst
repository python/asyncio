Trollius
========

.. warning::
   :ref:`The Trollius project is now deprecated! <deprecated>`

.. image:: trollius.jpg
   :alt: Trollius altaicus from Khangai Mountains (Mongòlia)
   :align: right
   :target: http://commons.wikimedia.org/wiki/File:Trollius_altaicus.jpg

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

Trollius is a portage of the `asyncio project <https://github.com/python/asyncio>`_
(``asyncio`` module, `PEP 3156 <http://legacy.python.org/dev/peps/pep-3156/>`_)
on Python 2. Trollius works on Python 2.7, 3.3 and 3.4. It has been tested on
Windows, Linux, Mac OS X, FreeBSD and OpenIndiana.

* `Asyncio documentation <http://docs.python.org/dev/library/asyncio.html>`_
* `Trollius documentation <http://trollius.readthedocs.org/>`_ (this document)
* `Trollius project in the Python Cheeseshop (PyPI)
  <https://pypi.python.org/pypi/trollius>`_ (download wheel packages and
  tarballs)
* `Trollius project at Github <https://github.com/haypo/trollius>`_
  (bug tracker, source code)
* Mailing list: `python-tulip Google Group
  <https://groups.google.com/forum/?fromgroups#!forum/python-tulip>`_
* IRC: ``#asyncio`` channel on the `Freenode network <https://freenode.net/>`_
* Copyright/license: Open source, Apache 2.0. Enjoy!

See also the `asyncio project at Github <https://github.com/python/asyncio>`_.


Table Of Contents
=================

.. toctree::

   deprecated
   using
   install
   libraries
   asyncio
   dev
   changelog


Trollius name
=============

Extract of `Trollius Wikipedia article
<http://en.wikipedia.org/wiki/Trollius>`_:

Trollius is a genus of about 30 species of plants in the family Ranunculaceae,
closely related to Ranunculus. The common name of some species is globeflower
or globe flower. Native to the cool temperate regions of the Northern
Hemisphere, with the greatest diversity of species in Asia, trollius usually
grow in heavy, wet clay soils.

