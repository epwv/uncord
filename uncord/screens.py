#popup screens pickers search image viewer profile gifs file browser
from __future__ import annotations

import asyncio
import io
import logging
from typing import Awaitable, Callable, List, Optional

import discord
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from .fmt import (PRIVATE, STATUS_NAME, activityText, humanSize, nameBadges, nameColour, platforms, snippet,
                  statusDot, userStatus)

log = logging.getLogger("uncord")

try:
    from textual_image.widget import Image as TermImage
except Exception:
    TermImage = None


def loadPil(data: bytes):
    from PIL import Image as PILImage

    img = PILImage.open(io.BytesIO(data))
    try:
        img.seek(0)
    except Exception:
        pass
    return img.convert("RGBA")


class Picker(ModalScreen):

    BINDINGS = [Binding("escape", "dismiss(None)", "Close", show=False)]

    def __init__(self, entries, placeholder: str = "Search…", query: str = "", title: str = "") -> None:
        super().__init__()
        self.entries = entries
        self.placeholder = placeholder
        self.initial = query
        self.titleText = title
        self._shown: List[int] = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            if self.titleText:
                yield Static(Text(self.titleText, style="bold"), classes="modal-title")
            yield Input(value=self.initial, placeholder=self.placeholder, id="pk-input")
            yield OptionList(id="pk-list")

    def on_mount(self) -> None:
        self._fill(self.initial)
        self.query_one("#pk-input", Input).focus()

    def _fill(self, q: str) -> None:
        ol = self.query_one(OptionList)
        ol.clear_options()
        q = q.lower().strip()
        words = q.split()
        scored = []
        for i, (_label, hay, _v) in enumerate(self.entries):
            if not words:
                scored.append((0, i))
                continue
            if all(w in hay for w in words):
                scored.append((hay.find(words[0]) + (0 if hay.startswith(words[0]) else 50), i))
            else:
                it = iter(hay)
                if all(c in it for c in q.replace(" ", "")):
                    scored.append((1000 + len(hay), i))
            if len(scored) > 4000:
                break
        scored.sort()
        self._shown = [i for _s, i in scored[:250]]
        ol.add_options([Option(self.entries[i][0], id=str(i)) for i in self._shown])
        if self._shown:
            ol.highlighted = 0

    @on(Input.Changed, "#pk-input")
    def _changed(self, ev: Input.Changed) -> None:
        self._fill(ev.value)

    def on_key(self, event) -> None:
        ol = self.query_one(OptionList)
        if event.key in ("down", "ctrl+n", "tab", "ctrl+j"):
            ol.action_cursor_down()
            event.stop()
        elif event.key in ("up", "ctrl+p", "shift+tab"):
            ol.action_cursor_up()
            event.stop()
        elif event.key in ("pagedown",):
            ol.action_page_down()
            event.stop()
        elif event.key in ("pageup",):
            ol.action_page_up()
            event.stop()

    @on(Input.Submitted, "#pk-input")
    def _submit(self) -> None:
        ol = self.query_one(OptionList)
        if ol.highlighted is not None and ol.option_count:
            self.dismiss(self.entries[int(ol.get_option_at_index(ol.highlighted).id)][2])

    @on(OptionList.OptionSelected)
    def _selected(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(self.entries[int(ev.option.id)][2])


class MessageSearch(ModalScreen):

    BINDINGS = [Binding("escape", "dismiss(None)", "Close", show=False)]

    def __init__(self, scopeName: str, searcher: Callable[[str], Awaitable[list]]) -> None:
        super().__init__()
        self.scopeName = scopeName
        self.searcher = searcher
        self.results: list = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static(Text(f"Search {self.scopeName}", style="bold"), classes="modal-title")
            yield Input(
                placeholder="words…  from:name  in:channel  has:link|image|file  — enter to search",
                id="sr-input",
            )
            yield Static("", id="sr-status")
            yield OptionList(id="pk-list")

    @on(Input.Submitted, "#sr-input")
    def _go(self, ev: Input.Submitted) -> None:
        if ev.value.strip():
            self.runSearch(ev.value.strip())

    @work(exclusive=True)
    async def runSearch(self, q: str) -> None:
        status = self.query_one("#sr-status", Static)
        status.update(Text("searching…", style="bright_black"))
        try:
            self.results = await self.searcher(q)
        except Exception as e:
            status.update(Text(f"search failed: {e}", style="red"))
            return
        ol = self.query_one(OptionList)
        ol.clear_options()
        for i, m in enumerate(self.results):
            t = Text()
            t.append(m.created_at.astimezone().strftime("%d %b %y %H:%M "), style="bright_black")
            if not isinstance(m.channel, PRIVATE):
                t.append(f"#{getattr(m.channel, 'name', '?')} ", style="cyan")
            t.append(m.author.display_name, style=f"bold {nameColour(m.author)}")
            t.append(": " + snippet(m.clean_content or ("[attachment]" if m.attachments else ""), 90))
            ol.add_option(Option(t, id=str(i)))
        status.update(Text(f"{len(self.results)} result(s) — ↓ then enter to jump", style="bright_black"))
        if self.results:
            ol.highlighted = 0
            ol.focus()

    @on(OptionList.OptionSelected)
    def _picked(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(self.results[int(ev.option.id)])


class Prompt(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss(None)", "Close", show=False)]

    def __init__(self, title: str, placeholder: str = "", value: str = "") -> None:
        super().__init__()
        self.titleText = title
        self.placeholder = placeholder
        self.value = value

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal small"):
            yield Static(Text(self.titleText, style="bold"), classes="modal-title")
            yield Input(value=self.value, placeholder=self.placeholder)

    @on(Input.Submitted)
    def _done(self, ev: Input.Submitted) -> None:
        self.dismiss(ev.value.strip())


class TextScreen(ModalScreen):

    BINDINGS = [Binding("escape,q,question_mark,f1", "dismiss(None)", "Close", show=False)]

    def __init__(self, title: str, body) -> None:
        super().__init__()
        self.titleText = title
        self.body = body

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal tall"):
            yield Static(Text(self.titleText, style="bold"), classes="modal-title")
            with VerticalScroll():
                yield Static(self.body)


class ImageScreen(ModalScreen):

    BINDINGS = [
        Binding("escape,q,v", "dismiss(None)", "Close", show=False),
        Binding("o", "open", "Open externally", show=False),
        Binding("space", "pause", "Pause", show=False),
        Binding("left,h", "step(-1)", "Prev", show=False),
        Binding("right,l", "step(1)", "Next", show=False),
    ]

    def __init__(self, items, fetch: Callable[[str], Awaitable[bytes]], index: int = 0) -> None:
        super().__init__()
        self.items = items
        self.index = index
        self.fetch = fetch
        self.frames: list = []
        self.paused = False
        self._widget = None

    @property
    def item(self):
        return self.items[self.index]

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal image"):
            yield Static("", classes="modal-title", id="img-title")
            yield Static(Text("loading…", style="bright_black"), id="img-slot")

    def _title(self) -> Text:
        t = Text(self.item.title, style="bold")
        if len(self.items) > 1:
            t.append(f"  {self.index + 1}/{len(self.items)}  ←/→", style="bright_black")
        hint = "   o open externally · esc close"
        if len(self.frames) > 1:
            hint = "   space pause" + hint
        t.append(hint, style="bright_black")
        return t

    def on_mount(self) -> None:
        self.load()

    def action_step(self, delta: int) -> None:
        if 0 <= self.index + delta < len(self.items):
            self.index += delta
            self.load()

    @work(exclusive=True, group="load")
    async def load(self) -> None:
        from . import media, sixelview

        self.frames = []
        self.query_one("#img-title", Static).update(self._title())
        item = self.item
        try:
            plain = item.url.lower().split("?")[0]
            if item.kind in ("gif", "video") and plain.endswith(media.VIDEO_EXT):
                frames = await media.videoFrames(item.url, fps=15, width=960, seconds=20)
            else:
                data = await self.fetch(item.url)
                frames = await asyncio.to_thread(media.gifFrames, data, 300, 1600)
        except Exception as e:
            log.debug("viewer load failed", exc_info=True)
            self._message(f"Couldn't show this here ({e.__class__.__name__}). Press o to open it externally.")
            return
        if not frames:
            self._message("Nothing to show. Press o to open externally.")
            return
        if item is not self.item:
            return
        self.frames = frames
        self.query_one("#img-title", Static).update(self._title())
        box = self.query_one(".modal")
        for w in list(self.query("#img-slot")) + ([self._widget] if self._widget is not None else []):
            await w.remove()
        size = box.content_size
        kw = {"alwaysAnimate": True} if sixelview.GRAPHICS else {}
        self._widget = sixelview.mediaWidget(
            frames, max(10, size.width), max(4, size.height - 2), classes="viewer", **kw
        )
        await box.mount(self._widget)

    def action_pause(self) -> None:
        self.paused = not self.paused
        if self._widget is not None and hasattr(self._widget, "paused"):
            self._widget.paused = self.paused

    def _message(self, text: str) -> None:
        for w in self.query("#img-slot"):
            w.update(Text(text, style="red"))
            return
        if self._widget is not None:
            self._widget.remove()
            self._widget = None
        self.query_one(".modal").mount(Static(Text(text, style="red"), id="img-slot"))

    def action_open(self) -> None:
        self.app.openExternal(self.item.url)


class Confirm(ModalScreen):
    BINDINGS = [
        Binding("y,enter", "dismiss(True)", "Yes", show=False),
        Binding("n,escape,q", "dismiss(False)", "No", show=False),
    ]

    def __init__(self, question: str, detail: str = "") -> None:
        super().__init__()
        self.question = question
        self.detail = detail

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal confirm"):
            yield Static(Text(self.question, style="bold"))
            if self.detail:
                yield Static(Text(self.detail, style="bright_black"))
            yield Static(Text.assemble(("y", "bold green"), "/enter yes   ", ("n", "bold red"), "/esc no"))


class ProfileScreen(ModalScreen):
    BINDINGS = [
        Binding("escape,q,p", "dismiss(None)", "Close", show=False),
        Binding("m", "dismiss('dm')", "Message", show=False),
        Binding("v", "avatar", "View avatar", show=False),
    ]

    def __init__(self, user, client: discord.Client, guild=None) -> None:
        super().__init__()
        self.user = user
        self.client = client
        self.guild = guild

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal profile"):
            with Horizontal(id="pf-row"):
                yield Static("", id="pf-avatar")
                yield Static(self._info(None), id="pf-info")
            yield Static(Text("m message · v view avatar · esc close", style="bright_black"), id="pf-help")

    #uservoiceshow which voice channel theyre in that we can see
    def _voiceChannel(self, uid: int):
        for g in self.client.guilds:
            m = g.get_member(uid)
            ch = getattr(getattr(m, "voice", None), "channel", None)
            if ch is not None:
                return f"{ch.name} ({g.name})"
        return None

    def _info(self, profile) -> Text:
        u = self.user
        t = Text()
        t.append(u.display_name, style=f"bold {nameColour(u)}")
        t.append_text(nameBadges(u))
        t.append(f"\n@{u.name}", style="bright_black")
        pron = getattr(profile, "pronouns", None) if profile else None
        if pron:
            t.append(f" · {pron}", style="bright_black")
        st = userStatus(u)
        t.append("\n")
        t.append_text(statusDot(st))
        t.append(STATUS_NAME.get(st, st))
        devices = [name for _i, name in platforms(u)]
        if devices:
            t.append(" · on " + (" and ".join(devices) if len(devices) < 3 else ", ".join(devices)), style="bright_black")
        act = activityText(u)
        if act:
            t.append(f"  ·  {act}", style="italic")
        bio = getattr(profile, "bio", None) if profile else None
        if bio:
            t.append("\n\n" + bio.strip())
        t.append("\n\n")
        t.append(f"Member since {u.created_at.astimezone():%d %b %Y}", style="bright_black")
        rel = getattr(u, "relationship", None) or getattr(self.client.get_user(u.id), "relationship", None)
        since = getattr(rel, "since", None)
        if since is not None and str(getattr(rel, "type", "")).endswith("friend"):
            t.append(f"\nFriends since {since.astimezone():%d %b %Y}", style="bright_black")
        conns = getattr(profile, "connections", None) if profile else None
        if conns and self.app.plugin("show_connections"):
            #showconnections linked accounts like steam spotify github
            names = [f"{str(getattr(c.type, 'name', c.type)).replace('_', ' ').title()}: {c.name}" for c in conns[:8]]
            t.append("\n🔗 " + " · ".join(names), style="cyan")
        where = self._voiceChannel(u.id)
        if where:
            t.append(f"\nIn voice: 🔊 {where}", style="green")
        if isinstance(u, discord.Member):
            if u.joined_at:
                t.append(f"\nJoined {u.guild.name} {u.joined_at.astimezone():%d %b %Y}", style="bright_black")
            roles = [r for r in reversed(u.roles) if not r.is_default()]
            if roles:
                t.append("\nRoles: ", style="bright_black")
                for r in roles[:15]:
                    t.append(f"{r.name} ", style=f"{nameColour(r)}")
        mg = getattr(profile, "mutual_guilds", None) if profile else None
        if mg:
            names = [getattr(x.guild, "name", "?") for x in mg[:6]]
            t.append(
                f"\nMutual servers: {', '.join(names)}" + (" …" if len(mg) > 6 else ""), style="bright_black"
            )
        if getattr(u, "is_friend", lambda: False)():
            t.append("\nFriend", style="green")
        return t

    def on_mount(self) -> None:
        self.load()

    @work(exclusive=True)
    async def load(self) -> None:
        profile = None
        try:
            profile = await self.client.fetch_user_profile(self.user.id)
        except Exception:
            log.debug("profile fetch failed", exc_info=True)
        self.query_one("#pf-info", Static).update(self._info(profile))
        if TermImage is None:
            return
        try:
            data = await self.user.display_avatar.with_size(128).with_static_format("png").read()
            img = loadPil(data)
        except Exception:
            return
        slot = self.query_one("#pf-avatar", Static)
        await self.query_one("#pf-row").mount(TermImage(img, id="pf-img"), before=slot)
        await slot.remove()

    def action_avatar(self) -> None:
        from .media import MediaItem

        url = self.user.display_avatar.with_size(512).url
        self.app.push_screen(
            ImageScreen(
                [MediaItem(f"{self.user.display_name}'s avatar", url, url, "image")], self.app.fetchCdn
            )
        )


class GifTile(Vertical):

    DEFAULT_CSS = """
    GifTile { width: 1fr; height: 10; border: round $foreground 15%; align: center middle; }
    GifTile.sel { border: round $accent; }
    GifTile .gifph { width: auto; height: 1; color: $text-muted; }
    """

    def __init__(self, i: int, g: dict) -> None:
        super().__init__()
        self.i = i
        self.g = g
        self.state = "new"
        self.media = None

    def compose(self) -> ComposeResult:
        yield Static(Text("·", style="bright_black"), classes="gifph")

    def on_click(self, event) -> None:
        event.stop()
        self.screen.dismiss(self.g)

    def on_enter(self, event) -> None:
        self.screen.select(self.i, scroll=False)


class GifPicker(ModalScreen):

    COLS = 4
    BINDINGS = [Binding("escape", "dismiss(None)", "Close", show=False)]
    DEFAULT_CSS = """
    #gif-grid { layout: grid; grid-size: 4; grid-gutter: 0 1; height: auto; }
    #gif-scroll { height: 1fr; scrollbar-size-vertical: 1; }
    """

    def __init__(self, loader, framesFor) -> None:
        super().__init__()
        self.loader = loader
        self.framesFor = framesFor
        self.all: list = []
        self.tiles: List[GifTile] = []
        self.sel = 0

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal gifs"):
            yield Static(
                Text("Favourite GIFs", style="bold")
                + Text(
                    "   type to filter · arrows move · enter/click send · esc close", style="bright_black"
                ),
                classes="modal-title",
                id="gif-title",
            )
            yield Input(placeholder="Search your favourites", id="gif-q", compact=True)
            with VerticalScroll(id="gif-scroll"):
                yield Static(Text("loading favourites…", style="bright_black"), id="gif-empty")
                yield Vertical(id="gif-grid")

    def on_mount(self) -> None:
        self.query_one("#gif-q", Input).focus()
        self.load()
        self.set_interval(0.2, self._loadVisible)

    @work(exclusive=True, group="gifload")
    async def load(self) -> None:
        try:
            self.all = await self.loader()
        except Exception as e:
            self.query_one("#gif-empty", Static).update(Text(f"Couldn't load favourites: {e}", style="red"))
            return
        await self.show(self.query_one("#gif-q", Input).value)

    async def show(self, q: str) -> None:
        q = q.strip().lower()
        gs = [g for g in self.all if not q or q in (g.get("title") or "").lower() or q in g["url"].lower()]
        grid = self.query_one("#gif-grid")
        await grid.remove_children()
        empty = self.query_one("#gif-empty", Static)
        if not gs:
            empty.display = True
            empty.update(
                Text(
                    "No favourite GIFs"
                    + (" match that." if q else " yet. Star GIFs in Discord and they show up here."),
                    style="bright_black",
                )
            )
            self.tiles = []
            return
        empty.display = False
        self.tiles = [GifTile(i, g) for i, g in enumerate(gs)]
        await grid.mount_all(self.tiles)
        self.sel = 0
        self.select(0)

    @on(Input.Changed, "#gif-q")
    def _changed(self, ev: Input.Changed) -> None:
        if self.all:
            self.run_worker(self.show(ev.value), exclusive=True, group="gifshow")

    def select(self, i: int, scroll: bool = True) -> None:
        if not self.tiles:
            return
        i = max(0, min(len(self.tiles) - 1, i))
        old = self.tiles[self.sel] if self.sel < len(self.tiles) else None
        if old is not None:
            old.remove_class("sel")
            if old.media is not None:
                old.media.alwaysAnimate = False
        self.sel = i
        t = self.tiles[i]
        t.add_class("sel")
        if t.media is not None:
            t.media.alwaysAnimate = True
        if scroll:
            self.query_one("#gif-scroll").scroll_to_widget(t, animate=False)

    def on_key(self, event) -> None:
        moves = {
            "right": 1,
            "left": -1,
            "down": self.COLS,
            "up": -self.COLS,
            "tab": 1,
            "shift+tab": -1,
            "pagedown": self.COLS * 3,
            "pageup": -self.COLS * 3,
        }
        if event.key in moves and self.tiles:
            self.select(self.sel + moves[event.key])
            event.stop()

    @on(Input.Submitted, "#gif-q")
    def _submit(self) -> None:
        if self.tiles:
            self.dismiss(self.tiles[self.sel].g)

    def _loadVisible(self) -> None:
        if not self.tiles:
            return
        view = self.query_one("#gif-scroll").region
        busy = sum(1 for t in self.tiles if t.state == "loading")
        for t in self.tiles:
            if busy >= 4:
                break
            if t.state == "new" and t.region.height and t.region.overlaps(view.grow((10, 0, 10, 0))):
                t.state = "loading"
                busy += 1
                self.run_worker(self._loadTile(t), group="giftile")

    async def _loadTile(self, t: GifTile) -> None:
        from . import sixelview

        try:
            frames = await self.framesFor(t.g)
        except Exception:
            frames = []
        t.state = "done"
        if not t.is_attached:
            return
        if not frames:
            for ph in t.query(".gifph"):
                ph.update(Text("(no preview)", style="bright_black"))
            return
        w = sixelview.mediaWidget(
            frames,
            24,
            8,
            classes="gifimg",
            **({"alwaysAnimate": t.has_class("sel")} if sixelview.GRAPHICS else {}),
        )
        await t.remove_children()
        t.media = w if sixelview.GRAPHICS else None
        await t.mount(w)


class UserMenu(ModalScreen):

    BINDINGS = [Binding("escape,q", "dismiss(None)", "Close", show=False)]

    def __init__(self, name: str, volume: int, on_volume: Callable[[int], None], actions: list) -> None:
        super().__init__()
        self.name_ = name
        self.volume = volume
        self.on_volume = on_volume
        self.actions = actions

    def compose(self) -> ComposeResult:
        from .widgets import Slider

        with Vertical(classes="modal usermenu"):
            yield Static(Text(self.name_, style="bold"), classes="modal-title")
            yield Static(Text("USER VOLUME", style="bold bright_black"))
            yield Slider(self.volume, 0, 200, 1, default=100, id="uvol")
            yield Static(Text("←/→ or drag · shift = ×10 · 0 mutes", style="bright_black"))
            yield OptionList(*[Option(lab, id=key) for lab, key in self.actions], id="pk-list")

    def on_mount(self) -> None:
        self.query_one("#uvol").focus()

    def on_slider_changed(self, ev) -> None:
        self.on_volume(int(ev.value))

    @on(OptionList.OptionSelected)
    def _sel(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(ev.option.id)


class FileBrowser(ModalScreen):

    BINDINGS = [Binding("escape", "close", "Close", show=False)]
    DEFAULT_CSS = """
    FileBrowser .fb-box { width: 92%; height: 90%; border: round $foreground 50%; background: $background; padding: 0 1; }
    FileBrowser #fb-path { height: 1; text-style: bold; }
    FileBrowser #fb-row { height: 1fr; }
    FileBrowser #fb-list { width: 1fr; border: none; background: transparent; }
    FileBrowser #fb-preview { width: 44%; padding: 0 1; border-left: vkey $foreground 20%; }
    FileBrowser #fb-info { height: auto; }
    FileBrowser #fb-help { height: 1; color: $text-muted; }
    """

    RECENT_DIRS = (
        "~/Pictures/Screenshots",
        "~/Pictures",
        "~/Downloads",
        "~/Videos/Clips",
        "~/Videos",
        "~/Documents",
        "~/Music",
        "~/Desktop",
    )

    def __init__(self, start: Optional[str] = None) -> None:
        super().__init__()
        import os

        self.os = os
        self.home = os.path.expanduser("~")
        self.cwd = start if start and os.path.isdir(start) else None
        self.filter = ""
        self.hidden = False
        self.marked: List[str] = []
        self.entries: List[str] = []
        self._previewFor = None
        self._gen = 0

    def compose(self) -> ComposeResult:
        with Vertical(classes="fb-box"):
            yield Static("", id="fb-path")
            with Horizontal(id="fb-row"):
                yield OptionList(id="fb-list")
                with VerticalScroll(id="fb-preview"):
                    yield Static("", id="fb-info")
            yield Static(
                Text(
                    "enter/→ open or pick · ←/⌫ up · space mark · type to filter · . hidden · ~ home · "
                    "tab recent · esc close"
                ),
                id="fb-help",
            )

    def on_mount(self) -> None:
        self.refreshList()
        self.query_one("#fb-list").focus()

    #listing

    def _short(self, path: str) -> str:
        return path.replace(self.home, "~", 1) if path.startswith(self.home) else path

    def _scan(self) -> List[str]:
        os = self.os
        if self.cwd is None:
            files = []
            for d in self.RECENT_DIRS:
                d = os.path.expanduser(d)
                try:
                    for e in os.scandir(d):
                        if e.is_file() and not e.name.startswith("."):
                            files.append((e.stat().st_mtime, e.path))
                except OSError:
                    continue
            return [p for _m, p in sorted(set(files), reverse=True)[:80]]
        try:
            ents = list(os.scandir(self.cwd))
        except OSError:
            return []
        ents = [e for e in ents if self.hidden or not e.name.startswith(".")]
        ents.sort(key=lambda e: (not e.is_dir(), e.name.lower()))
        return [e.path for e in ents]

    def refreshList(self, keep: Optional[str] = None) -> None:
        os = self.os
        items = self._scan()
        f = self.filter.lower()
        if f:
            items = [p for p in items if f in os.path.basename(p).lower()]
        self.entries = items
        ol = self.query_one("#fb-list", OptionList)
        ol.clear_options()
        opts = [Option(self._label(p)) for p in items]
        if not opts:
            opts.append(
                Option(
                    Text(
                        "  (empty)" if not f else f"  nothing matches “{self.filter}”", style="bright_black"
                    ),
                    disabled=True,
                )
            )
        ol.add_options(opts)
        self._updateHead()
        idx = items.index(keep) if keep in items else 0
        self._gen += 1
        if items:
            ol.highlighted = idx
            #optionlist can lose the cursor on relayout
            self.call_after_refresh(self._setCursor, idx, self._gen)
            self.preview(items[idx])

    def _setCursor(self, idx: int, gen: int) -> None:
        ol = self.query_one("#fb-list", OptionList)
        if gen == self._gen and ol.highlighted is None and 0 <= idx < ol.option_count:
            ol.highlighted = idx

    def _label(self, p: str) -> Text:
        os = self.os
        name = os.path.basename(p)
        mark = "✓ " if p in self.marked else "  "
        if os.path.isdir(p):
            return Text(mark) + Text("📁 " + name + "/", style="bold cyan")
        try:
            size = humanSize(os.stat(p).st_size)
        except OSError:
            size = "?"
        label = Text(mark, style="green bold") + Text(
            _fileIcon(p) + " " + name, style="bold green" if p in self.marked else ""
        )
        label.append(f"  {size}", style="bright_black")
        if self.cwd is None:
            label.append(f"  {self._short(os.path.dirname(p))}", style="bright_black")
        return label

    def _updateHead(self) -> None:
        where = "Recent files" if self.cwd is None else self._short(self.cwd)
        head = Text("Upload  ", style="bold") + Text(where, style="bold cyan")
        if self.filter:
            head.append(f"   filter: {self.filter}", style="yellow")
        if self.marked:
            head.append(f"   {len(self.marked)} marked · enter sends them", style="green")
        self.query_one("#fb-path", Static).update(head)

    def current(self) -> Optional[str]:
        ol = self.query_one("#fb-list", OptionList)
        i = ol.highlighted
        return self.entries[i] if i is not None and 0 <= i < len(self.entries) else None

    def go(self, folder: Optional[str], keep: Optional[str] = None) -> None:
        self.cwd = folder
        self.filter = ""
        self.refreshList(keep)

    #keys

    def on_key(self, event) -> None:
        os = self.os
        k = event.key
        cur = self.current()
        handled = True
        if k in ("enter", "right"):
            if cur and os.path.isdir(cur):
                self.go(cur)
            elif cur and k == "enter":
                self.dismiss(self.marked or [cur])
            elif cur is None and k == "enter" and self.marked:
                self.dismiss(self.marked)
        elif k == "left" or (k == "backspace" and not self.filter):
            if self.cwd is None:
                self.go(self.home)
            else:
                parent = os.path.dirname(self.cwd.rstrip("/")) or "/"
                self.go(parent, keep=self.cwd)
        elif k == "backspace":
            self.filter = self.filter[:-1]
            self.refreshList()
        elif k == "space":
            if cur and os.path.isfile(cur):
                (self.marked.remove if cur in self.marked else self.marked.append)(cur)
                ol = self.query_one("#fb-list", OptionList)
                i = self.entries.index(cur)
                ol.replace_option_prompt_at_index(i, self._label(cur))
                if i + 1 < len(self.entries):
                    ol.highlighted = i + 1
                self._updateHead()
        elif k == "tab":
            self.go(None)
        elif k == "tilde":
            self.go(self.home)
        elif k == "full_stop" and not self.filter:
            self.hidden = not self.hidden
            self.refreshList(keep=cur)
        elif event.character and event.character.isprintable() and len(event.character) == 1:
            self.filter += event.character
            self.refreshList()
        else:
            handled = False
        if handled:
            event.stop()
            event.prevent_default()

    def action_close(self) -> None:
        if self.filter:
            self.filter = ""
            self.refreshList()
        else:
            self.dismiss(None)

    @on(OptionList.OptionHighlighted, "#fb-list")
    def _hl(self, ev) -> None:
        cur = self.current()
        if cur:
            self.preview(cur)

    #preview

    @work(exclusive=True, group="fbpreview")
    async def preview(self, path: str) -> None:
        await asyncio.sleep(0.08)
        os = self.os
        box = self.query_one("#fb-preview", VerticalScroll)
        for w in list(box.query(".fbimg")):
            await w.remove()
        info = self.query_one("#fb-info", Static)
        try:
            st = os.stat(path)
        except OSError:
            info.update(Text("(can't read)", style="red"))
            return
        import datetime as _dt

        when = _dt.datetime.fromtimestamp(st.st_mtime).strftime("%d %b %Y %H:%M")
        name = Text(os.path.basename(path) + ("/" if os.path.isdir(path) else ""), style="bold")
        meta = Text(f"\n{when}", style="bright_black")
        if os.path.isdir(path):
            try:
                n = len([e for e in os.listdir(path) if not e.startswith(".")])
            except OSError:
                n = 0
            info.update(name + meta + Text(f"\n{n} items", style="bright_black"))
            return
        meta.append(f" · {humanSize(st.st_size)}", style="bright_black")
        low = path.lower()
        if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
            info.update(name + meta)
            try:
                from PIL import Image as PILImage

                def load():
                    img = PILImage.open(path)
                    size = img.size
                    img.seek(0)
                    img = img.convert("RGBA")
                    img.thumbnail((800, 800))
                    return img, size

                img, size = await asyncio.to_thread(load)
            except Exception:
                return
            if self._still(path):
                from . import sixelview

                info.update(name + meta + Text(f" · {size[0]}×{size[1]}", style="bright_black"))
                w = sixelview.mediaWidget(
                    [(img, 0.1)], max(10, box.size.width - 2), max(4, box.size.height - 5), classes="fbimg"
                )
                await box.mount(w)
            return
        if st.st_size < 2_000_000 and not low.endswith(
            (
                ".mp4",
                ".mkv",
                ".webm",
                ".mov",
                ".mp3",
                ".ogg",
                ".flac",
                ".wav",
                ".zip",
                ".gz",
                ".xz",
                ".iso",
                ".pdf",
            )
        ):
            try:
                with open(path, "rb") as fh:
                    head = fh.read(3000)
                if b"\x00" not in head:
                    text = head.decode(errors="replace")
                    lines = text.splitlines()[: max(5, box.size.height - 4)]
                    info.update(
                        name + meta + Text("\n\n" + "\n".join(l[:200] for l in lines), style="default")
                    )
                    return
            except OSError:
                pass
        info.update(name + meta)

    def _still(self, path: str) -> bool:
        return self.current() == path and self.is_attached


def _fileIcon(path: str) -> str:
    low = path.lower()
    if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
        return "🖼"
    if low.endswith((".mp4", ".mkv", ".webm", ".mov", ".avi")):
        return "🎞"
    if low.endswith((".mp3", ".ogg", ".flac", ".wav", ".opus", ".m4a")):
        return "🎵"
    if low.endswith((".zip", ".gz", ".xz", ".7z", ".tar", ".rar")):
        return "📦"
    return "📄"
