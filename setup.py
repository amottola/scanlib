import sys
import sysconfig

from setuptools import Extension, setup

define_macros = []
libraries = []

if sys.platform == "linux":
    define_macros.append(("HAVE_JPEGLIB", "1"))
    libraries.append("jpeg")

# Use stable ABI (abi3) for non-free-threaded builds
free_threaded = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
py_limited_api = not free_threaded
if py_limited_api:
    define_macros.append(("Py_LIMITED_API", "0x03090000"))

options = {}
if py_limited_api:
    options["bdist_wheel"] = {"py_limited_api": "cp39"}

setup(
    ext_modules=[
        Extension(
            "_scanlib_accel",
            sources=["src/accel/_scanlib_accel.c"],
            define_macros=define_macros,
            libraries=libraries,
            py_limited_api=py_limited_api,
        )
    ],
    options=options,
)
