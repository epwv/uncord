//rtp video: depacketizers for h264 h265 vp8 vp9 av1, the receive jitter buffer, rtcp and outgoing packets
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <structmember.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <new>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

using Bytes = std::string;
using View = std::string_view;

enum Codec { H264, H265, VP8, VP9, AV1, NCODECS };

const char START[] = "\x00\x00\x00\x01";

//reading past the end of a packet, the frame gets dropped
struct Truncated {};

inline uint8_t at(View p, size_t i) {
    if (i >= p.size())
        throw Truncated{};
    return static_cast<uint8_t>(p[i]);
}

//slices clamp like python ones do
inline View tail(View p, size_t i) {
    return i >= p.size() ? View() : p.substr(i);
}

inline View slice(View p, size_t i, size_t n) {
    return i >= p.size() ? View() : p.substr(i, n);
}

inline size_t advance(size_t i, uint64_t n) {
    return n > SIZE_MAX - i ? SIZE_MAX : i + static_cast<size_t>(n);
}

inline uint16_t be16(View p, size_t i) {
    return static_cast<uint16_t>((at(p, i) << 8) | at(p, i + 1));
}

inline void put16(Bytes &out, uint32_t v) {
    out.push_back(static_cast<char>((v >> 8) & 0xFF));
    out.push_back(static_cast<char>(v & 0xFF));
}

inline void put32(Bytes &out, uint32_t v) {
    put16(out, v >> 16);
    put16(out, v & 0xFFFF);
}

inline void putLe(Bytes &out, uint64_t v, int n) {
    for (int k = 0; k < n; k++)
        out.push_back(static_cast<char>((v >> (8 * k)) & 0xFF));
}

//av1 variable length numbers 7 bits per byte high bit means more
void leb128(Bytes &out, uint64_t n) {
    while (true) {
        uint8_t b = n & 0x7F;
        n >>= 7;
        if (n) {
            out.push_back(static_cast<char>(b | 0x80));
        } else {
            out.push_back(static_cast<char>(b));
            return;
        }
    }
}

//anything too big to fit just means take the rest
uint64_t readLeb128(View buf, size_t &i) {
    uint64_t val = 0;
    unsigned shift = 0;
    bool huge = false;
    while (i < buf.size()) {
        uint8_t b = static_cast<uint8_t>(buf[i++]);
        uint64_t part = b & 0x7F;
        if (part && (shift >= 64 || (shift > 57 && (part >> (64 - shift)))))
            huge = true;
        else if (shift < 64)
            val |= part << shift;
        shift += 7;
        if (!(b & 0x80))
            return huge ? UINT64_MAX : val;
    }
    throw Truncated{};
}

struct Frame {
    std::optional<Bytes> data;
    bool key = false;
};

Bytes annexb(const std::vector<Bytes> &nals) {
    Bytes out;
    for (const auto &n : nals) {
        out.append(START, 4);
        out += n;
    }
    return out;
}

//h264


//rfc 6184 single nal stap-a (several nals) and fu-a (one nal split up)
Frame assembleH264(const std::vector<View> &payloads) {
    std::vector<Bytes> nals;
    std::optional<Bytes> fu;
    for (View p : payloads) {
        if (p.empty())
            continue;
        uint8_t t = p[0] & 0x1F;
        if (t >= 1 && t <= 23) {
            nals.emplace_back(p);
        } else if (t == 24) {  //stap-a
            size_t i = 1;
            while (i + 2 <= p.size()) {
                size_t n = be16(p, i);
                i += 2;
                if (n && i + n <= p.size())
                    nals.emplace_back(p.substr(i, n));
                i += n;
            }
        } else if (t == 28 && p.size() > 2) {  //fu-a
            uint8_t fh = p[1];
            if (fh & 0x80)
                fu = Bytes(1, static_cast<char>((p[0] & 0xE0) | (fh & 0x1F)));
            if (fu) {
                *fu += p.substr(2);
                if (fh & 0x40) {
                    nals.push_back(std::move(*fu));
                    fu.reset();
                }
            }
        }
    }
    Frame f;
    if (nals.empty())
        return f;
    for (const auto &n : nals) {
        uint8_t t = n[0] & 0x1F;
        f.key = f.key || t == 5 || t == 7;
    }
    f.data = annexb(nals);
    return f;
}

bool startH264(View p) {
    if (p.empty())
        return false;
    uint8_t t = p[0] & 0x1F;
    return t == 7 || (t == 24 && p.size() > 3 && (p[3] & 0x1F) == 7);
}

//h265


//rfc 7798 same idea as h264 with a 2 byte nal header, 48 is an aggregation packet 49 a fragment
Frame assembleH265(const std::vector<View> &payloads) {
    std::vector<Bytes> nals;
    std::optional<Bytes> fu;
    for (View p : payloads) {
        if (p.size() < 3)
            continue;
        uint8_t t = (p[0] >> 1) & 0x3F;
        if (t == 48) {
            size_t i = 2;
            while (i + 2 <= p.size()) {
                size_t n = be16(p, i);
                i += 2;
                if (n && i + n <= p.size())
                    nals.emplace_back(p.substr(i, n));
                i += n;
            }
        } else if (t == 49) {
            uint8_t fh = p[2];
            if (fh & 0x80) {
                fu = Bytes();
                fu->push_back(static_cast<char>((p[0] & 0x81) | ((fh & 0x3F) << 1)));
                fu->push_back(p[1]);
            }
            if (fu) {
                *fu += p.substr(3);
                if (fh & 0x40) {
                    nals.push_back(std::move(*fu));
                    fu.reset();
                }
            }
        } else {
            nals.emplace_back(p);
        }
    }
    Frame f;
    if (nals.empty())
        return f;
    for (const auto &n : nals) {
        uint8_t t = (n[0] >> 1) & 0x3F;
        f.key = f.key || (t >= 16 && t <= 21) || t == 32;
    }
    f.data = annexb(nals);
    return f;
}

bool startH265(View p) {
    if (p.size() < 3)
        return false;
    uint8_t t = (p[0] >> 1) & 0x3F;
    return t == 32 || (t == 48 && p.size() > 4 && ((p[4] >> 1) & 0x3F) == 32);
}

//vp8


struct Split {
    bool a, b;
    View data;
};

