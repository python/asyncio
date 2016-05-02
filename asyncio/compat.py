"""Compatibility helpers for the different Python versions."""

import os
import sys

PY34 = sys.version_info >= (3, 4)
PY35 = sys.version_info >= (3, 5)
PY352 = sys.version_info >= (3, 5, 2)


def flatten_list_bytes(list_of_data):
    """Concatenate a sequence of bytes-like objects."""
    if not PY34:
        # On Python 3.3 and older, bytes.join() doesn't handle
        # memoryview.
        list_of_data = (
            bytes(data) if isinstance(data, memoryview) else data
            for data in list_of_data)
    return b''.join(list_of_data)


try:
    SC_IOV_MAX = os.sysconf(os.sysconf_names['SC_IOV_MAX'])
except (KeyError, AttributeError):
    # Windows-version have no os.sysconf() at all.
    # It is not defined how IOV-related syscalls are limited
    # when SC_IOV_MAX is missing.
    # MacOS X, FreeBSD and Linux typically have value of 1024
    SC_IOV_MAX = 16

assert SC_IOV_MAX >= 16
