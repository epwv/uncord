#fast paths from the c extension with the same functions in python when it isnt built
from __future__ import annotations

import struct
from typing import List, Optional, Tuple

try:
    from . import _native
except ImportError:
    _native = None

ACTIVE = _native is not None


def _hasPixels(sixel) -> bool:
    if isinstance(sixel, bytes):
        sixel = sixel.decode("ascii", "replace")
    body = sixel[sixel.find("q") + 1 :] if "q" in sixel else ""
    end = body.find("\x1b")
    if end >= 0:
        body = body[:end]
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c in "#\"":
            i += 1
            while i < n and (body[i].isdigit() or body[i] == ";"):
                i += 1
            continue
        if "@" <= c <= "~":
            return True
        i += 1
    return False


def _placeholderRow(row: int, x0: int, width: int) -> str:
    from textual_image.renderable.tgp import _NUMBER_TO_DIACRITIC as d

    r = chr(d[row])
    return "".join("\U0010EEEE" + r + chr(d[c]) for c in range(x0, x0 + width))


def _splitAnnexb(buf: bytes) -> List[bytes]:
    nals, i, n = [], 0, len(buf)
    starts = []
    while True:
        j = buf.find(b"\x00\x00\x01", i)
        if j < 0:
            break
        starts.append(j)
        i = j + 3
    for k, s in enumerate(starts):
        begin = s + 3
        end = starts[k + 1] if k + 1 < len(starts) else n
        if k + 1 < len(starts) and end > begin and buf[end - 1] == 0:
            end -= 1
        if end > begin:
            nals.append(buf[begin:end])
    return nals


def _fuaPayloads(nals: List[bytes], mtu: int) -> List[bytes]:
    out = []
    for nal in nals:
        if not nal:
            continue
        if len(nal) <= mtu:
            out.append(nal)
            continue
        ind = (nal[0] & 0xE0) | 28
        typ = nal[0] & 0x1F
        body = nal[1:]
        for off in range(0, len(body), mtu):
            fu = (0x80 if off == 0 else 0) | (0x40 if off + mtu >= len(body) else 0) | typ
            out.append(bytes((ind, fu)) + body[off : off + mtu])
    return out


def _rtpHeader(data: bytes) -> Optional[Tuple[int, int, bool, int, int, int]]:
    if len(data) < 16 or (data[0] >> 6) != 2:
        return None
    hlen = 12 + 4 * (data[0] & 0x0F) + (4 if data[0] & 0x10 else 0)
    if hlen > len(data):
        return None
    seq, ts, ssrc = struct.unpack_from(">HII", data, 2)
    return hlen, data[1] & 0x7F, bool(data[1] & 0x80), seq, ts, ssrc


hasPixels = _native.hasPixels if ACTIVE else _hasPixels
placeholderRow = _native.placeholderRow if ACTIVE else _placeholderRow
splitAnnexb = _native.splitAnnexb if ACTIVE else _splitAnnexb
fuaPayloads = _native.fuaPayloads if ACTIVE else _fuaPayloads
rtpHeader = _native.rtpHeader if ACTIVE else _rtpHeader
