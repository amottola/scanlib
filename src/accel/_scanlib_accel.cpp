/*
 * _scanlib_accel — CPython C++ extension for scanlib hot paths.
 *
 * Pixel conversion utilities (rgb_to_gray, rgb_to_bgr, gray_to_bw,
 * trim_rows, strip_alpha) and BMP parsing (bmp_to_raw).
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <cstring>

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
/* rgb_to_bgr                                                         */
/* ------------------------------------------------------------------ */

static PyObject *py_rgb_to_bgr(PyObject * /*self*/, PyObject *args) {
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

    PyObject *result = PyBytes_FromStringAndSize(nullptr, count * 3);
    if (!result) {
        PyBuffer_Release(&data);
        return nullptr;
    }

    const auto *src = static_cast<const unsigned char *>(data.buf);
    auto *dst = reinterpret_cast<unsigned char *>(PyBytes_AS_STRING(result));

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
/* bmp_to_raw                                                         */
/* ------------------------------------------------------------------ */

static PyObject *py_bmp_to_raw(PyObject * /*self*/, PyObject *args) {
    Py_buffer data;

    if (!PyArg_ParseTuple(args, "y*", &data))
        return nullptr;

    const auto *buf = static_cast<const unsigned char *>(data.buf);
    Py_ssize_t buf_len = data.len;

    /* Validate BMP signature */
    if (buf_len < 54 || buf[0] != 'B' || buf[1] != 'M') {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError, "Invalid BMP data");
        return nullptr;
    }

    /* Parse BMP file header */
    unsigned int data_offset;
    std::memcpy(&data_offset, buf + 10, 4);

    /* Parse DIB header */
    unsigned int header_size;
    std::memcpy(&header_size, buf + 14, 4);

    if (header_size < 40) {
        PyBuffer_Release(&data);
        PyErr_Format(PyExc_ValueError,
                     "Unsupported BMP header size: %u", header_size);
        return nullptr;
    }

    int bmp_width;
    int bmp_height_signed;
    std::memcpy(&bmp_width, buf + 18, 4);
    std::memcpy(&bmp_height_signed, buf + 22, 4);

    unsigned short bits_per_pixel;
    std::memcpy(&bits_per_pixel, buf + 28, 2);

    bool bottom_up = bmp_height_signed > 0;
    int height = bottom_up ? bmp_height_signed : -bmp_height_signed;
    int width = bmp_width;

    if (width <= 0 || height <= 0) {
        PyBuffer_Release(&data);
        PyErr_SetString(PyExc_ValueError, "Invalid BMP dimensions");
        return nullptr;
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
        return nullptr;
    }

    const unsigned char *pixel_data = buf + data_offset;

    PyObject *result = nullptr;

    if (bits_per_pixel == 1) {
        /* 1-bit BMP: rows are bit-packed, padded to 4 bytes */
        unsigned int palette_offset = 14 + header_size;
        if (static_cast<Py_ssize_t>(palette_offset + 8) > buf_len) {
            PyBuffer_Release(&data);
            PyErr_SetString(PyExc_ValueError, "Invalid BMP: palette truncated");
            return nullptr;
        }
        unsigned char pal_0 = buf[palette_offset];       /* blue of entry 0 */
        unsigned char pal_1 = buf[palette_offset + 4];   /* blue of entry 1 */
        bool invert = pal_0 > pal_1;

        int bmp_row_size = ((width + 31) / 32) * 4;
        int png_row_bytes = (width + 7) / 8;
        Py_ssize_t out_len = static_cast<Py_ssize_t>(png_row_bytes) * height;

        result = PyBytes_FromStringAndSize(nullptr, out_len);
        if (!result) {
            PyBuffer_Release(&data);
            return nullptr;
        }
        auto *dst = reinterpret_cast<unsigned char *>(PyBytes_AS_STRING(result));

        Py_BEGIN_ALLOW_THREADS
        for (int y = 0; y < height; y++) {
            int src_y = bottom_up ? (height - 1 - y) : y;
            const unsigned char *row_src = pixel_data +
                static_cast<Py_ssize_t>(src_y) * bmp_row_size;
            unsigned char *row_dst = dst +
                static_cast<Py_ssize_t>(y) * png_row_bytes;
            std::memcpy(row_dst, row_src, static_cast<size_t>(png_row_bytes));
            if (invert) {
                for (int i = 0; i < png_row_bytes; i++)
                    row_dst[i] ^= 0xFF;
            }
            /* Mask unused trailing bits in last byte */
            int remainder = width % 8;
            if (remainder)
                row_dst[png_row_bytes - 1] &=
                    static_cast<unsigned char>((0xFF << (8 - remainder)) & 0xFF);
        }
        Py_END_ALLOW_THREADS
    } else {
        /* 8/24/32-bit BMP */
        int bmp_row_size = (width * channels + 3) & ~3;
        Py_ssize_t out_len = static_cast<Py_ssize_t>(width) * height * channels;

        result = PyBytes_FromStringAndSize(nullptr, out_len);
        if (!result) {
            PyBuffer_Release(&data);
            return nullptr;
        }
        auto *dst = reinterpret_cast<unsigned char *>(PyBytes_AS_STRING(result));

        Py_BEGIN_ALLOW_THREADS
        for (int y = 0; y < height; y++) {
            int src_y = bottom_up ? (height - 1 - y) : y;
            const unsigned char *row_src = pixel_data +
                static_cast<Py_ssize_t>(src_y) * bmp_row_size;
            unsigned char *row_dst = dst +
                static_cast<Py_ssize_t>(y) * width * channels;

            std::memcpy(row_dst, row_src,
                        static_cast<size_t>(width * channels));

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
        return nullptr;
    }
    PyTuple_SET_ITEM(tuple, 0, result);
    PyTuple_SET_ITEM(tuple, 1, PyLong_FromLong(width));
    PyTuple_SET_ITEM(tuple, 2, PyLong_FromLong(height));
    PyTuple_SET_ITEM(tuple, 3, PyLong_FromLong(color_type));
    PyTuple_SET_ITEM(tuple, 4, PyLong_FromLong(bit_depth));
    return tuple;
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
    "Accelerated helpers for scanlib (pixel conversion, BMP parsing).",
    0,
    methods,
    module_slots,
};

PyMODINIT_FUNC PyInit__scanlib_accel(void) {
    return PyModuleDef_Init(&module);
}
