from setuptools import Extension, setup

setup(ext_modules=[Extension("_scanlib_accel", sources=["src/_scanlib_accel.c"])])
