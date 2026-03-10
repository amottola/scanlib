/*
 * _scanlib_accel — CPython C extension for scanlib hot paths.
 *
 * Pixel conversion utilities (rgb_to_gray, rgb_to_bgr, gray_to_bw,
 * trim_rows, strip_alpha), BMP parsing (bmp_to_raw), and rotation
 * (rotate_pixels).
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <string.h>
#include <stdlib.h>

/* ------------------------------------------------------------------ */
/* rgb_to_gray                                                        */
/* ------------------------------------------------------------------ */

static PyObject *py_rgb_to_gray(PyObject *Py_UNUSED(self), PyObject *args) {
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
/* rgb_to_bgr                                                         */
/* ------------------------------------------------------------------ */

static PyObject *py_rgb_to_bgr(PyObject *Py_UNUSED(self), PyObject *args) {
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

    PyObject *result = PyBytes_FromStringAndSize(NULL, count * 3);
    if (!result) {
        PyBuffer_Release(&data);
        return NULL;
    }

    const unsigned char *src = (const unsigned char *)data.buf;
    unsigned char *dst = (unsigned char *)PyBytes_AS_STRING(result);

    Py_BEGIN_ALLOW_THREADS
    for (Py_ssize_t i = 0; i < count; i++) {
        Py_ssize_t off = i * 3;
        dst[off]     = src[off + 2];  /* B <- R */
        dst[off + 1] = src[off + 1];  /* G <- G */
        dst[off + 2] = src[off];      /* R <- B */
    }
    Py_END_ALLOW_THREADS

    PyBuffer_Release(&data);
    return result;
}

/* ------------------------------------------------------------------ */
/* gray_to_bw                                                         */
/* ------------------------------------------------------------------ */

static PyObject *py_gray_to_bw(PyObject *Py_UNUSED(self), PyObject *args) {
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

static PyObject *py_trim_rows(PyObject *Py_UNUSED(self), PyObject *args) {
    Py_buffer data;
    int height, stride, row_width;

    if (!PyArg_ParseTuple(args, "y*iii", &data, &height, &stride, &row_width))
        return NULL;

    if (stride <= row_width) {
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
        memcpy(dst + y * row_width, src + y * stride,
               (size_t)row_width);
    }
    Py_END_ALLOW_THREADS

    PyBuffer_Release(&data);
    return result;
}

/* ------------------------------------------------------------------ */
/* strip_alpha                                                        */
/* ------------------------------------------------------------------ */

static PyObject *py_strip_alpha(PyObject *Py_UNUSED(self), PyObject *args) {
    Py_buffer data;
    int width, height, src_channels;

    if (!PyArg_ParseTuple(args, "y*iii", &data, &width, &height, &src_channels))
        return NULL;

    if (src_channels < 4) {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError, "src_channels must be >= 4");
        return NULL;
    }

    Py_ssize_t expected = (Py_ssize_t)width * height * src_channels;
    if (data.len < expected) {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError, "pixel buffer too small");
        return NULL;
    }

    Py_ssize_t out_len = (Py_ssize_t)width * height * 3;
    PyObject *result = PyBytes_FromStringAndSize(NULL, out_len);
    if (!result) {
        PyBuffer_Release(&data);
        return NULL;
    }

    const unsigned char *src = (const unsigned char *)data.buf;
    unsigned char *dst = (unsigned char *)PyBytes_AS_STRING(result);
    Py_ssize_t count = (Py_ssize_t)width * height;

    Py_BEGIN_ALLOW_THREADS
    for (Py_ssize_t i = 0; i < count; i++) {
        const unsigned char *p = src + i * src_channels;
        unsigned char *q = dst + i * 3;
        q[0] = p[0];
        q[1] = p[1];
        q[2] = p[2];
    }
    Py_END_ALLOW_THREADS

    PyBuffer_Release(&data);
    return result;
}

/* ------------------------------------------------------------------ */
/* bmp_to_raw                                                         */
/* ------------------------------------------------------------------ */

