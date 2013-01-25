from distutils.core import setup, Extension

ext = Extension('_overlapped', ['overlapped.c'], libraries=['ws2_32'])
setup(name='_overlapped', ext_modules=[ext])
