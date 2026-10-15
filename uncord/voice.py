#voice calls mic in speaker out and our own rtp receive since discord.py-self only sends
#audio goes through pw-record and pw-play
from __future__ import annotations

import asyncio
import collections
import os
import shutil
import struct
import subprocess
import threading
import time
from typing import Callable, Dict, Optional

import logging
import math

import discord
import nacl.secret
from discord import opus
from discord.voice_state import VoiceConnectionState

from . import _dsp, _rtp

try:
    import davey
except ImportError:
    davey = None

log = logging.getLogger("uncord.voice")

FRAME_MS = 20
RATE = 48000
CHANNELS = 2
FRAME_BYTES = opus.Decoder.FRAME_SIZE
SILENCE_PCM = b"\x00" * FRAME_BYTES

OP_SPEAKING = 5
OP_CLIENT_DISCONNECT = 13


def load_opus() -> bool:
    if opus.is_loaded():
        return True
    from .deps import OPUS

    if not OPUS:
        return False
    try:
        opus.load_opus(OPUS)
        return True
    except OSError:
        return False


def _pwArgs(tool: str, name: str, latency: str, target: Optional[str] = None) -> list:
    #pw-cat has no --media-name use -P
    args = [
        tool,
        "--raw",
        "--format",
        "s16",
        "--rate",
        str(RATE),
        "--channels",
        str(CHANNELS),
        "--latency",
        latency,
        "-P",
        '{ media.name = "%s" node.description = "%s" application.name = "uncord" }' % (name, name),
    ]
    if target:
        args += ["--target", target]
    return args + ["-"]


def _watch(proc: subprocess.Popen, what: str, owner=None) -> None:
    def run():
        err = proc.stderr.read() if proc.stderr else b""
        code = proc.wait()
        if owner is not None and getattr(owner, "_stop", False):
            return
        if code not in (0, -15):
            log.error("%s exited with %s: %s", what, code, err.decode(errors="replace").strip()[:300])

    threading.Thread(target=run, daemon=True, name=f"uncord-watch-{what}").start()


def rmsToDb(rms: float) -> float:
    return -100.0 if rms <= 0 else max(-100.0, 20 * math.log10(rms / 32768))


def dbToRms(db: float) -> int:
    return int(32768 * 10 ** (db / 20))


def audioToolsOk() -> Optional[str]:
    for tool in ("pw-record", "pw-play"):
        if not shutil.which(tool):
            return f"{tool} not found (PipeWire tools needed for voice)"
    return None


