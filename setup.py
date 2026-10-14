#the c part is required so installing needs a c compiler and the python headers
from setuptools import Extension, setup

setup(ext_modules=[Extension("uncord._native", ["uncord/_native.c"], extra_compile_args=["-O2"])])