//rfc 7741 strip the vp8 payload descriptor, a is start of partition b is partition 0
Split splitVp8(View p) {
    uint8_t b = at(p, 0);
    size_t i = 1;
    if (b & 0x80) {
        uint8_t x = at(p, i++);
        if (x & 0x80)
            i += (at(p, i) & 0x80) ? 2 : 1;
        if (x & 0x40)
            i++;
        if (x & 0x30)
            i++;
    }
    return {(b & 0x10) != 0, (b & 0x07) == 0, tail(p, i)};
}

Frame assembleVp8(const std::vector<View> &payloads) {
    Bytes out;
    Frame f;
    for (size_t k = 0; k < payloads.size(); k++) {
        View p = payloads[k];
        if (p.empty())
            continue;
        Split s = splitVp8(p);
        if (k == 0 && s.a && s.b && !s.data.empty())
            f.key = !(s.data[0] & 0x01);
        out += s.data;
    }
    if (!out.empty())
        f.data = std::move(out);
    return f;
}

bool startVp8(View p) {
    if (p.empty())
        return false;
    Split s = splitVp8(p);
    return s.a && s.b && !s.data.empty() && !(s.data[0] & 0x01);
}

//vp9


//rfc 9628 strip the vp9 descriptor (picture id layers scalability info), a is frame begin b is inter
Split splitVp9(View p) {
    uint8_t b = at(p, 0);
    size_t i = 1;
    if (b & 0x80)
        i += (at(p, i) & 0x80) ? 2 : 1;
    if (b & 0x20) {
        i++;
        if (!(b & 0x10))
            i++;
    }
    if ((b & 0x10) && (b & 0x40)) {
        while (at(p, i++) & 0x01) {
        }
    }
    if (b & 0x02) {
        uint8_t ss = at(p, i++);
        int layers = (ss >> 5) + 1;
        if (ss & 0x10)
            i += 4 * static_cast<size_t>(layers);
        if (ss & 0x08) {
            int groups = at(p, i++);
            for (int g = 0; g < groups; g++) {
                int refs = (at(p, i) >> 2) & 0x03;
                i += 1 + refs;
            }
        }
    }
    return {(b & 0x08) != 0, (b & 0x40) != 0, tail(p, i)};
}

Frame assembleVp9(const std::vector<View> &payloads) {
    Bytes out;
    Frame f;
    for (size_t k = 0; k < payloads.size(); k++) {
        View p = payloads[k];
        if (p.empty())
            continue;
        Split s = splitVp9(p);
        if (k == 0)
            f.key = s.a && !s.b;
        out += s.data;
    }
    if (!out.empty())
        f.data = std::move(out);
    return f;
}

bool startVp9(View p) {
    if (p.empty())
        return false;
    Split s = splitVp9(p);
    return s.a && !s.b;
}

//av1

constexpr int OBU_SEQUENCE_HEADER = 1, OBU_TEMPORAL_DELIMITER = 2, OBU_TILE_LIST = 8, OBU_PADDING = 15;

struct Obu {
    Bytes header;
    Bytes payload;
};

//av1 frames are a list of obus each with a header and maybe a size
std::vector<Obu> parseObus(View buf) {
    std::vector<Obu> out;
    size_t i = 0;
    while (i < buf.size()) {
        uint8_t h = buf[i];
        size_t hl = (h & 0x04) ? 2 : 1;
        Obu o;
        o.header = Bytes(slice(buf, i, hl));
        i += hl;
        if (h & 0x02) {
            uint64_t size = readLeb128(buf, i);
            o.payload = Bytes(slice(buf, i, size));
            i = advance(i, size);
        } else {
            o.payload = Bytes(tail(buf, i));
            i = buf.size();
        }
        out.push_back(std::move(o));
    }
    return out;
}

Bytes writeObus(const std::vector<Obu> &obus, bool lastSized = true) {
    Bytes out;
    for (size_t k = 0; k < obus.size(); k++) {
        const Obu &o = obus[k];
        bool sized = lastSized || k + 1 < obus.size();
        uint8_t h = static_cast<uint8_t>(o.header[0]);
        out.push_back(static_cast<char>(sized ? (h | 0x02) : (h & ~0x02 & 0xFF)));
        out.append(o.header, 1, Bytes::npos);
        if (sized)
            leb128(out, o.payload.size());
        out += o.payload;
    }
    return out;
}

//av1 rtp spec an aggregation byte then obu elements that can be split across packets
//z means the first element continues the last packet y means the last one carries on
Frame assembleAv1(const std::vector<View> &payloads) {
    std::vector<Bytes> obus;
    std::optional<Bytes> frag;
    Frame f;
    for (size_t k = 0; k < payloads.size(); k++) {
        View p = payloads[k];
        if (p.empty())
            continue;
        uint8_t agg = p[0];
        bool z = agg & 0x80, y = agg & 0x40, n = agg & 0x08;
        size_t w = (agg >> 4) & 0x03;
        if (k == 0 && n)
            f.key = true;
        size_t i = 1;
        std::vector<View> elems;
        while (i < p.size()) {
            if (w && elems.size() == w - 1) {
                elems.push_back(tail(p, i));
                break;
            }
            uint64_t size = readLeb128(p, i);
            elems.push_back(slice(p, i, size));
            i = advance(i, size);
        }
        for (size_t e = 0; e < elems.size(); e++) {
            bool first = e == 0, last = e + 1 == elems.size();
            if (first && z) {
                if (!frag)
                    continue;
                *frag += elems[e];
                if (!(last && y)) {
                    obus.push_back(std::move(*frag));
                    frag.reset();
                }
                continue;
            }
            if (last && y)
                frag = Bytes(elems[e]);
            else
                obus.emplace_back(elems[e]);
        }
    }
    std::vector<Obu> parsed;
    for (const Bytes &ob : obus) {
        if (ob.empty())
            continue;
        View o(ob);
        uint8_t h = o[0];
        size_t hl = (h & 0x04) ? 2 : 1;
        View body = tail(o, hl);
        if (h & 0x02) {
            size_t j = hl;
            uint64_t size = readLeb128(o, j);
            body = slice(o, j, size);
        }
        int t = (h >> 3) & 0x0F;
        if (t == OBU_TEMPORAL_DELIMITER || t == OBU_TILE_LIST || t == OBU_PADDING)
            continue;
        if (t == OBU_SEQUENCE_HEADER)
            f.key = true;
        parsed.push_back({Bytes(slice(o, 0, hl)), Bytes(body)});
    }
    if (parsed.empty())
        return Frame{};
    f.data = writeObus(parsed);
    return f;
}

