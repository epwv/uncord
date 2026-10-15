//audio maths for voice: 16 bit pcm gain, mixing, levels and the call sounds
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <utility>
#include <vector>

namespace {

//holds a read only view of anything with the buffer protocol and lets it go on scope exit
class Pcm {
public:
    explicit Pcm(PyObject *obj) {
        ok = PyObject_GetBuffer(obj, &view, PyBUF_SIMPLE) == 0;
    }
    ~Pcm() {
        if (ok)
            PyBuffer_Release(&view);
    }
    Pcm(const Pcm &) = delete;
    Pcm &operator=(const Pcm &) = delete;

    bool ok = false;
    Py_buffer view{};

    Py_ssize_t samples() const { return view.len / 2; }

    int16_t at(Py_ssize_t i) const {
        int16_t s;
        std::memcpy(&s, static_cast<const char *>(view.buf) + i * 2, 2);
        return s;
    }
};

inline int16_t clip(double v) {
    return static_cast<int16_t>(std::floor(std::clamp(v, -32768.0, 32767.0)));
}

inline int16_t clip(int32_t v) {
    return static_cast<int16_t>(std::clamp<int32_t>(v, -32768, 32767));
}

//builds the bytes result in place so there is no extra copy
template <typename Fill>
PyObject *makeBytes(Py_ssize_t samples, Fill fill) {
    PyObject *out = PyBytes_FromStringAndSize(nullptr, samples * 2);
    if (!out)
        return nullptr;
    auto *dst = reinterpret_cast<int16_t *>(PyBytes_AS_STRING(out));
    fill(dst);
    return out;
}

PyObject *gain(PyObject *, PyObject *args) {
    PyObject *obj;
    double g;
    if (!PyArg_ParseTuple(args, "Od", &obj, &g))
        return nullptr;
    Pcm in(obj);
    if (!in.ok)
        return nullptr;
    Py_ssize_t n = in.samples();
    return makeBytes(n, [&](int16_t *dst) {
        for (Py_ssize_t i = 0; i < n; i++)
            dst[i] = clip(in.at(i) * g);
    });
}

//sums any number of equal length frames then applies the volume, clipping once at the end
PyObject *mix(PyObject *, PyObject *args) {
    PyObject *frames;
    double volume = 1.0;
    if (!PyArg_ParseTuple(args, "O|d", &frames, &volume))
        return nullptr;
    PyObject *seq = PySequence_Fast(frames, "mix wants a list of frames");
    if (!seq)
        return nullptr;
    Py_ssize_t count = PySequence_Fast_GET_SIZE(seq);
    std::vector<int32_t> acc;
    for (Py_ssize_t f = 0; f < count; f++) {
        Pcm in(PySequence_Fast_GET_ITEM(seq, f));
        if (!in.ok) {
            Py_DECREF(seq);
            return nullptr;
        }
        if (acc.size() < static_cast<size_t>(in.samples()))
            acc.resize(in.samples(), 0);
        for (Py_ssize_t i = 0; i < in.samples(); i++)
            acc[i] += in.at(i);
    }
    Py_DECREF(seq);
    return makeBytes(static_cast<Py_ssize_t>(acc.size()), [&](int16_t *dst) {
        for (size_t i = 0; i < acc.size(); i++)
            dst[i] = volume == 1.0 ? clip(acc[i]) : clip(acc[i] * volume);
    });
}

PyObject *rms(PyObject *, PyObject *obj) {
    Pcm in(obj);
    if (!in.ok)
        return nullptr;
    Py_ssize_t n = in.samples();
    if (n == 0)
        return PyLong_FromLong(0);
    double sum = 0;
    for (Py_ssize_t i = 0; i < n; i++) {
        double s = in.at(i);
        sum += s * s;
    }
    return PyLong_FromLong(static_cast<long>(std::sqrt(sum / n)));
}

PyObject *peak(PyObject *, PyObject *obj) {
    Pcm in(obj);
    if (!in.ok)
        return nullptr;
    int32_t best = 0;
    for (Py_ssize_t i = 0; i < in.samples(); i++)
        best = std::max(best, std::abs(static_cast<int32_t>(in.at(i))));
    return PyLong_FromLong(best);
}

//one level per chunk for voice message waveforms
PyObject *levels(PyObject *, PyObject *args) {
    PyObject *obj;
    Py_ssize_t chunk;
    if (!PyArg_ParseTuple(args, "On", &obj, &chunk))
        return nullptr;
    if (chunk < 2) {
        PyErr_SetString(PyExc_ValueError, "chunk must be at least 2 bytes");
        return nullptr;
    }
    Pcm in(obj);
    if (!in.ok)
        return nullptr;
    Py_ssize_t per = chunk / 2, n = in.samples();
    PyObject *out = PyList_New(0);
    if (!out)
        return nullptr;
    for (Py_ssize_t start = 0; start < n; start += per) {
        Py_ssize_t end = std::min(n, start + per);
        double sum = 0;
        for (Py_ssize_t i = start; i < end; i++) {
            double s = in.at(i);
            sum += s * s;
        }
        PyObject *v = PyLong_FromLong(static_cast<long>(std::sqrt(sum / (end - start))));
        if (!v || PyList_Append(out, v) < 0) {
            Py_XDECREF(v);
            Py_DECREF(out);
            return nullptr;
        }
        Py_DECREF(v);
    }
    return out;
}

//stereo sine notes with a short attack and release, [(freq, secs), ...]
PyObject *tones(PyObject *, PyObject *args) {
    PyObject *notes;
    int rate;
    double volume;
    if (!PyArg_ParseTuple(args, "Oid", &notes, &rate, &volume))
        return nullptr;
    PyObject *seq = PySequence_Fast(notes, "tones wants a list of (freq, secs)");
    if (!seq)
        return nullptr;
    std::vector<std::pair<double, double>> parts;
    for (Py_ssize_t k = 0; k < PySequence_Fast_GET_SIZE(seq); k++) {
        double freq, secs;
        if (!PyArg_ParseTuple(PySequence_Fast_GET_ITEM(seq, k), "dd", &freq, &secs)) {
            Py_DECREF(seq);
            return nullptr;
        }
        parts.emplace_back(freq, secs);
    }
    Py_DECREF(seq);
    std::vector<int16_t> pcm;
    const double tau = 2 * M_PI;
    const long attack = static_cast<long>(rate * 0.005), release = static_cast<long>(rate * 0.03);
    for (auto [freq, secs] : parts) {
        long n = static_cast<long>(rate * secs);
        for (long i = 0; i < n; i++) {
            double env = std::min({1.0, attack ? double(i) / attack : 1.0, release ? double(n - i) / release : 1.0});
            double t = double(i) / rate;
            double v = (std::sin(tau * freq * t) + 0.25 * std::sin(2 * tau * freq * t)) / 1.25;
            int16_t s = clip(32767 * volume * env * v);
            pcm.push_back(s);
            pcm.push_back(s);
        }
    }
    pcm.resize(pcm.size() + static_cast<size_t>(rate / 50) * 2, 0);
    return makeBytes(static_cast<Py_ssize_t>(pcm.size()), [&](int16_t *dst) {
        std::copy(pcm.begin(), pcm.end(), dst);
    });
}

PyMethodDef methods[] = {
    {"gain", gain, METH_VARARGS, nullptr},
    {"mix", mix, METH_VARARGS, nullptr},
    {"rms", rms, METH_O, nullptr},
    {"peak", peak, METH_O, nullptr},
    {"levels", levels, METH_VARARGS, nullptr},
    {"tones", tones, METH_VARARGS, nullptr},
    {nullptr, nullptr, 0, nullptr},
};

PyModuleDef module = {PyModuleDef_HEAD_INIT, "_dsp", nullptr, -1, methods};

}  //namespace

PyMODINIT_FUNC PyInit__dsp() {
    return PyModule_Create(&module);
}
