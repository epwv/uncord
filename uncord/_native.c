//byte crunching that python is slow at: sixel scans, kitty placeholder rows, h264 and rtp packets
//optional, uncord/native.py has the same functions in python if this isnt built
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <string.h>

//textual_image's table, kitty uses these combining marks to number rows and columns
static const uint32_t DIACRITICS[] = {
    0x0305, 0x030d, 0x030e, 0x0310, 0x0312, 0x033d, 0x033e, 0x033f, 0x0346, 0x034a, 0x034b, 0x034c,
    0x0350, 0x0351, 0x0352, 0x0357, 0x035b, 0x0363, 0x0364, 0x0365, 0x0366, 0x0367, 0x0368, 0x0369,
    0x036a, 0x036b, 0x036c, 0x036d, 0x036e, 0x036f, 0x0483, 0x0484, 0x0485, 0x0486, 0x0487, 0x0592,
    0x0593, 0x0594, 0x0595, 0x0597, 0x0598, 0x0599, 0x059c, 0x059d, 0x059e, 0x059f, 0x05a0, 0x05a1,
    0x05a8, 0x05a9, 0x05ab, 0x05ac, 0x05af, 0x05c4, 0x0610, 0x0611, 0x0612, 0x0613, 0x0614, 0x0615,
    0x0616, 0x0617, 0x0657, 0x0658, 0x0659, 0x065a, 0x065b, 0x065d, 0x065e, 0x06d6, 0x06d7, 0x06d8,
    0x06d9, 0x06da, 0x06db, 0x06dc, 0x06df, 0x06e0, 0x06e1, 0x06e2, 0x06e4, 0x06e7, 0x06e8, 0x06eb,
    0x06ec, 0x0730, 0x0732, 0x0733, 0x0735, 0x0736, 0x073a, 0x073d, 0x073f, 0x0740, 0x0741, 0x0743,
    0x0745, 0x0747, 0x0749, 0x074a, 0x07eb, 0x07ec, 0x07ed, 0x07ee, 0x07ef, 0x07f0, 0x07f1, 0x07f3,
    0x0816, 0x0817, 0x0818, 0x0819, 0x081b, 0x081c, 0x081d, 0x081e, 0x081f, 0x0820, 0x0821, 0x0822,
    0x0823, 0x0825, 0x0826, 0x0827, 0x0829, 0x082a, 0x082b, 0x082c, 0x082d, 0x0951, 0x0953, 0x0954,
    0x0f82, 0x0f83, 0x0f86, 0x0f87, 0x135d, 0x135e, 0x135f, 0x17dd, 0x193a, 0x1a17, 0x1a75, 0x1a76,
    0x1a77, 0x1a78, 0x1a79, 0x1a7a, 0x1a7b, 0x1a7c, 0x1b6b, 0x1b6d, 0x1b6e, 0x1b6f, 0x1b70, 0x1b71,
    0x1b72, 0x1b73, 0x1cd0, 0x1cd1, 0x1cd2, 0x1cda, 0x1cdb, 0x1ce0, 0x1dc0, 0x1dc1, 0x1dc3, 0x1dc4,
    0x1dc5, 0x1dc6, 0x1dc7, 0x1dc8, 0x1dc9, 0x1dcb, 0x1dcc, 0x1dd1, 0x1dd2, 0x1dd3, 0x1dd4, 0x1dd5,
    0x1dd6, 0x1dd7, 0x1dd8, 0x1dd9, 0x1dda, 0x1ddb, 0x1ddc, 0x1ddd, 0x1dde, 0x1ddf, 0x1de0, 0x1de1,
    0x1de2, 0x1de3, 0x1de4, 0x1de5, 0x1de6, 0x1dfe, 0x20d0, 0x20d1, 0x20d4, 0x20d5, 0x20d6, 0x20d7,
    0x20db, 0x20dc, 0x20e1, 0x20e7, 0x20e9, 0x20f0, 0x2cef, 0x2cf0, 0x2cf1, 0x2de0, 0x2de1, 0x2de2,
    0x2de3, 0x2de4, 0x2de5, 0x2de6, 0x2de7, 0x2de8, 0x2de9, 0x2dea, 0x2deb, 0x2dec, 0x2ded, 0x2dee,
    0x2def, 0x2df0, 0x2df1, 0x2df2, 0x2df3, 0x2df4, 0x2df5, 0x2df6, 0x2df7, 0x2df8, 0x2df9, 0x2dfa,
    0x2dfb, 0x2dfc, 0x2dfd, 0x2dfe, 0x2dff, 0xa66f, 0xa67c, 0xa67d, 0xa6f0, 0xa6f1, 0xa8e0, 0xa8e1,
    0xa8e2, 0xa8e3, 0xa8e4, 0xa8e5, 0xa8e6, 0xa8e7, 0xa8e8, 0xa8e9, 0xa8ea, 0xa8eb, 0xa8ec, 0xa8ed,
    0xa8ee, 0xa8ef, 0xa8f0, 0xa8f1, 0xaab0, 0xaab2, 0xaab3, 0xaab7, 0xaab8, 0xaabe, 0xaabf, 0xaac1,
    0xfe20, 0xfe21, 0xfe22, 0xfe23, 0xfe24, 0xfe25, 0xfe26, 0x10a0f, 0x10a38, 0x1d185, 0x1d186,
    0x1d187, 0x1d188, 0x1d189, 0x1d1aa, 0x1d1ab, 0x1d1ac, 0x1d1ad, 0x1d242, 0x1d243, 0x1d244,
};
#define NDIACRITICS ((Py_ssize_t)(sizeof(DIACRITICS) / sizeof(DIACRITICS[0])))
#define PLACEHOLDER 0x10EEEE

