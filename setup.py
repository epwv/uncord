#the c speedups are optional, without a compiler uncord installs and uses the python versions
from setuptools import Extension, setup

setup(ext_modules=[Extension("uncord._native", ["uncord/_native.c"], optional=True, extra_compile_args=["-O2"])])
