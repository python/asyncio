.. image:: https://travis-ci.org/python/asyncio.svg?branch=master
    :target: https://travis-ci.org/python/asyncio

.. image:: https://ci.appveyor.com/api/projects/status/u72781t69ljdpm2y?svg=true
    :target: https://ci.appveyor.com/project/1st1/asyncio


The asyncio module provides infrastructure for writing single-threaded
concurrent code using coroutines, multiplexing I/O access over sockets and
other resources, running network clients and servers, and other related
primitives.  Here is a more detailed list of the package contents:

* a pluggable event loop with various system-specific implementations;

* transport and protocol abstractions (similar to those in Twisted);

* concrete support for TCP, UDP, SSL, subprocess pipes, delayed calls, and
  others (some may be system-dependent);

* a Future class that mimics the one in the concurrent.futures module, but
  adapted for use with the event loop;

* coroutines and tasks based on ``yield from`` (PEP 380), to help write
  concurrent code in a sequential fashion;

* cancellation support for Futures and coroutines;

* synchronization primitives for use between coroutines in a single thread,
  mimicking those in the threading module;

* an interface for passing work off to a threadpool, for times when you
  absolutely, positively have to use a library that makes blocking I/O calls.

Note: The implementation of asyncio was previously called "Tulip".


Installation
============

To install asyncio, type::

    pip install asyncio

asyncio requires Python 3.3 or later! The asyncio module is part of the Python
standard library since Python 3.4.

asyncio is a free software distributed under the Apache license version 2.0.


Websites
========

* `asyncio project at GitHub <https://github.com/python/asyncio>`_: source
  code, bug tracker
* `asyncio documentation <https://docs.python.org/dev/library/asyncio.html>`_
* Mailing list: `python-tulip Google Group
  <https://groups.google.com/forum/?fromgroups#!forum/python-tulip>`_
* IRC: join the ``#asyncio`` channel on the Freenode network


Development
===========

The actual code lives in the 'asyncio' subdirectory. Tests are in the 'tests'
subdirectory.

To run tests, run::

    tox

Or use the Makefile::

    make test

To run coverage (coverage package is required)::

    make coverage

On Windows, things are a little more complicated.  Assume ``P`` is your
Python binary (for example ``C:\Python33\python.exe``).

You must first build the _overlapped.pyd extension and have it placed
in the asyncio directory, as follows::

    C:\> P setup.py build_ext --inplace

If this complains about vcvars.bat, you probably don't have the
required version of Visual Studio installed.  Compiling extensions for
Python 3.3 requires Microsoft Visual C++ 2010 (MSVC 10.0) of any
edition; you can download Visual Studio Express 2010 for free from
http://www.visualstudio.com/downloads (scroll down to Visual C++ 2010
Express).

Once you have built the _overlapped.pyd extension successfully you can
run the tests as follows::

    C:\> P runtests.py

And coverage as follows::

    C:\> P runtests.py --coverage
