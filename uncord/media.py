#finds images gifs videos and emoji in messages and loads them into frames
from __future__ import annotations

import asyncio
import collections
import io
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Tuple

import discord

log = logging.getLogger("uncord")

from .deps import FFMPEG, MPV

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
VIDEO_EXT = (".mp4", ".webm", ".mov", ".mkv", ".m4v")

THUMB_W, THUMB_MAX_H = 32, 8


@dataclass
class MediaItem:
    title: str
    url: str
    thumb: str
    kind: str
    spoiler: bool = False
    page: str = ""
    emoji: bool = False
    small: bool = False
    alts: tuple = ()


def _alts(*urls) -> tuple:
    out = []
    for u in urls:
        if u and u not in out:
            out.append(u)
    return tuple(out)


EMOJI_RE = __import__("re").compile(r"<(a?):(\w+):(\d+)>")


def emojiItems(content: str) -> list:
    out, seen = [], set()
    for mt in EMOJI_RE.finditer(content or ""):
        if mt.group(3) in seen:
            continue
        seen.add(mt.group(3))
        anim = bool(mt.group(1))
        url = f"https://cdn.discordapp.com/emojis/{mt.group(3)}.{'gif' if anim else 'png'}?size=96&quality=lossless"
        out.append(MediaItem(f":{mt.group(2)}:", url, url, "gif" if anim else "emoji", emoji=True))
    return out[:12]


def onlyEmojis(content: str) -> bool:
    c = (content or "").strip()
    return bool(c) and not EMOJI_RE.sub("", c).strip() and len(EMOJI_RE.findall(c)) <= 27


#ask discords image proxy for a smaller copy in the images own shape
#giving it a box that isnt the same shape crops the picture
def _sized(url: str, w: int = 800, h: int = 600, src=None) -> str:
    if not ("media.discordapp.net" in url or "images-ext" in url):
        return url
    sw, sh = (getattr(src, "width", None), getattr(src, "height", None)) if src is not None else (None, None)
    if sw and sh:
        scale = min(w / sw, h / sh, 1.0)
        w, h = max(1, round(sw * scale)), max(1, round(sh * scale))
        return url + ("&" if "?" in url else "?") + f"width={w}&height={h}"
    return url + ("&" if "?" in url else "?") + f"width={w}"


def itemsOf(m: discord.Message) -> List[MediaItem]:
    out: List[MediaItem] = []
    for a in m.attachments:
        ct = (a.content_type or "").lower()
        name = a.filename.lower()
        if ct.startswith("image/") or name.endswith(IMAGE_EXT):
            kind = "gif" if name.endswith(".gif") or ct == "image/gif" else "image"
            out.append(MediaItem(a.filename, a.url, _sized(a.proxy_url, src=a), kind, a.is_spoiler()))
        elif ct.startswith("video/") or name.endswith(VIDEO_EXT):
            thumb = _sized(a.proxy_url + ("&" if "?" in a.proxy_url else "?") + "format=webp", 400, 300, src=a)
            out.append(MediaItem(a.filename, a.url, thumb, "video", a.is_spoiler()))
    for s in m.stickers:
        fmt = str(getattr(s, "format", "")).lower()
        if "lottie" in fmt:
            continue
        out.append(
            MediaItem(
                f"sticker: {s.name}", s.url, s.url, "gif" if ("gif" in fmt or "apng" in fmt) else "sticker"
            )
        )
    for e in m.embeds:
        video = getattr(e, "video", None)
        thumb = getattr(e, "thumbnail", None)
        image = getattr(e, "image", None)
        tUrl = thumb.url if thumb and thumb.url else None
        tProxy = (thumb.proxy_url or None) if thumb else None
        if e.type == "gifv" and video and video.url:
            still = tProxy or tUrl or video.url
            out.append(
                MediaItem(
                    e.provider.name if e.provider and e.provider.name else "gif",
                    video.url,
                    _sized(still, src=thumb),
                    "gif",
                    page=e.url or "",
                    alts=_alts(still, tUrl),
                )
            )
        elif e.type == "image" and tUrl:
            out.append(
                MediaItem(
                    e.title or "image",
                    tUrl,
                    _sized(tProxy or tUrl, src=thumb),
                    "gif" if tUrl.lower().split("?")[0].endswith(".gif") else "image",
                    page=e.url or "",
                    alts=_alts(tProxy, tUrl),
                )
            )
        elif e.type == "video" and video and video.url and tUrl:
            out.append(
                MediaItem(
                    e.title or "video",
                    e.url or video.url,
                    _sized(tProxy or tUrl, src=thumb),
                    "video",
                    alts=_alts(tProxy, tUrl),
                )
            )
        elif image and image.url:
            out.append(
                MediaItem(
                    e.title or "image",
                    image.url,
                    _sized(image.proxy_url or image.url, src=image),
                    "image",
                    alts=_alts(image.proxy_url, image.url),
                )
            )
        elif tUrl and e.type in ("article", "link", "rich", "video"):
            out.append(
                MediaItem(
                    e.title or "thumbnail",
                    tUrl,
                    _sized(tProxy or tUrl, 400, 300, src=thumb),
                    "image",
                    small=True,
                    alts=_alts(tProxy, tUrl),
                )
            )
    return out


