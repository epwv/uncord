#the fullscreen stream viewer frames are written straight to the terminal as sixel
#textual only paints black there so it doesnt fight the picture
from __future__ import annotations

import fcntl
import os
import random
import logging
import struct
import termios
import threading
import time
from typing import Optional

from rich.control import Control
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.geometry import Region
from textual.screen import Screen
from textual.strip import Strip
from textual.widget import Widget

from . import _native as native, chafasixel, sixelview

log = logging.getLogger("uncord")
_DIM = Style(color="bright_black")

#kitty image ids for the stream, two so a new frame goes up before the old one is removed
KITTY_IDS = (random.randint(1 << 22, 1 << 23), random.randint(1 << 23, 1 << 24))
#ssh means the terminal cant read our shared memory files
LOCAL = not (os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))

#preview sizes for + and - the default 1600x900 still keeps up with a 30fps stream in foot
BOXES = ((960, 540), (1280, 720), (1600, 900), (1920, 1080), (2240, 1260))
DEFAULT_BOX = 2
#picture sizes to fall back to when the terminal cant keep up
SCALES = (1.0, 0.8, 0.64, 0.5)
#how many bytes may sit unread in the tty before we skip a frame, under one frame so we never lag behind
MAX_BACKLOG = 96 * 1024
#below this the picture shrinks a step
MIN_FPS = 15


#bytes still waiting in the tty so we dont pile frames up behind a slow terminal
def _ptyBacklog(fd: int) -> int:
    try:
        return struct.unpack("i", fcntl.ioctl(fd, termios.TIOCOUTQ, b"\0\0\0\0"))[0]
    except Exception:
        return 0


