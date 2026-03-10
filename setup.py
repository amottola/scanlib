import sys

from setuptools import Extension, setup

if sys.platform == "win32":
    extra_compile_args = ["/std:c++11"]
else:
    extra_compile_args = ["-std=c++11"]

setup(ext_modules=[Extension(
    "_scanlib_accel",
    sources=["src/accel/_scanlib_accel.cpp"],
    extra_compile_args=extra_compile_args,
)])
