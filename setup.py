# Release procedure:
#  - fill Tulip changelog
#  - run maybe update_tulip.sh
#  - run unit tests with concurrent.futures
#  - run unit tests without concurrent.futures
#  - run unit tests without ssl: set sys.modules['ssl']=None at startup
#  - test examples
#  - update version in setup.py (version) and doc/conf.py (version, release)
#  - set release date in doc/changelog.rst
#  - check that "python setup.py sdist" contains all files tracked by
#    the SCM (Mercurial): update MANIFEST.in if needed
#  - hg ci
#  - hg tag trollius-VERSION
#  - hg push
#  - On Linux: python setup.py register sdist bdist_wheel upload
#  - On Windows: python release.py release
#  - increment version in setup.py (version) and doc/conf.py (version, release)
#  - hg ci && hg push

import os
import sys
try:
    from setuptools import setup, Extension
    SETUPTOOLS = True
except ImportError:
    SETUPTOOLS = False
    # Use distutils.core as a fallback.
    # We won't be able to build the Wheel file on Windows.
    from distutils.core import setup, Extension

with open("README") as fp:
    long_description = fp.read()

extensions = []
if os.name == 'nt':
    ext = Extension(
        'trollius._overlapped', ['overlapped.c'], libraries=['ws2_32'],
    )
    extensions.append(ext)

requirements = []
if sys.version_info < (2, 7):
    requirements.append('ordereddict')
if sys.version_info < (3,):
    requirements.append('futures')

install_options = {
    "name": "trollius",
    "version": "1.0.5",
    "license": "Apache License 2.0",
    "author": 'Victor Stinner',
    "author_email": 'victor.stinner@gmail.com',

    "description": "Port of the Tulip project (asyncio module, PEP 3156) on Python 2",
    "long_description": long_description,
    "url": "https://bitbucket.org/enovance/trollius/",

    "classifiers": [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache Software License",
    ],

    "packages": ["trollius"],
    "test_suite": "runtests.runtests",

    "ext_modules": extensions,
}
if SETUPTOOLS:
    install_options['install_requires'] = requirements

setup(**install_options)
