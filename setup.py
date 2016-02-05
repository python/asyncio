# Prepare a release:
#
#  - fill trollius changelog
#  - run maybe ./update-asyncio-step1.sh
#  - run all tests on Linux: tox
#  - run tests on Windows
#  - test examples
#  - check that "python setup.py sdist" contains all files tracked by
#    the SCM (Mercurial): update MANIFEST.in if needed
#  - run test on Windows: releaser.py test
#  - update version in setup.py (version) and doc/conf.py (version, release)
#  - set release date in doc/changelog.rst
#  - git commit
#  - git push
#
# Release a new version:
#
#  - git tag trollius-VERSION
#  - git push --tags
#  - On Linux: python setup.py register sdist upload
#    FIXME: don't use bdist_wheel because of
#    FIXME: https://github.com/haypo/trollius/issues/1
#  - On Windows: python releaser.py release
#
# After the release:
#
#  - increment version in setup.py (version) and doc/conf.py (version, release)
#  - git commit -a && git push

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

requirements = ['six']
if sys.version_info < (2, 7):
    requirements.append('ordereddict')
if sys.version_info < (3,):
    requirements.append('futures')

install_options = {
    "name": "trollius",
    "version": "2.1",
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

print("!!! WARNING !!! The Trollius project is now deprecated!")
print("")

setup(**install_options)

print("")
print("!!! WARNING !!! The Trollius project is now deprecated!")