#the video area a worker thread turns decoded frames into sixel and writes them straight to the terminal
#textual never draws the picture itself it only paints black underneath
class LiveSixel(Widget, can_focus=False):
    DEFAULT_CSS = "LiveSixel { width: 1fr; height: 1fr; background: black; }"

    def __init__(self, decoder, fps: float = 60.0, full: bool = False, box: int = DEFAULT_BOX, **kw) -> None:
        super().__init__(**kw)
        self.decoder = decoder
        self.fps = fps
        self.full = full
        self.box = max(0, min(len(BOXES) - 1, box))
        self.message = "Connecting…"
        self.shown = 0
        self.skipped = 0
        self.scaleIdx = 0
        self._tooSlow = -1  #smallest scale step that was too slow
        self._encodeMs = 0.0
        self._bytes = 0
        self.hasFrame = False
        self.visibleNow = False
        self._place: Optional[tuple] = None
        self._lastSeq: Optional[str] = None
        self._kittyId = 0
        self._healPending = False
        self._stop = threading.Event()
        self._wlock = threading.Lock()

    def on_mount(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="uncord-live-enc").start()
        self.set_interval(0.2, self._checkVisible)

    def on_unmount(self) -> None:
        with self._wlock:
            self._stop.set()
            self.visibleNow = False
        self.decoder.newFrame.set()
        self._kittyClear()

    #kitty frames go through a shared memory file so we only send the path
    #over ssh the file isnt there so send the pixels zlib compressed instead
    def _kittyFrame(self, data: bytes, w: int, h: int, x: int, y: int, cols: int, rows: int) -> str:
        import base64

        old = self._kittyId
        self._kittyId = KITTY_IDS[0] if old != KITTY_IDS[0] else KITTY_IDS[1]
        keys = dict(a="T", f=24, s=w, v=h, i=self._kittyId, p=1, c=cols, r=rows, C=1, q=2)
        if LOCAL:
            path = f"/dev/shm/tty-graphics-protocol-uncord-{os.getpid()}-{self._kittyId}.rgb"
            with open(path, "wb") as f:
                f.write(data)
            body = sixelview.kittyMessage(base64.b64encode(path.encode()).decode(), t="t", **keys)
        else:
            import zlib

            first = ",".join(f"{k}={v}" for k, v in dict(keys, o="z").items())
            body = native.kittyChunks(zlib.compress(data, 1), first, "")
        self._bytes = len(data)
        drop = sixelview.kittyMessage(a="d", d="I", i=old, q=2) if old else ""
        return "\x1b[?2026h\x1b7" + Control.move_to(x, y).segment.text + body + drop + "\x1b8\x1b[?2026l"

    def _kittyClear(self) -> None:
        if not sixelview.KITTY:
            return
        try:
            for i in KITTY_IDS:
                self.app._driver.write(sixelview.kittyMessage(a="d", d="I", i=i, q=2))
        except Exception:
            pass
        self._kittyId = 0

    def _checkVisible(self) -> None:
        try:
            vis = self.screen.is_active and self.app._driver is not None
        except Exception:
            vis = False
        if vis and not self.visibleNow:
            self.call_after_refresh(self._show)
        elif not vis:
            with self._wlock:
                self.visibleNow = False

    def _show(self) -> None:
        self.visibleNow = True

    def on_resize(self, event) -> None:
        self.relayout()

    #work out the cell box for the picture and tell ffmpeg to scale to exactly that many pixels
    def relayout(self) -> None:
        try:
            region = self.region
        except Exception:
            return
        cols, rows = region.width, region.height
        if cols < 2 or rows < 2:
            return
        cw, ch = sixelview.cellSize()
        if not self.full:
            bw, bh = BOXES[self.box]
            cols = min(cols, max(2, bw // cw))
            rows = min(rows, max(2, bh // ch))
        scale = SCALES[self.scaleIdx]
        cols, rows = max(2, int(cols * scale)), max(2, int(rows * scale))
        x = region.x + (region.width - cols) // 2
        y = region.y + (region.height - rows) // 2
        self._kittyClear()
        with self._wlock:
            self._place = (x, y, cols, rows, cw, ch)
            self.hasFrame = False
            self._lastSeq = None
        self.decoder.resize(cols * cw, rows * ch)
        self.refresh()

    def toggleFull(self) -> None:
        self.full = not self.full
        self.scaleIdx = 0
        self._tooSlow = -1
        self.relayout()

    def resizeBox(self, step: int) -> None:
        self.full = False
        self.box = max(0, min(len(BOXES) - 1, self.box + step))
        self.scaleIdx = 0
        self._tooSlow = -1
        self.relayout()

    #skip a frame if textual still has output queued or the terminal is behind
    def _busy(self) -> bool:
        try:
            wt = self.app._driver._writer_thread
            if wt._queue.qsize() > 0:
                return True
            return _ptyBacklog(wt.fileno()) > MAX_BACKLOG
        except Exception:
            return False

    def _run(self) -> None:
        from PIL import Image as PILImage

        dec = self.decoder
        lastSeq, lastT = -1, 0.0
        win = (time.monotonic(), 0, 0, dec.decoded)  #start shown skipped decoded
        lastSkip = time.monotonic()
        while not self._stop.is_set():
            dec.newFrame.wait(0.5)
            dec.newFrame.clear()
            if self._stop.is_set():
                return
            now = time.monotonic()
            if now - win[0] >= 10:
                win, lastSkip = self._adapt(win, lastSkip, now)
            if dec.seq == lastSeq or not self.visibleNow:
                continue
            wait = 1 / self.fps - (time.monotonic() - lastT)
            if wait > 0:
                time.sleep(wait)
            if self._busy():
                self.skipped += 1
                continue
            lastT = time.monotonic()
            lastSeq = dec.seq
            place = self._place
            data, (w, h) = dec.latest, dec.latestSize
            if data is None or place is None:
                continue
            x, y, cols, rows, cw, ch = place
            bw, bh = cols * cw, rows * ch
            if (w, h) != (bw - bw % 2, bh - bh % 2):
                continue
            t = time.monotonic()
            if sixelview.KITTY:
                seq = self._kittyFrame(data, w, h, x, y, cols, rows)
                self._encodeMs = self._encodeMs * 0.9 + (time.monotonic() - t) * 100
                with self._wlock:
                    if self._stop.is_set() or not self.visibleNow or place != self._place:
                        continue
                    try:
                        self.app._driver.write(seq)
                    except Exception:
                        continue
                    self.hasFrame = True
                self.shown += 1
                continue
            try:
                img = PILImage.frombytes("RGB", (w, h), data)
                if img.size != (bw, bh):
                    canvas = PILImage.new("RGB", (bw, bh))
                    canvas.paste(img)
                    img = canvas
                six = chafasixel.encode(img, cols, rows, cw, ch, chafasixel.DITHER_NONE)
                if six is None:
                    six = sixelview.sixelEncode(img)
            except Exception:
                log.debug("live sixel encode failed", exc_info=True)
                continue
            self._encodeMs = self._encodeMs * 0.9 + (time.monotonic() - t) * 100
            self._bytes = len(six)
            #synced output so the terminal swaps the whole frame at once
            seq = "\x1b[?2026h\x1b7" + Control.move_to(x, y).segment.text + six + "\x1b8\x1b[?2026l"
            with self._wlock:
                if self._stop.is_set() or not self.visibleNow or place != self._place:
                    continue
                try:
                    self.app._driver.write(seq)
                except Exception:
                    continue
                self.hasFrame = True
                self._lastSeq = seq
            self.shown += 1

    #every 10s log how the viewer is doing and shrink the picture if the terminal keeps falling behind
    #or grow it back once its been keeping up for a while
    def _adapt(self, win, lastSkip, now):
        t0, shown0, skipped0, decoded0 = win
        secs = now - t0
        shown, skipped = self.shown - shown0, self.skipped - skipped0
        place = self._place
        log.info("watch view: shown %.1f fps, skipped %s, decoded %.1f fps, encode %.0f ms, %s KiB/frame, %s px, scale %s",
                 shown / secs, skipped, (self.decoder.decoded - decoded0) / secs, self._encodeMs, self._bytes // 1024,
                 f"{place[2] * place[4]}x{place[3] * place[5]}" if place else "?", SCALES[self.scaleIdx])
        #big beats smooth so only shrink when its really choppy
        #and never grow back to a size that already was too slow
        fps = shown / secs
        want = min((self.decoder.decoded - decoded0) / secs, self.fps)
        behind = want > 5 and fps < min(MIN_FPS, want * 0.7)
        if behind:
            lastSkip = now
            self._tooSlow = min(self._tooSlow, self.scaleIdx)
        old = self.scaleIdx
        if behind and self.scaleIdx < len(SCALES) - 1:
            self.scaleIdx += 1
        elif (not behind and fps >= want * 0.9 and now - lastSkip > 30 and self.scaleIdx > 0
              and self.scaleIdx - 1 > self._tooSlow):
            self.scaleIdx -= 1
            lastSkip = now
        if self.scaleIdx != old:
            log.info("watch view: terminal %s, picture size now %s", "behind" if self.scaleIdx > old else "keeping up",
                     SCALES[self.scaleIdx])
            self.app.call_from_thread(self.relayout)
        return (now, self.shown, self.skipped, self.decoder.decoded), lastSkip

    #textual just painted over us so put the last frame back
    def _heal(self) -> None:
        self._healPending = False
        with self._wlock:
            seq = self._lastSeq
            if seq is None or self._stop.is_set() or not self.visibleNow:
                return
            try:
                self.app._driver.write(seq)
            except Exception:
                pass

    def render_lines(self, crop: Region) -> list:
        if self._lastSeq is not None and not self._healPending:
            self._healPending = True
            self.call_after_refresh(self._heal)
        #textual only paints black here the worker draws on top
        style = self.rich_style
        mid = self.size.height // 2
        out = []
        for line in range(crop.y, crop.bottom):
            if line == mid and self.message and not self.hasFrame:
                msg = self.message.center(self.size.width)[crop.x : crop.x + crop.width]
                out.append(Strip([Segment(msg, style + _DIM)]).adjust_cell_length(crop.width, style))
            else:
                out.append(Strip.blank(crop.width, style))
        return out


#the one line under the picture refreshes without a relayout so it never touches the picture
class _Bar(Widget, can_focus=False):

    DEFAULT_CSS = "_Bar { height: 1; padding: 0 1; background: black; }"

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.text = Text()

    def set(self, text: Text) -> None:
        if text.plain != self.text.plain or text.spans != self.text.spans:
            self.text = text
            self.refresh()

    def render(self) -> Text:
        return self.text


#fullscreen viewer esc closes it and keeps watching in the background x stops
class StreamScreen(Screen):

    DEFAULT_CSS = """
    StreamScreen { background: black; }
    StreamScreen > Vertical { width: 100%; height: 100%; }
    StreamScreen ToastRack { display: none; }
    """
    BINDINGS = [
        Binding("escape,q", "close", "Close", show=False),
        Binding("question_mark", "keys", "Keys", show=False),
        Binding("f", "full", "Full size", show=False),
        Binding("plus,equals_sign", "size(1)", "Bigger", show=False),
        Binding("minus,underscore", "size(-1)", "Smaller", show=False),
        Binding("p,o", "popout", "Pop out", show=False),
        Binding("m", "mute", "Mute stream", show=False),
        Binding("k", "keyframe", "Refresh", show=False),
        Binding("s", "stats", "Stats", show=False),
        Binding("x", "stop", "Stop watching", show=False),
    ]

    KEYS = "esc close · + - size · f full size · p mpv window · m mute · k refresh · s stats · x stop"

    def __init__(self, watch, decoder, title: str, fps: float = 60.0, full: bool = False,
                 box: int = DEFAULT_BOX) -> None:
        super().__init__()
        self.watch = watch
        self.decoder = decoder
        self.titleText = title
        self.fps = fps
        self.full = full
        self.box = box
        self.showStats = False
        self.showKeys = False
        self._lastShown = 0
        self._lastT = time.monotonic()
        self._fpsNow = 0.0

    def refresh(self, *regions, **kw):
        #textual forwards changes from the chat screen underneath as region refreshes
        #they paint blank cells over the picture and thats the flicker so drop them
        if regions and not kw:
            return self
        return super().refresh(*regions, **kw)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield LiveSixel(self.decoder, fps=self.fps, full=self.full, box=self.box, id="live")
            yield _Bar(id="live-bar")  #keeps the picture off the last row

    def on_mount(self) -> None:
        self.set_interval(1.0, self._tick)
        if not sixelview.GRAPHICS:
            self.query_one(LiveSixel).message = "This terminal can't show sixel graphics, opened it in mpv"
            if not self.decoder.popoutOpen():
                self.decoder.popout(f"uncord · {self.titleText}")
        self._tick()

    def _tick(self) -> None:
        live = self.query_one(LiveSixel)
        now = time.monotonic()
        self._fpsNow = (live.shown - self._lastShown) / max(0.001, now - self._lastT)
        self._lastShown, self._lastT = live.shown, now
        st = self.watch.stats
        if not live.hasFrame:
            old = live.message
            if st["frames"]:
                live.message = "Decoding…"
            elif st["video_dave_fail"]:
                live.message = "Waiting for encryption keys…"
            elif st["video"]:
                live.message = "Waiting for a keyframe…"
            elif st["unknown"]:
                live.message = "Can't decode this stream"
            elif self.watch.conn is not None:
                live.message = "Waiting for video…"
            if live.message != old:
                live.refresh()
        t = Text(self.titleText, style="bright_black")
        if getattr(self.app, "_watchAudioMuted", False):
            t.append("  muted", style="bright_black")
        elif st["frames"] > 120 and not st["audio"] and getattr(self.watch, "hasAudio", True):
            t.append("  no audio", style="bright_black")
        if self.decoder.popoutOpen():
            t.append("  · in mpv", style="bright_black")
        if self.showStats:
            t.append(
                f"  {self._fpsNow:.0f} fps (skipped {live.skipped}) · " + self.watch.statsText(),
                style="bright_black",
            )
        if self.showKeys:
            t.append("   " + self.KEYS, style="bright_black")
        self.query_one("#live-bar", _Bar).set(t)

    def action_keys(self) -> None:
        self.showKeys = not self.showKeys
        self._tick()

    def action_close(self) -> None:
        self.dismiss("close")

    def action_stop(self) -> None:
        self.dismiss("stop")

    def action_full(self) -> None:
        live = self.query_one(LiveSixel)
        live.toggleFull()
        try:
            self.app.cfg["watch_full"] = live.full
            self.app.cfg.save()
        except Exception:
            pass

    def action_size(self, step: int) -> None:
        live = self.query_one(LiveSixel)
        live.resizeBox(step)
        try:
            self.app.cfg["watch_size"] = live.box
            self.app.cfg["watch_full"] = False
            self.app.cfg.save()
        except Exception:
            pass
        self._tick()

    def action_popout(self) -> None:
        if self.decoder.popoutOpen():
            self.decoder.closePopout()
        elif not self.decoder.popout(f"uncord · {self.titleText}"):
            self.app.notify("mpv isn't installed.", severity="error")
        self._tick()

    def action_mute(self) -> None:
        self.app._watchAudioMuted = not getattr(self.app, "_watchAudioMuted", False)
        self._tick()

    def action_keyframe(self) -> None:
        self.watch._lastPli = 0.0
        self.watch.requestKeyframe()

    def action_stats(self) -> None:
        self.showStats = not self.showStats
        self._tick()
