#settings with tabs along the top, opened from the ⚙ button by your name or f3, changes apply straight away
from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static, Switch, TabbedContent, TabPane

from . import __version__, env, plugins

TABS = (("appearance", "Appearance"), ("notifications", "Notifications"), ("chat", "Chat"),
        ("voice", "Voice & video"), ("privacy", "Privacy"), ("plugins", "Plugins"), ("about", "About"))

SPACING = (("Compact", "0"), ("Cozy (a gap between people)", "1"), ("Roomy", "2"))


class SettingsScreen(ModalScreen):

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Close", show=False),
        Binding("ctrl+pagedown,alt+right", "step(1)", show=False),
        Binding("ctrl+pageup,alt+left", "step(-1)", show=False),
    ]
    DEFAULT_CSS = """
    SettingsScreen .settings { width: 112; max-width: 98%; height: 38; max-height: 95%; }
    SettingsScreen TabbedContent { height: 1fr; }
    SettingsScreen TabPane { padding: 0; height: 1fr; }
    SettingsScreen .st-body { height: 1fr; padding: 0 1; scrollbar-size-vertical: 1; }
    SettingsScreen .st-row { height: auto; margin-bottom: 1; }
    SettingsScreen .st-row Switch { border: none; height: 1; width: 6; padding: 0; margin-right: 1; }
    SettingsScreen .st-row Static { width: 1fr; }
    SettingsScreen .st-head { margin: 1 0 0 0; text-style: bold; color: $text-muted; }
    SettingsScreen .st-cols { height: auto; }
    SettingsScreen .st-col { width: 1fr; height: auto; margin-right: 2; }
    SettingsScreen .st-col Select { width: 1fr; }
    SettingsScreen Select { width: 44; margin-bottom: 1; }
    SettingsScreen Input { width: 44; margin-bottom: 1; }
    SettingsScreen Button { margin: 0 1 1 0; min-width: 16; }
    SettingsScreen Slider { margin-bottom: 1; }
    SettingsScreen #st-foot { height: 1; color: $text-muted; }
    """

    def __init__(self, tab: str = "appearance") -> None:
        super().__init__()
        self.tab = tab if tab in dict(TABS) else "appearance"
        self.filter = ""
        self.built: set = set()
        self.devices: dict = {}
        self._meterTimer = None

    @property
    def cfg(self):
        return self.app.cfg

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal settings"):
            yield Static(Text("Settings", style="bold") + Text(f"   uncord {__version__}", style="bright_black"),
                         classes="modal-title")
            with TabbedContent(initial=self.tab, id="st-tabs"):
                for key, label in TABS:
                    with TabPane(label, id=key):
                        yield VerticalScroll(id=f"st-{key}", classes="st-body")
            yield Static("←/→ on the tabs or alt+←/→ to switch · esc close · changes save straight away", id="st-foot")

    def on_mount(self) -> None:
        self.build(self.tab)
        self.query_one("#st-tabs Tabs").focus()

    def on_unmount(self) -> None:
        self.app.stopMicTest()

    @on(TabbedContent.TabActivated)
    def _tab(self, ev) -> None:
        key = ev.pane.id
        if key:
            self.tab = key
            self.build(key)
        self._meter(key == "voice")

    def action_step(self, delta: int) -> None:
        keys = [k for k, _l in TABS]
        tabs = self.query_one("#st-tabs", TabbedContent)
        tabs.active = keys[(keys.index(self.tab) + delta) % len(keys)]

    def build(self, key: str, again: bool = False) -> None:
        if key in self.built and not again:
            return
        self.built.add(key)
        body = self.query_one(f"#st-{key}", VerticalScroll)
        body.remove_children()
        body.mount_all(list(getattr(self, f"_{key}")()))
        body.scroll_home(animate=False)

    #pieces

    def switch(self, key: str, label: str, value: bool, hint: str = "") -> Horizontal:
        text = Text(label)
        if hint:
            text.append(f"\n{hint}", style="bright_black")
        return Horizontal(Switch(value=value, id=f"sw-{key}"), Static(text), classes="st-row")

    def head(self, text: str) -> Static:
        return Static(text.upper(), classes="st-head")

    def select(self, key: str, options, value) -> Select:
        vals = {v for _l, v in options}
        return Select(options, value=value if value in vals else options[0][1], allow_blank=False, id=f"sel-{key}")

    def slider(self, key: str, value, lo, hi, step=1, **kw):
        from .widgets import Slider

        return Slider(value, lo, hi, step, id=f"sl-{key}", **kw)

    #tabs

    def _appearance(self):
        cfg = self.cfg
        themes = sorted(self.app.available_themes)
        yield self.head("Theme")
        yield self.select("theme", [(t, t) for t in themes], self.app.theme)
        yield self.head("Messages")
        yield Static("Line spacing")
        yield self.select("message_spacing", SPACING, str(cfg.get("message_spacing", 1)))
        yield self.switch("inline_media", "Show images and GIFs in chat", cfg.get("inline_media", True))
        yield self.switch("emoji_images", "Draw custom emoji as images", cfg.get("emoji_images", True))
        yield self.switch("show_link_domains", "Show where masked links go",
                          cfg.get("show_link_domains", True), "[text](link) also shows the site it opens")
        yield self.head("Layout")
        yield self.switch("show_sidebar", "Server and DM list (Ctrl+B)", cfg.get("show_sidebar", True))
        yield self.switch("show_members", "Members list (F2)", bool(cfg.get("show_members", False)))
        yield self.head("Streams and cameras")
        from .streamview import BOXES, DEFAULT_BOX

        yield Static("Picture size in the terminal (+ and - while watching)")
        yield self.select("watch_size", [(f"{w}×{h}", str(i)) for i, (w, h) in enumerate(BOXES)],
                          str(cfg.get("watch_size", DEFAULT_BOX)))
        yield Static("Highest frame rate")
        yield self.select("watch_fps", [(f"{n} fps", str(n)) for n in (15, 24, 30, 60)],
                          str(int(cfg.get("watch_fps", 60))))

    def _notifications(self):
        cfg = self.cfg
        yield self.switch("desktop_notify", "Desktop notifications", cfg.get("desktop_notify", True),
                          "for DMs and mentions while uncord isn't focused")
        yield self.switch("notify_preview", "Show message text in notifications", cfg.get("notify_preview", True),
                          "off shows only who it's from, handy when sharing your screen")
        yield self.switch("mark_read", "Mark chats as read when you open them", cfg.get("mark_read", True))
        yield Static(Text("Call sounds are in Voice & video.", style="bright_black"))

    def _chat(self):
        cfg = self.cfg
        yield Static("DMs listed in the sidebar")
        yield self.select("dm_limit", [(str(n), str(n)) for n in (10, 20, 30, 50, 100)], str(cfg.get("dm_limit", 30)))
        yield Static("Translate messages into (language code like en, fr, es)")
        yield Input(value=cfg.get("translate_to") or "", placeholder="your system language", id="in-translate_to")
        yield Static("File picker for uploads")
        from .filepick import TUI_CHOOSERS

        yield self.select("file_picker", [("Automatic", "auto"), ("Built in", "builtin"), ("Desktop dialog", "portal")]
                          + [(n, n) for n in TUI_CHOOSERS], cfg.get("file_picker", "auto"))

    def _voice(self):
        from . import voice as vmod

        cfg = self.cfg
        self.devices = vmod.listDevices()

        def opts(kind: str, default: str):
            name = self.devices.get(default)
            desc = next((d for n, d in self.devices.get(kind, []) if n == name), name or "system")
            return [(f"Default ({desc})", "")] + [(d, n) for n, d in self.devices.get(kind, [])]

        src, sink = opts("sources", "default_source"), opts("sinks", "default_sink")
        yield Horizontal(
            Vertical(self.head("Input device"), self.select("input_device", src, cfg.get("input_device") or ""),
                     self.head("Input volume"), self.slider("input_volume", cfg.get("input_volume", 100), 0, 200,
                                                            default=100), classes="st-col"),
            Vertical(self.head("Output device"), self.select("output_device", sink, cfg.get("output_device") or ""),
                     self.head("Output volume"), self.slider("volume", cfg.get("volume", 100), 0, 200, default=100),
                     classes="st-col"),
            classes="st-cols")
        yield self.head("Input sensitivity")
        yield self.switch("auto_sensitivity", "Work it out automatically", bool(cfg.get("auto_sensitivity", True)))
        yield self.slider("sensitivity_db", cfg.get("sensitivity_db", -50), -100, 0, fmt=lambda v: f"{v:.0f}dB")
        yield Static("", id="st-meter")
        yield self.switch("agc", "Automatic gain control", bool(cfg.get("agc", True)),
                          "boosts a quiet mic to a normal level, like Discord")
        yield self.head("Call sounds")
        yield self.switch("sfx", "Play sounds when people join or leave, and for mute and deafen",
                          bool(cfg.get("sfx", True)))
        yield self.slider("sfx_volume", int(cfg.get("sfx_volume", 100)), 0, 200, 5, default=100)
        from .calls import SHARE_QUALITIES

        yield self.head("Screen sharing")
        yield Static("Quality")
        yield self.select("share_quality", [(q, q) for q in SHARE_QUALITIES], self.app.shareQuality())
        yield self.head("Camera")
        from . import golive

        devices = sorted({m[0] for m in golive.listCameras()})
        if devices:
            yield self.select("camera_device", [(d, d) for d in devices], cfg.get("camera_device"))
        else:
            yield Static(Text("No camera found (needs gpu-screen-recorder)", style="bright_black"))

    def _privacy(self):
        cfg = self.cfg
        yield self.switch("confirm_links", "Ask before opening links", cfg.get("confirm_links", False),
                          "shows the full address first, links that aren't http or https are never opened")
        yield self.switch("rich_presence_ipc", "Let apps on this computer set your activity",
                          self.app.plugin("rich_presence"), "games and music players use this for Rich Presence")
        yield self.head("Your account")
        yield Static(Text(f"Your login is kept in {env.tokenFile()}, readable only by you, unless it comes from "
                          f"UNCORD_TOKEN or UNCORD_TOKEN_CMD. Tokens are scrubbed from {env.logFile()}.",
                          style="bright_black"))
        yield Button("Log out", id="btn-logout", variant="error")
        yield Static("", id="privacy-out")

    def _plugins(self):
        yield Input(value=self.filter, placeholder="Search plugins…", id="in-plugin-filter")
        count = sum(plugins.enabled(self.cfg, k) for k, *_r in plugins.PLUGINS)
        yield Static(Text(f"{count} of {len(plugins.PLUGINS)} on", style="bright_black"))
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
        t.append("\n", style="bright_black")
        t.append("https://github.com/owainxyz/uncord\nhttps://discord.gg/fubfK8C6Cm", style="#00a8fc")
        yield Static(t)
        yield Button("Check for updates", id="btn-update")
        yield Button("What's installed", id="btn-doctor")
        yield Static("", id="about-out")

    #mic meter on the voice tab

    def _meter(self, on_: bool) -> None:
        if on_ and self._meterTimer is None:
            if self.app.vc is None:
                self.app.startMicTest()
            self._meterTimer = self.set_interval(0.06, self._tick)
            self._syncSens()
        elif not on_ and self._meterTimer is not None:
            self._meterTimer.stop()
            self._meterTimer = None
            self.app.stopMicTest()

    def _syncSens(self) -> None:
        try:
            self.query_one("#sl-sensitivity_db").disabled = self.query_one("#sw-auto_sensitivity", Switch).value
        except Exception:
            pass

    def _tick(self) -> None:
        from .widgets import Slider

        try:
            sens = self.query_one("#sl-sensitivity_db", Slider)
            meter = self.query_one("#st-meter", Static)
        except Exception:
            return
        lvl, gate = self.app.micLevelDb(), self.app.micGateDb()
        sens.setMarker(lvl)
        if self.query_one("#sw-auto_sensitivity", Switch).value:
            sens.set_reactive(Slider.value, round(gate))
            sens.refresh()
        if lvl is None:
            meter.update(Text("(the mic meter starts when a microphone is available)", style="bright_black"))
            return
        talking = lvl >= gate
        meter.update(Text.assemble(
            ("● transmitting" if talking else "○ quiet", "bold #23a55a" if talking else "bright_black"),
            (f"   you {lvl:.0f} dB · gate {gate:.0f} dB · talk normally and set the knob just above the quiet level",
             "bright_black")))

    #changes

    VOICE_KEYS = {"input_device", "output_device", "input_volume", "volume", "auto_sensitivity", "sensitivity_db",
                  "agc", "sfx"}

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
        if key in self.VOICE_KEYS:
            app.applyVoiceSetting(key, ev.value)
            if key == "auto_sensitivity":
                self._syncSens()
            return
        if key == "rich_presence_ipc":
            if app.plugin("rich_presence") != ev.value:
                plugins.toggle(self.cfg, "rich_presence")
                app.pluginChanged("rich_presence")
            return
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
        elif key in ("inline_media", "emoji_images", "show_link_domains"):
            app.applyPlugins()
            if app.current is not None:
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
        if key in ("input_device", "output_device"):
            if (value or None) != (self.cfg.get(key) or None):
                app.applyVoiceSetting(key, value or None)
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
        key = (ev.slider.id or "")[3:]
        if key == "sfx_volume":
            self.cfg["sfx_volume"] = int(ev.value)
            self.cfg.save()
        elif key == "sensitivity_db" and self.query_one("#sw-auto_sensitivity", Switch).value:
            return
        elif key in self.VOICE_KEYS:
            self.app.applyVoiceSetting(key, int(ev.value))

    def on_input_changed(self, ev) -> None:
        if ev.input.id == "in-plugin-filter":
            self.filter = ev.value.lower()
            for row in self.query(".plugin-row"):
                row.display = not self.filter or self.filter in row.searchText

    def _saveTranslate(self, value: str) -> None:
        value = value.strip().lower()[:5]
        if self.cfg.get("translate_to", "") != value:
            self.cfg["translate_to"] = value
            self.cfg.save()
            self.app.notify("Saved.", timeout=2)

    def on_input_submitted(self, ev) -> None:
        if ev.input.id == "in-translate_to":
            self._saveTranslate(ev.value)

    def on_input_blurred(self, ev) -> None:
        if getattr(ev.input, "id", "") == "in-translate_to":
            self._saveTranslate(ev.input.value)

    def on_button_pressed(self, ev) -> None:
        bid = ev.button.id
        if bid == "btn-plugin-reset":
            self.cfg["plugins"] = {}
            self.cfg.save()
            for key, *_r in plugins.PLUGINS:
                self.app.pluginChanged(key)
            self.build("plugins", again=True)
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
        elif bid == "btn-logout":
            from .screens import Confirm

            self.app.push_screen(Confirm("Log out of uncord?", "This forgets your login on this computer and quits."),
                                 lambda yes: yes and self.app.logout())

    async def _checkUpdate(self) -> None:
        import asyncio

        from .update import _git, sourceDir

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
            out.update(Text("You're up to date.", style="#23a55a"))