bool startAv1(View p) {
    return !p.empty() && !(p[0] & 0x80) && (p[0] & 0x08);
}

//throws Truncated on a broken packet
Frame assemble(int codec, const std::vector<View> &payloads) {
    switch (codec) {
    case H264: return assembleH264(payloads);
    case H265: return assembleH265(payloads);
    case VP8: return assembleVp8(payloads);
    case VP9: return assembleVp9(payloads);
    default: return assembleAv1(payloads);
    }
}

bool isFrameStart(int codec, View p) {
    try {
        switch (codec) {
        case H264: return startH264(p);
        case H265: return startH265(p);
        case VP8: return startVp8(p);
        case VP9: return startVp9(p);
        default: return startAv1(p);
        }
    } catch (const Truncated &) {
        return false;
    }
}

//python glue

//holds a read only view of anything with the buffer protocol and lets it go on scope exit
class Buf {
public:
    explicit Buf(PyObject *obj) { ok = PyObject_GetBuffer(obj, &view, PyBUF_SIMPLE) == 0; }
    ~Buf() {
        if (ok)
            PyBuffer_Release(&view);
    }
    Buf(const Buf &) = delete;
    Buf &operator=(const Buf &) = delete;

    View str() const { return View(static_cast<const char *>(view.buf), static_cast<size_t>(view.len)); }

    bool ok = false;
    Py_buffer view{};
};

PyObject *toBytes(const Bytes &b) {
    return PyBytes_FromStringAndSize(b.data(), static_cast<Py_ssize_t>(b.size()));
}

PyObject *pair(const Bytes &a, const Bytes &b) {
    return Py_BuildValue("(y#y#)", a.data(), static_cast<Py_ssize_t>(a.size()), b.data(),
                         static_cast<Py_ssize_t>(b.size()));
}

PyObject *frameResult(const Frame &f) {
    if (!f.data)
        return Py_BuildValue("(OO)", Py_None, f.key ? Py_True : Py_False);
    return Py_BuildValue("(y#O)", f.data->data(), static_cast<Py_ssize_t>(f.data->size()),
                         f.key ? Py_True : Py_False);
}

bool checkCodec(int codec) {
    if (codec < 0 || codec >= NCODECS) {
        PyErr_SetString(PyExc_ValueError, "unknown codec");
        return false;
    }
    return true;
}

//assemble(codec, payloads) -> (frame or None, keyframe)
PyObject *pyAssemble(PyObject *, PyObject *args) {
    int codec;
    PyObject *seq;
    if (!PyArg_ParseTuple(args, "iO", &codec, &seq) || !checkCodec(codec))
        return nullptr;
    PyObject *fast = PySequence_Fast(seq, "expected a list of payloads");
    if (!fast)
        return nullptr;
    Py_ssize_t count = PySequence_Fast_GET_SIZE(fast);
    std::vector<View> payloads;
    for (Py_ssize_t k = 0; k < count; k++) {
        PyObject *item = PySequence_Fast_GET_ITEM(fast, k);
        if (!PyBytes_Check(item)) {
            Py_DECREF(fast);
            PyErr_SetString(PyExc_TypeError, "payloads must be bytes");
            return nullptr;
        }
        payloads.emplace_back(PyBytes_AS_STRING(item), PyBytes_GET_SIZE(item));
    }
    PyObject *res;
    try {
        res = frameResult(assemble(codec, payloads));
    } catch (const Truncated &) {
        PyErr_SetString(PyExc_ValueError, "truncated packet");
        res = nullptr;
    }
    Py_DECREF(fast);
    return res;
}

PyObject *pyIsFrameStart(PyObject *, PyObject *args) {
    int codec;
    PyObject *obj;
    if (!PyArg_ParseTuple(args, "iO", &codec, &obj) || !checkCodec(codec))
        return nullptr;
    Buf b(obj);
    if (!b.ok)
        return nullptr;
    return PyBool_FromLong(isFrameStart(codec, b.str()));
}

//dave may strip the last obu size so try both layouts
PyObject *av1Candidates(PyObject *, PyObject *arg) {
    Buf b(arg);
    if (!b.ok)
        return nullptr;
    Bytes frame(b.str());
    try {
        Bytes unsized = writeObus(parseObus(frame), false);
        return Py_BuildValue("[y#y#]", frame.data(), static_cast<Py_ssize_t>(frame.size()), unsized.data(),
                             static_cast<Py_ssize_t>(unsized.size()));
    } catch (const Truncated &) {
        return Py_BuildValue("[y#]", frame.data(), static_cast<Py_ssize_t>(frame.size()));
    }
}

//put the temporal delimiter back so decoders see a proper temporal unit
PyObject *av1Finish(PyObject *, PyObject *arg) {
    Buf b(arg);
    if (!b.ok)
        return nullptr;
    try {
        Bytes out("\x12\x00", 2);
        out += writeObus(parseObus(b.str()));
        return toBytes(out);
    } catch (const Truncated &) {
        return toBytes(Bytes(b.str()));
    }
}

//ffmpeg and mpv cant read raw vp8 vp9 av1 from a pipe so they get wrapped in ivf
PyObject *ivfHeader(PyObject *, PyObject *args, PyObject *kw) {
    static const char *names[] = {"fourcc", "width", "height", nullptr};
    const char *fourcc;
    Py_ssize_t len;
    unsigned int width = 1920, height = 1080;
    if (!PyArg_ParseTupleAndKeywords(args, kw, "y#|II", const_cast<char **>(names), &fourcc, &len, &width, &height))
        return nullptr;
    Bytes out("DKIF", 4);
    putLe(out, 0, 2);
    putLe(out, 32, 2);
    Bytes cc(fourcc, static_cast<size_t>(std::min<Py_ssize_t>(len, 4)));
    cc.resize(4, '\0');
    out += cc;
    putLe(out, width, 2);
    putLe(out, height, 2);
    putLe(out, 1000, 4);
    putLe(out, 1, 4);
    putLe(out, 0, 4);
    putLe(out, 0, 4);
    return toBytes(out);
}

