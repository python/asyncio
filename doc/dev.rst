Run tests
=========

.. warning::
   :ref:`The Trollius project is now deprecated! <deprecated>`

Run tests with tox
------------------

The `tox project <https://testrun.org/tox/latest/>`_ can be used to build a
virtual environment with all runtime and test dependencies and run tests
against different Python versions (2.7, 3.3, 3.4).

For example, to run tests with Python 2.7, just type::

    tox -e py27

To run tests against other Python versions:

* ``py27``: Python 2.7
* ``py33``: Python 3.3
* ``py34``: Python 3.4


Test Dependencies
-----------------

On Python older than 3.3, unit tests require the `mock
<https://pypi.python.org/pypi/mock>`_ module. Python 2.6 and 2.7 require also
`unittest2 <https://pypi.python.org/pypi/unittest2>`_.

To run ``run_aiotest.py``, you need the `aiotest
<https://pypi.python.org/pypi/aiotest>`_ test suite: ``pip install aiotest``.


Run tests on UNIX
-----------------

Run the following commands from the directory of the Trollius project.

To run tests::

    make test

To run coverage (``coverage`` package is required)::

    make coverage


Run tests on Windows
--------------------

Run the following commands from the directory of the Trollius project.

You can run the tests as follows::

    C:\Python27\python.exe runtests.py

And coverage as follows::

    C:\Python27\python.exe runtests.py --coverage


CPython bugs
============

The development of asyncio and trollius helped to identify different bugs in CPython:

* 2.5.0 <= python <= 3.4.2: `sys.exc_info() bug when yield/yield-from is used
  in an except block in a generator (#23353>)
  <http://bugs.python.org/issue23353>`_.  The fix will be part of Python 3.4.3.
  _UnixSelectorEventLoop._make_subprocess_transport() and
  ProactorEventLoop._make_subprocess_transport() work around the bug.
* python == 3.4.0: `Segfault in gc with cyclic trash (#21435)
  <http://bugs.python.org/issue21435>`_.
  Regression introduced in Python 3.4.0, fixed in Python 3.4.1.
  Status in Ubuntu the February, 3th 2015: only Ubuntu Trusty (14.04 LTS) is
  impacted (`bug #1367907:  Segfault in gc with cyclic trash
  <https://bugs.launchpad.net/ubuntu/+source/python3.4/+bug/1367907>`_, see
  also `update Python3 for trusty #1348954
  <https://bugs.launchpad.net/ubuntu/+source/python3.4/+bug/1348954>`_)
* 3.3.0 <= python <= 3.4.0: `gen.send(tuple) unpacks the tuple instead of
  passing 1 argument (the tuple) when gen is an object with a send() method,
  not a classic generator (#21209) <http://bugs.python.org/21209>`_.
  Regression introduced in Python 3.4.0, fixed in Python 3.4.1.
  trollius.CoroWrapper.send() works around the issue, the bug is checked at
  runtime once, when the module is imported.
