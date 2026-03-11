import sys

from setuptools import Extension, setup

define_macros = []
libraries = []

if sys.platform == "linux":
    define_macros.append(("HAVE_JPEGLIB", "1"))
    libraries.append("jpeg")

setup(
    ext_modules=[
        Extension(
            "_scanlib_accel",
            sources=["src/accel/_scanlib_accel.c"],
            define_macros=define_macros,
            libraries=libraries,
        )
    ]
)