static PyObject *py_bmp_to_raw(PyObject *Py_UNUSED(self), PyObject *args) {
    Py_buffer data;

    if (!PyArg_ParseTuple(args, "y*", &data))
        return NULL;

    const unsigned char *buf = (const unsigned char *)data.buf;
    Py_ssize_t buf_len = data.len;

    /* Validate BMP signature */
    if (buf_len < 54 || buf[0] != 'B' || buf[1] != 'M') {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError, "Invalid BMP data");
        return NULL;
    }

    /* Parse BMP file header */
    unsigned int data_offset;
    memcpy(&data_offset, buf + 10, 4);

    /* Parse DIB header */
    unsigned int header_size;
    memcpy(&header_size, buf + 14, 4);

    if (header_size < 40) {
        PyBuffer_Release(&data);
        PyErr_Format(PyExc_ValueError,
                     "Unsupported BMP header size: %u", header_size);
        return NULL;
    }

    int bmp_width;
    int bmp_height_signed;
    memcpy(&bmp_width, buf + 18, 4);
    memcpy(&bmp_height_signed, buf + 22, 4);

    unsigned short bits_per_pixel;
    memcpy(&bits_per_pixel, buf + 28, 2);

    int bottom_up = bmp_height_signed > 0;
    int height = bottom_up ? bmp_height_signed : -bmp_height_signed;
    int width = bmp_width;

    if (width <= 0 || height <= 0) {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError, "Invalid BMP dimensions");
        return NULL;
    }

    int color_type, bit_depth, channels;
    if (bits_per_pixel == 24) {
        color_type = 2;  /* RGB */
        channels = 3;
        bit_depth = 8;
    } else if (bits_per_pixel == 32) {
        color_type = 6;  /* RGBA */
        channels = 4;
        bit_depth = 8;
    } else if (bits_per_pixel == 8) {
        color_type = 0;  /* Grayscale */
        channels = 1;
        bit_depth = 8;
    } else if (bits_per_pixel == 1) {
        color_type = 0;  /* Grayscale 1-bit */
        channels = 0;    /* special handling */
        bit_depth = 1;
    } else {
        PyBuffer_Release(&data);
        PyErr_Format(PyExc_ValueError,
                     "Unsupported BMP bit depth: %u", bits_per_pixel);
        return NULL;
    }

    const unsigned char *pixel_data = buf + data_offset;

    PyObject *result = NULL;

    if (bits_per_pixel == 1) {
        /* 1-bit BMP: rows are bit-packed, padded to 4 bytes */
        unsigned int palette_offset = 14 + header_size;
        if ((Py_ssize_t)(palette_offset + 8) > buf_len) {
            PyBuffer_Release(&data);
            PyErr_SetString(PyExc_ValueError, "Invalid BMP: palette truncated");
            return NULL;
        }
        unsigned char pal_0 = buf[palette_offset];       /* blue of entry 0 */
        unsigned char pal_1 = buf[palette_offset + 4];   /* blue of entry 1 */
        int invert = pal_0 > pal_1;

        int bmp_row_size = ((width + 31) / 32) * 4;
        int png_row_bytes = (width + 7) / 8;
        Py_ssize_t out_len = (Py_ssize_t)png_row_bytes * height;

        result = PyBytes_FromStringAndSize(NULL, out_len);
        if (!result) {
            PyBuffer_Release(&data);
            return NULL;
        }
        unsigned char *dst = (unsigned char *)PyBytes_AS_STRING(result);

        Py_BEGIN_ALLOW_THREADS
        for (int y = 0; y < height; y++) {
            int src_y = bottom_up ? (height - 1 - y) : y;
            const unsigned char *row_src = pixel_data +
                (Py_ssize_t)src_y * bmp_row_size;
            unsigned char *row_dst = dst +
                (Py_ssize_t)y * png_row_bytes;
            memcpy(row_dst, row_src, (size_t)png_row_bytes);
            if (invert) {
                for (int i = 0; i < png_row_bytes; i++)
                    row_dst[i] ^= 0xFF;
            }
            /* Mask unused trailing bits in last byte */
            int remainder = width % 8;
            if (remainder)
                row_dst[png_row_bytes - 1] &=
                    (unsigned char)((0xFF << (8 - remainder)) & 0xFF);
        }
        Py_END_ALLOW_THREADS
    } else {
        /* 8/24/32-bit BMP */
        int bmp_row_size = (width * channels + 3) & ~3;
        Py_ssize_t out_len = (Py_ssize_t)width * height * channels;

        result = PyBytes_FromStringAndSize(NULL, out_len);
        if (!result) {
            PyBuffer_Release(&data);
            return NULL;
        }
        unsigned char *dst = (unsigned char *)PyBytes_AS_STRING(result);

        Py_BEGIN_ALLOW_THREADS
        for (int y = 0; y < height; y++) {
            int src_y = bottom_up ? (height - 1 - y) : y;
            const unsigned char *row_src = pixel_data +
                (Py_ssize_t)src_y * bmp_row_size;
            unsigned char *row_dst = dst +
                (Py_ssize_t)y * width * channels;

            memcpy(row_dst, row_src,
                   (size_t)(width * channels));

            /* BGR(A) -> RGB(A) swap */
            if (channels >= 3) {
                for (int x = 0; x < width; x++) {
                    int i = x * channels;
                    unsigned char tmp = row_dst[i];
                    row_dst[i] = row_dst[i + 2];
                    row_dst[i + 2] = tmp;
                }
            }
        }
        Py_END_ALLOW_THREADS
    }

    PyBuffer_Release(&data);

    /* Return (raw_bytes, width, height, color_type, bit_depth) */
    PyObject *tuple = PyTuple_New(5);
    if (!tuple) {
        Py_DECREF(result);
        return NULL;
    }
    PyTuple_SET_ITEM(tuple, 0, result);
    PyTuple_SET_ITEM(tuple, 1, PyLong_FromLong(width));
    PyTuple_SET_ITEM(tuple, 2, PyLong_FromLong(height));
    PyTuple_SET_ITEM(tuple, 3, PyLong_FromLong(color_type));
    PyTuple_SET_ITEM(tuple, 4, PyLong_FromLong(bit_depth));
    return tuple;
}