class MediaCache:

    def __init__(self, fetch: Callable[[str], Awaitable[bytes]], size: int = 300) -> None:
        self.fetch = fetch
        self.images: "collections.OrderedDict[str, object]" = collections.OrderedDict()
        self.size = size
        self.sem = asyncio.Semaphore(4)
        self.inflight: dict = {}

    async def thumb(self, url: str):
        if url in self.images:
            self.images.move_to_end(url)
            return self.images[url]
        if url in self.inflight:
            return await self.inflight[url]
        fut = asyncio.get_running_loop().create_future()
        self.inflight[url] = fut
        img = None
        try:
            async with self.sem:
                data = await self.fetch(url)
            img = await asyncio.to_thread(_decodeFirst, data)
        except Exception:
            log.debug("thumb failed %s", url, exc_info=True)
        finally:
            self.inflight.pop(url, None)
            fut.set_result(img)
        if img is not None:
            self.images[url] = img
            while len(self.images) > self.size:
                self.images.popitem(last=False)
        return img


def _decodeFirst(data: bytes):
    from PIL import Image as PILImage

    img = PILImage.open(io.BytesIO(data))
    try:
        img.seek(0)
    except Exception:
        pass
    img = img.convert("RGB")
    img.thumbnail((480, 360))
    return img


def thin(frames: list, limit: int) -> list:
    if len(frames) <= limit:
        return frames
    step = len(frames) / limit
    out = []
    for k in range(limit):
        a, b = int(k * step), int((k + 1) * step)
        out.append((frames[a][0], sum(d for _f, d in frames[a:b])))
    return out


class FrameCache:

    def __init__(
        self, fetch: Callable[[str], Awaitable[bytes]], size: int = 400, budget: int = 256 << 20
    ) -> None:
        self.fetch = fetch
        self.cache: "collections.OrderedDict[str, list]" = collections.OrderedDict()
        self.size = size
        self.budget = budget
        self.used = 0
        self.sem = asyncio.Semaphore(3)
        self.inflight: dict = {}

    async def _fetchAny(self, *urls) -> bytes:
        err = None
        for u in dict.fromkeys(u for u in urls if u):
            try:
                return await self.fetch(u)
            except Exception as e:
                err = e
        raise err or RuntimeError("no url")

    async def frames(self, item: "MediaItem", animate: bool = True) -> list:
        key = f"{item.url}|{animate}"
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        if key in self.inflight:
            return await self.inflight[key]
        fut = asyncio.get_running_loop().create_future()
        self.inflight[key] = fut
        frames: list = []
        try:
            async with self.sem:
                plain = item.url.lower().split("?")[0]
                if animate and item.kind == "gif" and plain.endswith(VIDEO_EXT):
                    frames = await videoFrames(item.url, fps=12, width=448, seconds=8)
                elif animate and item.kind in ("gif", "sticker"):
                    data = await self._fetchAny(item.url, *item.alts)
                    frames = await asyncio.to_thread(gifFrames, data, 240, 512)
                else:
                    data = await self._fetchAny(item.thumb, *item.alts)
                    frames = await asyncio.to_thread(gifFrames, data, 1, 960)
            frames = thin(frames, 48)
        except Exception:
            log.debug("frames failed %s", item.url, exc_info=True)
        finally:
            self.inflight.pop(key, None)
            fut.set_result(frames)
        if frames:
            self.cache[key] = frames
            self.used += _nbytes(frames)
            while len(self.cache) > 1 and (len(self.cache) > self.size or self.used > self.budget):
                _k, old = self.cache.popitem(last=False)
                self.used -= _nbytes(old)
        return frames