//true if a sixel string draws anything, palette and raster settings dont count
static PyObject *hasPixels(PyObject *self, PyObject *arg) {
    Py_ssize_t n;
    const char *s;
    if (PyUnicode_Check(arg)) {
        s = PyUnicode_AsUTF8AndSize(arg, &n);
        if (s == NULL) return NULL;
    } else if (PyBytes_Check(arg)) {
        s = PyBytes_AS_STRING(arg);
        n = PyBytes_GET_SIZE(arg);
    } else {
        PyErr_SetString(PyExc_TypeError, "expected str or bytes");
        return NULL;
    }
    const char *q = memchr(s, 'q', (size_t)n);
    if (q == NULL) Py_RETURN_FALSE;
    const char *p = q + 1, *end = s + n;
    while (p < end) {
        char c = *p;
        if (c == 0x1b) break;
        if (c == '#' || c == '"') {
            p++;
            while (p < end && ((*p >= '0' && *p <= '9') || *p == ';')) p++;
            continue;
        }
        if (c >= '@' && c <= '~') Py_RETURN_TRUE;
        p++;
    }
    Py_RETURN_FALSE;
}

//one row of kitty unicode placeholders: placeholder, row mark, column mark for each cell
static PyObject *placeholderRow(PyObject *self, PyObject *args) {
    Py_ssize_t row, x0, width;
    if (!PyArg_ParseTuple(args, "nnn", &row, &x0, &width)) return NULL;
    if (row < 0 || x0 < 0 || width < 0 || row >= NDIACRITICS || x0 + width > NDIACRITICS) {
        PyErr_SetString(PyExc_ValueError, "row or column out of range");
        return NULL;
    }
    PyObject *out = PyUnicode_New(width * 3, 0x10FFFF);
    if (out == NULL) return NULL;
    Py_UCS4 *buf = PyUnicode_4BYTE_DATA(out);
    Py_UCS4 r = DIACRITICS[row];
    for (Py_ssize_t i = 0; i < width; i++) {
        buf[i * 3] = PLACEHOLDER;
        buf[i * 3 + 1] = r;
        buf[i * 3 + 2] = DIACRITICS[x0 + i];
    }
    return out;
}

static Py_ssize_t findStart(const uint8_t *s, Py_ssize_t from, Py_ssize_t n) {
    for (Py_ssize_t i = from; i + 2 < n;) {
        const uint8_t *z = memchr(s + i, 0, (size_t)(n - 2 - i));
        if (z == NULL) return -1;
        i = z - s;
        if (s[i + 1] == 0 && s[i + 2] == 1) return i;
        i++;
    }
    return -1;
}

//annex b stream to nal units, splitting on 00 00 01 and dropping the extra zero of 4 byte codes
static PyObject *splitAnnexb(PyObject *self, PyObject *arg) {
    Py_buffer view;
    if (PyObject_GetBuffer(arg, &view, PyBUF_SIMPLE) < 0) return NULL;
    const uint8_t *s = view.buf;
    Py_ssize_t n = view.len;
    PyObject *nals = PyList_New(0);
    if (nals == NULL) goto fail;
    Py_ssize_t start = findStart(s, 0, n);
    while (start >= 0) {
        Py_ssize_t begin = start + 3;
        Py_ssize_t next = findStart(s, begin, n);
        Py_ssize_t end = next >= 0 ? next : n;
        if (next >= 0 && end > begin && s[end - 1] == 0) end--;
        if (end > begin) {
            PyObject *nal = PyBytes_FromStringAndSize((const char *)s + begin, end - begin);
            if (nal == NULL || PyList_Append(nals, nal) < 0) {
                Py_XDECREF(nal);
                goto fail;
            }
            Py_DECREF(nal);
        }
        start = next;
    }
    PyBuffer_Release(&view);
    return nals;
fail:
    Py_XDECREF(nals);
    PyBuffer_Release(&view);
    return NULL;
}

