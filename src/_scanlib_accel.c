/*
 * _scanlib_accel — CPython C extension for scanlib hot paths.
 *
 * JPEG encoding is delegated to stb_image_write (public domain).
 * Pixel conversion utilities (rgb_to_gray, gray_to_bw, trim_rows)
 * are simple C reimplementations of the former pure-Python code.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#define STB_IMAGE_WRITE_IMPLEMENTATION
#define STBI_WRITE_NO_STDIO
#include "stb_image_write.h"

/* ------------------------------------------------------------------ */
/* Growable byte buffer for stb callback                              */
/* ------------------------------------------------------------------ */

typedef struct {
    unsigned char *data;
    size_t len;
    size_t cap;
} ByteBuf;

static void bytebuf_init(ByteBuf *b) {
    b->data = NULL;
    b->len = 0;
    b->cap = 0;
}

static int bytebuf_append(ByteBuf *b, const void *src, size_t n) {
    if (b->len + n > b->cap) {
        size_t new_cap = (b->cap == 0) ? 4096 : b->cap;
        while (new_cap < b->len + n)
            new_cap *= 2;
        unsigned char *tmp = (unsigned char *)realloc(b->data, new_cap);
        if (!tmp) return -1;
        b->data = tmp;
        b->cap = new_cap;
    }
    memcpy(b->data + b->len, src, n);
    b->len += n;
    return 0;
}

static void bytebuf_free(ByteBuf *b) {
    free(b->data);
    b->data = NULL;
    b->len = b->cap = 0;
}

/* stb write callback */
static void stb_write_cb(void *context, void *data, int size) {
    ByteBuf *buf = (ByteBuf *)context;
    bytebuf_append(buf, data, (size_t)size);
}

/* ------------------------------------------------------------------ */
/* encode_jpeg                                                        */
/* ------------------------------------------------------------------ */

static PyObject *py_encode_jpeg(PyObject *self, PyObject *args) {
    Py_buffer pixels;
    int width, height, color_type, quality;

    if (!PyArg_ParseTuple(args, "y*iiii", &pixels, &width, &height,
                          &color_type, &quality))
        return NULL;

    int comp;
    if (color_type == 0)
        comp = 1;
    else if (color_type == 2)
        comp = 3;
    else {
        PyBuffer_Release(&pixels);
        PyErr_SetString(PyExc_ValueError, "color_type must be 0 or 2");
        return NULL;
    }

    Py_ssize_t expected = (Py_ssize_t)width * height * comp;
    if (pixels.len < expected) {
        PyBuffer_Release(&pixels);
        PyErr_Format(PyExc_ValueError,
                     "pixel buffer too small: need %zd, got %zd",
                     expected, pixels.len);
        return NULL;
    }

    if (quality < 1) quality = 1;
    if (quality > 100) quality = 100;

    ByteBuf buf;
    bytebuf_init(&buf);
    const unsigned char *pdata = (const unsigned char *)pixels.buf;

    int ok;
    Py_BEGIN_ALLOW_THREADS
    ok = stbi_write_jpg_to_func(stb_write_cb, &buf, width, height,
                                comp, pdata, quality);
    Py_END_ALLOW_THREADS

    PyBuffer_Release(&pixels);

    if (!ok) {
        bytebuf_free(&buf);
        PyErr_SetString(PyExc_RuntimeError, "JPEG encoding failed");
        return NULL;
    }

    PyObject *result = PyBytes_FromStringAndSize((const char *)buf.data,
                                                 (Py_ssize_t)buf.len);
    bytebuf_free(&buf);
    return result;
}

/* ------------------------------------------------------------------ */
/* rgb_to_gray                                                        */
/* ------------------------------------------------------------------ */

static PyObject *py_rgb_to_gray(PyObject *self, PyObject *args) {
    Py_buffer data;
    int width, height;

    if (!PyArg_ParseTuple(args, "y*ii", &data, &width, &height))
        return NULL;

    Py_ssize_t count = (Py_ssize_t)width * height;
    if (data.len < count * 3) {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError, "pixel buffer too small");
        return NULL;
    }

    PyObject *result = PyBytes_FromStringAndSize(NULL, count);
    if (!result) {
        PyBuffer_Release(&data);
        return NULL;
    }

    const unsigned char *src = (const unsigned char *)data.buf;
    unsigned char *dst = (unsigned char *)PyBytes_AS_STRING(result);

    Py_BEGIN_ALLOW_THREADS
    for (Py_ssize_t i = 0; i < count; i++) {
        Py_ssize_t off = i * 3;
        dst[i] = (unsigned char)(
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

static PyObject *py_gray_to_bw(PyObject *self, PyObject *args) {
    Py_buffer data;
    int width, height;

    if (!PyArg_ParseTuple(args, "y*ii", &data, &width, &height))
        return NULL;

    Py_ssize_t count = (Py_ssize_t)width * height;
    if (data.len < count) {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError, "pixel buffer too small");
        return NULL;
    }

    int row_bytes = (width + 7) / 8;
    Py_ssize_t out_len = (Py_ssize_t)row_bytes * height;

    PyObject *result = PyBytes_FromStringAndSize(NULL, out_len);
    if (!result) {
        PyBuffer_Release(&data);
        return NULL;
    }

    const unsigned char *src = (const unsigned char *)data.buf;
    unsigned char *dst = (unsigned char *)PyBytes_AS_STRING(result);
    memset(dst, 0, (size_t)out_len);

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
                    byte_val |= (unsigned char)(0x80 >> bit);
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

static PyObject *py_trim_rows(PyObject *self, PyObject *args) {
    Py_buffer data;
    int height, stride, row_width;

    if (!PyArg_ParseTuple(args, "y*iii", &data, &height, &stride, &row_width))
        return NULL;

    if (stride <= row_width) {
        /* No trimming needed — return input data as-is */
        PyObject *result = PyBytes_FromStringAndSize(
            (const char *)data.buf, data.len);
        PyBuffer_Release(&data);
        return result;
    }

    Py_ssize_t expected = (Py_ssize_t)stride * height;
    if (data.len < expected) {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError, "data buffer too small");
        return NULL;
    }

    Py_ssize_t out_len = (Py_ssize_t)row_width * height;
    PyObject *result = PyBytes_FromStringAndSize(NULL, out_len);
    if (!result) {
        PyBuffer_Release(&data);
        return NULL;
    }

    const unsigned char *src = (const unsigned char *)data.buf;
    unsigned char *dst = (unsigned char *)PyBytes_AS_STRING(result);

    Py_BEGIN_ALLOW_THREADS
    for (int y = 0; y < height; y++) {
        memcpy(dst + y * row_width, src + y * stride, (size_t)row_width);
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
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_scanlib_accel",
    "Accelerated helpers for scanlib (JPEG encoding, pixel conversion).",
    -1,
    methods
};

PyMODINIT_FUNC PyInit__scanlib_accel(void) {
    return PyModule_Create(&module);
}