def _nbytes(frames) -> int:
    return sum(f.width * f.height * len(f.getbands()) for f, _d in frames)


def thumbRenderable(img, maxW: int = THUMB_W, maxH: int = THUMB_MAX_H):
    from textual_image.renderable.halfcell import Image as Halfcell

    w, h = img.size
    cols = max(4, min(maxW, w // 8 or 4))
    rows = max(2, min(maxH, round(cols * h / w / 2)))
    cols = max(4, min(maxW, round(rows * 2 * w / h)))
    return Halfcell(img, width=cols, height=rows)


#animation


def gifFrames(data: bytes, maxFrames: int = 300, maxSide: int = 0) -> List[Tuple[object, float]]:
    from PIL import Image as PILImage, ImageSequence

    img = PILImage.open(io.BytesIO(data))
    frames = []
    for i, frame in enumerate(ImageSequence.Iterator(img)):
        if i >= maxFrames:
            break
        dur = (frame.info.get("duration") or img.info.get("duration") or 80) / 1000
        f = frame.convert("RGBA").copy()
        if maxSide and max(f.size) > maxSide:
            f.thumbnail((maxSide, maxSide))
        frames.append((f, max(0.02, dur)))
    return frames


async def videoFrames(
    url: str, fps: int = 12, width: int = 480, seconds: int = 20
) -> List[Tuple[object, float]]:
    if not FFMPEG:
        raise RuntimeError("ffmpeg not available")
    proc = await asyncio.create_subprocess_exec(
        FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-t",
        str(seconds),
        "-i",
        url,
        "-vf",
        f"fps={fps},scale='min({width},iw)':-2:flags=lanczos",
        "-f",
        "image2pipe",
        "-vcodec",
        "ppm",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0 or not out:
        raise RuntimeError(err.decode(errors="replace")[-200:] or "ffmpeg failed")
    return await asyncio.to_thread(_ppmFrames, out, 1 / fps)


def _ppmFrames(data: bytes, dur: float) -> List[Tuple[object, float]]:
    from PIL import Image as PILImage

    frames, pos, n = [], 0, len(data)
    while pos < n:
        parts, p = [], pos
        while len(parts) < 4:
            while data[p : p + 1].isspace():
                p += 1
            q = p
            while q < n and not data[q : q + 1].isspace():
                q += 1
            parts.append(data[p:q])
            p = q
        p += 1
        w, h = int(parts[1]), int(parts[2])
        size = w * h * 3
        if parts[0] != b"P6" or p + size > n:
            break
        frames.append((PILImage.frombuffer("RGB", (w, h), data[p : p + size], "raw", "RGB", 0, 1), dur))
        pos = p + size
    return frames


def playExternal(url: str) -> bool:
    if not MPV:
        return False
    asyncio.get_running_loop().create_task(
        asyncio.create_subprocess_exec(
            MPV,
            "--really-quiet",
            "--loop-file=inf" if url.lower().split("?")[0].endswith(".gif") else "--keep-open=no",
            "--force-window=immediate",
            url,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    )
    return True
