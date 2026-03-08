/*
 * _scanlib_accel — CPython C++ extension for scanlib hot paths.
 *
 * JPEG encoding is delegated to toojpeg (zlib license).
 * Pixel conversion utilities (rgb_to_gray, gray_to_bw, trim_rows)
 * are simple C reimplementations of the former pure-Python code.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <cstdlib>
#include <cstring>

#include "toojpeg.h"

/* ------------------------------------------------------------------ */
/* Growable byte buffer                                                */
/* ------------------------------------------------------------------ */

struct ByteBuf {
    unsigned char *data;
    size_t len;
    size_t cap;
};

static void bytebuf_init(ByteBuf *b) {
    b->data = nullptr;
    b->len = 0;
    b->cap = 0;
}

static void bytebuf_append(ByteBuf *b, unsigned char byte) {
    if (b->len >= b->cap) {
        size_t new_cap = (b->cap == 0) ? 4096 : b->cap * 2;
        auto *tmp = static_cast<unsigned char *>(std::realloc(b->data, new_cap));
        if (!tmp) return;
        b->data = tmp;
        b->cap = new_cap;
    }
    b->data[b->len++] = byte;
}

static void bytebuf_free(ByteBuf *b) {
    std::free(b->data);
    b->data = nullptr;
    b->len = b->cap = 0;
}

/* Thread-local buffer pointer for toojpeg callback (no context param) */
static thread_local ByteBuf *tl_buf = nullptr;

static void toojpeg_write_cb(unsigned char byte) {
    bytebuf_append(tl_buf, byte);
}

/* ------------------------------------------------------------------ */
/* encode_jpeg                                                        */
/* ------------------------------------------------------------------ */

static PyObject *py_encode_jpeg(PyObject * /*self*/, PyObject *args) {
    Py_buffer pixels;
    int width, height, color_type, quality;

    if (!PyArg_ParseTuple(args, "y*iiii", &pixels, &width, &height,
                          &color_type, &quality))
        return nullptr;

    bool isRGB;
    int comp;
    if (color_type == 0) {
        isRGB = false;
        comp = 1;
    } else if (color_type == 2) {
        isRGB = true;
        comp = 3;
    } else {
        PyBuffer_Release(&pixels);
        PyErr_SetString(PyExc_ValueError, "color_type must be 0 or 2");
        return nullptr;
    }

    Py_ssize_t expected = static_cast<Py_ssize_t>(width) * height * comp;
    if (pixels.len < expected) {
        PyBuffer_Release(&pixels);
        PyErr_Format(PyExc_ValueError,
                     "pixel buffer too small: need %zd, got %zd",
                     expected, pixels.len);
        return nullptr;
    }

    if (quality < 1) quality = 1;
    if (quality > 100) quality = 100;

    ByteBuf buf;
    bytebuf_init(&buf);
    tl_buf = &buf;
    const auto *pdata = static_cast<const unsigned char *>(pixels.buf);

    bool ok;
    Py_BEGIN_ALLOW_THREADS
    ok = TooJpeg::writeJpeg(toojpeg_write_cb, pdata,
                            static_cast<unsigned short>(width),
                            static_cast<unsigned short>(height),
                            isRGB,
                            static_cast<unsigned char>(quality),
                            isRGB /* downsample 4:2:0 for RGB only */);
    Py_END_ALLOW_THREADS

    tl_buf = nullptr;
    PyBuffer_Release(&pixels);

    if (!ok) {
        bytebuf_free(&buf);
        PyErr_SetString(PyExc_RuntimeError, "JPEG encoding failed");
        return nullptr;
    }

    PyObject *result = PyBytes_FromStringAndSize(
        reinterpret_cast<const char *>(buf.data),
        static_cast<Py_ssize_t>(buf.len));
    bytebuf_free(&buf);
    return result;
}

/* ------------------------------------------------------------------ */
/* rgb_to_gray                                                        */
/* ------------------------------------------------------------------ */

