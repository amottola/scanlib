/*
 * _scanlib_accel — CPython C extension for scanlib hot paths.
 *
 * Pixel conversion utilities (rgb_to_gray, rgb_to_bgr, gray_to_bw,
 * trim_rows, strip_alpha), BMP parsing (bmp_to_raw), and rotation
 * (rotate_pixels).
 */

#define PY_SSIZE_T_CLEAN
/* Py_LIMITED_API is defined via setup.py for non-free-threaded builds */
#include <Python.h>

#include <string.h>
#include <stdlib.h>

#ifdef HAVE_JPEGLIB
#include <setjmp.h>
#include <jpeglib.h>
#endif

/* ------------------------------------------------------------------ */
/* rgb_to_gray                                                        */
/* ------------------------------------------------------------------ */

static PyObject *py_rgb_to_gray(PyObject *Py_UNUSED(self), PyObject *args) {
    const char *data;
    Py_ssize_t data_len;
    int width, height;

    if (!PyArg_ParseTuple(args, "y#ii", &data, &data_len, &width, &height))
        return NULL;

    Py_ssize_t count = (Py_ssize_t)width * height;
    if (data_len < count * 3) {
        PyErr_SetString(PyExc_ValueError, "pixel buffer too small");
        return NULL;
    }

    PyObject *result = PyBytes_FromStringAndSize(NULL, count);
    if (!result)
        return NULL;

    const unsigned char *src = (const unsigned char *)data;
    unsigned char *dst = (unsigned char *)PyBytes_AsString(result);

    Py_BEGIN_ALLOW_THREADS
    for (Py_ssize_t i = 0; i < count; i++) {
        Py_ssize_t off = i * 3;
        dst[i] = (unsigned char)(
            (76 * src[off] + 150 * src[off + 1] + 29 * src[off + 2]) >> 8
        );
    }
    Py_END_ALLOW_THREADS

    return result;
}

/* ------------------------------------------------------------------ */
/* rgb_to_bgr                                                         */
/* ------------------------------------------------------------------ */

static PyObject *py_rgb_to_bgr(PyObject *Py_UNUSED(self), PyObject *args) {
    const char *data;
    Py_ssize_t data_len;
    int width, height;

    if (!PyArg_ParseTuple(args, "y#ii", &data, &data_len, &width, &height))
        return NULL;

    Py_ssize_t count = (Py_ssize_t)width * height;
    if (data_len < count * 3) {
        PyErr_SetString(PyExc_ValueError, "pixel buffer too small");
        return NULL;
    }

    PyObject *result = PyBytes_FromStringAndSize(NULL, count * 3);
    if (!result)
        return NULL;

    const unsigned char *src = (const unsigned char *)data;
    unsigned char *dst = (unsigned char *)PyBytes_AsString(result);

    Py_BEGIN_ALLOW_THREADS
    for (Py_ssize_t i = 0; i < count; i++) {
        Py_ssize_t off = i * 3;
        dst[off]     = src[off + 2];  /* B <- R */
        dst[off + 1] = src[off + 1];  /* G <- G */
        dst[off + 2] = src[off];      /* R <- B */
    }
    Py_END_ALLOW_THREADS

    return result;
}

/* ------------------------------------------------------------------ */
/* gray_to_bw                                                         */
/* ------------------------------------------------------------------ */

static PyObject *py_gray_to_bw(PyObject *Py_UNUSED(self), PyObject *args) {
    const char *data;
    Py_ssize_t data_len;
    int width, height;

    if (!PyArg_ParseTuple(args, "y#ii", &data, &data_len, &width, &height))
        return NULL;

    Py_ssize_t count = (Py_ssize_t)width * height;
    if (data_len < count) {
        PyErr_SetString(PyExc_ValueError, "pixel buffer too small");
        return NULL;
    }

    int row_bytes = (width + 7) / 8;
    Py_ssize_t out_len = (Py_ssize_t)row_bytes * height;

    PyObject *result = PyBytes_FromStringAndSize(NULL, out_len);
    if (!result)
        return NULL;

    const unsigned char *src = (const unsigned char *)data;
    unsigned char *dst = (unsigned char *)PyBytes_AsString(result);
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
                if (src[src_off + x + bit] >= 64)
                    byte_val |= (unsigned char)(0x80 >> bit);
            }
            dst[dst_off + x / 8] = byte_val;
        }
    }
    Py_END_ALLOW_THREADS

    return result;
}

