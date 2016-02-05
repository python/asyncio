.. _deprecated:

Trollius is deprecated
======================

.. warning::
   The Trollius project is now deprecated!

Trollius is deprecated since the release 2.1. The maintainer of Trollius,
Victor Stinner, doesn't want to maintain the project anymore for many reasons.
This page lists some reasons.

DON'T PANIC! There is the asyncio project which has the same API and is well
maintained! Only trollius is deprecated.

Since the Trollius is used for some projects in the wild, Trollius will
not disappear. You can only expect *minimum* maintenance like minor bugfixes.
Don't expect new features nor synchronization with the latest asyncio.

To be clear: I am looking for a new maintainer. If you want to take over
trollius: please do it, I will give you everything you need (and maybe more!).

asyncio
-------

`asyncio is here <https://github.com/python/asyncio>`_! asyncio is well
maintainted, has a growing community, has many libraries and don't stop
evolving to be enhanced by users feedbacks. I (Victor Stinner) even heard that
it is fast!

asyncio requires Python 3.3 or newer. Yeah, you all have a long list of reasons
to not port your legacy code for Python 3. But I have my own reasons to prefer
to invest in the Python 3 rather than in legacy Python (Python 2).


No Trollius Community
---------------------

* Very the asyncio is growing everyday, there is no trollius community.
  Sadly, asyncio libraries don't work for trollius.
* Only :ref:`very few libraries support Trollius <libraries>`: to be clear,
  there is no HTTP client for Trollius, whereas HTTP is the most common
  protocol in 2015.
* It's a deliberate choice of library authors to not support Trollius to
  keep a simple code base. The Python 3 is simpler than Python 2: supporting
  Python 2 in a library requires more work. For example, aiohttp doesn't
  want to support trollius.

Python 2
--------

* Seriously? Come on! Stop procrastination and upgrade your code to Python 3!

Lack of interest
----------------

* The Trollius project was created to replace eventlet with asyncio (trollius)
  in the OpenStack project, but replacing eventlet with trollius has failed for
  different reasons. The original motivation is gone.

Technical issues with trollius
------------------------------

* While Trollius API is "simple", the implementation is very complex to be
  efficient on all platforms.
* Trollius requires :ref:`backports <backports>` of libraries to support
  old Python versions. These backports are not as well supported as the version
  in the Python standard library.
* Supporting Python 2.7, Python 3.3 and Python 3.5 in the same code base
  and supporting asyncio is very difficult. Generators and coroutines changed
  a lot in each Python version. For example, hacks are required to support
  Python 3.5 with the PEP 479 which changed the usage of the ``StopIteration``
  exception. Trollius initially also supported Python 2.6 and 3.2.

Technical issues related to asyncio and yield from
--------------------------------------------------

* Synchronizing trollius with asyncio is a complex, tricky and error-prone task
  which requires to be very carefull and a lot of manual changes.
* Porting Python 3 asyncio to Python 2 requires a lot of subtle changes which
  takes a lot of time at each synchronization.
* It is not possible to use asyncio ``yield from`` coroutines in Python 2,
  since the ``yield from`` instruction raises a ``SyntaxError`` exceptions.
  Supporting Trollius and asyncio requires to duplicate some parts of the
  library and application code which makes the code more complex and more
  difficult to maintain.
* Trollius coroutines are slower than asyncio coroutines: the ``yield``
  instruction requires to delegate manually nested coroutines, whereas the
  ``yield from`` instruction delegates them directly in the Python language.
  asyncio requires less loop iterations than trollius for the same nested
  coroutine.

Other technical issues
----------------------

* Building wheel packages on Windows is difficult (need a running Windows,
  need working Windows SDKs for each version of Python, need to test
  and fix bugs specific to Windows, etc.)
