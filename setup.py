import os
import sys
from setuptools import setup, Extension

with open("README.rst") as fp:
    long_description = fp.read()

extensions = []
if os.name == 'nt':
    ext = Extension(
        'trollius._overlapped', ['overlapped.c'], libraries=['ws2_32'],
    )
    extensions.append(ext)

requirements = ['six']
if sys.version_info < (3,):
    requirements.append('futures')

setup(
    name="trollius",
    version="2.2.post1",
    license="Apache License 2.0",
    author='Victor Stinner',
    author_email='victor.stinner@gmail.com',
    description="Deprecated, unmaintained port of the asyncio module (PEP 3156) on Python 2",
    long_description=long_description,
    url="https://github.com/jamadden/trollius",
    classifiers=[
        "Programming Language :: Python",
        "Programming Language :: Python :: 2.7",
        "License :: OSI Approved :: Apache Software License",
    ],
    packages=[
        "trollius",
    ],
    zip_safe=False,
    keywords="Deprecated Unmaintained asyncio backport",
    ext_modules=extensions,
    install_requires=requirements,
    python_requires=">=2.7, < 3",
)