//h264 nals to rtp payloads, small ones whole and big ones as fu-a fragments
static PyObject *fuaPayloads(PyObject *self, PyObject *args) {
    PyObject *seq;
    Py_ssize_t mtu;
    if (!PyArg_ParseTuple(args, "On", &seq, &mtu)) return NULL;
    if (mtu < 3) {
        PyErr_SetString(PyExc_ValueError, "mtu too small");
        return NULL;
    }
    PyObject *fast = PySequence_Fast(seq, "expected a list of nal units");
    if (fast == NULL) return NULL;
    PyObject *out = PyList_New(0);
    if (out == NULL) goto fail;
    Py_ssize_t count = PySequence_Fast_GET_SIZE(fast);
    for (Py_ssize_t k = 0; k < count; k++) {
        PyObject *item = PySequence_Fast_GET_ITEM(fast, k);
        if (!PyBytes_Check(item)) {
            PyErr_SetString(PyExc_TypeError, "nal units must be bytes");
            goto fail;
        }
        Py_ssize_t len = PyBytes_GET_SIZE(item);
        const uint8_t *nal = (const uint8_t *)PyBytes_AS_STRING(item);
        if (len == 0) continue;
        if (len <= mtu) {
            if (PyList_Append(out, item) < 0) goto fail;
            continue;
        }
        uint8_t ind = (nal[0] & 0xE0) | 28, typ = nal[0] & 0x1F;
        const uint8_t *body = nal + 1;
        Py_ssize_t blen = len - 1;
        for (Py_ssize_t off = 0; off < blen; off += mtu) {
            Py_ssize_t chunk = blen - off < mtu ? blen - off : mtu;
            PyObject *pkt = PyBytes_FromStringAndSize(NULL, chunk + 2);
            if (pkt == NULL) goto fail;
            uint8_t *p = (uint8_t *)PyBytes_AS_STRING(pkt);
            p[0] = ind;
            p[1] = (off == 0 ? 0x80 : 0) | (off + mtu >= blen ? 0x40 : 0) | typ;
            memcpy(p + 2, body + off, (size_t)chunk);
            if (PyList_Append(out, pkt) < 0) {
                Py_DECREF(pkt);
                goto fail;
            }
            Py_DECREF(pkt);
        }
    }
    Py_DECREF(fast);
    return out;
fail:
    Py_XDECREF(out);
    Py_DECREF(fast);
    return NULL;
}

//rtp fixed header: (header length, payload type, marker, seq, timestamp, ssrc) or None if its not rtp
static PyObject *rtpHeader(PyObject *self, PyObject *arg) {
    Py_buffer view;
    if (PyObject_GetBuffer(arg, &view, PyBUF_SIMPLE) < 0) return NULL;
    const uint8_t *d = view.buf;
    Py_ssize_t n = view.len;
    PyObject *res;
    if (n < 16 || (d[0] >> 6) != 2) {
        res = Py_None;
        Py_INCREF(res);
    } else {
        Py_ssize_t hlen = 12 + 4 * (d[0] & 0x0F) + ((d[0] & 0x10) ? 4 : 0);
        if (hlen > n) {
            res = Py_None;
            Py_INCREF(res);
        } else {
            res = Py_BuildValue("(niNHkk)", hlen, d[1] & 0x7F, PyBool_FromLong((d[1] & 0x80) != 0),
                                (unsigned short)((d[2] << 8) | d[3]),
                                (unsigned long)(((uint32_t)d[4] << 24) | ((uint32_t)d[5] << 16) | ((uint32_t)d[6] << 8) | d[7]),
                                (unsigned long)(((uint32_t)d[8] << 24) | ((uint32_t)d[9] << 16) | ((uint32_t)d[10] << 8) | d[11]));
        }
    }
    PyBuffer_Release(&view);
    return res;
}

static PyMethodDef methods[] = {
    {"hasPixels", hasPixels, METH_O, NULL},
    {"placeholderRow", placeholderRow, METH_VARARGS, NULL},
    {"splitAnnexb", splitAnnexb, METH_O, NULL},
    {"fuaPayloads", fuaPayloads, METH_VARARGS, NULL},
    {"rtpHeader", rtpHeader, METH_O, NULL},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "_native", NULL, -1, methods};

PyMODINIT_FUNC PyInit__native(void) {
    PyObject *m = PyModule_Create(&module);
    if (m != NULL) PyModule_AddIntConstant(m, "maxCells", NDIACRITICS);
    return m;
}
