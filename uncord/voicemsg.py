#recording sending and playing voice messages
from __future__ import annotations

import asyncio
import base64
import io
import subprocess
import threading
import time
from typing import Optional

import discord
from discord.file import CloudFile
from discord.http import handle_message_parameters
from discord.utils import MISSING

from . import _dsp
from .media import FFMPEG, MPV
from .voice import _pwArgs

RATE = 48000


class Recorder:

    def __init__(self) -> None:
        args = _pwArgs("pw-record", "uncord voice message", "20ms")
        args[args.index("--channels") + 1] = "1"
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.buf = bytearray()
        self.started = time.monotonic()
        self.level = 0
        self._t = threading.Thread(target=self._read, daemon=True, name="uncord-vmsg")
        self._t.start()

    def _read(self) -> None:
        out = self.proc.stdout
        while True:
            chunk = out.read(1920)
            if not chunk:
                break
            self.buf.extend(chunk)
            try:
                self.level = _dsp.rms(chunk)
            except Exception:
                pass

    @property
    def seconds(self) -> float:
        return len(self.buf) / (RATE * 2)

    def stop(self) -> bytes:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=2)
        except Exception:
            pass
        self._t.join(timeout=1)
        return bytes(self.buf)


def waveform(pcm: bytes, points: int = 0) -> bytes:
    secs = len(pcm) / (RATE * 2)
    n = points or max(1, min(256, int(secs * 10)))
    step = max(2, (len(pcm) // n) & ~1)
    vals = _dsp.levels(pcm[: n * step], step)
    vals += [0] * (n - len(vals))
    peak = max(vals) or 1
    return bytes(min(255, int(v / peak * 255)) for v in vals)


async def encodeOgg(pcm: bytes) -> bytes:
    if not FFMPEG:
        raise RuntimeError("ffmpeg not available for encoding")
    proc = await asyncio.create_subprocess_exec(
        FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "s16le",
        "-ar",
        str(RATE),
        "-ac",
        "1",
        "-i",
        "-",
        "-c:a",
        "libopus",
        "-b:a",
        "48k",
        "-application",
        "voip",
        "-f",
        "ogg",
        "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(pcm)
    if proc.returncode != 0 or not out:
        raise RuntimeError(err.decode(errors="replace")[-200:] or "ffmpeg failed")
    return out


class VoiceCloudFile(CloudFile):

    __slots__ = ("duration", "waveform")

    def to_dict(self, index: int):
        d = super().to_dict(index)
        d["duration_secs"] = self.duration
        d["waveform"] = self.waveform
        return d


async def sendVoiceMessage(
    channel, pcm: bytes, reference: Optional[discord.Message] = None
) -> discord.Message:
    ogg = await encodeOgg(pcm)
    duration = round(len(pcm) / (RATE * 2), 2)
    wave = base64.b64encode(waveform(pcm)).decode()
    (uploaded,) = await channel.upload_files(discord.File(io.BytesIO(ogg), filename="voice-message.ogg"))
    cloud = VoiceCloudFile(uploaded.url, uploaded.filename, uploaded.upload_filename, state=uploaded._state)
    cloud.duration, cloud.waveform = duration, wave
    flags = discord.MessageFlags._from_value(0)
    flags.voice = True
    target = await channel._get_channel()
    state = target._state
    ref = reference.to_message_reference_dict() if reference is not None else MISSING
    with handle_message_parameters(file=cloud, flags=flags, message_reference=ref) as params:
        data = await state.http.send_message(target.id, params=params)
    return state.create_message(channel=target, data=data)


#playback


class Player:

    def __init__(self) -> None:
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.url: Optional[str] = None

    @property
    def playing(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def toggle(self, url: str) -> bool:
        was = self.url if self.playing else None
        await self.stop()
        if was == url:
            return False
        if MPV:
            cmd = [MPV, "--no-video", "--really-quiet", "--audio-client-name=uncord", url]
        elif FFMPEG:
            cmd = [
                "sh",
                "-c",
                f'"{FFMPEG}" -loglevel error -i "$0" -f s16le -ar 48000 -ac 2 - | '
                "pw-play --raw --format s16 --rate 48000 --channels 2 -",
                url,
            ]
        else:
            raise RuntimeError("need mpv or ffmpeg to play audio")
        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.url = url
        return True

    async def stop(self) -> None:
        if self.playing:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), 2)
            except Exception:
                pass
        self.proc = None
        self.url = None


def waveBars(wave: Optional[bytes], width: int = 24) -> str:
    bars = "▁▂▃▄▅▆▇█"
    if not wave:
        return "▁" * width
    n = len(wave)
    out = []
    for i in range(width):
        seg = wave[i * n // width : (i + 1) * n // width] or wave[min(n - 1, i * n // width) :][:1]
        v = max(seg) if seg else 0
        out.append(bars[min(7, v * 8 // 256)])
    return "".join(out)
