#draws images and gifs inline as sixel and only animates whats on screen
from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

from rich.control import Control
from rich.segment import ControlType, Segment
from rich.style import Style
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Static

from . import chafasixel

log = logging.getLogger("uncord")
_NULL = Style()
_ZW = ((ControlType.CURSOR_FORWARD, 0),)

try:
    from textual_image._sixel import image_to_sixels
    from textual_image._terminal import get_cell_size
    from textual_image.renderable import Image as _Auto
    from textual_image.renderable.sixel import Image as _SixelR

    from textual_image.renderable.tgp import Image as _KittyR

    SIXEL = _Auto is _SixelR
    #kitty graphics with unicode placeholders, kitty and ghostty, much better than half blocks
    KITTY = _Auto is _KittyR
except Exception:
    SIXEL = KITTY = False
GRAPHICS = SIXEL or KITTY

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="uncord-sixel")


#pixel size of one terminal cell so images line up with the text grid
def cellSize() -> Tuple[int, int]:
    try:
        cs = get_cell_size()
        return max(1, cs.width), max(1, cs.height)
    except Exception:
        return 10, 20


def boxFor(w: int, h: int, maxCols: int, maxRows: int, upscale: float = 2.0) -> Tuple[int, int]:
    cw, ch = cellSize()
    scale = min(maxCols * cw / w, maxRows * ch / h, upscale)
    return max(1, math.ceil(w * scale / cw)), max(1, math.ceil(h * scale / ch))


#encode an image as one sixel strip per text row so textual can draw it line by line
def encodeRows(img, cols: int, rows: int, bg, animated: bool) -> List[str]:
    from PIL import Image as PILImage

    cw, ch = cellSize()
    alpha = None
    if img.mode == "RGBA":
        a = img.getchannel("A")
        if a.getextrema()[0] < 128:
            alpha = a.point(lambda v: 255 if v >= 128 else 0)
    rgb = img.convert("RGB")
    if rgb.getcolors(256) is None:
        pal = rgb.quantize(256, method=PILImage.Quantize.MEDIANCUT)
        dither = PILImage.Dither.NONE if animated else PILImage.Dither.FLOYDSTEINBERG
        rgb = rgb.quantize(palette=pal, dither=dither).convert("RGB")
    out = []
    for r in range(rows):
        box = (0, r * ch, cols * cw, (r + 1) * ch)
        strip = rgb.crop(box)
        if alpha is not None:
            strip.putalpha(alpha.crop(box))
        data = chafasixel.encode(strip, cols, 1, cw, ch, chafasixel.DITHER_NONE)
        if data is not None and alpha is None and not chafasixel.hasPixels(data):
            log.warning("libchafa returned an empty image; using the built-in encoder")
            data = None
        if data is None:
            if strip.mode == "RGBA":
                base = PILImage.new("RGBA", strip.size, bg)
                base.alpha_composite(strip)
                strip = base.convert("RGB")
            data = image_to_sixels(strip, None, bg)
        out.append(data)
    return out


#one timer for every animated gif on screen and only a few animate at once
class _Director:

    MAX_ANIMATED = 3
    TICK = 1 / 12

    def __init__(self) -> None:
        self.widgets: "set[SixelMedia]" = set()
        self.timer = None

    def register(self, w: "SixelMedia") -> None:
        self.widgets.add(w)
        if self.timer is None:
            try:
                self.timer = w.app.set_interval(self.TICK, self.tick)
            except Exception:
                self.timer = None

    def unregister(self, w: "SixelMedia") -> None:
        self.widgets.discard(w)

    def tick(self) -> None:
        self.widgets = {w for w in self.widgets if w.is_attached}
        live = [w for w in self.widgets if len(w.frames) > 1 and not w.paused]
        try:
            focused = live[0].app.app_focus if live else True
        except Exception:
            focused = True
        visible = [w for w in live if w._fullyVisible()] if focused else []
        visible.sort(key=lambda w: w.region.y, reverse=True)
        chosen = set(visible[: self.MAX_ANIMATED]) | {w for w in visible if w.alwaysAnimate}
        now = time.monotonic()
        for w in live:
            if w not in chosen:
                if len(w._encoded) > 1 and not w._fullyVisible():
                    keep = w._encoded.get(w.idx)
                    w._encoded = {w.idx: keep} if keep else {}
                continue
            nxt = (w.idx + 1) % len(w.frames)
            if nxt not in w._encoded:
                w.request(nxt)
                continue
            if now - w._shownAt >= max(self.TICK, float(w.frames[w.idx][1])):
                w.idx = nxt
                w._shownAt = now
                w.refresh()
                w.request((nxt + 1) % len(w.frames))