/* ------------------------------------------------------------------ */
/* bw_to_gray                                                         */
/* ------------------------------------------------------------------ */

static PyObject *py_bw_to_gray(PyObject *Py_UNUSED(self), PyObject *args) {
    const char *data;
    Py_ssize_t data_len;
    int width, height;

    if (!PyArg_ParseTuple(args, "y#ii", &data, &data_len, &width, &height))
        return NULL;

    int row_bytes = (width + 7) / 8;
    Py_ssize_t expected = (Py_ssize_t)row_bytes * height;
    if (data_len < expected) {
        PyErr_SetString(PyExc_ValueError, "pixel buffer too small");
        return NULL;
    }

    Py_ssize_t out_len = (Py_ssize_t)width * height;
    PyObject *result = PyBytes_FromStringAndSize(NULL, out_len);
    if (!result)
        return NULL;

    const unsigned char *src = (const unsigned char *)data;
    unsigned char *dst = (unsigned char *)PyBytes_AsString(result);

    Py_BEGIN_ALLOW_THREADS
    for (int y = 0; y < height; y++) {
        int src_off = y * row_bytes;
        int dst_off = y * width;
        for (int x = 0; x < width; x++) {
            int bit = (src[src_off + x / 8] >> (7 - (x & 7))) & 1;
            dst[dst_off + x] = bit ? 255 : 0;
        }
    }
    Py_END_ALLOW_THREADS

    return result;
}

/* ------------------------------------------------------------------ */
/* trim_rows                                                          */
/* ------------------------------------------------------------------ */

static PyObject *py_trim_rows(PyObject *Py_UNUSED(self), PyObject *args) {
    const char *data;
    Py_ssize_t data_len;
    int height, stride, row_width;

    if (!PyArg_ParseTuple(args, "y#iii", &data, &data_len, &height,
                          &stride, &row_width))
        return NULL;

    if (stride <= row_width) {
        return PyBytes_FromStringAndSize(data, data_len);
    }

    Py_ssize_t expected = (Py_ssize_t)stride * height;
    if (data_len < expected) {
        PyErr_SetString(PyExc_ValueError, "data buffer too small");
        return NULL;
    }

    Py_ssize_t out_len = (Py_ssize_t)row_width * height;
    PyObject *result = PyBytes_FromStringAndSize(NULL, out_len);
    if (!result)
        return NULL;

    const unsigned char *src = (const unsigned char *)data;
    unsigned char *dst = (unsigned char *)PyBytes_AsString(result);

    Py_BEGIN_ALLOW_THREADS
    for (int y = 0; y < height; y++) {
        memcpy(dst + y * row_width, src + y * stride,
               (size_t)row_width);
    }
    Py_END_ALLOW_THREADS

    return result;
}

/* ------------------------------------------------------------------ */
/* strip_alpha                                                        */
/* ------------------------------------------------------------------ */

static PyObject *py_strip_alpha(PyObject *Py_UNUSED(self), PyObject *args) {
    const char *data;
    Py_ssize_t data_len;
    int width, height, src_channels;

    if (!PyArg_ParseTuple(args, "y#iii", &data, &data_len, &width, &height,
                          &src_channels))
        return NULL;

    if (src_channels < 4) {
        PyErr_SetString(PyExc_ValueError, "src_channels must be >= 4");
        return NULL;
    }

    Py_ssize_t expected = (Py_ssize_t)width * height * src_channels;
    if (data_len < expected) {
        PyErr_SetString(PyExc_ValueError, "pixel buffer too small");
        return NULL;
    }

    Py_ssize_t out_len = (Py_ssize_t)width * height * 3;
    PyObject *result = PyBytes_FromStringAndSize(NULL, out_len);
    if (!result)
        return NULL;

    const unsigned char *src = (const unsigned char *)data;
    unsigned char *dst = (unsigned char *)PyBytes_AsString(result);
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

    return result;
}