/* ------------------------------------------------------------------ */
/* rotate_pixels                                                      */
/* ------------------------------------------------------------------ */

static PyObject *py_rotate_pixels(PyObject *Py_UNUSED(self), PyObject *args) {
    Py_buffer data;
    int width, height, color_type, bit_depth, degrees;

    if (!PyArg_ParseTuple(args, "y*iiiii", &data, &width, &height,
                          &color_type, &bit_depth, &degrees))
        return NULL;

    if (degrees != 90 && degrees != 180 && degrees != 270) {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError,
                        "degrees must be 90, 180, or 270");
        return NULL;
    }

    if (width <= 0 || height <= 0) {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError, "invalid dimensions");
        return NULL;
    }

    int bpp;  /* bytes per pixel for 8-bit modes */
    Py_ssize_t expected;
    if (bit_depth == 1) {
        bpp = 0;
        int row_bytes = (width + 7) / 8;
        expected = (Py_ssize_t)row_bytes * height;
    } else if (color_type == 0) {
        bpp = 1;
        expected = (Py_ssize_t)width * height;
    } else {
        bpp = 3;
        expected = (Py_ssize_t)width * height * 3;
    }

    if (data.len < expected) {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError, "pixel buffer too small");
        return NULL;
    }

    const unsigned char *src = (const unsigned char *)data.buf;
    int new_w = (degrees == 180) ? width : height;
    int new_h = (degrees == 180) ? height : width;

    PyObject *result = NULL;

    if (bit_depth == 1) {
        /* ---- 1-bit packed rotation ---- */
        int src_row_bytes = (width + 7) / 8;
        int dst_row_bytes = (new_w + 7) / 8;
        Py_ssize_t out_len = (Py_ssize_t)dst_row_bytes * new_h;

        result = PyBytes_FromStringAndSize(NULL, out_len);
        if (!result) {
            PyBuffer_Release(&data);
            return NULL;
        }
        unsigned char *dst = (unsigned char *)PyBytes_AS_STRING(result);
        memset(dst, 0, (size_t)out_len);

        Py_BEGIN_ALLOW_THREADS
        if (degrees == 180) {
            for (int y = 0; y < height; y++) {
                int dy = height - 1 - y;
                for (int x = 0; x < width; x++) {
                    int dx = width - 1 - x;
                    int src_bit = (src[y * src_row_bytes + x / 8]
                                   >> (7 - (x & 7))) & 1;
                    if (src_bit)
                        dst[dy * dst_row_bytes + dx / 8] |=
                            (unsigned char)(0x80 >> (dx & 7));
                }
            }
        } else {
            /* 90/270: unpack, rotate, repack */
            Py_ssize_t npix = (Py_ssize_t)width * height;
            unsigned char *tmp = (unsigned char *)malloc((size_t)npix);

            /* Unpack bits to bytes (1 = white, 0 = black) */
            for (int y = 0; y < height; y++) {
                for (int x = 0; x < width; x++) {
                    tmp[y * width + x] =
                        (src[y * src_row_bytes + x / 8]
                         >> (7 - (x & 7))) & 1;
                }
            }

            /* Rotate and repack */
            for (int y = 0; y < height; y++) {
                for (int x = 0; x < width; x++) {
                    int dx, dy;
                    if (degrees == 90) {
                        dx = height - 1 - y;
                        dy = x;
                    } else { /* 270 */
                        dx = y;
                        dy = width - 1 - x;
                    }
                    if (tmp[y * width + x])
                        dst[dy * dst_row_bytes + dx / 8] |=
                            (unsigned char)(0x80 >> (dx & 7));
                }
            }
            free(tmp);
        }
        Py_END_ALLOW_THREADS
    } else {
        /* ---- 8-bit grayscale or RGB rotation ---- */
        Py_ssize_t out_len = (Py_ssize_t)new_w * new_h * bpp;

        result = PyBytes_FromStringAndSize(NULL, out_len);
        if (!result) {
            PyBuffer_Release(&data);
            return NULL;
        }
        unsigned char *dst = (unsigned char *)PyBytes_AS_STRING(result);

        Py_BEGIN_ALLOW_THREADS
        if (degrees == 180) {
            for (int y = 0; y < height; y++) {
                int dy = height - 1 - y;
                const unsigned char *srow = src + y * width * bpp;
                unsigned char *drow = dst + dy * width * bpp;
                for (int x = 0; x < width; x++) {
                    int dx = width - 1 - x;
                    memcpy(drow + dx * bpp, srow + x * bpp,
                           (size_t)bpp);
                }
            }
        } else if (degrees == 90) {
            /* (x,y) -> (h-1-y, x) in new (h x w) image */
            for (int y = 0; y < height; y++) {
                for (int x = 0; x < width; x++) {
                    int dx = height - 1 - y;
                    int dy = x;
                    memcpy(
                        dst + (dy * new_w + dx) * bpp,
                        src + (y * width + x) * bpp,
                        (size_t)bpp);
                }
            }
        } else { /* 270 */
            /* (x,y) -> (y, w-1-x) in new (h x w) image */
            for (int y = 0; y < height; y++) {
                for (int x = 0; x < width; x++) {
                    int dx = y;
                    int dy = width - 1 - x;
                    memcpy(
                        dst + (dy * new_w + dx) * bpp,
                        src + (y * width + x) * bpp,
                        (size_t)bpp);
                }
            }
        }
        Py_END_ALLOW_THREADS
    }

    PyBuffer_Release(&data);
    return result;
}

