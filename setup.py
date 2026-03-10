from setuptools import Extension, setup

setup(
    ext_modules=[
        Extension(
            "_scanlib_accel",
            sources=["src/accel/_scanlib_accel.c"],
        )
    ]
)
