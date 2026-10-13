#settings screen categories on the left and their options on the right, changes apply straight away
from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Select, Static, Switch
from textual.widgets.option_list import Option

from . import __version__, plugins

CATEGORIES = (("appearance", "🎨 Appearance"), ("notifications", "🔔 Notifications"), ("chat", "💬 Chat"),
              ("voice", "🎙 Voice & video"), ("plugins", "🧩 Plugins"), ("about", "ℹ About"))

SPACING = (("Compact", "0"), ("Cozy (a gap between people)", "1"), ("Roomy", "2"))


class SettingsScreen(ModalScreen):

    BINDINGS = [Binding("escape", "dismiss(None)", "Close", show=False)]
    DEFAULT_CSS = """
    SettingsScreen .settings { width: 110; max-width: 98%; height: 36; max-height: 95%; }
    SettingsScreen #st-cats { width: 24; height: 1fr; border: none; background: transparent; }
    SettingsScreen #st-body { width: 1fr; height: 1fr; padding: 0 1; border-left: vkey $foreground 20%; }
    SettingsScreen .st-row { height: auto; margin-bottom: 1; }
    SettingsScreen .st-row Switch { border: none; height: 1; width: 6; padding: 0; margin-right: 1; }
    SettingsScreen .st-row Static { width: 1fr; }
    SettingsScreen .st-head { margin: 1 0 0 0; text-style: bold; color: $text-muted; }
    SettingsScreen Select { width: 40; margin-bottom: 1; }
    SettingsScreen Input { width: 40; margin-bottom: 1; }
    SettingsScreen Button { margin: 0 1 1 0; min-width: 16; }
    """

    def __init__(self, category: str = "appearance") -> None:
        super().__init__()
        self.category = category
        self.filter = ""

    @property
    def cfg(self):
        return self.app.cfg

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal settings"):
            yield Static(Text("Settings", style="bold") + Text("   esc close · changes save straight away",
                                                               style="bright_black"), classes="modal-title")
            with Horizontal():
                yield OptionList(*[Option(label, id=key) for key, label in CATEGORIES], id="st-cats")
                yield VerticalScroll(id="st-body")

    def on_mount(self) -> None:
        cats = self.query_one("#st-cats", OptionList)
        cats.highlighted = [k for k, _l in CATEGORIES].index(self.category)
        cats.focus()
        self.show(self.category)

    @on(OptionList.OptionHighlighted, "#st-cats")
    def _pick(self, ev) -> None:
        if ev.option.id != self.category:
            self.show(ev.option.id)

    #building the right side

    def switch(self, key: str, label: str, value: bool, hint: str = "") -> Horizontal:
        text = Text(label)
        if hint:
            text.append(f"\n{hint}", style="bright_black")
        return Horizontal(Switch(value=value, id=f"sw-{key}"), Static(text), classes="st-row")

    def head(self, text: str) -> Static:
        return Static(text.upper(), classes="st-head")

    def show(self, category: str) -> None:
        self.category = category
        body = self.query_one("#st-body", VerticalScroll)
        body.remove_children()
        body.mount_all(list(getattr(self, f"_{category}")()))
        body.scroll_home(animate=False)

    def _appearance(self):
        cfg = self.cfg
        themes = sorted(self.app.available_themes)
        yield self.head("Theme")
        yield Select([(t, t) for t in themes], value=self.app.theme if self.app.theme in themes else themes[0],
                     allow_blank=False, id="sel-theme")
        yield self.head("Messages")
        yield Static("Line spacing")
        yield Select(SPACING, value=str(cfg.get("message_spacing", 1)), allow_blank=False, id="sel-message_spacing")
        yield self.switch("inline_media", "Show images and GIFs in chat", cfg.get("inline_media", True))
        yield self.switch("emoji_images", "Draw custom emoji as images", cfg.get("emoji_images", True))
        yield self.head("Layout")
        yield self.switch("show_sidebar", "Server and DM list (Ctrl+B)", cfg.get("show_sidebar", True))
        yield self.switch("show_members", "Members list (F2)", bool(cfg.get("show_members", False)))
        yield self.head("Streams and cameras")
        from .streamview import BOXES, DEFAULT_BOX

        yield Static("Picture size in the terminal (+ and - while watching)")
        yield Select([(f"{w}×{h}", str(i)) for i, (w, h) in enumerate(BOXES)],
                     value=str(cfg.get("watch_size", DEFAULT_BOX)), allow_blank=False, id="sel-watch_size")
        yield Static("Highest frame rate")
        yield Select([(f"{n} fps", str(n)) for n in (15, 24, 30, 60)], value=str(int(cfg.get("watch_fps", 60))),
                     allow_blank=False, id="sel-watch_fps")

    def _notifications(self):
        cfg = self.cfg
        yield self.switch("desktop_notify", "Desktop notifications", cfg.get("desktop_notify", True),
                          "for DMs and mentions while uncord isn't focused")
        yield self.switch("sfx", "Call sounds", cfg.get("sfx", True), "join, leave, mute and deafen")
        from .widgets import Slider

        yield Static("Call sound volume")
        yield Slider(int(cfg.get("sfx_volume", 100)), 0, 200, 5, default=100, id="sl-sfx_volume")
        yield self.switch("mark_read", "Mark chats as read when you open them", cfg.get("mark_read", True))

    def _chat(self):
        cfg = self.cfg
        yield Static("DMs listed in the sidebar")
        yield Select([(str(n), str(n)) for n in (10, 20, 30, 50, 100)], value=str(cfg.get("dm_limit", 30)),
                     allow_blank=False, id="sel-dm_limit")
        yield Static("Translate messages into (language code like en, fr, es)")
        yield Input(value=cfg.get("translate_to") or "", placeholder="your system language", id="in-translate_to")
        yield Static("File picker for uploads")
        from .filepick import TUI_CHOOSERS

        choices = [("Automatic", "auto"), ("Built in", "builtin"), ("Desktop dialog", "portal")] + [
            (n, n) for n in TUI_CHOOSERS]
        yield Select(choices, value=cfg.get("file_picker", "auto") if cfg.get("file_picker", "auto") in
                     {v for _l, v in choices} else "auto", allow_blank=False, id="sel-file_picker")

    def _voice(self):
        cfg = self.cfg
        yield Static("Microphone, speakers, volumes and mic sensitivity are in voice settings.")
        yield Button("Open voice settings (F9)", id="btn-voice")
        from .calls import SHARE_QUALITIES

        yield self.head("Screen sharing")
        yield Static("Quality")
        yield Select([(q, q) for q in SHARE_QUALITIES], value=self.app.shareQuality(), allow_blank=False,
                     id="sel-share_quality")
        yield self.head("Camera")
        from . import golive

        devices = sorted({m[0] for m in golive.listCameras()})
        if devices:
            yield Select([(d, d) for d in devices],
                         value=cfg.get("camera_device") if cfg.get("camera_device") in devices else devices[0],
                         allow_blank=False, id="sel-camera_device")
        else:
            yield Static(Text("No camera found (needs gpu-screen-recorder)", style="bright_black"))

    def _plugins(self):
        yield Input(value=self.filter, placeholder="Search plugins…", id="in-plugin-filter")
        on = sum(plugins.enabled(self.cfg, k) for k, *_r in plugins.PLUGINS)
        yield Static(Text(f"{on} of {len(plugins.PLUGINS)} on", style="bright_black"))
        for key, name, desc, _d in plugins.PLUGINS:
            row = Horizontal(Switch(value=plugins.enabled(self.cfg, key), id=f"pl-{key}"),
                             Static(Text(name, style="bold") + Text(f"\n{desc}", style="bright_black")),
                             classes="st-row plugin-row", id=f"row-{key}")
            row.searchText = f"{name} {desc}".lower()
            yield row
        yield Button("Turn all back to defaults", id="btn-plugin-reset")

    def _about(self):
        user = self.app.client.user
        t = Text(f"uncord {__version__}\n", style="bold")
        t.append("Discord in your terminal\n\n", style="bright_black")
        if user is not None:
            t.append(f"Logged in as {user.name}\n", style="bright_black")
        t.append("https://github.com/epwv/uncord\nhttps://discord.gg/fubfK8C6Cm", style="cyan")
        yield Static(t)
        yield Button("Check for updates", id="btn-update")
        yield Button("What's installed", id="btn-doctor")
        yield Static("", id="about-out")

    #changes

    def on_switch_changed(self, ev) -> None:
        sid = ev.switch.id or ""
        app = self.app
        if sid.startswith("pl-"):
            key = sid[3:]
            if plugins.enabled(self.cfg, key) != ev.value:
                plugins.toggle(self.cfg, key)
                app.pluginChanged(key)
            return
        if not sid.startswith("sw-"):
            return
        key = sid[3:]
        if key == "show_sidebar":
            if app.cfg.get("show_sidebar", True) != ev.value:
                app.action_toggle_sidebar()
            return
        if key == "show_members":
            if bool(app.cfg.get("show_members", False)) != ev.value:
                app.action_toggle_members()
            return
        self.cfg[key] = ev.value
        self.cfg.save()
        if key == "desktop_notify":
            app.applyNotify()
        elif key in ("inline_media", "emoji_images") and app.current is not None:
            app.loadHistory(app.current)

    def on_select_changed(self, ev) -> None:
        sid = ev.select.id or ""
        if not sid.startswith("sel-") or ev.value is Select.BLANK:
            return
        key, app = sid[4:], self.app
        value = ev.value
        if key == "theme":
            if value != app.theme:
                app._setTheme(value)
            return
        if key in ("message_spacing", "watch_size", "dm_limit", "watch_fps"):
            value = int(value)
        if self.cfg.get(key) == value:
            return
        self.cfg[key] = value
        self.cfg.save()
        if key == "message_spacing":
            app.applySpacing()
        elif key == "dm_limit":
            app.rebuildDms()

    def on_slider_changed(self, ev) -> None:
        if ev.slider.id == "sl-sfx_volume":
            self.cfg["sfx_volume"] = int(ev.value)
            self.cfg.save()

    def on_input_changed(self, ev) -> None:
        if ev.input.id == "in-plugin-filter":
            self.filter = ev.value.lower()
            for row in self.query(".plugin-row"):
                row.display = not self.filter or self.filter in row.searchText

    def on_input_submitted(self, ev) -> None:
        if ev.input.id == "in-translate_to":
            self.cfg["translate_to"] = ev.value.strip().lower()[:5]
            self.cfg.save()
            self.app.notify("Saved.", timeout=2)

    def on_input_blurred(self, ev) -> None:
        if getattr(ev.input, "id", "") == "in-translate_to":
            self.cfg["translate_to"] = ev.input.value.strip().lower()[:5]
            self.cfg.save()

    def on_button_pressed(self, ev) -> None:
        bid = ev.button.id
        if bid == "btn-voice":
            self.dismiss(None)
            self.app.call_later(self.app.action_voice_settings)
        elif bid == "btn-plugin-reset":
            self.cfg["plugins"] = {}
            self.cfg.save()
            for key, *_r in plugins.PLUGINS:
                self.app.pluginChanged(key)
            self.show("plugins")
        elif bid == "btn-update":
            self.query_one("#about-out", Static).update(Text("checking…", style="bright_black"))
            self.run_worker(self._checkUpdate(), exclusive=True)
        elif bid == "btn-doctor":
            import contextlib
            import io

            from .deps import doctor

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                doctor()
            self.query_one("#about-out", Static).update(Text(buf.getvalue()))

    async def _checkUpdate(self) -> None:
        import asyncio

        from .update import sourceDir, _git

        out = self.query_one("#about-out", Static)
        src = sourceDir()
        if src is None:
            out.update(Text("Not a git checkout, run uncord --update to reinstall the latest.", style="bright_black"))
            return
        fetched = await asyncio.to_thread(_git, src, "fetch", "--quiet")
        if fetched.returncode != 0:
            out.update(Text("Couldn't reach GitHub.", style="red"))
            return
        behind = (await asyncio.to_thread(_git, src, "rev-list", "--count", "HEAD..@{u}")).stdout.strip()
        if behind.isdigit() and int(behind):
            out.update(Text(f"{behind} new commit(s). Quit and run uncord --update.", style="bold yellow"))
        else:
            out.update(Text("You're up to date.", style="green"))