/* ------------------------------------------------------------------ */
/* Module definition                                                  */
/* ------------------------------------------------------------------ */

static PyMethodDef methods[] = {
    {"rgb_to_gray", py_rgb_to_gray, METH_VARARGS,
     "rgb_to_gray(data, width, height) -> bytes\n"
     "Convert 8-bit interleaved RGB to 8-bit grayscale."},
    {"rgb_to_bgr", py_rgb_to_bgr, METH_VARARGS,
     "rgb_to_bgr(data, width, height) -> bytes\n"
     "Convert 8-bit interleaved RGB to BGR (swap R and B channels)."},
    {"gray_to_bw", py_gray_to_bw, METH_VARARGS,
     "gray_to_bw(data, width, height) -> bytes\n"
     "Convert 8-bit grayscale to 1-bit packed (MSB first)."},
    {"trim_rows", py_trim_rows, METH_VARARGS,
     "trim_rows(data, height, stride, row_width) -> bytes\n"
     "Remove row padding from raw scan data."},
    {"strip_alpha", py_strip_alpha, METH_VARARGS,
     "strip_alpha(data, width, height, src_channels) -> bytes\n"
     "Strip extra channels from interleaved pixel data (e.g. RGBX -> RGB)."},
    {"bmp_to_raw", py_bmp_to_raw, METH_VARARGS,
     "bmp_to_raw(data) -> tuple[bytes, int, int, int, int]\n"
     "Convert BMP file bytes to raw pixels. Returns "
     "(raw_data, width, height, color_type, bit_depth)."},
    {"rotate_pixels", py_rotate_pixels, METH_VARARGS,
     "rotate_pixels(data, width, height, color_type, bit_depth, degrees)"
     " -> bytes\n"
     "Rotate raw pixels clockwise by 90, 180, or 270 degrees."},
    {NULL, NULL, 0, NULL}
};

static PyModuleDef_Slot module_slots[] = {
#ifdef Py_GIL_DISABLED
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},
#endif
    {0, NULL}
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_scanlib_accel",
    "Accelerated helpers for scanlib (pixel conversion, rotation, BMP parsing).",
    0,
    methods,
    module_slots,
};

PyMODINIT_FUNC PyInit__scanlib_accel(void) {
    return PyModuleDef_Init(&module);
}