PyObject *ivfFrame(PyObject *, PyObject *args) {
    PyObject *obj;
    unsigned long long pts;
    if (!PyArg_ParseTuple(args, "OK", &obj, &pts))
        return nullptr;
    Buf b(obj);
    if (!b.ok)
        return nullptr;
    Bytes out;
    out.reserve(12 + b.str().size());
    putLe(out, b.str().size(), 4);
    putLe(out, pts, 8);
    out += b.str();
    return toBytes(out);
}

//rtcp

inline bool seqAfter(uint16_t a, uint16_t b) {
    uint16_t d = static_cast<uint16_t>(a - b);
    return d > 0 && d < 0x8000;
}

Bytes rtcpHeader(uint8_t first, uint8_t type, uint16_t words, uint32_t ssrc) {
    Bytes h;
    h.push_back(static_cast<char>(first));
    h.push_back(static_cast<char>(type));
    put16(h, words);
    put32(h, ssrc);
    return h;
}

//sender report so the server can sync and measure us
PyObject *senderReport(PyObject *, PyObject *args) {
    unsigned long long ssrc, ntpHi, ntpLo, ts, packets, nbytes;
    if (!PyArg_ParseTuple(args, "KKKKKK", &ssrc, &ntpHi, &ntpLo, &ts, &packets, &nbytes))
        return nullptr;
    Bytes body;
    for (auto v : {ntpHi, ntpLo, ts, packets, nbytes})
        put32(body, static_cast<uint32_t>(v));
    return pair(rtcpHeader(0x80, 200, 6, static_cast<uint32_t>(ssrc)), body);
}

//receiver report plus remb bandwidth estimate, without these some servers send nothing
//highest is a list of (ssrc, cycles, seq) and only the first 8 are reported
PyObject *feedback(PyObject *, PyObject *args) {
    unsigned long long own, bps;
    PyObject *highest, *media;
    if (!PyArg_ParseTuple(args, "KOOK", &own, &highest, &media, &bps))
        return nullptr;
    PyObject *hs = PySequence_Fast(highest, "expected a list of (ssrc, cycles, seq)");
    if (!hs)
        return nullptr;
    Bytes blocks;
    for (Py_ssize_t k = 0; k < PySequence_Fast_GET_SIZE(hs) && k < 8; k++) {
        unsigned long long ssrc, cycles, seq;
        if (!PyArg_ParseTuple(PySequence_Fast_GET_ITEM(hs, k), "KKK", &ssrc, &cycles, &seq)) {
            Py_DECREF(hs);
            return nullptr;
        }
        put32(blocks, static_cast<uint32_t>(ssrc));
        put32(blocks, 0);
        put32(blocks, static_cast<uint32_t>((cycles << 16) | seq));
        for (int z = 0; z < 3; z++)
            put32(blocks, 0);
    }
    Py_DECREF(hs);
    PyObject *ms = PySequence_Fast(media, "expected a list of ssrcs");
    if (!ms)
        return nullptr;
    std::vector<uint32_t> ssrcs;
    for (Py_ssize_t k = 0; k < PySequence_Fast_GET_SIZE(ms); k++) {
        unsigned long long s = PyLong_AsUnsignedLongLongMask(PySequence_Fast_GET_ITEM(ms, k));
        if (PyErr_Occurred()) {
            Py_DECREF(ms);
            return nullptr;
        }
        ssrcs.push_back(static_cast<uint32_t>(s));
    }
    Py_DECREF(ms);
    uint32_t exp = 0;
    while (bps >= (1u << 18)) {
        bps >>= 1;
        exp++;
    }
    Bytes remb("REMB", 4);
    remb.push_back(static_cast<char>(ssrcs.size() & 0xFF));
    uint32_t mant = (exp << 18) | static_cast<uint32_t>(bps);
    remb.push_back(static_cast<char>((mant >> 16) & 0xFF));
    put16(remb, mant & 0xFFFF);
    for (uint32_t s : ssrcs)
        put32(remb, s);
    Bytes body = blocks + rtcpHeader(0x8F, 206, static_cast<uint16_t>((12 + remb.size()) / 4 - 1),
                                     static_cast<uint32_t>(own));
    put32(body, 0);
    body += remb;
    size_t rc = blocks.size() / 24;
    return pair(rtcpHeader(static_cast<uint8_t>(0x80 | rc), 201, static_cast<uint16_t>((8 + blocks.size()) / 4 - 1),
                           static_cast<uint32_t>(own)),
                body);
}

//picture loss indication asks for a keyframe
PyObject *pli(PyObject *, PyObject *args) {
    unsigned long long own, media;
    if (!PyArg_ParseTuple(args, "KK", &own, &media))
        return nullptr;
    Bytes body;
    put32(body, static_cast<uint32_t>(media));
    return pair(rtcpHeader(0x81, 206, 2, static_cast<uint32_t>(own)), body);
}

//generic nack each entry is a seq plus a 16 bit mask of the ones after it, at most 16 entries
Bytes nackBody(uint32_t media, std::vector<uint16_t> seqs) {
    uint16_t base = seqs[0];
    std::stable_sort(seqs.begin(), seqs.end(), [base](uint16_t a, uint16_t b) {
        return static_cast<uint16_t>(a - base) < static_cast<uint16_t>(b - base);
    });
    Bytes body;
    put32(body, media);
    size_t i = 0, entries = 0;
    while (i < seqs.size() && entries < 16) {
        uint16_t pid = seqs[i++], blp = 0;
        while (i < seqs.size()) {
            uint16_t d = static_cast<uint16_t>(seqs[i] - pid);
            if (d == 0 || d > 16)
                break;
            blp |= static_cast<uint16_t>(1u << (d - 1));
            i++;
        }
        put16(body, pid);
        put16(body, blp);
        entries++;
    }
    return body;
}