director: Optional[_Director] = None


#an inline image or gif in the chat
#frames are encoded on a thread pool and cached so scrolling stays fast
class SixelMedia(Widget, can_focus=False):
    DEFAULT_CSS = "SixelMedia { width: auto; height: auto; }"

    def __init__(
        self, frames: List[Tuple[object, float]], cols: int, rows: int, *, alwaysAnimate: bool = False, **kw
    ) -> None:
        super().__init__(**kw)
        self.frames = frames
        self.idx = 0
        self.cols, self.rows = cols, rows
        self.styles.width = cols
        self.styles.height = rows
        self.alwaysAnimate = alwaysAnimate
        self._encoded: dict = {}
        self._pending: set = set()
        self._bg = (0, 0, 0, 255)
        self._shownAt = 0.0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.paused = False

    #worker thread

    def _scaled(self, i: int):
        from PIL import Image as PILImage

        cw, ch = cellSize()
        box = (self.cols * cw, self.rows * ch)
        src = self.frames[i][0]
        if src.mode not in ("RGB", "RGBA"):
            src = src.convert("RGBA" if src.mode in ("LA", "P", "PA") else "RGB")
        return src if src.size == box else src.resize(box, PILImage.LANCZOS)

    def _job(self, i: int) -> None:
        try:
            if self.is_attached and i not in self._encoded:
                self._encoded[i] = encodeRows(
                    self._scaled(i), self.cols, self.rows, self._bg, len(self.frames) > 1
                )
        except Exception:
            log.debug("sixel encode failed", exc_info=True)
        finally:
            self._pending.discard(i)
            if i == self.idx:
                loop = self._loop
                if loop is not None and not loop.is_closed():
                    loop.call_soon_threadsafe(self.refresh)

    def request(self, i: int) -> None:
        if i in self._encoded or i in self._pending:
            return
        self._pending.add(i)
        _POOL.submit(self._job, i)

    def on_mount(self) -> None:
        global director
        self._loop = asyncio.get_running_loop()
        try:
            _, col = self.background_colors
            self._bg = (col.r, col.g, col.b, 255)
        except Exception:
            pass
        self.request(0)
        if len(self.frames) > 1:
            if director is None:
                director = _Director()
            director.register(self)

    def on_unmount(self) -> None:
        if director is not None:
            director.unregister(self)
        self._encoded.clear()

    def _fullyVisible(self) -> bool:
        try:
            vis = self.screen.find_widget(self).visible_region
            return vis.width == self.size.width and vis.height == self.size.height and self.screen.is_active
        except Exception:
            return False

    #ui thread never encodes

    def render_lines(self, crop: Region) -> list:
        style = self.rich_style
        blank = [Strip.blank(crop.width, style)] * crop.height
        try:
            if not self.screen.is_active:
                return blank
            geo = self.screen.find_widget(self).region
        except Exception:
            return blank
        rows = self._encoded.get(self.idx)
        if rows is None:
            self.request(self.idx)
            return blank
        if crop.x != 0 or crop.width != self.size.width:
            return blank
        out = []
        #one zero width segment at col 0 or textual crops it
        clear = "\x1b[0m" + " " * crop.width
        for line in range(crop.y, crop.bottom):
            if line >= len(rows):
                out.append(Strip.blank(crop.width, style))
                continue
            sy = geo.y + line
            home = Control.move_to(geo.x, sy).segment.text
            seq = home + clear + home + rows[line] + Control.move_to(geo.x + crop.width, sy).segment.text
            out.append(Strip([Segment(seq, style=_NULL, control=_ZW)], cell_length=crop.width))
        return out


