#the c part is required so installing needs a c compiler and the python headers
import re

from setuptools import Extension, setup


#the title bar shows versions like 0.75f but pip only allows a b and rc after the number, so 0.75f installs as 0.75.6
def version() -> str:
    with open("uncord/__init__.py") as f:
        v = re.search(r'__version__ = "([^"]+)"', f.read()).group(1)
    m = re.fullmatch(r"(\d+(?:\.\d+)*)([a-z])", v)
    return f"{m.group(1)}.{ord(m.group(2)) - 96}" if m else v


setup(version=version(), ext_modules=[Extension("uncord._native", ["uncord/_native.c"], extra_compile_args=["-O2"])])