PyObject *nack(PyObject *, PyObject *args) {
    unsigned long long own, media;
    PyObject *obj;
    if (!PyArg_ParseTuple(args, "KKO", &own, &media, &obj))
        return nullptr;
    PyObject *fast = PySequence_Fast(obj, "expected a list of sequence numbers");
    if (!fast)
        return nullptr;
    std::vector<uint16_t> seqs;
    for (Py_ssize_t k = 0; k < PySequence_Fast_GET_SIZE(fast); k++) {
        unsigned long long s = PyLong_AsUnsignedLongLongMask(PySequence_Fast_GET_ITEM(fast, k));
        if (PyErr_Occurred()) {
            Py_DECREF(fast);
            return nullptr;
        }
        seqs.push_back(static_cast<uint16_t>(s));
    }
    Py_DECREF(fast);
    if (seqs.empty())
        Py_RETURN_NONE;
    Bytes body = nackBody(static_cast<uint32_t>(media), seqs);
    return pair(rtcpHeader(0x81, 205, static_cast<uint16_t>(2 + (body.size() - 4) / 4), static_cast<uint32_t>(own)),
                body);
}

//outgoing video, one (header, body) per payload with the marker on the last
//the one byte header extensions are the ones the desktop client sends: abs send time, playout delay, 3 byte id 11
PyObject *videoPackets(PyObject *, PyObject *args) {
    PyObject *obj;
    unsigned int seq, pt;
    unsigned long long ts, ssrc, absSend;
    if (!PyArg_ParseTuple(args, "OIKKIK", &obj, &seq, &ts, &ssrc, &pt, &absSend))
        return nullptr;
    PyObject *fast = PySequence_Fast(obj, "expected a list of payloads");
    if (!fast)
        return nullptr;
    Bytes ext;
    ext.push_back(static_cast<char>((3 << 4) | 2));
    ext.push_back(static_cast<char>((absSend >> 16) & 0xFF));
    put16(ext, absSend & 0xFFFF);
    ext.push_back(static_cast<char>((6 << 4) | 2));
    ext.append(3, '\0');
    ext.push_back(static_cast<char>((11 << 4) | 2));
    ext += "100";
    Py_ssize_t count = PySequence_Fast_GET_SIZE(fast);
    PyObject *out = PyList_New(count);
    if (!out) {
        Py_DECREF(fast);
        return nullptr;
    }
    for (Py_ssize_t k = 0; k < count; k++) {
        PyObject *item = PySequence_Fast_GET_ITEM(fast, k);
        if (!PyBytes_Check(item)) {
            PyErr_SetString(PyExc_TypeError, "payloads must be bytes");
            Py_DECREF(out);
            Py_DECREF(fast);
            return nullptr;
        }
        Bytes header;
        header.push_back('\x90');
        header.push_back(static_cast<char>((k == count - 1 ? 0x80 : 0) | (pt & 0x7F)));
        put16(header, static_cast<uint16_t>(seq + k));
        put32(header, static_cast<uint32_t>(ts));
        put32(header, static_cast<uint32_t>(ssrc));
        header += "\xbe\xde";
        put16(header, static_cast<uint16_t>(ext.size() / 4));
        Bytes body = ext;
        body.append(PyBytes_AS_STRING(item), static_cast<size_t>(PyBytes_GET_SIZE(item)));
        PyObject *p = pair(header, body);
        if (!p) {
            Py_DECREF(out);
            Py_DECREF(fast);
            return nullptr;
        }
        PyList_SET_ITEM(out, k, p);
    }
    Py_DECREF(fast);
    return out;
}

//after transport decryption: drop the encrypted extension body and any rtp padding
PyObject *rtpPayload(PyObject *, PyObject *args) {
    PyObject *pktObj, *plainObj;
    if (!PyArg_ParseTuple(args, "OO", &pktObj, &plainObj))
        return nullptr;
    Buf pkt(pktObj), plainBuf(plainObj);
    if (!pkt.ok || !plainBuf.ok)
        return nullptr;
    View d = pkt.str(), plain = plainBuf.str();
    if (d.size() < 12)
        return toBytes(Bytes(plain));
    uint8_t first = d[0];
    if (first & 0x10) {
        size_t hlen = 12 + 4 * (first & 0x0F) + 4;
        if (hlen > d.size())
            return PyBytes_FromStringAndSize(nullptr, 0);
        plain = tail(plain, static_cast<size_t>(be16(d, hlen - 2)) * 4);
    }
    if ((first & 0x20) && !plain.empty()) {
        uint8_t pad = plain.back();
        plain = (pad == 0 || pad > plain.size()) ? View() : plain.substr(0, plain.size() - pad);
    }
    return toBytes(Bytes(plain));
}

//annex b capture to access units

Py_ssize_t findStart(View s, size_t from) {
    for (size_t i = from; i + 2 < s.size();) {
        const void *z = std::memchr(s.data() + i, 0, s.size() - 2 - i);
        if (!z)
            return -1;
        i = static_cast<const char *>(z) - s.data();
        if (s[i + 1] == 0 && s[i + 2] == 1)
            return static_cast<Py_ssize_t>(i);
        i++;
    }
    return -1;
}

std::vector<Bytes> splitAnnexb(View s) {
    std::vector<Bytes> nals;
    Py_ssize_t start = findStart(s, 0);
    while (start >= 0) {
        size_t begin = static_cast<size_t>(start) + 3;
        Py_ssize_t next = findStart(s, begin);
        size_t end = next >= 0 ? static_cast<size_t>(next) : s.size();
        if (next >= 0 && end > begin && s[end - 1] == 0)
            end--;
        if (end > begin)
            nals.emplace_back(s.substr(begin, end - begin));
        start = next;
    }
    return nals;
}

struct Pkt {
    uint32_t ts;
    bool marker;
    PyObject *payload;
    uint64_t order;
};

struct AsmState {
    std::unordered_map<uint16_t, Pkt> pkts;
    std::unordered_map<uint16_t, std::pair<double, int>> nacked;
    int nextSeq = -1;
    double gapSince = 0.0;
    uint64_t counter = 0;
};

struct Assembler {
    PyObject_HEAD
    int codec;
    PyObject *codecObj, *onFrame, *requestKeyframe, *nackCb;
    int needKey;
    Py_ssize_t lostFrames;
    AsmState *st;
};

struct AccessUnits {
    PyObject_HEAD
    Bytes *buf;
    std::vector<Bytes> *au;
    int hasSlice;
};

}  //namespace