static PyObject *py_rgb_to_gray(PyObject * /*self*/, PyObject *args) {
    Py_buffer data;
    int width, height;

    if (!PyArg_ParseTuple(args, "y*ii", &data, &width, &height))
        return nullptr;

    Py_ssize_t count = static_cast<Py_ssize_t>(width) * height;
    if (data.len < count * 3) {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError, "pixel buffer too small");
        return nullptr;
    }

    PyObject *result = PyBytes_FromStringAndSize(nullptr, count);
    if (!result) {
        PyBuffer_Release(&data);
        return nullptr;
    }

    const auto *src = static_cast<const unsigned char *>(data.buf);
    auto *dst = reinterpret_cast<unsigned char *>(PyBytes_AS_STRING(result));

    Py_BEGIN_ALLOW_THREADS
    for (Py_ssize_t i = 0; i < count; i++) {
        Py_ssize_t off = i * 3;
        dst[i] = static_cast<unsigned char>(
            (76 * src[off] + 150 * src[off + 1] + 29 * src[off + 2]) >> 8
        );
    }
    Py_END_ALLOW_THREADS

    PyBuffer_Release(&data);
    return result;
}

/* ------------------------------------------------------------------ */
/* gray_to_bw                                                         */
/* ------------------------------------------------------------------ */

static PyObject *py_gray_to_bw(PyObject * /*self*/, PyObject *args) {
    Py_buffer data;
    int width, height;

    if (!PyArg_ParseTuple(args, "y*ii", &data, &width, &height))
        return nullptr;

    Py_ssize_t count = static_cast<Py_ssize_t>(width) * height;
    if (data.len < count) {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError, "pixel buffer too small");
        return nullptr;
    }

    int row_bytes = (width + 7) / 8;
    Py_ssize_t out_len = static_cast<Py_ssize_t>(row_bytes) * height;

    PyObject *result = PyBytes_FromStringAndSize(nullptr, out_len);
    if (!result) {
        PyBuffer_Release(&data);
        return nullptr;
    }

    const auto *src = static_cast<const unsigned char *>(data.buf);
    auto *dst = reinterpret_cast<unsigned char *>(PyBytes_AS_STRING(result));
    std::memset(dst, 0, static_cast<size_t>(out_len));

    Py_BEGIN_ALLOW_THREADS
    for (int y = 0; y < height; y++) {
        int src_off = y * width;
        int dst_off = y * row_bytes;
        for (int x = 0; x < width; x += 8) {
            unsigned char byte_val = 0;
            int bits = width - x;
            if (bits > 8) bits = 8;
            for (int bit = 0; bit < bits; bit++) {
                if (src[src_off + x + bit] >= 128)
                    byte_val |= static_cast<unsigned char>(0x80 >> bit);
            }
            dst[dst_off + x / 8] = byte_val;
        }
    }
    Py_END_ALLOW_THREADS

    PyBuffer_Release(&data);
    return result;
}

/* ------------------------------------------------------------------ */
/* trim_rows                                                          */
/* ------------------------------------------------------------------ */

static PyObject *py_trim_rows(PyObject * /*self*/, PyObject *args) {
    Py_buffer data;
    int height, stride, row_width;

    if (!PyArg_ParseTuple(args, "y*iii", &data, &height, &stride, &row_width))
        return nullptr;

    if (stride <= row_width) {
        PyObject *result = PyBytes_FromStringAndSize(
            static_cast<const char *>(data.buf), data.len);
        PyBuffer_Release(&data);
        return result;
    }

    Py_ssize_t expected = static_cast<Py_ssize_t>(stride) * height;
    if (data.len < expected) {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError, "data buffer too small");
        return nullptr;
    }

    Py_ssize_t out_len = static_cast<Py_ssize_t>(row_width) * height;
    PyObject *result = PyBytes_FromStringAndSize(nullptr, out_len);
    if (!result) {
        PyBuffer_Release(&data);
        return nullptr;
    }

    const auto *src = static_cast<const unsigned char *>(data.buf);
    auto *dst = reinterpret_cast<unsigned char *>(PyBytes_AS_STRING(result));

    Py_BEGIN_ALLOW_THREADS
    for (int y = 0; y < height; y++) {
        std::memcpy(dst + y * row_width, src + y * stride,
                     static_cast<size_t>(row_width));
    }
    Py_END_ALLOW_THREADS

    PyBuffer_Release(&data);
    return result;
}

