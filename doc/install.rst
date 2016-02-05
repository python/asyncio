++++++++++++++++
Install Trollius
++++++++++++++++

.. warning::
   :ref:`The Trollius project is now deprecated! <deprecated>`

Trollius supports Python 2.7, 3.3 and 3.4.

There is an experimental support of Python 3.5. Issues with Python 3.5:

* don't support asyncio coroutines
* ``Task.get_task()`` is broken
* ``repr(Task)`` is broken

Support of Python 2.6 and 3.2 was dropped in Trollius 2.1.


Packages for Linux
==================

* `Debian package
  <https://packages.debian.org/fr/sid/python-trollius>`_
* `ArchLinux package
  <https://aur.archlinux.org/packages/python2-trollius/>`_
* `Fedora and CentOS package: python-trollius
  <http://pkgs.org/download/python-trollius>`_


Install Trollius on Windows using pip
=====================================

Since Trollius 0.2, `precompiled wheel packages <http://pythonwheels.com/>`_
are now distributed on the Python Cheeseshop (PyPI). Procedure to install
Trollius on Windows:

* `Install pip
  <http://www.pip-installer.org/en/latest/installing.html>`_, download
  ``get-pip.py`` and type::

  \Python27\python.exe get-pip.py

* If you already have pip, ensure that you have at least pip 1.4. If you need
  to upgrade::

  \Python27\python.exe -m pip install -U pip

* Install Trollius::

  \Python27\python.exe -m pip install trollius

* pip also installs the ``futures`` dependency

.. note::

   Only wheel packages for Python 2.7, 3.3 and 3.4 are currently distributed on
   the Cheeseshop (PyPI). If you need wheel packages for other Python versions,
   please ask.

Download source code
====================

Command to download the development version of the source code (``trollius``
branch)::

    git clone https://github.com/haypo/trollius.git -b trollius

The actual code lives in the ``trollius`` subdirectory. Tests are in the
``tests`` subdirectory.

See the `trollius project at Github
<https://github.com/haypo/trollius>`_.

The source code of the Trollius project is in the ``trollius`` branch of the
Mercurial repository, not in the default branch. The default branch is the
Tulip project, Trollius repository is a fork of the Tulip repository.


Dependencies
============

Trollius requires the `six <https://pypi.python.org/pypi/six>`_ module.

Python 2.7 requires `futures <https://pypi.python.org/pypi/futures>`_ to get a
backport of ``concurrent.futures``.


Build manually Trollius on Windows
==================================

On Windows, if you cannot use precompiled wheel packages, an extension module
must be compiled: the ``_overlapped`` module (source code: ``overlapped.c``).
Read `Compile Python extensions on Windows
<http://haypo-notes.readthedocs.org/python.html#compile-python-extensions-on-windows>`_
to prepare your environment to build the Python extension. Then build the
extension using::

    C:\Python27\python.exe setup.py build_ext

.. _backports:

Backports
=========

To support old Python versions, many Python modules of the standard library
have been backported:

========================  =========  =======================
Name                      Python     Backport
========================  =========  =======================
OSError                        3.3   asyncio.py33_exceptions
_overlapped                    3.4   asyncio._overlapped
_winapi                        3.3   asyncio.py33_winapi
collections.OrderedDict   2.7, 3.1   ordereddict (PyPI)
concurrent.futures             3.2   futures (PyPI)
selectors                      3.4   asyncio.selectors
ssl                       3.2, 3.3   asyncio.py3_ssl
time.monotonic                 3.3   asyncio.time_monotonic
unittest                  2.7, 3.1   unittest2 (PyPI)
unittest.mock                  3.3   mock (PyPI)
weakref.WeakSet           2.7, 3.0   asyncio.py27_weakrefset
========================  =========  =======================