//cut gsr's h264 into access units (one per frame), a new picture starts at sps pps after a slice or at first mb 0
static PyObject *auFeed(AccessUnits *self, PyObject *arg) {
    Buf chunk(arg);
    if (!chunk.ok)
        return nullptr;
    Bytes &buf = *self->buf;
    buf += chunk.str();
    PyObject *out = PyList_New(0);
    if (!out)
        return nullptr;
    size_t last = buf.rfind(Bytes("\x00\x00\x01", 3));
    if (last == Bytes::npos || last == 0)
        return out;
    size_t cut = buf[last - 1] == 0 ? last - 1 : last;
    std::vector<Bytes> nals = splitAnnexb(View(buf).substr(0, cut));
    buf.erase(0, cut);
    auto &au = *self->au;
    auto flush = [&]() -> bool {
        PyObject *list = PyList_New(static_cast<Py_ssize_t>(au.size()));
        if (!list)
            return false;
        for (size_t k = 0; k < au.size(); k++) {
            PyObject *b = toBytes(au[k]);
            if (!b) {
                Py_DECREF(list);
                return false;
            }
            PyList_SET_ITEM(list, static_cast<Py_ssize_t>(k), b);
        }
        int rc = PyList_Append(out, list);
        Py_DECREF(list);
        au.clear();
        return rc == 0;
    };
    for (Bytes &nal : nals) {
        uint8_t t = nal[0] & 0x1F;
        if (t == 6 || t == 9 || t == 12)
            continue;
        bool newPic = self->hasSlice &&
                      (t == 7 || t == 8 || ((t == 1 || t == 5) && nal.size() > 1 && (nal[1] & 0x80)));
        if (newPic) {
            if (!flush()) {
                Py_DECREF(out);
                return nullptr;
            }
            self->hasSlice = 0;
        }
        au.push_back(std::move(nal));
        self->hasSlice = self->hasSlice || t == 1 || t == 5;
    }
    return out;
}

static int auInit(AccessUnits *self, PyObject *args, PyObject *kw) {
    if (!PyArg_ParseTuple(args, ":AccessUnits"))
        return -1;
    self->buf->clear();
    self->au->clear();
    self->hasSlice = 0;
    return 0;
}

static PyObject *auNew(PyTypeObject *type, PyObject *, PyObject *) {
    auto *self = reinterpret_cast<AccessUnits *>(type->tp_alloc(type, 0));
    if (!self)
        return nullptr;
    self->buf = new (std::nothrow) Bytes();
    self->au = new (std::nothrow) std::vector<Bytes>();
    if (!self->buf || !self->au) {
        Py_DECREF(self);
        return PyErr_NoMemory();
    }
    return reinterpret_cast<PyObject *>(self);
}

static void auDealloc(AccessUnits *self) {
    delete self->buf;
    delete self->au;
    Py_TYPE(self)->tp_free(reinterpret_cast<PyObject *>(self));
}

static PyMethodDef auMethods[] = {
    {"feed", reinterpret_cast<PyCFunction>(auFeed), METH_O, nullptr},
    {nullptr, nullptr, 0, nullptr},
};

static PyTypeObject AccessUnitsType = {PyVarObject_HEAD_INIT(nullptr, 0)};

//jitter buffer

//puts packets for one video ssrc back in order and hands out whole frames
//asks for resends (nack) when a packet goes missing and a keyframe (pli) if it stays missing
constexpr double GAP_TIMEOUT = 0.35, NACK_DELAY = 0.015, NACK_RETRY = 0.08;
constexpr size_t MAX_PKTS = 3000;

static double monotonic() {
    using namespace std::chrono;
    return duration<double>(steady_clock::now().time_since_epoch()).count();
}

static void dropPkt(Pkt &p) {
    Py_CLEAR(p.payload);
}

//keep only packets after seq, drop the rest
static void keepAfter(AsmState &st, uint16_t seq) {
    for (auto it = st.pkts.begin(); it != st.pkts.end();) {
        if (seqAfter(it->first, seq)) {
            ++it;
        } else {
            dropPkt(it->second);
            it = st.pkts.erase(it);
        }
    }
}

static int callNoArgs(PyObject *fn) {
    PyObject *r = PyObject_CallNoArgs(fn);
    if (!r)
        return -1;
    Py_DECREF(r);
    return 0;
}

static int asmDrain(Assembler *self);

static int asmResync(Assembler *self) {
    AsmState &st = *self->st;
    self->lostFrames++;
    self->needKey = 1;
    if (callNoArgs(self->requestKeyframe) < 0)
        return -1;
    st.gapSince = 0.0;
    st.nacked.clear();
    bool anyMarker = false;
    int ref = st.nextSeq;
    if (ref < 0) {
        ref = 0xFFFF;
        for (auto &kv : st.pkts)
            ref = std::min<int>(ref, kv.first);
    }
    uint16_t newest = 0, best = 0;
    for (auto &kv : st.pkts) {
        if (!kv.second.marker)
            continue;
        uint16_t d = static_cast<uint16_t>(kv.first - ref);
        if (!anyMarker || d > best) {
            best = d;
            newest = kv.first;
            anyMarker = true;
        }
    }
    if (!anyMarker) {
        for (auto &kv : st.pkts)
            dropPkt(kv.second);
        st.pkts.clear();
        st.nextSeq = -1;
        return 0;
    }
    st.nextSeq = static_cast<uint16_t>(newest + 1);
    keepAfter(st, newest);
    return 0;
}

//a hole in the sequence, nack it after a moment then give up and resync
static int asmWaiting(Assembler *self) {
    AsmState &st = *self->st;
    double now = monotonic();
    if (!st.gapSince)
        st.gapSince = now;
    if (now - st.gapSince > GAP_TIMEOUT) {
        if (asmResync(self) < 0)
            return -1;
        return asmDrain(self);
    }
    if (now - st.gapSince < NACK_DELAY)
        return 0;  //probably just reordered
    uint16_t next = static_cast<uint16_t>(st.nextSeq), span = 0;
    for (auto &kv : st.pkts)
        span = std::max<uint16_t>(span, static_cast<uint16_t>(kv.first - next));
    if (span > 512)
        return 0;
    std::vector<uint16_t> missing;
    for (uint16_t k = 0; k < span; k++) {
        uint16_t q = static_cast<uint16_t>(next + k);
        if (st.pkts.count(q))
            continue;
        auto it = st.nacked.find(q);
        if (it == st.nacked.end()) {
            st.nacked[q] = {now, 1};
            missing.push_back(q);
        } else if (now - it->second.first > NACK_RETRY && it->second.second < 3) {  //rtx gets lost too
            it->second = {now, it->second.second + 1};
            missing.push_back(q);
        }
    }
    if (missing.empty())
        return 0;
    PyObject *list = PyList_New(static_cast<Py_ssize_t>(missing.size()));
    if (!list)
        return -1;
    for (size_t k = 0; k < missing.size(); k++)
        PyList_SET_ITEM(list, static_cast<Py_ssize_t>(k), PyLong_FromLong(missing[k]));
    PyObject *r = PyObject_CallOneArg(self->nackCb, list);
    Py_DECREF(list);
    if (!r)
        return -1;
    Py_DECREF(r);
    return 0;
}