/* ------------------------------------------------------------------ */
/* strip_alpha                                                        */
/* ------------------------------------------------------------------ */

static PyObject *py_strip_alpha(PyObject * /*self*/, PyObject *args) {
    Py_buffer data;
    int width, height, src_channels;

    if (!PyArg_ParseTuple(args, "y*iii", &data, &width, &height, &src_channels))
        return nullptr;

    if (src_channels < 4) {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError, "src_channels must be >= 4");
        return nullptr;
    }

    Py_ssize_t expected = static_cast<Py_ssize_t>(width) * height * src_channels;
    if (data.len < expected) {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError, "pixel buffer too small");
        return nullptr;
    }

    Py_ssize_t out_len = static_cast<Py_ssize_t>(width) * height * 3;
    PyObject *result = PyBytes_FromStringAndSize(nullptr, out_len);
    if (!result) {
        PyBuffer_Release(&data);
        return nullptr;
    }

    const auto *src = static_cast<const unsigned char *>(data.buf);
    auto *dst = reinterpret_cast<unsigned char *>(PyBytes_AS_STRING(result));
    Py_ssize_t count = static_cast<Py_ssize_t>(width) * height;

    Py_BEGIN_ALLOW_THREADS
    for (Py_ssize_t i = 0; i < count; i++) {
        const auto *p = src + i * src_channels;
        auto *q = dst + i * 3;
        q[0] = p[0];
        q[1] = p[1];
        q[2] = p[2];
    }
    Py_END_ALLOW_THREADS

    PyBuffer_Release(&data);
    return result;
}

/* ------------------------------------------------------------------ */
/* Module definition                                                  */
/* ------------------------------------------------------------------ */

static PyMethodDef methods[] = {
    {"encode_jpeg", py_encode_jpeg, METH_VARARGS,
     "encode_jpeg(pixels, width, height, color_type, quality) -> bytes\n"
     "Encode raw pixels as baseline JPEG."},
    {"rgb_to_gray", py_rgb_to_gray, METH_VARARGS,
     "rgb_to_gray(data, width, height) -> bytes\n"
     "Convert 8-bit interleaved RGB to 8-bit grayscale."},
    {"gray_to_bw", py_gray_to_bw, METH_VARARGS,
     "gray_to_bw(data, width, height) -> bytes\n"
     "Convert 8-bit grayscale to 1-bit packed (MSB first)."},
    {"trim_rows", py_trim_rows, METH_VARARGS,
     "trim_rows(data, height, stride, row_width) -> bytes\n"
     "Remove row padding from raw scan data."},
    {"strip_alpha", py_strip_alpha, METH_VARARGS,
     "strip_alpha(data, width, height, src_channels) -> bytes\n"
     "Strip extra channels from interleaved pixel data (e.g. RGBX -> RGB)."},
    {nullptr, nullptr, 0, nullptr}
};

static PyModuleDef_Slot module_slots[] = {
#ifdef Py_GIL_DISABLED
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},
#endif
    {0, nullptr}
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_scanlib_accel",
    "Accelerated helpers for scanlib (JPEG encoding, pixel conversion).",
    0,
    methods,
    module_slots,
};

PyMODINIT_FUNC PyInit__scanlib_accel(void) {
    return PyModuleDef_Init(&module);
}