#kitty graphics protocol
#each frame is uploaded once as a png then drawn with placeholder cells whose colour is the image id
#so textual treats it like text and scrolling or repainting needs no special handling

_PLACEHOLDER = "\U0010EEEE"
_KITTY_IDS = iter(range(random.randint(1 << 16, 1 << 22), 1 << 24))


def _diacritic(n: int) -> str:
    from textual_image.renderable.tgp import _NUMBER_TO_DIACRITIC

    return chr(_NUMBER_TO_DIACRITIC[n])


def kittyMessage(payload: str = "", **keys) -> str:
    return "\x1b_G" + ",".join(f"{k}={v}" for k, v in keys.items()) + (";" + payload if payload else "") + "\x1b\\"


def kittyUpload(img, imageId: int, cols: int, rows: int) -> str:
    import base64
    import io as _io

    buf = _io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)
    data = base64.b64encode(buf.getvalue()).decode()
    out = []
    first = True
    while data:
        chunk, data = data[:4096], data[4096:]
        keys = dict(a="t", f=100, i=imageId, q=2) if first else dict(q=2)
        keys["m"] = 1 if data else 0
        out.append(kittyMessage(chunk, **keys))
        first = False
    out.append(kittyMessage(a="p", U=1, i=imageId, c=cols, r=rows, q=2))
    return "".join(out)


class KittyMedia(SixelMedia):

    def _job(self, i: int) -> None:
        try:
            if self.is_attached and i not in self._encoded:
                imageId = next(_KITTY_IDS)
                seq = kittyUpload(self._scaled(i), imageId, self.cols, self.rows)
                self.app._driver.write(seq)
                self._encoded[i] = imageId
        except Exception:
            log.debug("kitty upload failed", exc_info=True)
        finally:
            self._pending.discard(i)
            if i == self.idx:
                loop = self._loop
                if loop is not None and not loop.is_closed():
                    loop.call_soon_threadsafe(self.refresh)

    def on_unmount(self) -> None:
        ids = list(self._encoded.values())
        super().on_unmount()
        try:
            for imageId in ids:
                self.app._driver.write(kittyMessage(a="d", d="I", i=imageId, q=2))
        except Exception:
            pass

    def render_lines(self, crop: Region) -> list:
        style = self.rich_style
        imageId = self._encoded.get(self.idx)
        if imageId is None:
            self.request(self.idx)
            return [Strip.blank(crop.width, style)] * crop.height
        colour = Style(color=f"rgb({(imageId >> 16) & 255},{(imageId >> 8) & 255},{imageId & 255})")
        out = []
        for line in range(crop.y, crop.bottom):
            if line >= self.rows:
                out.append(Strip.blank(crop.width, style))
                continue
            row = _diacritic(line)
            text = "".join(_PLACEHOLDER + row + _diacritic(c) for c in range(crop.x, crop.x + crop.width))
            out.append(Strip([Segment(text, colour)], cell_length=crop.width))
        return out


def mediaWidget(frames, maxCols: int, maxRows: int, classes: str = "media", **kw) -> Widget:
    first = frames[0][0]
    w, h = first.size
    cols, rows = boxFor(w, h, maxCols, maxRows)
    if SIXEL:
        return SixelMedia(frames, cols, rows, classes=classes, **kw)
    if KITTY:
        return KittyMedia(frames, cols, rows, classes=classes, **kw)
    from textual_image.renderable.halfcell import Image as Halfcell

    rowsHc = max(1, round(cols * h / w / 2))
    st = Static(Halfcell(first.convert("RGB"), width=cols, height=min(maxRows, rowsHc)), classes=classes)
    st.styles.width = cols
    st.styles.height = min(maxRows, rowsHc)
    return st
