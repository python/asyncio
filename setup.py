import os
try:
    from setuptools import setup, Extension
except ImportError:
    # Use distutils.core as a fallback.
    # We won't be able to build the Wheel file on Windows.
    from distutils.core import setup, Extension

extensions = []
if os.name == 'nt':
    ext = Extension(
        'asyncio._overlapped', ['overlapped.c'], libraries=['ws2_32'],
    )
    extensions.append(ext)

setup(
    name="asyncio",
    version="0.4.1",

    description="reference implementation of PEP 3156",
    long_description=open("README").read(),
    url="http://www.python.org/dev/peps/pep-3156/",

    classifiers=[
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.3",
    ],

    packages=["asyncio"],

    ext_modules=extensions,
)
