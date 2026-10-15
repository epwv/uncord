#rtp video codecs h264 h265 vp8 vp9 av1, the depacketizing itself is in _rtp.cpp
from __future__ import annotations

from typing import List, Optional, Tuple

from . import _rtp


#assemble turns one frames rtp payloads into a frame and says if its a keyframe
#isFrameStart spots a keyframe start so we can join mid stream
#candidates and finish only do something for av1
class Codec:

    def __init__(self, name: str, ffFormat: str, ivfFourcc: Optional[bytes], id: int) -> None:
        self.name, self.ffFormat, self.ivfFourcc, self.id = name, ffFormat, ivfFourcc, id

    def assemble(self, payloads: List[bytes]) -> Tuple[Optional[bytes], bool]:
        return _rtp.assemble(self.id, payloads)

    def isFrameStart(self, payload: bytes) -> bool:
        return _rtp.isFrameStart(self.id, payload)

    #dave may strip the last obu size so av1 has two layouts to try
    def candidates(self, frame: bytes) -> List[bytes]:
        return _rtp.av1Candidates(frame) if self.id == _rtp.AV1 else [frame]

    def finish(self, frame: bytes) -> bytes:
        return _rtp.av1Finish(frame) if self.id == _rtp.AV1 else frame


CODECS = {
    c.name: c
    for c in (
        Codec("H264", "h264", None, _rtp.H264),
        Codec("H265", "hevc", None, _rtp.H265),
        Codec("VP8", "ivf", b"VP80", _rtp.VP8),
        Codec("VP9", "ivf", b"VP90", _rtp.VP9),
        Codec("AV1", "ivf", b"AV01", _rtp.AV1),
    )
}

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

#ffmpeg and mpv cant read raw vp8 vp9 av1 from a pipe so they get wrapped in ivf
ivfHeader = _rtp.ivfHeader
ivfFrame = _rtp.ivfFrame
