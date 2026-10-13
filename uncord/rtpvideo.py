#rtp video depacketizers turn packets back into frames for ffmpeg
#h264 h265 vp8 vp9 av1 plus ivf framing for the vpx and av1 ones
from __future__ import annotations

import struct
from typing import List, Optional, Tuple

START = b"\x00\x00\x00\x01"


#av1 variable length numbers 7 bits per byte high bit means more
def leb128(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def readLeb128(buf: bytes, i: int) -> Tuple[int, int]:
    val = shift = 0
    while i < len(buf):
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return val, i
    raise ValueError("truncated leb128")


#what every codec does
#assemble turns one frames rtp payloads into a frame and says if its a keyframe
#isFrameStart spots a keyframe start so we can join mid stream
#candidates and finish exist for av1 see below
class Codec:
    name = ""
    ffFormat = ""
    ivfFourcc: Optional[bytes] = None

    def assemble(self, payloads: List[bytes]) -> Tuple[Optional[bytes], bool]:
        raise NotImplementedError

    def isFrameStart(self, payload: bytes) -> bool:
        return False

    def candidates(self, frame: bytes) -> List[bytes]:
        return [frame]

    def finish(self, frame: bytes) -> bytes:
        return frame


#h264


#rfc 6184 single nal stap-a (several nals) and fu-a (one nal split up)
class H264(Codec):
    name, ffFormat = "H264", "h264"

    @staticmethod
    def nals(payloads: List[bytes]) -> List[bytes]:
        nals: List[bytes] = []
        fu: Optional[bytearray] = None
        for p in payloads:
            if not p:
                continue
            t = p[0] & 0x1F
            if 1 <= t <= 23:
                nals.append(p)
            elif t == 24:  #stap-a
                i = 1
                while i + 2 <= len(p):
                    n = struct.unpack_from(">H", p, i)[0]
                    i += 2
                    if n and i + n <= len(p):
                        nals.append(p[i : i + n])
                    i += n
            elif t == 28 and len(p) > 2:  #fu-a
                if p[1] & 0x80:
                    fu = bytearray(((p[0] & 0xE0) | (p[1] & 0x1F),))
                if fu is not None:
                    fu += p[2:]
                    if p[1] & 0x40:
                        nals.append(bytes(fu))
                        fu = None
        return nals

    def assemble(self, payloads):
        nals = self.nals(payloads)
        if not nals:
            return None, False
        key = any((n[0] & 0x1F) in (5, 7) for n in nals)
        return b"".join(START + n for n in nals), key

    def isFrameStart(self, p):
        return bool(p) and ((p[0] & 0x1F) == 7 or ((p[0] & 0x1F) == 24 and len(p) > 3 and (p[3] & 0x1F) == 7))


#h265


#rfc 7798 same idea as h264 with a 2 byte nal header
#48 is an aggregation packet 49 is a fragment
class H265(Codec):
    name, ffFormat = "H265", "hevc"

    def assemble(self, payloads):
        nals: List[bytes] = []
        fu: Optional[bytearray] = None
        for p in payloads:
            if len(p) < 3:
                continue
            t = (p[0] >> 1) & 0x3F
            if t == 48:
                i = 2
                while i + 2 <= len(p):
                    n = struct.unpack_from(">H", p, i)[0]
                    i += 2
                    if n and i + n <= len(p):
                        nals.append(p[i : i + n])
                    i += n
            elif t == 49:
                fh = p[2]
                if fh & 0x80:
                    fu = bytearray((((p[0] & 0x81) | ((fh & 0x3F) << 1)), p[1]))
                if fu is not None:
                    fu += p[3:]
                    if fh & 0x40:
                        nals.append(bytes(fu))
                        fu = None
            else:
                nals.append(p)
        if not nals:
            return None, False
        key = any(16 <= ((n[0] >> 1) & 0x3F) <= 21 or ((n[0] >> 1) & 0x3F) == 32 for n in nals)
        return b"".join(START + n for n in nals), key

    def isFrameStart(self, p):
        if len(p) < 3:
            return False
        t = (p[0] >> 1) & 0x3F
        return t == 32 or (t == 48 and len(p) > 4 and ((p[4] >> 1) & 0x3F) == 32)


#vp8


#rfc 7741 strip the vp8 payload descriptor and glue the rest together
class VP8(Codec):
    name, ffFormat, ivfFourcc = "VP8", "ivf", b"VP80"

    @staticmethod
    def split(p: bytes) -> Tuple[int, int, bytes]:
        b = p[0]
        i = 1
        if b & 0x80:
            x = p[i]
            i += 1
            if x & 0x80:
                i += 2 if p[i] & 0x80 else 1
            if x & 0x40:
                i += 1
            if x & 0x30:
                i += 1
        return b & 0x10, b & 0x07, p[i:]

    def assemble(self, payloads):
        out = bytearray()
        key = False
        for k, p in enumerate(payloads):
            if not p:
                continue
            s, pid, data = self.split(p)
            if k == 0 and s and pid == 0 and data:
                key = not (data[0] & 0x01)
            out += data
        return (bytes(out) if out else None), key

    def isFrameStart(self, p):
        if not p:
            return False
        s, pid, data = self.split(p)
        return bool(s) and pid == 0 and bool(data) and not (data[0] & 0x01)


#vp9


#rfc 9628 strip the vp9 descriptor (picture id layers scalability info)
class VP9(Codec):
    name, ffFormat, ivfFourcc = "VP9", "ivf", b"VP90"

    @staticmethod
    def split(p: bytes) -> Tuple[int, int, bytes]:
        b = p[0]
        i = 1
        if b & 0x80:
            i += 2 if p[i] & 0x80 else 1
        if b & 0x20:
            i += 1
            if not b & 0x10:
                i += 1
        if b & 0x10 and b & 0x40:
            while True:
                n = p[i]
                i += 1
                if not n & 0x01:
                    break
        if b & 0x02:
            ss = p[i]
            i += 1
            nS = (ss >> 5) + 1
            if ss & 0x10:
                i += 4 * nS
            if ss & 0x08:
                nG = p[i]
                i += 1
                for _ in range(nG):
                    r = (p[i] >> 2) & 0x03
                    i += 1 + r
        return b & 0x08, b & 0x40, p[i:]

    def assemble(self, payloads):
        out = bytearray()
        key = False
        for k, p in enumerate(payloads):
            if not p:
                continue
            beg, inter, data = self.split(p)
            if k == 0:
                key = bool(beg) and not inter
            out += data
        return (bytes(out) if out else None), key

    def isFrameStart(self, p):
        if not p:
            return False
        beg, inter, _ = self.split(p)
        return bool(beg) and not inter


#av1

OBU_SEQUENCE_HEADER, OBU_TEMPORAL_DELIMITER, OBU_TILE_LIST, OBU_PADDING = 1, 2, 8, 15


#av1 frames are a list of obus each with a header and maybe a size
def parseObus(buf: bytes) -> List[Tuple[bytes, bytes]]:
    out = []
    i = 0
    while i < len(buf):
        h = buf[i]
        hl = 2 if h & 0x04 else 1
        header = bytearray(buf[i : i + hl])
        i += hl
        if h & 0x02:
            size, i = readLeb128(buf, i)
        else:
            size = len(buf) - i
        out.append((bytes(header), buf[i : i + size]))
        i += size
    return out


def writeObus(obus: List[Tuple[bytes, bytes]], lastSized: bool = True) -> bytes:
    out = bytearray()
    for k, (header, payload) in enumerate(obus):
        sized = lastSized or k < len(obus) - 1
        h = (header[0] | 0x02) if sized else (header[0] & ~0x02 & 0xFF)
        out.append(h)
        out += header[1:]
        if sized:
            out += leb128(len(payload))
        out += payload
    return bytes(out)


#av1 rtp spec an aggregation byte then obu elements that can be split across packets
#z means the first element continues the last packet y means the last one carries on
class AV1(Codec):
    name, ffFormat, ivfFourcc = "AV1", "ivf", b"AV01"

    def assemble(self, payloads):
        obus: List[bytes] = []
        frag: Optional[bytearray] = None
        key = False
        for k, p in enumerate(payloads):
            if not p:
                continue
            agg = p[0]
            z, y, w, n = agg & 0x80, agg & 0x40, (agg >> 4) & 0x03, agg & 0x08
            if k == 0 and n:
                key = True
            i = 1
            elems = []
            while i < len(p):
                if w and len(elems) == w - 1:
                    elems.append(p[i:])
                    break
                size, i = readLeb128(p, i)
                elems.append(p[i : i + size])
                i += size
            for eI, e in enumerate(elems):
                first, last = eI == 0, eI == len(elems) - 1
                if first and z:
                    if frag is None:
                        continue
                    frag += e
                    if not (last and y):
                        obus.append(bytes(frag))
                        frag = None
                    continue
                if last and y:
                    frag = bytearray(e)
                else:
                    obus.append(e)
        parsed = []
        for o in obus:
            if not o:
                continue
            h = o[0]
            hl = 2 if h & 0x04 else 1
            body = o[hl:]
            if h & 0x02:
                size, j = readLeb128(o, hl)
                body = o[j : j + size]
            t = (h >> 3) & 0x0F
            if t in (OBU_TEMPORAL_DELIMITER, OBU_TILE_LIST, OBU_PADDING):
                continue
            if t == OBU_SEQUENCE_HEADER:
                key = True
            parsed.append((o[:hl], body))
        if not parsed:
            return None, False
        self._last = parsed
        return writeObus(parsed), key

    def candidates(self, frame):
        #dave may strip the last obu size so try both
        try:
            obus = parseObus(frame)
        except ValueError:
            return [frame]
        return [frame, writeObus(obus, lastSized=False)]

    def finish(self, frame):
        try:
            return b"\x12\x00" + writeObus(parseObus(frame))
        except ValueError:
            return frame

    def isFrameStart(self, p):
        return bool(p) and not p[0] & 0x80 and bool(p[0] & 0x08)


CODECS = {c.name: c for c in (H264(), H265(), VP8(), VP9(), AV1())}

#decode only same payload types as the desktop client
OFFER = [
    ("H264", 1000, 105, 106),
    ("AV1", 2000, 101, 102),
    ("VP8", 3000, 107, 108),
    ("VP9", 4000, 109, 110),
    ("H265", 5000, 103, 104),
]
PT_CODEC = {pt: name for name, _p, pt, _r in OFFER}
RTX_PT = {rtx: pt for _n, _p, pt, rtx in OFFER}


#ffmpeg and mpv cant read raw vp8 vp9 av1 from a pipe so wrap them in ivf
def ivfHeader(fourcc: bytes, width: int = 1920, height: int = 1080) -> bytes:
    return b"DKIF" + struct.pack("<HH4sHHIIII", 0, 32, fourcc, width, height, 1000, 1, 0, 0)


def ivfFrame(frame: bytes, ptsMs: int) -> bytes:
    return struct.pack("<IQ", len(frame), ptsMs) + frame
