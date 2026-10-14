#go live
#sending your screen (ScreenShare) and watching someone elses (StreamWatch + StreamDecoder)
#rtp packets are encrypted with the voice key then dave e2ee on top
from __future__ import annotations

import asyncio
import logging
import os
import struct
import subprocess
import threading
import time
from typing import List, Optional, Tuple

import discord
import nacl.secret
from discord.stream import StreamProtocol
from discord.voice_media import VoiceCodec, VoiceStream, VoiceStreamResolution
from discord.voice_state import ConnectionFlowState, VoiceConnectionState

from .deps import FFMPEG, GSR, MPV
from . import native, rtpvideo

try:
    import davey
except ImportError:
    davey = None

log = logging.getLogger("uncord.golive")

#same payload types as the desktop client there 103 means h265 and viewers cant decode it
H264_PT, H264_RTX_PT = 105, 106
CLOCK = 90000
MTU_PAYLOAD = 1100


_gsrHelp: Optional[str] = None


#older gsr builds lack some flags so check --help once
def gsrSupports(flag: str) -> bool:
    #older gsr builds in distro repos lack newer flags
    global _gsrHelp
    if _gsrHelp is None:
        try:
            _gsrHelp = (
                subprocess.run(GSR + ["--help"], capture_output=True, text=True, timeout=5).stdout
                if GSR
                else ""
            )
        except Exception:
            _gsrHelp = ""
    return flag in _gsrHelp