//hand out every complete frame starting at nextSeq
//a frame is all packets with the same timestamp ending at one with the marker bit
static int asmDrain(Assembler *self) {
    AsmState &st = *self->st;
    while (!st.pkts.empty()) {
        if (st.nextSeq < 0) {
            //first contact, start at a keyframe if we caught one
            const std::pair<const uint16_t, Pkt> *oldest = nullptr, *firstMarker = nullptr;
            for (auto &kv : st.pkts) {
                if (!oldest || kv.second.order < oldest->second.order)
                    oldest = &kv;
                if (kv.second.marker && (!firstMarker || kv.second.order < firstMarker->second.order))
                    firstMarker = &kv;
            }
            uint16_t lo = 0, best = 0xFFFF;
            bool found = false;
            for (auto &kv : st.pkts) {
                uint16_t d = static_cast<uint16_t>(kv.first - oldest->first + 0x8000);
                if (!found || d < best) {
                    best = d;
                    lo = kv.first;
                    found = true;
                }
            }
            PyObject *p = st.pkts[lo].payload;
            if (isFrameStart(self->codec, View(PyBytes_AS_STRING(p), PyBytes_GET_SIZE(p)))) {
                st.nextSeq = lo;
                continue;
            }
            //otherwise after the first frame end
            if (!firstMarker)
                return 0;
            uint16_t m = firstMarker->first;
            st.nextSeq = static_cast<uint16_t>(m + 1);
            keepAfter(st, m);
            continue;
        }
        uint16_t s = static_cast<uint16_t>(st.nextSeq), cur = s;
        std::vector<uint16_t> seqs;
        bool complete = false;
        auto first = st.pkts.find(s);
        if (first != st.pkts.end()) {
            uint32_t ts0 = first->second.ts;
            while (true) {
                auto it = st.pkts.find(cur);
                if (it == st.pkts.end() || it->second.ts != ts0)
                    break;
                seqs.push_back(cur);
                if (it->second.marker) {
                    complete = true;
                    break;
                }
                cur = static_cast<uint16_t>(cur + 1);
            }
        }
        if (!complete)
            return asmWaiting(self);
        std::vector<PyObject *> owned;
        std::vector<View> payloads;
        for (uint16_t q : seqs) {
            auto it = st.pkts.find(q);
            PyObject *p = it->second.payload;
            owned.push_back(p);
            payloads.emplace_back(PyBytes_AS_STRING(p), PyBytes_GET_SIZE(p));
            st.pkts.erase(it);
        }
        st.nextSeq = static_cast<uint16_t>(cur + 1);
        st.gapSince = 0.0;
        st.nacked.clear();
        Frame f;
        try {
            f = assemble(self->codec, payloads);
        } catch (const Truncated &) {
            f = Frame{};
        }
        for (PyObject *p : owned)
            Py_DECREF(p);
        if (!f.data || f.data->empty()) {
            self->needKey = 1;
            continue;
        }
        if (self->needKey && !f.key) {
            if (callNoArgs(self->requestKeyframe) < 0)
                return -1;
            continue;
        }
        self->needKey = 0;
        PyObject *frame = toBytes(*f.data);
        if (!frame)
            return -1;
        PyObject *r = PyObject_CallFunctionObjArgs(self->onFrame, frame, f.key ? Py_True : Py_False, nullptr);
        Py_DECREF(frame);
        if (!r)
            return -1;
        Py_DECREF(r);
    }
    return 0;
}

static PyObject *asmAdd(Assembler *self, PyObject *args) {
    unsigned int seqArg;
    unsigned long long ts;
    int marker;
    PyObject *payload;
    if (!PyArg_ParseTuple(args, "IKpO", &seqArg, &ts, &marker, &payload))
        return nullptr;
    AsmState &st = *self->st;
    uint16_t seq = static_cast<uint16_t>(seqArg);
    if (st.nextSeq >= 0 && !(seq == st.nextSeq || seqAfter(seq, static_cast<uint16_t>(st.nextSeq))))
        Py_RETURN_NONE;
    if (st.pkts.count(seq))
        Py_RETURN_NONE;
    PyObject *b = PyBytes_Check(payload) ? (Py_INCREF(payload), payload) : PyBytes_FromObject(payload);
    if (!b)
        return nullptr;
    st.pkts[seq] = Pkt{static_cast<uint32_t>(ts), marker != 0, b, st.counter++};
    if (st.pkts.size() > MAX_PKTS && asmResync(self) < 0)
        return nullptr;
    if (asmDrain(self) < 0)
        return nullptr;
    Py_RETURN_NONE;
}

static PyObject *asmNew(PyTypeObject *type, PyObject *, PyObject *) {
    auto *self = reinterpret_cast<Assembler *>(type->tp_alloc(type, 0));
    if (!self)
        return nullptr;
    self->st = new (std::nothrow) AsmState();
    if (!self->st) {
        Py_DECREF(self);
        return PyErr_NoMemory();
    }
    self->needKey = 1;
    return reinterpret_cast<PyObject *>(self);
}