/* ------------------------------------------------------------------ */
/* bmp_to_raw                                                         */
/* ------------------------------------------------------------------ */

static PyObject *py_bmp_to_raw(PyObject *Py_UNUSED(self), PyObject *args) {
    const char *data;
    Py_ssize_t data_len;

    if (!PyArg_ParseTuple(args, "y#", &data, &data_len))
        return NULL;

    const unsigned char *buf = (const unsigned char *)data;
    Py_ssize_t buf_len = data_len;

    /* Validate BMP signature */
    if (buf_len < 54 || buf[0] != 'B' || buf[1] != 'M') {
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
        if (!result)
            return NULL;
        unsigned char *dst = (unsigned char *)PyBytes_AsString(result);

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
        if (!result)
            return NULL;
        unsigned char *dst = (unsigned char *)PyBytes_AsString(result);

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

    /* Return (raw_bytes, width, height, color_type, bit_depth) */
    PyObject *py_w = PyLong_FromLong(width);
    PyObject *py_h = PyLong_FromLong(height);
    PyObject *py_ct = PyLong_FromLong(color_type);
    PyObject *py_bd = PyLong_FromLong(bit_depth);
    if (!py_w || !py_h || !py_ct || !py_bd) {
        Py_DECREF(result);
        Py_XDECREF(py_w);
        Py_XDECREF(py_h);
        Py_XDECREF(py_ct);
        Py_XDECREF(py_bd);
        return NULL;
    }

    PyObject *tuple = PyTuple_New(5);
    if (!tuple) {
        Py_DECREF(result);
        Py_DECREF(py_w);
        Py_DECREF(py_h);
        Py_DECREF(py_ct);
        Py_DECREF(py_bd);
        return NULL;
    }
    PyTuple_SetItem(tuple, 0, result);
    PyTuple_SetItem(tuple, 1, py_w);
    PyTuple_SetItem(tuple, 2, py_h);
    PyTuple_SetItem(tuple, 3, py_ct);
    PyTuple_SetItem(tuple, 4, py_bd);
    return tuple;
}

/* ------------------------------------------------------------------ */
/* rotate_pixels                                                      */
/* ------------------------------------------------------------------ */

static PyObject *py_rotate_pixels(PyObject *Py_UNUSED(self), PyObject *args) {
    const char *data;
    Py_ssize_t data_len;
    int width, height, bpp, degrees;

    if (!PyArg_ParseTuple(args, "y#iiii", &data, &data_len, &width, &height,
                          &bpp, &degrees))
        return NULL;

    if (degrees != 90 && degrees != 180 && degrees != 270) {
        PyErr_SetString(PyExc_ValueError,
                        "degrees must be 90, 180, or 270");
        return NULL;
    }

    if (width <= 0 || height <= 0) {
        PyErr_SetString(PyExc_ValueError, "invalid dimensions");
        return NULL;
    }

    Py_ssize_t expected;
    if (bpp == 0) {
        int row_bytes = (width + 7) / 8;
        expected = (Py_ssize_t)row_bytes * height;
    } else {
        expected = (Py_ssize_t)width * height * bpp;
    }

    if (data_len < expected) {
        PyErr_SetString(PyExc_ValueError, "pixel buffer too small");
        return NULL;
    }

    const unsigned char *src = (const unsigned char *)data;
    int new_w = (degrees == 180) ? width : height;
    int new_h = (degrees == 180) ? height : width;

    PyObject *result = NULL;

    if (bpp == 0) {
        /* ---- 1-bit packed rotation ---- */
        int src_row_bytes = (width + 7) / 8;
        int dst_row_bytes = (new_w + 7) / 8;
        Py_ssize_t out_len = (Py_ssize_t)dst_row_bytes * new_h;

        result = PyBytes_FromStringAndSize(NULL, out_len);
        if (!result)
            return NULL;
        unsigned char *dst = (unsigned char *)PyBytes_AsString(result);
        memset(dst, 0, (size_t)out_len);

        if (degrees != 180) {
            /* 90/270: need a temp buffer — allocate before releasing GIL */
            Py_ssize_t npix = (Py_ssize_t)width * height;
            unsigned char *tmp = (unsigned char *)malloc((size_t)npix);
            if (!tmp) {
                Py_DECREF(result);
                return PyErr_NoMemory();
            }

            Py_BEGIN_ALLOW_THREADS
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
            Py_END_ALLOW_THREADS
        } else {
            Py_BEGIN_ALLOW_THREADS
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
            Py_END_ALLOW_THREADS
        }
    } else {
        /* ---- 8-bit grayscale or RGB rotation ---- */
        Py_ssize_t out_len = (Py_ssize_t)new_w * new_h * bpp;

        result = PyBytes_FromStringAndSize(NULL, out_len);
        if (!result)
            return NULL;
        unsigned char *dst = (unsigned char *)PyBytes_AsString(result);

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

    return result;
}

/* ------------------------------------------------------------------ */
/* encode_jpeg (Linux only, requires libjpeg)                         */
/* ------------------------------------------------------------------ */

#ifdef HAVE_JPEGLIB

struct my_error_mgr {
    struct jpeg_error_mgr pub;
    jmp_buf setjmp_buffer;
};

static void my_error_exit(j_common_ptr cinfo) {
    struct my_error_mgr *myerr = (struct my_error_mgr *)cinfo->err;
    longjmp(myerr->setjmp_buffer, 1);
}

static PyObject *py_encode_jpeg(PyObject *Py_UNUSED(self), PyObject *args) {
    const char *data;
    Py_ssize_t data_len;
    int width, height, num_components, quality;

    if (!PyArg_ParseTuple(args, "y#iiii", &data, &data_len, &width, &height,
                          &num_components, &quality))
        return NULL;

    if (width <= 0 || height <= 0) {
        PyErr_SetString(PyExc_ValueError, "invalid dimensions");
        return NULL;
    }
    if (num_components != 1 && num_components != 3) {
        PyErr_SetString(PyExc_ValueError,
                        "num_components must be 1 or 3");
        return NULL;
    }

    Py_ssize_t expected = (Py_ssize_t)width * height * num_components;
    if (data_len < expected) {
        PyErr_SetString(PyExc_ValueError, "pixel buffer too small");
        return NULL;
    }

    struct jpeg_compress_struct cinfo;
    struct my_error_mgr jerr;
    unsigned char *outbuf = NULL;
    unsigned long outsize = 0;
    volatile PyThreadState *gil_state = NULL;

    cinfo.err = jpeg_std_error(&jerr.pub);
    jerr.pub.error_exit = my_error_exit;

    if (setjmp(jerr.setjmp_buffer)) {
        if (gil_state)
            PyEval_RestoreThread((PyThreadState *)gil_state);
        jpeg_destroy_compress(&cinfo);
        free(outbuf);
        PyErr_SetString(PyExc_RuntimeError, "libjpeg encoding error");
        return NULL;
    }

    jpeg_create_compress(&cinfo);
    jpeg_mem_dest(&cinfo, &outbuf, &outsize);

    cinfo.image_width = (JDIMENSION)width;
    cinfo.image_height = (JDIMENSION)height;
    cinfo.input_components = num_components;
    cinfo.in_color_space = (num_components == 3) ? JCS_RGB : JCS_GRAYSCALE;

    jpeg_set_defaults(&cinfo);
    jpeg_set_quality(&cinfo, quality, TRUE);
    jpeg_start_compress(&cinfo, TRUE);

    const unsigned char *src = (const unsigned char *)data;
    int row_stride = width * num_components;

    gil_state = PyEval_SaveThread();
    while (cinfo.next_scanline < cinfo.image_height) {
        const unsigned char *row =
            src + (Py_ssize_t)cinfo.next_scanline * row_stride;
        JSAMPROW row_ptr = (JSAMPROW)row;
        jpeg_write_scanlines(&cinfo, &row_ptr, 1);
    }
    PyEval_RestoreThread((PyThreadState *)gil_state);
    gil_state = NULL;

    jpeg_finish_compress(&cinfo);
    jpeg_destroy_compress(&cinfo);

    PyObject *result = PyBytes_FromStringAndSize(
        (const char *)outbuf, (Py_ssize_t)outsize);
    free(outbuf);

    return result;
}

/* ------------------------------------------------------------------ */
/* decode_jpeg (Linux only, requires libjpeg)                         */
/* ------------------------------------------------------------------ */

static PyObject *py_decode_jpeg(PyObject *Py_UNUSED(self), PyObject *args) {
    const char *data;
    Py_ssize_t data_len;

    if (!PyArg_ParseTuple(args, "y#", &data, &data_len))
        return NULL;

    if (data_len < 2) {
        PyErr_SetString(PyExc_ValueError, "data too short for JPEG");
        return NULL;
    }

    struct jpeg_decompress_struct cinfo;
    struct my_error_mgr jerr;
    volatile PyThreadState *gil_state = NULL;

    cinfo.err = jpeg_std_error(&jerr.pub);
    jerr.pub.error_exit = my_error_exit;

    if (setjmp(jerr.setjmp_buffer)) {
        if (gil_state)
            PyEval_RestoreThread((PyThreadState *)gil_state);
        jpeg_destroy_decompress(&cinfo);
        PyErr_SetString(PyExc_RuntimeError, "libjpeg decoding error");
        return NULL;
    }

    jpeg_create_decompress(&cinfo);
    jpeg_mem_src(&cinfo, (const unsigned char *)data, (unsigned long)data_len);
    jpeg_read_header(&cinfo, TRUE);

    /* Force RGB or grayscale output */
    if (cinfo.num_components == 1) {
        cinfo.out_color_space = JCS_GRAYSCALE;
    } else {
        cinfo.out_color_space = JCS_RGB;
    }

    jpeg_start_decompress(&cinfo);

    int width = (int)cinfo.output_width;
    int height = (int)cinfo.output_height;
    int components = (int)cinfo.output_components;
    Py_ssize_t row_stride = (Py_ssize_t)width * components;
    Py_ssize_t total = row_stride * height;

    PyObject *result = PyBytes_FromStringAndSize(NULL, total);
    if (!result) {
        jpeg_destroy_decompress(&cinfo);
        return NULL;
    }

    unsigned char *dst = (unsigned char *)PyBytes_AsString(result);

    gil_state = PyEval_SaveThread();
    while (cinfo.output_scanline < cinfo.output_height) {
        unsigned char *row = dst + (Py_ssize_t)cinfo.output_scanline * row_stride;
        JSAMPROW row_ptr = (JSAMPROW)row;
        jpeg_read_scanlines(&cinfo, &row_ptr, 1);
    }
    PyEval_RestoreThread((PyThreadState *)gil_state);
    gil_state = NULL;

    jpeg_finish_decompress(&cinfo);
    jpeg_destroy_decompress(&cinfo);

    return Py_BuildValue("(Niii)", result, width, height, components);
}

#endif /* HAVE_JPEGLIB */

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
    {"bw_to_gray", py_bw_to_gray, METH_VARARGS,
     "bw_to_gray(data, width, height) -> bytes\n"
     "Convert 1-bit packed (MSB first) to 8-bit grayscale."},
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
     "rotate_pixels(data, width, height, bpp, degrees) -> bytes\n"
     "Rotate raw pixels clockwise by 90, 180, or 270 degrees.\n"
     "bpp is bytes per pixel (0 for 1-bit packed, 1 for gray, 3 for RGB)."},
#ifdef HAVE_JPEGLIB
    {"encode_jpeg", py_encode_jpeg, METH_VARARGS,
     "encode_jpeg(data, width, height, num_components, quality) -> bytes\n"
     "Encode raw pixels to JPEG using libjpeg."},
    {"decode_jpeg", py_decode_jpeg, METH_VARARGS,
     "decode_jpeg(data) -> tuple[bytes, int, int, int]\n"
     "Decode JPEG bytes to raw pixels. Returns "
     "(raw_data, width, height, num_components)."},
#endif
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
