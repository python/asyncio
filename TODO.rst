Unsorted "TODO" tasks:

* reuse selectors backport from PyPI
* check ssl.SSLxxx in update_xxx.sh
* document how to port asyncio to trollius
* use six instead of compat
* Replace logger with warning in monotonic clock and synchronous executor
* Windows: use _overlapped in py33_winapi?
* Fix tests failing with PyPy:

  - sys.getrefcount()
  - test_queues.test_repr
  - test_futures.test_tb_logger_exception_unretrieved

* write unit test for create_connection(ssl=True)
* Fix examples:

  - stacks.py: 'exceptions.ZeroDivisionError' object has no attribute '__traceback__'

* Fix all FIXME in the code
