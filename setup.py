# Release procedure:
#  - fill trollius changelog
#  - run maybe ./update-asyncio-step1.sh
#  - run all tests: tox
#  - test examples
#  - check that "python setup.py sdist" contains all files tracked by
#    the SCM (Mercurial): update MANIFEST.in if needed
#  - run test on Windows: releaser.py test
#  - update version in setup.py (version) and doc/conf.py (version, release)
#  - set release date in doc/changelog.rst
#  - git commit
#  - git tag trollius-VERSION
#  - git push --tags
#  - git push
#  - On Linux: python setup.py register sdist bdist_wheel upload
#  - On Windows: python releaser.py release
#  - increment version in setup.py (version) and doc/conf.py (version, release)
#  - gt commit && git push

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

with open("README.rst") as fp:
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
    "version": "2.0",
    "license": "Apache License 2.0",
    "author": 'Victor Stinner',
    "author_email": 'victor.stinner@gmail.com',

    "description": "Port of the Tulip project (asyncio module, PEP 3156) on Python 2",
    "long_description": long_description,
    "url": "https://github.com/haypo/trollius",

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