#reads 20ms stereo frames from pw-record on a thread
class Mic:

    def __init__(self, target: Optional[str] = None, gain: float = 1.0) -> None:
        self._stop = False
        self.target = target
        self.gain = gain
        self.proc = subprocess.Popen(
            _pwArgs("pw-record", "uncord mic", "20ms", target),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _watch(self.proc, "pw-record", self)
        self.frames: collections.deque[bytes] = collections.deque(maxlen=4)
        self.framesRead = 0
        self.everSignal = False
        self.peakRms = 0
        self.level = 0
        self.cond = threading.Condition()
        threading.Thread(target=self._reader, daemon=True, name="uncord-mic").start()

    def _reader(self) -> None:
        out = self.proc.stdout
        assert out is not None
        while not self._stop:
            buf = out.read(FRAME_BYTES)
            if not buf or len(buf) < FRAME_BYTES:
                break
            self.framesRead += 1
            if self.gain != 1.0:
                buf = _dsp.gain(buf, self.gain)
            try:
                self.level = _dsp.rms(buf)
                self.peakRms = max(self.peakRms, self.level)
                if self.level:
                    self.everSignal = True
            except Exception:
                pass
            with self.cond:
                self.frames.append(buf)
                self.cond.notify()

    def get(self, timeout: float) -> Optional[bytes]:
        with self.cond:
            if not self.frames:
                self.cond.wait(timeout)
            return self.frames.popleft() if self.frames else None

    @property
    def silent(self) -> bool:
        return self.framesRead > 150 and not self.everSignal

    def flush(self) -> None:
        with self.cond:
            self.frames.clear()

    def close(self) -> None:
        self._stop = True
        try:
            self.proc.terminate()
        except Exception:
            pass


#mixes everyone into one pw-play stream
#each ssrc gets a small queue and waits for a few frames before playing so jitter doesnt crackle
class Speaker:

    MAX_QUEUE = 10
    PREBUFFER = 3

    def __init__(self, target: Optional[str] = None) -> None:
        self._stop = False
        self.target = target
        self.proc = subprocess.Popen(
            _pwArgs("pw-play", "uncord voice", "40ms", target), stdin=subprocess.PIPE, stderr=subprocess.PIPE
        )
        _watch(self.proc, "pw-play", self)
        self.framesPlayed = 0
        self.queues: Dict[int, collections.deque[bytes]] = {}
        self.started: Dict[int, bool] = {}
        self.lock = threading.Lock()
        self.volume = 1.0
        self.lastWrite = time.monotonic()
        self.dropped = 0
        self.thread = threading.Thread(target=self._run, daemon=True, name="uncord-speaker")
        self.thread.start()

    #pcm that isnt exactly 20ms gets padded and cut into 20ms frames
    def push(self, ssrc: int, pcm: bytes) -> None:
        if len(pcm) != FRAME_BYTES:
            if len(pcm) % FRAME_BYTES:
                pcm += b"\x00" * (FRAME_BYTES - len(pcm) % FRAME_BYTES)
            for i in range(0, len(pcm), FRAME_BYTES):
                self.push(ssrc, pcm[i : i + FRAME_BYTES])
            return
        with self.lock:
            q = self.queues.get(ssrc)
            if q is None:
                q = self.queues[ssrc] = collections.deque(maxlen=self.MAX_QUEUE)
                self.started[ssrc] = False
            q.append(pcm)

    def drop(self, ssrc: int) -> None:
        with self.lock:
            self.queues.pop(ssrc, None)
            self.started.pop(ssrc, None)

    def _mix(self) -> bytes:
        frames = []
        with self.lock:
            for ssrc, q in self.queues.items():
                if not self.started[ssrc]:
                    if len(q) < self.PREBUFFER:
                        continue
                    self.started[ssrc] = True
                if not q:
                    self.started[ssrc] = False
                    continue
                frame = q.popleft()
                self.framesPlayed += 1
                frames.append(frame)
        if not frames:
            return SILENCE_PCM
        if len(frames) == 1 and self.volume == 1.0:
            return frames[0]
        return _dsp.mix(frames, self.volume)

    #writes a mixed frame every 20ms nonblocking so a stuck pw-play cant freeze us
    def _run(self) -> None:
        stdin = self.proc.stdin
        assert stdin is not None
        fd = stdin.fileno()
        os.set_blocking(fd, False)
        pending = bytearray()
        cap = FRAME_BYTES * 8
        nxt = time.monotonic()
        while not self._stop:
            nxt += FRAME_MS / 1000
            pending += self._mix()
            if len(pending) > cap:
                excess = len(pending) - cap
                excess -= excess % 4
                del pending[:excess]
                self.dropped += excess // FRAME_BYTES
            try:
                n = os.write(fd, pending)
                if n:
                    del pending[:n]
                    self.lastWrite = time.monotonic()
            except BlockingIOError:
                pass
            except (BrokenPipeError, ValueError, OSError):
                if not self._stop:
                    log.error("speaker pipe closed; pw-play is gone")
                break
            delay = nxt - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.2:
                nxt = time.monotonic()

    def close(self) -> None:
        self._stop = True
        try:
            self.proc.terminate()
        except Exception:
            pass


#discord.VoiceClient plus receive mic gating agc and device switching
class UncordVoiceClient(discord.VoiceClient):

    supported_modes = ("aead_xchacha20_poly1305_rtpsize",)

    #video so we can see and send cameras, the server gives us a camera ssrc for the stream below
    def supports_video(self) -> bool:
        return True

    @property
    def codecs(self):
        from discord.voice_media import VoiceCodec
        from .rtpvideo import OFFER

        return (VoiceCodec.opus(),) + tuple(
            VoiceCodec.video(name, priority=prio, payload_type=pt, rtx_payload_type=rtx, encode=name == "H264",
                             decode=True)
            for name, prio, pt, rtx in OFFER)

    @property
    def video_streams(self):
        from discord.voice_media import VoiceStream, VoiceStreamResolution

        #off until you turn the camera on
        return (VoiceStream("video", rid="100", quality=100, active=False, max_bitrate=1_500_000, max_framerate=30,
                            max_resolution=VoiceStreamResolution(type="fixed", width=1280, height=720)),)

    #audio camera and rtcp all share the one nonce counter so lock it
    def _encrypt_aead_xchacha20_poly1305_rtpsize(self, header: bytes, data) -> bytes:
        with self._sealLock:
            return super()._encrypt_aead_xchacha20_poly1305_rtpsize(header, data)

    def sealRtp(self, header: bytes, payload: bytes) -> bytes:
        return self._encrypt_aead_xchacha20_poly1305_rtpsize(header, payload)

    #cameras we watch at full quality, ones we stop watching paused
    #never send any 0 because that default covers every stream and can pause call audio too
    async def setVideoWants(self, wants: dict) -> None:
        ws = self._connection.ws
        old = getattr(self, "_videoWanted", set())
        self._videoWanted = set(wants)
        data = {s: 0 for s in old - set(wants)}
        data.update(wants)
        if data and ws is not None and ws is not discord.utils.MISSING:
            await ws.media_sink_wants(data)

    def __init__(self, client: discord.Client, channel) -> None:
        self._sealLock = threading.Lock()
        super().__init__(client, channel)
        self.ssrcToUser: Dict[int, int] = {}
        self.decoders: Dict[int, opus.Decoder] = {}
        self.lastSeq: Dict[int, int] = {}
        self._aead = None
        self._aeadKey = b""
        self.lastHeard: Dict[int, float] = {}
        self.selfMuted = False
        self.selfDeafened = False
        self.vadThreshold = int(os.environ.get("UNCORD_VAD", "600"))
        self.autoSensitivity = True
        self.sensitivityDb = -50.0
        self.noiseFloorDb = -60.0
        self.levelDb = -100.0
        self.inputDevice: Optional[str] = None
        self.outputDevice: Optional[str] = None
        self.inputGain = 1.0
        #agc like discord
        self.outputStalled = False
        self.agc = True
        self.agcGain = 1.0
        self._agcLevel = 0.0
        self.onChange: Optional[Callable[[], None]] = None
        self.speaker: Optional[Speaker] = None
        self.mic: Optional[Mic] = None
        self._txStop = threading.Event()
        self._speaking = False
        self.rxErrors = 0
        self.stats: collections.Counter = collections.Counter()
        self.rxTypes: collections.Counter = collections.Counter()  #payload types that arrived, 120 is call audio
        self.userVolume: Dict[int, float] = {}
        self.level = 0

    def create_connection_state(self) -> VoiceConnectionState:
        return VoiceConnectionState(self, hook=self._wsHook)

    #voice gateway tells us which ssrc belongs to which user
    async def _wsHook(self, ws, msg) -> None:
        op = msg.get("op")
        d = msg.get("d") or {}
        if op == OP_SPEAKING and "ssrc" in d and "user_id" in d:
            self.ssrcToUser[int(d["ssrc"])] = int(d["user_id"])
        elif op == OP_CLIENT_DISCONNECT and "user_id" in d:
            uid = int(d["user_id"])
            for ssrc in [s for s, u in self.ssrcToUser.items() if u == uid]:
                self.ssrcToUser.pop(ssrc, None)
                self.decoders.pop(ssrc, None)
                self.lastSeq.pop(ssrc, None)
                if self.speaker:
                    self.speaker.drop(ssrc)
            self.lastHeard.pop(uid, None)

    #lifecycle

    #mic gate level auto mode sits 12db above the measured noise floor
    def thresholdDb(self) -> float:
        if self.vadThreshold <= 0:
            return -100.0
        if self.autoSensitivity:
            return max(-65.0, min(-25.0, self.noiseFloorDb + 12.0))
        return self.sensitivityDb

    def setInputDevice(self, node: Optional[str]) -> None:
        self.inputDevice = node
        if self.mic is not None:
            old, self.mic = self.mic, Mic(node, self.inputGain)
            old.close()
        log.info("voice input device -> %s", node or "default")

    def setOutputDevice(self, node: Optional[str]) -> None:
        self.outputDevice = node
        if self.speaker is not None:
            old = self.speaker
            new = Speaker(node)
            new.volume = old.volume
            self.speaker = new
            old.close()
        log.info("voice output device -> %s", node or "default")

    def setInputGain(self, gain: float) -> None:
        self.inputGain = gain
        if self.mic is not None:
            self.mic.gain = gain

    def startAudio(self) -> None:
        self.speaker = Speaker(self.outputDevice)
        self._connection.add_socket_listener(self._onPacket)
        self.mic = Mic(self.inputDevice, self.inputGain)
        self.encoder = opus.Encoder()
        self._txStop.clear()
        threading.Thread(target=self._txLoop, daemon=True, name="uncord-tx").start()
        threading.Thread(target=self._statsLoop, daemon=True, name="uncord-vstats").start()
        threading.Thread(target=self._watchdog, daemon=True, name="uncord-vwatch").start()
        threading.Thread(
            target=udpKeepalive, args=(self._connection, self._txStop), daemon=True, name="uncord-vkeepalive"
        ).start()
        log.info(
            "voice audio started: mode=%s dave=%s ssrc=%s",
            self.mode,
            self._connection.dave_protocol_version,
            self.ssrc,
        )

    def _err(self, kind: str) -> None:
        self.stats[kind] += 1
        self.rxErrors += kind.startswith("rx")
        if self.stats[kind] <= 3:
            log.warning("voice %s", kind, exc_info=True)

    def statsText(self) -> str:
        st = self.stats
        mic = self.mic
        spk = self.speaker
        return (
            f"rx packets {st['rx_audio']} → decoded {st['rx_decoded']} → played {spk.framesPlayed if spk else 0} · "
            f"mic frames {mic.framesRead if mic else 0}, peak {rmsToDb(mic.peakRms if mic else 0):.0f} dB "
            f"(gate {self.thresholdDb():.0f} dB, floor {self.noiseFloorDb:.0f}, agc {'x%.1f' % self.agcGain if self.agc else 'off'}) · sent {st['tx_sent']} · "
            f"errors: "
            + (
                ", ".join(
                    f"{k}={v}"
                    for k, v in st.items()
                    if k.endswith(("_fail", "_err", "_nouser", "_restarts", "_lost", "_late"))
                )
                or "none"
            )
            + f" · mic {'alive' if mic and mic.proc.poll() is None else 'DEAD'}, speaker {'alive' if spk and spk.proc.poll() is None else 'DEAD'}"
            f"{f' (dropped {spk.dropped})' if spk and spk.dropped else ''}"
        )

    def _statsLoop(self) -> None:
        while not self._txStop.wait(10):
            log.info("voice stats: %s", self.statsText())
            try:
                reader = self._connection._socket_reader
                quiet = time.monotonic() - getattr(self, "lastUdp", 0)
                spk = self.speaker
                log.info(
                    "voice rx health: last udp %.1fs ago, packet types %s, deafened %s, reader %s, speaker thread %s, "
                    "speaker queues %s",
                    quiet,
                    dict(self.rxTypes),
                    self.selfDeafened,
                    "alive" if reader.is_alive() else "DEAD",
                    "alive" if spk and spk.thread.is_alive() else "DEAD",
                    {str(k): len(q) for k, q in (spk.queues.items() if spk else [])},
                )
            except Exception:
                log.debug("rx health", exc_info=True)
            if self.mic:
                self.mic.peakRms = 0

    @property
    def micSilent(self) -> bool:
        return self.mic is not None and self.mic.silent

    #restarts pw-play or pw-record if they die or stop reading
    def _watchdog(self) -> None:
        restarts = 0
        while not self._txStop.wait(1.0):
            spk, mic = self.speaker, self.mic
            if spk is not None and (spk.proc.poll() is not None or time.monotonic() - spk.lastWrite > 3.0):
                restarts += 1
                self.stats["speaker_restarts"] += 1
                log.warning(
                    "speaker stalled (pw-play not reading); output %s; restarting",
                    describeStream("uncord voice"),
                )
                self.outputStalled = restarts >= 2
                if self.onChange:
                    self.onChange()
                self.setOutputDevice(self.outputDevice)
                self._txStop.wait(min(10, 2 * restarts))
            elif spk is not None and time.monotonic() - spk.lastWrite < 1.0:
                if self.outputStalled:
                    self.outputStalled = False
                    if self.onChange:
                        self.onChange()
                restarts = 0
            if mic is not None and mic.proc.poll() is not None:
                log.warning("mic died; restarting pw-record")
                self.stats["mic_restarts"] += 1
                self.setInputDevice(self.inputDevice)

    def stopAudio(self) -> None:
        self._txStop.set()
        try:
            self._connection.remove_socket_listener(self._onPacket)
        except Exception:
            pass
        for thing in (self.mic, self.speaker):
            if thing:
                thing.close()
        self.mic = self.speaker = None

    async def disconnect(self, *, force: bool = False) -> None:
        self.stopAudio()
        await super().disconnect(force=force)

    #receive

    #every udp packet from the voice server lands here on the socket thread
    #rtp header then xchacha20 encrypted payload then a 4 byte nonce
    #after that the payload may be dave encrypted opus
    def _onPacket(self, data: bytes) -> None:
        self.lastUdp = time.monotonic()
        spk = self.speaker  #stopAudio can clear it mid packet
        if self.selfDeafened or spk is None or len(data) < 16:
            return
        #rtcp not audio
        if 200 <= data[1] <= 204:
            return
        #payload type 120 is opus
        self.rxTypes[data[1] & 0x7F] += 1
        if (data[1] & 0x7F) != 0x78:
            return
        ssrc = struct.unpack_from(">I", data, 8)[0]
        if ssrc == self.ssrc:
            return
        self.stats["rx_audio"] += 1
        try:
            #header is 12 bytes plus csrcs plus the 4 byte extension header
            #the extension body is encrypted with the payload so strip it after
            cc = data[0] & 0x0F
            hasExt = bool(data[0] & 0x10)
            hlen = 12 + 4 * cc + (4 if hasExt else 0)
            header = data[:hlen]
            nonce = data[-4:] + b"\x00" * 20
            key = bytes(self.secret_key)
            box = self._aead
            if box is None or self._aeadKey != key:
                box, self._aeadKey = nacl.secret.Aead(key), key
                self._aead = box
            plain = _rtp.rtpPayload(data, box.decrypt(data[hlen:-4], header, nonce))
        except Exception:
            self._err("rx_transport_fail")
            return
        if not plain:
            return

        #drop late or duplicate packets and count gaps for loss concealment
        seq = struct.unpack_from(">H", data, 2)[0]
        last = self.lastSeq.get(ssrc)
        lost = 0
        if last is not None:
            gap = (seq - last) & 0xFFFF
            if gap == 0 or gap > 0x8000:
                self.stats["rx_late"] += 1
                return
            lost = gap - 1
        self.lastSeq[ssrc] = seq

        uid = self.ssrcToUser.get(ssrc) or getattr(self._connection, "ssrc_user_ids", {}).get(ssrc)
        if plain == opus.OPUS_SILENCE:
            return
        conn = self._connection
        if conn.dave_protocol_version and conn.dave_session is not None:
            if plain[-2:] != b"\xfa\xfa":
                #no dave trailer means plain opus
                self.stats["rx_passthrough"] += 1
            elif uid is None:
                self.stats["rx_nouser"] += 1
                return
            else:
                try:
                    plain = conn.dave_session.decrypt(uid, davey.MediaType.audio, plain)
                except Exception:
                    self._err("rx_dave_fail")
                    return

        dec = self.decoders.get(ssrc)
        if dec is None:
            dec = self.decoders[ssrc] = opus.Decoder()
            lost = 0
        gain = self.userVolume.get(uid, 1.0) if uid is not None else 1.0
        if 0 < lost <= 5 and gain > 0:
            #fec for the last lost frame plc for the rest
            self.stats["rx_lost"] += lost
            try:
                for k in range(lost):
                    pcm = dec.decode(bytes(plain), fec=True) if k == lost - 1 else dec.decode(None, fec=False)
                    spk.push(ssrc, pcm if gain == 1.0 else _dsp.gain(pcm, gain))
            except Exception:
                pass
        try:
            pcm = dec.decode(bytes(plain), fec=False)
        except Exception:
            self._err("rx_opus_fail")
            return
        self.stats["rx_decoded"] += 1
        if gain <= 0:
            pcm = None
        elif gain != 1.0:
            pcm = _dsp.gain(pcm, gain)
        if pcm is not None:
            spk.push(ssrc, pcm)
        if uid is not None:
            first = time.monotonic() - self.lastHeard.get(uid, 0) > 0.5
            self.lastHeard[uid] = time.monotonic()
            if first and self.onChange:
                self.onChange()

    def speakingUserIds(self, window: float = 0.4):
        now = time.monotonic()
        return [u for u, t in self.lastHeard.items() if now - t < window]

    #transmit

    def _setSpeaking(self, on: bool) -> None:
        if on == self._speaking:
            return
        self._speaking = on
        try:
            asyncio.run_coroutine_threadsafe(
                self.update_speaking_state(discord.SpeakingFlags(voice=on)), self.loop
            )
        except Exception:
            pass
        if self.onChange:
            self.onChange()

    @property
    def transmitting(self) -> bool:
        return self._speaking

    AGC_TARGET = dbToRms(-18.0)
    AGC_MAX = 16.0

    #auto gain slowly pulls speech towards -18db and never clips
    def _applyAgc(self, frame: bytes, speech: bool) -> bytes:
        if not self.agc:
            return frame
        rms = _dsp.rms(frame)
        if speech and rms > 0:
            self._agcLevel = rms if self._agcLevel <= 0 else self._agcLevel * 0.97 + rms * 0.03
        if self._agcLevel > 0:
            want = max(1.0, min(self.AGC_MAX, self.AGC_TARGET / self._agcLevel))
            self.agcGain = want if want < self.agcGain else self.agcGain + (want - self.agcGain) * 0.05
        peak = _dsp.peak(frame) or 1
        g = min(self.agcGain, 30000 / peak)
        return frame if g == 1.0 else _dsp.gain(frame, g)

    def _send(self, frame: bytes) -> None:
        try:
            self.send_audio_packet(frame, encode=True)
            self.stats["tx_sent"] += 1
        except Exception:
            self._err("tx_send_err")

    def _sendSilence(self) -> None:
        for _ in range(5):
            try:
                self.send_audio_packet(opus.OPUS_SILENCE, encode=False)
            except Exception:
                self._err("tx_send_err")
                return

    #mic to discord voice gate with a 300ms hang and a short preroll so first words arent cut
    def _txLoop(self) -> None:
        hang = 0
        preroll: collections.deque = collections.deque(maxlen=8)
        history: collections.deque = collections.deque(maxlen=150)
        n = 0
        while not self._txStop.is_set():
            mic = self.mic
            if mic is None:
                break
            frame = mic.get(FRAME_MS / 1000 * 3)
            if frame is None or not self.is_connected():
                continue
            self.level = _dsp.rms(frame)
            self.levelDb = db = rmsToDb(self.level)
            if self.selfMuted or self.selfDeafened:
                if self._speaking:
                    self._sendSilence()
                    self._setSpeaking(False)
                preroll.clear()
                continue
            #noise floor is the 20th percentile of the last 3s
            history.append(db)
            n += 1
            if len(history) >= 25 and (n % 10 == 0 or len(history) == 25):
                self.noiseFloorDb = sorted(history)[len(history) // 5]
            gate = self.thresholdDb()
            if self.autoSensitivity and len(history) < 25:
                gate = max(gate, -25.0)
            if db >= gate:
                hang = 15
            if hang > 0:
                hang -= 1
                if not self._speaking:
                    self._setSpeaking(True)
                    while preroll:
                        self._send(self._applyAgc(preroll.popleft(), False))
                self._send(self._applyAgc(frame, db >= gate))
            else:
                if self._speaking:
                    self._sendSilence()
                    self._setSpeaking(False)
                preroll.append(frame)


#sounds

_SFX_NOTES = {
    "join": [(587.3, 0.07), (880.0, 0.11)],
    "leave": [(880.0, 0.07), (587.3, 0.11)],
    "connect": [(523.3, 0.06), (659.3, 0.06), (987.8, 0.12)],
    "disconnect": [(987.8, 0.06), (659.3, 0.06), (440.0, 0.12)],
    "mute": [(392.0, 0.07)],
    "unmute": [(784.0, 0.07)],
    "deafen": [(523.3, 0.06), (349.2, 0.09)],
    "undeafen": [(349.2, 0.06), (523.3, 0.09)],
    "boom": [(62.0, 0.09), (48.0, 0.14), (41.0, 0.22)],
}
_sfxCache: Dict[str, bytes] = {}


def _renderSfx(notes, volume: float = 0.22) -> bytes:
    return _dsp.tones(notes, RATE, volume)


def playSfx(kind: str, volume: float = 1.0) -> None:
    notes = _SFX_NOTES.get(kind)
    if not notes or not shutil.which("pw-play"):
        return
    pcm = _sfxCache.get(kind)
    if pcm is None:
        pcm = _sfxCache[kind] = _renderSfx(notes)
    if volume != 1.0:
        pcm = _dsp.gain(pcm, max(0.0, min(volume, 3.0)))

    def run():
        try:
            p = subprocess.Popen(
                _pwArgs("pw-play", "uncord sfx", "40ms"),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            p.communicate(pcm, timeout=5)
        except Exception:
            log.debug("sfx failed", exc_info=True)

    threading.Thread(target=run, daemon=True, name="uncord-sfx").start()


#devices


def udpKeepalive(conn, stop: threading.Event, interval: float = 5.0) -> None:
    #discord drops udp that sends nothing for ~15s
    n = 0
    while not stop.wait(interval):
        try:
            if conn.is_connected() and conn.socket is not None:
                conn.send_packet(struct.pack(">Q", n))
                n += 1
        except Exception:
            log.debug("udp keepalive", exc_info=True)


def describeStream(mediaName: str) -> str:
    import json as _json

    try:
        data = _json.loads(subprocess.run(["pw-dump"], capture_output=True, timeout=5).stdout or b"[]")
    except Exception:
        return "?"
    nodes = {o["id"]: o for o in data if o.get("type") == "PipeWire:Interface:Node"}
    ours = [
        o for o in nodes.values() if ((o.get("info") or {}).get("props") or {}).get("media.name") == mediaName
    ]
    out = []
    for o in ours:
        info = o.get("info") or {}
        peers = []
        for l in data:
            if l.get("type") != "PipeWire:Interface:Link":
                continue
            li = l.get("info") or {}
            if li.get("output-node-id") == o["id"]:
                peer = nodes.get(li.get("input-node-id")) or {}
                peers.append(
                    ((peer.get("info") or {}).get("props") or {}).get("node.name", "?")
                    + f"[{(peer.get('info') or {}).get('state')}]"
                )
        out.append(
            f"state={info.get('state')} error={info.get('error')} → {', '.join(sorted(set(peers))) or 'NOT LINKED'}"
        )
    return "; ".join(out) or "stream not found"


def listDevices() -> dict:
    import json as _json

    out = {"sources": [], "sinks": [], "default_source": None, "default_sink": None}
    try:
        data = _json.loads(subprocess.run(["pw-dump"], capture_output=True, timeout=5).stdout or b"[]")
    except Exception:
        log.debug("pw-dump failed", exc_info=True)
        return out
    for o in data:
        if o.get("type") == "PipeWire:Interface:Node":
            p = (o.get("info") or {}).get("props") or {}
            mc = p.get("media.class", "")
            name = p.get("node.name")
            desc = p.get("node.description") or p.get("node.nick") or name
            if mc == "Audio/Source" and name:
                out["sources"].append((name, desc))
            elif mc == "Audio/Sink" and name:
                out["sinks"].append((name, desc))
        elif (
            o.get("type") == "PipeWire:Interface:Metadata"
            and (o.get("props") or {}).get("metadata.name") == "default"
        ):
            for m in o.get("metadata") or []:
                v = m.get("value") or {}
                if m.get("key") == "default.audio.source":
                    out["default_source"] = v.get("name")
                elif m.get("key") == "default.audio.sink":
                    out["default_sink"] = v.get("name")
    return out