#webcams (gsr lists them as /dev/videoN|WxH@NNhz|format)
def listCameras() -> List[Tuple[str, int, int, int]]:
    if not GSR or not gsrSupports("--list-v4l2-devices"):
        return []
    try:
        out = subprocess.run(GSR + ["--list-v4l2-devices"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    modes = []
    for line in out.splitlines():
        parts = line.strip().split("|")
        if len(parts) < 2 or "x" not in parts[1]:
            continue
        try:
            size, _, rate = parts[1].partition("@")
            w, h = map(int, size.split("x"))
            fps = int(rate.rstrip("hz") or 0)
        except ValueError:
            continue
        modes.append((parts[0], w, h, fps))
    return modes


def pickCameraMode(modes, device: Optional[str] = None) -> Optional[Tuple[str, int, int, int]]:
    #biggest mode up to 720p that still does 30fps otherwise just the biggest
    modes = [m for m in modes if device is None or m[0] == device]
    if not modes:
        return None
    good = [m for m in modes if m[3] >= 30 and m[1] * m[2] <= 1280 * 720]
    return max(good or modes, key=lambda m: (m[1] * m[2], m[3]))


def listMonitors() -> List[Tuple[str, str]]:
    if not GSR:
        return []
    try:
        out = subprocess.run(GSR + ["--list-monitors"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    mons = []
    for line in out.splitlines():
        name, _, res = line.strip().partition("|")
        if name:
            mons.append((name, res))
    if not mons and gsrSupports("portal"):
        mons.append(("portal", "pick in the desktop dialog"))  #gnome/kde wayland
    return mons


#splits an h264 annex b stream on 00 00 01 start codes into nal units
splitAnnexb = native.splitAnnexb


#one byte rtp header extensions the same ones the desktop client sends
def _extPayload(elements) -> bytes:
    out = bytearray()
    for extId, data in elements:
        out.append((extId << 4) | (len(data) - 1))
        out += data
    while len(out) % 4:
        out.append(0)
    return bytes(out)


#a voice connection pointed at the stream server instead of the call server
#discord.py-self has the parts but not this glue so we fill in the endpoint token and dave group
class _StreamConnection(VoiceConnectionState):

    def __init__(self, proto, hook=None) -> None:
        super().__init__(proto, hook=hook)
        stream = proto.stream
        self.channel_id = int(stream.rtc_channel_id or proto.channel.id)
        #stream dave group is rtc server id minus 1
        self._groupId = int(stream.rtc_server_id) - 1 if stream.rtc_server_id else self.channel_id

    @property
    def dave_group_id(self) -> int:
        return self._groupId

    @property
    def self_voice_state(self):
        return None

    async def _voice_connect(self, **_kw) -> None:
        proto: ScreenShare = self.voice_client
        data = proto.serverData or {}
        self.session_id = proto.voice_client._connection.session_id
        self.token = data.get("token")
        self.server_id = int(proto.stream.rtc_server_id)
        endpoint = (data.get("endpoint") or "").removeprefix("wss://").rstrip("/")
        self.endpoint = endpoint
        self.endpoint_ip = discord.utils.MISSING
        self._create_socket()
        self.state = ConnectionFlowState.got_both_voice_updates

    async def _voice_disconnect(self) -> None:
        self.state = ConnectionFlowState.disconnected


#sending your screen
#gsr captures and encodes h264 ffmpeg cleans it up _Sender packs it into rtp
class ScreenShare(StreamProtocol):

    supported_modes = ("aead_xchacha20_poly1305_rtpsize",)
    monitor = "DP-2"
    width, height, fps, kbps = 1280, 720, 30, 2500

    def __init__(self, voice_client, stream) -> None:
        super().__init__(voice_client, stream)
        self.channel = voice_client.channel
        self.guild = getattr(voice_client, "guild", None)
        self.loop = voice_client.loop
        self._state = voice_client._state
        self.user = voice_client.client.user
        self.serverData: Optional[dict] = None
        self._server = asyncio.Event()
        self.conn: Optional[_StreamConnection] = None
        self.capture: Optional[_Capture] = None
        self.sender: Optional[_Sender] = None
        self.onEnd = None

    def supports_video(self) -> bool:
        return True

    def get_experiments(self, ready_experiments):
        return ()

    @property
    def codecs(self):
        return (
            VoiceCodec.opus(),
            VoiceCodec.video(
                "H264",
                priority=1000,
                payload_type=H264_PT,
                rtx_payload_type=H264_RTX_PT,
                encode=True,
                decode=False,
            ),
        )

    @property
    def video_streams(self):
        return (
            VoiceStream.screen(
                rid="100",
                quality=100,
                max_bitrate=self.kbps * 1000,
                max_framerate=self.fps,
                max_resolution=VoiceStreamResolution(type="fixed", width=self.width, height=self.height),
            ),
        )

    @property
    def ws(self):
        return self.conn.ws if self.conn is not None else None

    def stop(self) -> None:
        if self.sender is not None:
            self.sender.stop()
        if self.capture is not None:
            self.capture.stop()

    async def on_stream_server_update(self, data, /) -> None:
        self.serverData = data
        self._server.set()

    async def on_stream_delete(self, stream, reason, /) -> None:
        log.info("stream deleted by Discord: %s", reason)
        await self.disconnect(force=True, notify=f"Stream ended ({reason}).")

    #wait for the stream server then connect and tell it which ssrc our video uses
    async def connect(self, *, timeout: float, reconnect: bool) -> None:
        await asyncio.wait_for(self._server.wait(), timeout)
        self.conn = _StreamConnection(self)
        await self.conn.connect(
            reconnect=False, timeout=timeout, self_deaf=False, self_mute=False, self_video=False, resume=False
        )
        streams = [s for s in self.conn.video_streams.values() if s.ssrc]
        if not streams:
            raise RuntimeError("stream server assigned no video SSRC")
        vs = streams[0]
        vs.active = True
        vs.max_bitrate = self.kbps * 1000
        vs.max_framerate = self.fps
        vs.max_resolution = VoiceStreamResolution(type="fixed", width=self.width, height=self.height)
        await self.conn.ws.video_state(video_ssrc=vs.ssrc, rtx_ssrc=vs.rtx_ssrc or 0, streams=[vs])
        log.info(
            "go live connected: endpoint=%s ssrc=%s video_ssrc=%s dave=%s group=%s mode=%s codec=%s",
            self.conn.endpoint,
            self.conn.ssrc,
            vs.ssrc,
            self.conn.dave_protocol_version,
            self.conn.dave_group_id,
            self.conn.mode,
            self.conn.video_codec,
        )
        self.sender = _Sender(self.conn, vs.ssrc, self.fps)
        self.capture = _Capture(
            self.monitor,
            self.width,
            self.height,
            self.fps,
            self.kbps,
            self.sender.feed,
            onExit=self._captureDied,
        )
        self.sender.start()
        self.capture.start()

    def _captureDied(self, why: str) -> None:
        log.error("screen capture stopped: %s", why)
        self.loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(
                self.disconnect(force=True, notify=f"Screen capture stopped: {why}")
            )
        )

    async def disconnect(self, *, force: bool = True, notify: Optional[str] = None) -> None:
        self.stop()
        conn, self.conn = self.conn, None
        if conn is not None:
            try:
                await conn.disconnect(force=True, cleanup=False)
            except Exception:
                log.debug("stream conn disconnect", exc_info=True)
            try:
                conn._stop_socket_reader()
            except Exception:
                pass
        try:
            await self.stream.delete()
        except Exception:
            pass
        try:
            self.cleanup()
        except Exception:
            pass
        if self.onEnd is not None:
            cb, self.onEnd = self.onEnd, None
            cb(notify)

    def statsText(self) -> str:
        s = self.sender
        if s is None:
            return "not sending"
        return (
            f"{s.frames} frames ({s.keyframes} key) · {s.bytes // 1024} KiB · {s.packets} packets · "
            f"dropped (no E2EE yet) {s.waiting} · dave={'yes' if s.conn.can_encrypt else 'no'}"
        )


#runs gpu-screen-recorder piped into ffmpeg and cuts the output into access units (one per frame)
class _Capture:

    def __init__(self, monitor, width, height, fps, kbps, feed, onExit, cursor: bool = True) -> None:
        self.feed = feed
        self.onExit = onExit
        self._stop = False
        self.args = (GSR or []) + [
            "-w",
            monitor,
            "-c",
            "mpegts",
            "-k",
            "h264",
            "-f",
            str(fps),
            "-s",
            f"{width}x{height}",
            "-bm",
            "cbr",
            "-q",
            str(kbps),
            "-keyint",
            "1",
            "-tune",
            "performance",
            "-cursor",
            "yes" if cursor else "no",
            "-fm",
            "cfr",
            "-ffmpeg-video-opts",
            #dpb_size=1 makes nvenc write max_dec_frame_buffering=1 otherwise the viewers webrtc rewrites
            #the sps to cut latency which breaks the dave check and they get a black screen
            "profile=baseline;dpb_size=1",
            "-o",
            "/dev/stdout",
        ]
        if gsrSupports("-fallback-cpu-encoding"):
            self.args[-2:-2] = ["-fallback-cpu-encoding", "yes"]
        self.ff = [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-fflags",
            "nobuffer",
            "-probesize",
            "32768",
            "-analyzeduration",
            "0",
            "-f",
            "mpegts",
            "-i",
            "-",
            "-map",
            "0:v",
            "-c:v",
            "copy",
            "-bsf:v",
            "filter_units=remove_types=12,dump_extra=freq=keyframe",
            "-flush_packets",
            "1",
            "-f",
            "h264",
            "-",
        ]
        self.gsr = self.ffp = None
        self.errLines: List[str] = []

    def _drain(self, pipe) -> None:
        #gsr prints fps every second and blocks once the pipe fills
        try:
            for line in pipe:
                line = line.decode(errors="replace").strip()
                if line and not line.startswith("update fps"):
                    self.errLines = (self.errLines + [line])[-5:]
        except Exception:
            pass

    def start(self) -> None:
        if not GSR or not FFMPEG:
            raise RuntimeError("needs gpu-screen-recorder and ffmpeg")
        self.gsr = subprocess.Popen(self.args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.ffp = subprocess.Popen(
            self.ff, stdin=self.gsr.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.gsr.stdout.close()
        for p in (self.gsr, self.ffp):
            threading.Thread(
                target=self._drain, args=(p.stderr,), daemon=True, name="uncord-golive-err"
            ).start()
        threading.Thread(target=self._read, daemon=True, name="uncord-golive-read").start()
        log.info("capture started: %s", " ".join(self.args[1:]))

    def _read(self) -> None:
        out = self.ffp.stdout
        buf = b""
        au: List[bytes] = []
        hasSlice = False
        while not self._stop:
            chunk = os.read(out.fileno(), 1 << 16)
            if not chunk:
                break
            buf += chunk
            #tail might be half a nal
            last = buf.rfind(b"\x00\x00\x01")
            if last <= 0:
                continue
            cut = last - 1 if buf[last - 1] == 0 else last
            nals, buf = splitAnnexb(buf[:cut]), buf[cut:]
            for nal in nals:
                t = nal[0] & 0x1F
                if t in (6, 9, 12):
                    continue
                #new picture at sps pps after a slice or at first mb 0
                newPic = hasSlice and (t in (7, 8) or (t in (1, 5) and len(nal) > 1 and nal[1] & 0x80))
                if newPic:
                    self.feed(au)
                    au = []
                    hasSlice = False
                au.append(nal)
                hasSlice = hasSlice or t in (1, 5)
        if not self._stop:
            try:
                self.gsr.wait(timeout=2)
            except Exception:
                pass
            time.sleep(0.1)
            self.onExit(self.errLines[-1] if self.errLines else "capture ended")

    def stop(self) -> None:
        self._stop = True
        for p in (self.gsr, self.ffp):
            if p is not None and p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass


#turns frames into encrypted rtp packets and sends sender reports
class _Sender:

    def __init__(self, conn, ssrc: int, fps: int, seal=None) -> None:
        self.conn = conn
        self.seal = seal  #the call connection shares one nonce counter with the voice client
        self.ssrc = ssrc
        self.fps = fps
        self.seq = 0
        self.nonce = 0
        self.t0 = time.monotonic()
        self.frames = self.keyframes = self.packets = self.bytes = self.waiting = 0
        self.offered = 0
        self.encryptFails = 0
        self.e2eeReady = False
        self.started = time.monotonic()
        self.lastTs = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._box = None
        self._boxKey = b""

    def start(self) -> None:
        threading.Thread(target=self._rtcpLoop, daemon=True, name="uncord-golive-rtcp").start()

    def stop(self) -> None:
        self._stop.set()

    def _aead(self):
        key = bytes(self.conn.secret_key)
        if self._box is None or key != self._boxKey:
            self._box, self._boxKey = nacl.secret.Aead(key), key
        return self._box

    def _encrypt(self, header: bytes, payload: bytes) -> bytes:
        if self.seal is not None:
            return self.seal(header, payload)
        nonce = struct.pack(">I", self.nonce)
        self.nonce = (self.nonce + 1) & 0xFFFFFFFF
        return header + self._aead().encrypt(payload, header, nonce + b"\x00" * 20).ciphertext + nonce

    def feed(self, nals: List[bytes]) -> None:
        self.offered += 1
        if self._stop.is_set() or not nals or not self.conn.is_connected():
            return
        key = any((n[0] & 0x1F) == 5 for n in nals)
        conn = self.conn
        #with e2ee on the whole frame goes through dave first
        if conn.dave_protocol_version:
            if conn.dave_session is None or not conn.can_encrypt:
                self.waiting += 1  #e2ee not ready yet
                return
            if not self.e2eeReady:
                self.e2eeReady = True
                log.info("go live: e2ee ready after %.1fs, sending video", time.monotonic() - self.started)
            frame = b"".join(b"\x00\x00\x00\x01" + n for n in nals)
            try:
                frame = conn.dave_session.encrypt(davey.MediaType.video, davey.Codec.h264, frame)
            except BaseException as e:  #a rust panic in davey is a BaseException and would kill the capture thread
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                self.encryptFails += 1
                if self.encryptFails in (1, 100):
                    log.warning("DAVE video encrypt failed (%s so far): %r", self.encryptFails, e)
                return
            nals = splitAnnexb(frame)
        ts = int((time.monotonic() - self.t0) * CLOCK) & 0xFFFFFFFF
        self.lastTs = ts
        #small nals go as is big ones get split into fu-a fragments
        payloads = native.fuaPayloads(nals, MTU_PAYLOAD)
        absSend = (int((time.time() % 64) * (1 << 18)) & 0xFFFFFF).to_bytes(3, "big")
        ext = _extPayload(((3, absSend), (6, b"\x00\x00\x00"), (11, b"100")))
        with self._lock:
            for i, p in enumerate(payloads):
                marker = 0x80 if i == len(payloads) - 1 else 0
                header = (
                    struct.pack(">BBHII", 0x90, marker | H264_PT, self.seq, ts, self.ssrc)
                    + b"\xbe\xde"
                    + struct.pack(">H", len(ext) // 4)
                )
                self.seq = (self.seq + 1) & 0xFFFF
                pkt = self._encrypt(header, ext + p)
                try:
                    conn.send_packet(pkt)
                except OSError:
                    return
                self.packets += 1
                self.bytes += len(pkt)
                if i and i % 16 == 0:
                    time.sleep(0.0005)  #pace keyframes a bit
        self.frames += 1
        self.keyframes += key
        if self.frames % 300 == 1:
            log.info("go live: %s", self.stats())

    def stats(self) -> str:
        return (
            f"frames {self.frames} (key {self.keyframes}), packets {self.packets}, {self.bytes // 1024} KiB, "
            f"waiting-for-e2ee {self.waiting}"
        )

    #sender report every 5s so the server can sync and measure us
    def _rtcpLoop(self) -> None:
        n = 0
        while not self._stop.wait(5):
            n += 1
            if n <= 6 or n % 12 == 0:  #every 5s for the first 30s then every minute
                c = self.conn
                log.info("go live: captured %s sent %s held for e2ee %s encrypt fails %s · connected %s dave v%s "
                         "session %s ready %s", self.offered, self.frames, self.waiting, self.encryptFails,
                         c.is_connected(), c.dave_protocol_version,
                         c.dave_session is not None, bool(c.dave_session is not None and c.dave_session.ready))
            if not self.conn.is_connected():
                continue
            now = time.time() + 2208988800
            ntpHi, ntpLo = int(now), int((now % 1) * (1 << 32))
            with self._lock:
                header = struct.pack(">BBHI", 0x80, 200, 6, self.ssrc)
                body = struct.pack(
                    ">IIIII", ntpHi, ntpLo, self.lastTs, self.packets & 0xFFFFFFFF, self.bytes & 0xFFFFFFFF
                )
                try:
                    self.conn.send_packet(self._encrypt(header, body))
                except OSError:
                    pass


#watching streams

RTCP_RTPFB, RTCP_PSFB = 205, 206


#true if sequence number a comes after b allowing for 16 bit wraparound
def _seqAfter(a: int, b: int) -> bool:
    return 0 < ((a - b) & 0xFFFF) < 0x8000


#puts packets for one video ssrc back in order and hands out whole frames
#asks for resends (nack) when a packet goes missing and a keyframe (pli) if it stays missing
class _Assembler:

    GAP_TIMEOUT = 0.35
    NACK_DELAY = 0.015
    NACK_RETRY = 0.08
    MAX_PKTS = 3000

    def __init__(self, codec, onFrame, requestKeyframe, nack) -> None:
        self.codec = codec
        self.onFrame = onFrame
        self.requestKeyframe = requestKeyframe
        self.nack = nack
        self.pkts: dict = {}
        self.nextSeq: Optional[int] = None
        self.needKey = True
        self.gapSince = 0.0
        self.nacked: dict = {}
        self.lostFrames = 0

    def add(self, seq: int, ts: int, marker: bool, payload: bytes) -> None:
        if self.nextSeq is not None and not (seq == self.nextSeq or _seqAfter(seq, self.nextSeq)):
            return
        if seq in self.pkts:
            return
        self.pkts[seq] = (ts, marker, payload)
        if len(self.pkts) > self.MAX_PKTS:
            self._resync()
        self._drain()

    def _resync(self) -> None:
        markers = [s for s, (_t, m, _p) in self.pkts.items() if m]
        self.lostFrames += 1
        self.needKey = True
        self.requestKeyframe()
        self.gapSince = 0.0
        self.nacked.clear()
        if not markers:
            self.pkts.clear()
            self.nextSeq = None
            return
        ref = self.nextSeq if self.nextSeq is not None else min(self.pkts)
        newest = max(markers, key=lambda s: (s - ref) & 0xFFFF)
        self.nextSeq = (newest + 1) & 0xFFFF
        self.pkts = {s: v for s, v in self.pkts.items() if _seqAfter(s, newest)}

    #hand out every complete frame starting at nextSeq
    #a frame is all packets with the same timestamp ending at one with the marker bit
    def _drain(self) -> None:
        while self.pkts:
            if self.nextSeq is None:
                #first contact start at a keyframe if we caught one
                lo = min(self.pkts, key=lambda q: (q - next(iter(self.pkts)) + 0x8000) & 0xFFFF)
                try:
                    starts = self.codec.isFrameStart(self.pkts[lo][2])
                except Exception:
                    starts = False
                if starts:
                    self.nextSeq = lo
                    continue
                #otherwise after the first frame end
                markers = [s for s, (_t, m, _p) in self.pkts.items() if m]
                if not markers:
                    return
                m = markers[0]
                self.nextSeq = (m + 1) & 0xFFFF
                self.pkts = {s: v for s, v in self.pkts.items() if _seqAfter(s, m)}
                continue
            s = self.nextSeq
            first = self.pkts.get(s)
            payloads, end = [], None
            if first is not None:
                ts0 = first[0]
                cur = s
                while True:
                    pk = self.pkts.get(cur)
                    if pk is None or pk[0] != ts0:
                        break
                    payloads.append(pk[2])
                    if pk[1]:
                        end = cur
                        break
                    cur = (cur + 1) & 0xFFFF
            if end is None:
                self._waiting()
                return
            for k in range(len(payloads)):
                self.pkts.pop((s + k) & 0xFFFF, None)
            self.nextSeq = (end + 1) & 0xFFFF
            self.gapSince = 0.0
            self.nacked.clear()
            try:
                frame, key = self.codec.assemble(payloads)
            except Exception:
                log.debug("watch: %s depacketize failed", self.codec.name, exc_info=True)
                frame, key = None, False
            if not frame:
                self.needKey = True
                continue
            if self.needKey and not key:
                self.requestKeyframe()
                continue
            self.needKey = False
            self.onFrame(frame, key)

    #a hole in the sequence nack it after a moment then give up and resync
    def _waiting(self) -> None:
        now = time.monotonic()
        if not self.gapSince:
            self.gapSince = now
        if now - self.gapSince > self.GAP_TIMEOUT:
            self._resync()
            self._drain()
            return
        if now - self.gapSince < self.NACK_DELAY:
            return  #probably just reordered
        newest = max(self.pkts, key=lambda q: (q - self.nextSeq) & 0xFFFF)
        span = (newest - self.nextSeq) & 0xFFFF
        if span > 512:
            return
        missing = []
        for k in range(span):
            q = (self.nextSeq + k) & 0xFFFF
            if q in self.pkts:
                continue
            sent = self.nacked.get(q)
            if sent is None or (now - sent[0] > self.NACK_RETRY and sent[1] < 3):  #rtx gets lost too
                self.nacked[q] = (now, (sent[1] + 1) if sent else 1)
                missing.append(q)
        if missing:
            self.nack(missing)


class _VideoReceiver:
    #rtp video receive shared by StreamWatch (go live) and CameraWatch (webcams in the call)
    #the class using it sets conn owner_id stats asm highestSeq codecName unknownPts audioSsrcs _lastPli
    #and the onFrame onCodec onAudio callbacks

    #receiver report plus remb bandwidth estimate without these some servers send nothing
    def _sendFeedback(self) -> None:
        #without feedback the server may not send video
        conn = self.conn
        if conn is None or not conn.is_connected() or conn.ssrc is discord.utils.MISSING:
            return
        blocks = b""
        for ssrc, (cycles, seq, _n) in list(self.highestSeq.items())[:8]:
            blocks += struct.pack(">IIIIII", ssrc, 0, (cycles << 16) | seq, 0, 0, 0)
        rc = len(blocks) // 24
        media = sorted(s for s in self.highestSeq if s in conn.video_ssrcs) or sorted(
            s for s, u in conn.ssrc_user_ids.items() if u == self.owner_id and s in conn.video_ssrcs
        )
        bps = 40_000_000
        exp = 0
        while bps >= (1 << 18):
            bps >>= 1
            exp += 1
        remb = (
            b"REMB"
            + bytes((len(media),))
            + struct.pack(">I", (exp << 18) | bps)[1:]
            + b"".join(struct.pack(">I", s) for s in media)
        )
        rembPkt = struct.pack(">BBHII", 0x8F, RTCP_PSFB, (12 + len(remb)) // 4 - 1, conn.ssrc, 0) + remb
        header = struct.pack(">BBHI", 0x80 | rc, 201, (8 + len(blocks)) // 4 - 1, conn.ssrc)
        self._sendRtcp(header, blocks + rembPkt)

    def _box(self):
        key = bytes(self.conn.secret_key)
        if self._aeadBox is None or key != self._aeadKey:
            self._aeadBox, self._aeadKey = nacl.secret.Aead(key), key
        return self._aeadBox

    def _seal(self, header: bytes, body: bytes) -> bytes:
        nonce = struct.pack(">I", self._nonce)
        self._nonce = (self._nonce + 1) & 0xFFFFFFFF
        return header + self._box().encrypt(body, header, nonce + b"\x00" * 20).ciphertext + nonce

    def _sendRtcp(self, header: bytes, body: bytes) -> None:
        conn = self.conn
        if conn is None or not conn.is_connected() or conn.secret_key is discord.utils.MISSING:
            return
        try:
            conn.send_packet(self._seal(header, body))
        except Exception:
            log.debug("rtcp send", exc_info=True)

    def _wants(self, ssrc: int, pt: int) -> bool:
        return True

    def _videoSsrc(self) -> Optional[int]:
        conn = self.conn
        if conn is None:
            return None
        for s in self.asm:
            return s
        for s in conn.video_ssrcs:
            if s not in conn.rtx_ssrc_media_ssrcs and conn.ssrc_user_ids.get(s) == self.owner_id:
                return s
        return None

    #pli picture loss indication rate limited to one per 500ms
    def requestKeyframe(self) -> None:
        now = time.monotonic()
        media = self._videoSsrc()
        if media is None or now - self._lastPli < 0.5 or self.conn is None:
            return
        self._lastPli = now
        self.stats["pli"] += 1
        self._sendRtcp(struct.pack(">BBHI", 0x81, RTCP_PSFB, 2, self.conn.ssrc), struct.pack(">I", media))

    #generic nack each entry is a seq plus a 16 bit mask of the ones after it
    def _nack(self, media: int, seqs: List[int]) -> None:
        if self.conn is None or not seqs:
            return
        seqs = sorted(seqs, key=lambda s: (s - seqs[0]) & 0xFFFF)
        fci = bytearray()
        i = 0
        while i < len(seqs) and len(fci) < 4 * 16:
            pid, blp = seqs[i], 0
            i += 1
            while i < len(seqs) and 0 < ((seqs[i] - pid) & 0xFFFF) <= 16:
                blp |= 1 << (((seqs[i] - pid) & 0xFFFF) - 1)
                i += 1
            fci += struct.pack(">HH", pid, blp)
        self.stats["nack"] += 1
        words = 2 + len(fci) // 4
        self._sendRtcp(
            struct.pack(">BBHI", 0x81, RTCP_RTPFB, words, self.conn.ssrc),
            struct.pack(">I", media) + bytes(fci),
        )

    #same transport decrypt as voice then route by payload type
    #rtx resends carry the original seq in their first 2 bytes
    def _onPacket(self, data: bytes) -> None:
        conn = self.conn
        head = native.rtpHeader(data) if conn is not None else None
        if head is None or 200 <= data[1] <= 206:
            return
        hlen, pt, marker, seq, ts, ssrc = head
        if ssrc == conn.ssrc or not self._wants(ssrc, pt):
            return
        self.stats["rx"] += 1
        try:
            hasExt = bool(data[0] & 0x10)
            header = data[:hlen]
            plain = self._box().decrypt(data[hlen:-4], header, data[-4:] + b"\x00" * 20)
            if hasExt:
                plain = plain[struct.unpack_from(">H", data, hlen - 2)[0] * 4 :]
            if data[0] & 0x20 and plain:  #rtp padding
                plain = plain[: -plain[-1]] if plain[-1] <= len(plain) else b""
        except Exception:
            self.stats["transport_fail"] += 1
            return
        if not plain:
            return
        hs = self.highestSeq.get(ssrc)
        if hs is None:
            self.highestSeq[ssrc] = (0, seq, 1)
        elif _seqAfter(seq, hs[1]):
            self.highestSeq[ssrc] = (hs[0] + (1 if seq < hs[1] else 0), seq, hs[2] + 1)
        rtxMedia = conn.rtx_ssrc_media_ssrcs.get(ssrc)
        if rtxMedia is not None or pt in rtpvideo.RTX_PT:
            if len(plain) < 3:
                return
            self.stats["rtx"] += 1
            media = rtxMedia if rtxMedia is not None else self._videoSsrc()
            asm = self.asm.get(media) if media is not None else None
            if asm is not None:
                asm.add(struct.unpack_from(">H", plain)[0], ts, marker, plain[2:])
            return
        if pt == 120:
            self.stats["audio"] += 1
            self.audioSsrcs.add(ssrc)
            if self.onAudio is not None:
                op = self._dave(conn, ssrc, plain, davey.MediaType.audio if davey else None)
                if op is not None:
                    self.onAudio(ssrc, op)
            return
        name = rtpvideo.PT_CODEC.get(pt)
        if name is None and ssrc in conn.video_ssrcs:
            name = (conn.video_codec or "H264").upper()
        if name in rtpvideo.CODECS:
            self.stats["video"] += 1
            asm = self.asm.get(ssrc)
            if asm is None or asm.codec.name != name:
                codec = rtpvideo.CODECS[name]
                asm = self.asm[ssrc] = _Assembler(
                    codec,
                    lambda f, key, s=ssrc, c=codec: self._frame(s, c, f, key),
                    self.requestKeyframe,
                    lambda seqs, s=ssrc: self._nack(s, seqs),
                )
                log.info("watch: video ssrc=%s pt=%s codec=%s", ssrc, pt, name)
                if self.codecName != name:
                    self.codecName = name
                    if self.onCodec is not None:
                        self.onCodec(codec)
            asm.add(seq, ts, marker, plain)
            return
        self.stats["unknown"] += 1
        if pt not in self.unknownPts:
            self.unknownPts.add(pt)
            log.info(
                "watch: packet with unexpected payload type %s (ssrc %s, video ssrcs %s, codec %s)",
                pt,
                ssrc,
                sorted(conn.video_ssrcs),
                conn.video_codec,
            )

    #dave decrypt if the payload has the dave trailer (ends fa fa) otherwise its plain
    def _dave(self, conn, ssrc: int, payload: bytes, mediaType, quiet: bool = False) -> Optional[bytes]:
        if not conn.dave_protocol_version or conn.dave_session is None or payload[-2:] != b"\xfa\xfa":
            if conn.dave_protocol_version and payload[-2:] != b"\xfa\xfa":
                self.stats["passthrough"] += 1
            return payload
        uid = conn.ssrc_user_ids.get(ssrc) or self.owner_id
        try:
            return conn.dave_session.decrypt(uid, mediaType, payload)
        except Exception:
            if quiet:
                return None
            self.stats["dave_fail"] += 1
            if self.stats["dave_fail"] in (1, 10, 100) or self.stats["dave_fail"] % 1000 == 0:
                log.warning(
                    "watch: DAVE decrypt failed (%s so far, can_encrypt=%s)",
                    self.stats["dave_fail"],
                    getattr(conn, "can_encrypt", "?"),
                    exc_info=self.stats["dave_fail"] == 1,
                )
            return None

    #a whole frame came out of the assembler decrypt it and pass it on
    #some codecs have more than one layout to try see rtpvideo candidates
    def _frame(self, ssrc: int, codec, frame: bytes, key: bool) -> None:
        conn = self.conn
        if conn is None:
            return
        out = None
        mt = davey.MediaType.video if davey else None
        cands = codec.candidates(frame)
        for i, cand in enumerate(cands):
            out = self._dave(conn, ssrc, cand, mt, quiet=i < len(cands) - 1)
            if out is not None:
                break
        if out is None:
            self.stats["video_dave_fail"] += 1
            asm = self.asm.get(ssrc)
            if asm is not None:
                asm.needKey = True
            self.requestKeyframe()
            return
        try:
            out = codec.finish(out)
        except Exception:
            log.debug("watch: finish frame", exc_info=True)
        self.stats["frames"] += 1
        self.stats["keyframes"] += key
        if self.stats["frames"] == 1:
            log.info("watch: first %s frame (%d bytes, key=%s)", codec.name, len(out), key)
        elif self.stats["frames"] % 600 == 0:
            log.info("watch: %s", self.statsText())
        if self.onFrame is not None:
            self.onFrame(out, key)


#watching someone elses stream
#connects to their stream server asks for their video and passes frames and audio to callbacks
class StreamWatch(StreamProtocol, _VideoReceiver):

    supported_modes = ("aead_xchacha20_poly1305_rtpsize",)

    def __init__(self, voice_client, stream) -> None:
        super().__init__(voice_client, stream)
        self.channel = voice_client.channel
        self.guild = getattr(voice_client, "guild", None)
        self.loop = voice_client.loop
        self._state = voice_client._state
        self.user = voice_client.client.user
        self.owner_id = stream.key.owner_id
        self.serverData: Optional[dict] = None
        self._server = asyncio.Event()
        self.conn: Optional[_StreamConnection] = None
        self.onFrame = None
        self.onAudio = None
        self.onEnd = None
        self.stats: dict = {
            "rx": 0,
            "video": 0,
            "rtx": 0,
            "audio": 0,
            "frames": 0,
            "keyframes": 0,
            "transport_fail": 0,
            "dave_fail": 0,
            "passthrough": 0,
            "unknown": 0,
            "pli": 0,
            "nack": 0,
            "video_dave_fail": 0,
        }
        self.unknownPts: set = set()
        self.audioSsrcs: set = set()
        self.codecName: Optional[str] = None
        self.onCodec = None
        self.highestSeq: dict = {}
        self.asm: dict = {}
        self._aeadBox = None
        self._aeadKey = b""
        self._nonce = 0
        self._lastPli = 0.0
        self._closed = False

    def supports_video(self) -> bool:
        return True

    def stop(self) -> None:
        pass  #discord.py calls this when the stream socket closes

    def get_experiments(self, ready_experiments):
        return ()

    @property
    def codecs(self):
        return (VoiceCodec.opus(),) + tuple(
            VoiceCodec.video(
                name, priority=prio, payload_type=pt, rtx_payload_type=rtx, encode=False, decode=True
            )
            for name, prio, pt, rtx in rtpvideo.OFFER
        )

    @property
    def video_streams(self):
        return (VoiceStream.screen(rid="100", quality=100),)

    @property
    def ws(self):
        return self.conn.ws if self.conn is not None else None

    async def on_stream_server_update(self, data, /) -> None:
        self.serverData = data
        self._server.set()

    async def on_stream_delete(self, stream, reason, /) -> None:
        log.info("watched stream deleted: %s", reason)
        await self.disconnect(force=True, notify=f"Stream ended ({getattr(reason, 'name', reason)}).")

    async def connect(self, *, timeout: float, reconnect: bool) -> None:
        await asyncio.wait_for(self._server.wait(), timeout)
        self.conn = _StreamConnection(self, hook=self._wsHook)
        try:
            await self.conn.connect(
                reconnect=False,
                timeout=timeout,
                self_deaf=False,
                self_mute=True,
                self_video=False,
                resume=False,
            )
        except BaseException:
            onEnd, self.onEnd = self.onEnd, None
            await self.disconnect(force=True)
            self.onEnd = onEnd
            raise
        streams = [s for s in self.conn.video_streams.values()]
        for s in streams:
            s.active = False
        self.conn.add_socket_listener(self._onPacket)
        try:
            await self.conn.ws.video_state(video_ssrc=0, rtx_ssrc=0, streams=streams)
            await self.conn.ws.media_sink_wants({}, any=100)
        except Exception:
            log.warning("watch: video_state/media_sink_wants failed", exc_info=True)
        from .voice import udpKeepalive

        self._kaStop = threading.Event()
        threading.Thread(
            target=udpKeepalive, args=(self.conn, self._kaStop), daemon=True, name="uncord-watch-keepalive"
        ).start()
        log.info(
            "watching stream %s: endpoint=%s ssrc=%s dave=%s group=%s mode=%s video_codec=%s",
            self.stream.key,
            self.conn.endpoint,
            self.conn.ssrc,
            self.conn.dave_protocol_version,
            self.conn.dave_group_id,
            self.conn.mode,
            self.conn.video_codec,
        )
        self._sinkTask = asyncio.ensure_future(self._sinkLoop())  #keep a ref or it can get collected

    #once a second ask for the streamers ssrcs send receiver feedback and nag for a keyframe until one arrives
    async def _sinkLoop(self) -> None:
        asked = set()
        while self.conn is not None and not self._closed:
            conn = self.conn
            ssrcs = {s for s, u in conn.ssrc_user_ids.items() if u == self.owner_id}
            want = ssrcs - asked
            if want:
                asked |= want
                try:
                    await conn.ws.media_sink_wants({s: 100 for s in asked}, any=100)
                    log.info(
                        "watch: streamer ssrcs %s (video %s, rtx %s)",
                        sorted(asked),
                        sorted(conn.video_ssrcs),
                        conn.rtx_ssrc_media_ssrcs,
                    )
                except Exception:
                    log.debug("media_sink_wants", exc_info=True)
            if time.monotonic() - getattr(self, "_lastStatlog", 0) > 10 and self.stats["rx"]:
                self._lastStatlog = time.monotonic()
                log.info("watch: %s · audio ssrcs seen %s", self.statsText(), sorted(self.audioSsrcs))
            if self.stats["frames"] == 0:
                self.requestKeyframe()
            self._sendFeedback()
            await asyncio.sleep(1.0)

    async def _wsHook(self, ws, msg) -> None:
        op = msg.get("op")
        if op in (12, 14, 15, 16, 18, 20):
            d = msg.get("d")
            log.info("watch: server op %s %s", op, str(d)[:600])

    async def disconnect(self, *, force: bool = True, notify: Optional[str] = None) -> None:
        if self._closed:
            return
        self._closed = True
        if getattr(self, "_kaStop", None) is not None:
            self._kaStop.set()
        conn, self.conn = self.conn, None
        if conn is not None:
            try:
                conn.remove_socket_listener(self._onPacket)
            except Exception:
                pass
            try:
                await conn.disconnect(force=True, cleanup=False)
            except Exception:
                log.debug("watch conn disconnect", exc_info=True)
            try:
                conn._stop_socket_reader()
            except Exception:
                pass
        try:
            await self.stream.delete()
        except Exception:
            pass
        try:
            self.cleanup()
        except Exception:
            pass
        if self.onEnd is not None:
            cb, self.onEnd = self.onEnd, None
            cb(notify)

    def statsText(self) -> str:
        s = self.stats
        conn = self.conn
        codec = self.codecName or (conn.video_codec if conn else "?")
        extra = f" · unknown PTs {sorted(self.unknownPts)}" if self.unknownPts else ""
        lost = sum(a.lostFrames for a in self.asm.values())
        return (
            f"{codec} · {s['frames']} frames ({s['keyframes']} key) · {s['video']} video pkts · {s['rtx']} rtx · "
            f"{s['audio']} audio · lost {lost} · dave fail {s['dave_fail']} · transport fail {s['transport_fail']}"
            f"{extra}"
        )



#your webcam in the call
#same capture and sender as screen sharing but on the call connection instead of a stream server
class CameraShare:

    def __init__(self, vc, device: str, width: int, height: int, fps: int = 30, kbps: int = 1500) -> None:
        self.vc = vc
        self.device, self.width, self.height, self.fps, self.kbps = device, width, height, fps, kbps
        self.sender: Optional[_Sender] = None
        self.capture: Optional[_Capture] = None
        self.stream = None
        self.onEnd = None

    async def start(self) -> None:
        conn = self.vc._connection
        streams = [st for st in conn.video_streams.values() if st.ssrc]
        if not streams:
            raise RuntimeError("the voice server gave us no video ssrc")
        vs = self.stream = streams[0]
        vs.active = True
        vs.max_bitrate = self.kbps * 1000
        vs.max_framerate = self.fps
        vs.max_resolution = VoiceStreamResolution(type="fixed", width=self.width, height=self.height)
        await conn.ws.video_state(video_ssrc=vs.ssrc, rtx_ssrc=vs.rtx_ssrc or 0, streams=[vs])
        self.sender = _Sender(conn, vs.ssrc, self.fps, seal=self.vc.sealRtp)
        self.capture = _Capture(self.device, self.width, self.height, self.fps, self.kbps, self.sender.feed,
                                onExit=self._died, cursor=False)
        self.sender.start()
        self.capture.start()
        log.info("camera on: %s %sx%s@%s ssrc=%s", self.device, self.width, self.height, self.fps, vs.ssrc)

    def _died(self, why: str) -> None:
        log.error("camera capture stopped: %s", why)
        if self.onEnd is not None:
            self.vc.loop.call_soon_threadsafe(self.onEnd, f"Camera stopped: {why}")

    async def stop(self) -> None:
        if self.sender is not None:
            self.sender.stop()
        if self.capture is not None:
            self.capture.stop()
        vs, conn = self.stream, self.vc._connection
        if vs is not None and conn.is_connected():
            vs.active = False
            try:
                await conn.ws.video_state(video_ssrc=0, rtx_ssrc=0, streams=[vs])
            except Exception:
                log.debug("camera off video_state", exc_info=True)
        log.info("camera off")

    def statsText(self) -> str:
        s = self.sender
        return "camera off" if s is None else f"camera {s.stats()}"


#someone elses webcam in the call
#video comes over the call connection so we just listen there for that users video ssrcs
class CameraWatch(_VideoReceiver):

    hasAudio = False

    def __init__(self, vc, uid: int) -> None:
        self.vc = vc
        self.conn = vc._connection
        self.owner_id = uid
        self.stats: dict = {"rx": 0, "video": 0, "rtx": 0, "audio": 0, "frames": 0, "keyframes": 0,
                            "transport_fail": 0, "dave_fail": 0, "passthrough": 0, "unknown": 0, "pli": 0,
                            "nack": 0, "video_dave_fail": 0}
        self.unknownPts: set = set()
        self.audioSsrcs: set = set()
        self.codecName: Optional[str] = None
        self.highestSeq: dict = {}
        self.asm: dict = {}
        self.onFrame = self.onCodec = self.onAudio = self.onEnd = None
        self._lastPli = 0.0
        self._closed = False
        self._task = None
        self._aeadBox = None
        self._aeadKey = b""

    def _wants(self, ssrc: int, pt: int) -> bool:
        if pt == 120:
            return False
        conn = self.conn
        media = conn.rtx_ssrc_media_ssrcs.get(ssrc, ssrc)
        return conn.ssrc_user_ids.get(media) == self.owner_id

    def _seal(self, header: bytes, body: bytes) -> bytes:
        return self.vc.sealRtp(header, body)

    def ownerSsrcs(self) -> set:
        conn = self.conn
        return {s for s in conn.video_ssrcs if conn.ssrc_user_ids.get(s) == self.owner_id}

    async def start(self) -> None:
        self.conn.add_socket_listener(self._onPacket)
        self._task = asyncio.ensure_future(self._loop())
        log.info("watching camera of %s", self.owner_id)

    async def _loop(self) -> None:
        asked = set()
        while not self._closed and self.conn.is_connected():
            want = self.ownerSsrcs()
            if want != asked:
                asked = want
                try:
                    await self.vc.setVideoWants({s: 100 for s in want})
                except Exception:
                    log.debug("camera media_sink_wants", exc_info=True)
            if self.stats["frames"] == 0:
                self.requestKeyframe()
            self._sendFeedback()
            await asyncio.sleep(1.0)
        if not self._closed and self.onEnd is not None:
            cb, self.onEnd = self.onEnd, None
            cb("The call ended.")

    async def disconnect(self, *, force: bool = True, notify: Optional[str] = None) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.conn.remove_socket_listener(self._onPacket)
        except Exception:
            pass
        try:
            await self.vc.setVideoWants({})
        except Exception:
            pass
        if self.onEnd is not None:
            cb, self.onEnd = self.onEnd, None
            cb(notify)

    def statsText(self) -> str:
        s = self.stats
        lost = sum(a.lostFrames for a in self.asm.values())
        return (f"{self.codecName or '?'} camera · {s['frames']} frames ({s['keyframes']} key) · "
                f"{s['video']} video pkts · lost {lost} · dave fail {s['dave_fail']}")


#feeds frames into ffmpeg which scales them to the viewer size as raw rgb
#keeps the current gop so a restarted ffmpeg or mpv can start from the last keyframe
class StreamDecoder:

    MAX_GOP = 900

    def __init__(self, requestKeyframe=None) -> None:
        self.requestKeyframe = requestKeyframe
        self.codec = rtpvideo.CODECS["H264"]
        self.t0 = time.monotonic()
        self.size = (0, 0)
        self.gop: List[Tuple[bytes, int]] = []
        self.lock = threading.Lock()
        self.latest: Optional[bytes] = None
        self.latestSize = (0, 0)
        self.seq = 0
        self.newFrame = threading.Event()
        self.ff: Optional[subprocess.Popen] = None
        self.mpv: Optional[subprocess.Popen] = None
        self.sourceSize: Optional[Tuple[int, int]] = None
        self.decoded = 0
        self._closed = False
        self._mpvTitle = "uncord"

    def setCodec(self, codec) -> None:
        if codec.name == self.codec.name:
            return
        log.info("watch: decoding %s", codec.name)
        with self.lock:
            self.codec = codec
            self.gop = []
            size, hadMpv = self.size, self.popoutOpen()
            self.size = (0, 0)
        if size != (0, 0):
            self.resize(*size)
        if hadMpv:
            title = self._mpvTitle
            self.closePopout()
            self.popout(title)

    def _wrap(self, frame: bytes, pts: int) -> bytes:
        return rtpvideo.ivfFrame(frame, pts) if self.codec.ivfFourcc else frame

    def _startBytes(self) -> bytes:
        return rtpvideo.ivfHeader(self.codec.ivfFourcc) if self.codec.ivfFourcc else b""

    def feed(self, frame: bytes, key: bool) -> None:
        pts = int((time.monotonic() - self.t0) * 1000)
        with self.lock:
            if key:
                self.gop = []
            if len(self.gop) < self.MAX_GOP:
                self.gop.append((frame, pts))
            elif self.requestKeyframe:
                self.requestKeyframe()
            data = self._wrap(frame, pts)
            for p in (self.ff, self.mpv):
                if p is not None:
                    self._write(p, data)

    @staticmethod
    def _write(p: subprocess.Popen, data: bytes) -> None:
        try:
            p.stdin.write(data)
            p.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass

    #(re)start ffmpeg for a new output size and replay the gop into it
    def resize(self, width: int, height: int) -> None:
        if not FFMPEG:
            return
        width, height = max(2, width - width % 2), max(2, height - height % 2)
        if (width, height) == self.size and self.ff is not None and self.ff.poll() is None:
            return
        old = self.ff
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=bilinear,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
        #no -fflags nobuffer it drops every frame
        cmd = [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-flags",
            "low_delay",
            "-threads",
            "4",
            "-thread_type",
            "slice",
            "-probesize",
            "32768",
            "-analyzeduration",
            "0",
            "-f",
            self.codec.ffFormat,
            "-i",
            "-",
            "-vf",
            vf,
            "-fps_mode",
            "passthrough",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ]
        ff = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
        )
        threading.Thread(
            target=self._read, args=(ff, width, height), daemon=True, name="uncord-watch-dec"
        ).start()
        threading.Thread(target=self._errlog, args=(ff,), daemon=True, name="uncord-watch-err").start()
        with self.lock:
            self.size = (width, height)
            self.ff = ff
            self._write(ff, self._startBytes() + b"".join(self._wrap(f, t) for f, t in self.gop))
        if old is not None:
            self._kill(old)

    def stopDecode(self) -> None:
        with self.lock:
            ff, self.ff = self.ff, None
            self.size = (0, 0)
            self.latest = None
        if ff is not None:
            self._kill(ff)

    def _read(self, ff: subprocess.Popen, width: int, height: int) -> None:
        need = width * height * 3
        out = ff.stdout
        while not self._closed:
            buf = bytearray()
            while len(buf) < need:
                chunk = out.read(need - len(buf))
                if not chunk:
                    return
                buf += chunk
            if ff is not self.ff:
                return
            self.latest = bytes(buf)
            self.latestSize = (width, height)
            self.seq += 1
            self.decoded += 1
            self.newFrame.set()

    @staticmethod
    def _errlog(p: subprocess.Popen) -> None:
        for line in p.stderr:
            line = line.decode(errors="replace").strip()
            if line:
                log.debug("watch ffmpeg: %s", line)

    #same stream into an mpv window at full quality
    def popout(self, title: str) -> bool:
        mpv = MPV
        if not mpv:
            return False
        if self.mpv is not None and self.mpv.poll() is None:
            return True
        self._mpvTitle = title
        p = subprocess.Popen(
            [
                mpv,
                "--profile=low-latency",
                "--untimed",
                "--no-cache",
                f"--demuxer-lavf-format={self.codec.ffFormat}",
                "--demuxer-lavf-probesize=32",
                "--demuxer-lavf-analyzeduration=0",
                "--no-audio",
                "--hwdec=auto-safe",
                f"--title={title}",
                "--force-window=immediate",
                "--keep-open=no",
                "--osc=no",
                "-",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        with self.lock:
            self.mpv = p
            self._write(p, self._startBytes() + b"".join(self._wrap(f, t) for f, t in self.gop))
        return True

    def popoutOpen(self) -> bool:
        return self.mpv is not None and self.mpv.poll() is None

    def closePopout(self) -> None:
        p, self.mpv = self.mpv, None
        if p is not None:
            self._kill(p)

    @staticmethod
    def _kill(p: subprocess.Popen) -> None:
        try:
            p.stdin.close()
        except Exception:
            pass
        if p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass

    def close(self) -> None:
        self._closed = True
        self.newFrame.set()
        ff, self.ff = self.ff, None
        if ff is not None:
            self._kill(ff)
        self.closePopout()