//Assembler(codec, onFrame, requestKeyframe, nack), codec is an rtpvideo.Codec
static int asmInit(Assembler *self, PyObject *args, PyObject *) {
    PyObject *codec, *onFrame, *requestKeyframe, *nackCb;
    if (!PyArg_ParseTuple(args, "OOOO:Assembler", &codec, &onFrame, &requestKeyframe, &nackCb))
        return -1;
    PyObject *id = PyObject_GetAttrString(codec, "id");
    if (!id)
        return -1;
    long c = PyLong_AsLong(id);
    Py_DECREF(id);
    if (c == -1 && PyErr_Occurred())
        return -1;
    if (!checkCodec(static_cast<int>(c)))
        return -1;
    self->codec = static_cast<int>(c);
    Py_INCREF(codec);
    Py_INCREF(onFrame);
    Py_INCREF(requestKeyframe);
    Py_INCREF(nackCb);
    Py_XSETREF(self->codecObj, codec);
    Py_XSETREF(self->onFrame, onFrame);
    Py_XSETREF(self->requestKeyframe, requestKeyframe);
    Py_XSETREF(self->nackCb, nackCb);
    return 0;
}

static int asmTraverse(Assembler *self, visitproc visit, void *arg) {
    Py_VISIT(self->codecObj);
    Py_VISIT(self->onFrame);
    Py_VISIT(self->requestKeyframe);
    Py_VISIT(self->nackCb);
    return 0;
}

static int asmClear(Assembler *self) {
    Py_CLEAR(self->codecObj);
    Py_CLEAR(self->onFrame);
    Py_CLEAR(self->requestKeyframe);
    Py_CLEAR(self->nackCb);
    return 0;
}

static void asmDealloc(Assembler *self) {
    PyObject_GC_UnTrack(self);
    asmClear(self);
    if (self->st) {
        for (auto &kv : self->st->pkts)
            dropPkt(kv.second);
        delete self->st;
    }
    Py_TYPE(self)->tp_free(reinterpret_cast<PyObject *>(self));
}

static PyObject *asmGetNeedKey(Assembler *self, void *) {
    return PyBool_FromLong(self->needKey);
}

static int asmSetNeedKey(Assembler *self, PyObject *v, void *) {
    if (!v) {
        PyErr_SetString(PyExc_AttributeError, "can't delete needKey");
        return -1;
    }
    int t = PyObject_IsTrue(v);
    if (t < 0)
        return -1;
    self->needKey = t;
    return 0;
}

static PyMethodDef asmMethods[] = {
    {"add", reinterpret_cast<PyCFunction>(asmAdd), METH_VARARGS, nullptr},
    {nullptr, nullptr, 0, nullptr},
};

static PyMemberDef asmMembers[] = {
    {"codec", T_OBJECT, offsetof(Assembler, codecObj), READONLY, nullptr},
    {"lostFrames", T_PYSSIZET, offsetof(Assembler, lostFrames), READONLY, nullptr},
    {nullptr, 0, 0, 0, nullptr},
};

static PyGetSetDef asmGetset[] = {
    {"needKey", reinterpret_cast<getter>(asmGetNeedKey), reinterpret_cast<setter>(asmSetNeedKey), nullptr, nullptr},
    {nullptr, nullptr, nullptr, nullptr, nullptr},
};

static PyTypeObject AssemblerType = {PyVarObject_HEAD_INIT(nullptr, 0)};

static PyMethodDef methods[] = {
    {"assemble", pyAssemble, METH_VARARGS, nullptr},
    {"isFrameStart", pyIsFrameStart, METH_VARARGS, nullptr},
    {"av1Candidates", av1Candidates, METH_O, nullptr},
    {"av1Finish", av1Finish, METH_O, nullptr},
    {"ivfHeader", reinterpret_cast<PyCFunction>(reinterpret_cast<void (*)(void)>(ivfHeader)),
     METH_VARARGS | METH_KEYWORDS, nullptr},
    {"ivfFrame", ivfFrame, METH_VARARGS, nullptr},
    {"senderReport", senderReport, METH_VARARGS, nullptr},
    {"feedback", feedback, METH_VARARGS, nullptr},
    {"pli", pli, METH_VARARGS, nullptr},
    {"nack", nack, METH_VARARGS, nullptr},
    {"videoPackets", videoPackets, METH_VARARGS, nullptr},
    {"rtpPayload", rtpPayload, METH_VARARGS, nullptr},
    {nullptr, nullptr, 0, nullptr},
};

static PyModuleDef module = {PyModuleDef_HEAD_INIT, "_rtp", nullptr, -1, methods};

PyMODINIT_FUNC PyInit__rtp() {
    AssemblerType.tp_name = "uncord._rtp.Assembler";
    AssemblerType.tp_basicsize = sizeof(Assembler);
    AssemblerType.tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC;
    AssemblerType.tp_new = asmNew;
    AssemblerType.tp_init = reinterpret_cast<initproc>(asmInit);
    AssemblerType.tp_dealloc = reinterpret_cast<destructor>(asmDealloc);
    AssemblerType.tp_traverse = reinterpret_cast<traverseproc>(asmTraverse);
    AssemblerType.tp_clear = reinterpret_cast<inquiry>(asmClear);
    AssemblerType.tp_methods = asmMethods;
    AssemblerType.tp_members = asmMembers;
    AssemblerType.tp_getset = asmGetset;
    AccessUnitsType.tp_name = "uncord._rtp.AccessUnits";
    AccessUnitsType.tp_basicsize = sizeof(AccessUnits);
    AccessUnitsType.tp_flags = Py_TPFLAGS_DEFAULT;
    AccessUnitsType.tp_new = auNew;
    AccessUnitsType.tp_init = reinterpret_cast<initproc>(auInit);
    AccessUnitsType.tp_dealloc = reinterpret_cast<destructor>(auDealloc);
    AccessUnitsType.tp_methods = auMethods;
    if (PyType_Ready(&AssemblerType) < 0 || PyType_Ready(&AccessUnitsType) < 0)
        return nullptr;
    PyObject *m = PyModule_Create(&module);
    if (!m)
        return nullptr;
    const char *names[] = {"H264", "H265", "VP8", "VP9", "AV1"};
    for (int k = 0; k < NCODECS; k++) {
        if (PyModule_AddIntConstant(m, names[k], k) < 0) {
            Py_DECREF(m);
            return nullptr;
        }
    }
    Py_INCREF(&AssemblerType);
    Py_INCREF(&AccessUnitsType);
    if (PyModule_AddObject(m, "Assembler", reinterpret_cast<PyObject *>(&AssemblerType)) < 0 ||
        PyModule_AddObject(m, "AccessUnits", reinterpret_cast<PyObject *>(&AccessUnitsType)) < 0) {
        Py_DECREF(m);
        return nullptr;
    }
    return m;
}
