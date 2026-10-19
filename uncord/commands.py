#slash commands and the f1 help
from __future__ import annotations

import logging
import os
import shlex
import time

import discord
from rich.text import Text

from . import filepick
from . import voice as vmod
from .fmt import PRIVATE, activityText, humanSize, nameColour, snippet, statusDot, userStatus, nameBadges, platformIcons
from .screens import Picker, TextScreen

from .calls import SHARE_QUALITIES

log = logging.getLogger("uncord")


class CommandsMixin:

    #commands

    HELP = """[b]Getting around[/b]
  Ctrl+K           jump anywhere: people, DMs, groups, channels, threads, servers
  Alt+↑ / Alt+↓    previous / next channel in this server (or DM list)
  Alt+Shift+↑/↓    previous / next channel with unread messages
  Ctrl+Alt+↑/↓     previous / next server (like Discord)
  Alt+← / Alt+→    back / forward through chats you've visited
  Tab / Esc        composer ↔ channel list   ·   Ctrl+B hide the left side   ·   F2 members list
  Sidebar: Enter/click a server to open it (jumps to where you left off), one at a time;
    Space folds folders/categories; right-click a server to mark it read.
    Clicking a voice channel joins it without leaving your chat (right-click: text chat)
  uncord reopens the last chat you were in when it starts

[b]Messages[/b]   press ↑ in an empty composer to select messages, then:
  r reply   e edit   d delete   + react   - unreact   1-9 quick react/unreact (recent emojis)
  click a reaction to add/remove yours, click the trailing + to pick a new one
  v view image/sticker/emoji in the terminal   o open link   s reveal spoilers
  y copy text   c copy message link   t open thread   P pin/unpin
  m DM the author   p profile (with avatar)   g/G top/bottom   esc back
  ↑ at the top loads older messages

[b]Composer[/b]
  @name, #channel, :emoji: autocomplete. Tab or Enter picks, ↑/↓ choose
  :pog: style server emojis and :skull: style emoji convert when sent
  Ctrl+G GIFs (favourites, trending, search)   Ctrl+E emoji picker   Ctrl+T send a sticker
  Ctrl+R record a voice message (again to send, Esc cancels)   :sob: turns into 😭 as you type
  The buttons next to the message box do the same: GIF ☺ 🏷 🎤
  Ctrl+A select all   click an image/GIF/emoji to enlarge it, click a voice message to play it
  Ctrl+F search messages (from:name in:channel has:link/image/file)

[b]You[/b]
  Ctrl+S status (online/idle/dnd/invisible, custom status)   Ctrl+U friends   F4 inbox (unreads and mentions)

[b]Voice[/b]
  Select a 🔊 channel to join · F5 call this DM/group or answer · F6 mute · F7 deafen · F8 leave · F9 voice and video settings
  Bottom-left panel: ✆ disconnect, 🎙 mute, 🎧 deafen, ⚙ settings (tabs for appearance, voice, privacy, plugins…)
  Right-click (or click in the call strip) a person for their User Volume slider (0-200%) and local mute
  /vad <n> mic sensitivity (0 = open mic) · /vol <0-300> output volume · /sfx on|off call sounds
  /voicestats shows what the audio pipeline is doing (packets in/out, mic level)

[b]Commands[/b]
  /dm name  /group a, b  /friends  /search  /pins  /members  /whois name  /status
  /upload ~/file \\[caption]  /sticker  /emoji  /media on|off  /read  /readall  /theme  /nick name
  /join /answer /decline /leave /mute /deafen /vad /vol  /quit
  /share [720p30|1080p30|1080p60|1440p60|stop|stats]  (F10 shares your screen in the call)
  /watch \\[name|stop|stats|popout]  watch someone's screen share or camera (F11, or click 🔴LIVE / 📷)
  /camera  turn your webcam on or off (F12)
  /settings (F3 or ⚙)  appearance, notifications, chat, voice and video, privacy, plugins, about
  /plugins  switch extras on and off: MessageLogger, ClearURLs, FakeNitro, SilentTyping, CallTimer and more
  Start a message with // to send a literal slash."""

    def action_help(self) -> None:
        try:
            body = Text.from_markup(self.HELP)
        except Exception:
            body = Text(self.HELP)
        self.push_screen(TextScreen("uncord keys", body))

    #everything typed after a /
    def action_plugins(self) -> None:
        self.action_settings("plugins")

    #bot slash commands

    BUILTINS = (("help", "all keys and commands"), ("dm", "message someone"), ("group", "start a group dm"),
                ("search", "search messages"), ("pins", "pinned messages"), ("members", "who's here"),
                ("status", "set your status"), ("upload", "send a file"), ("share", "share your screen"),
                ("watch", "watch a stream or camera"), ("camera", "your webcam on or off"),
                ("plugins", "switch extras on and off"), ("inbox", "unreads and mentions"),
                ("join", "join or call"), ("leave", "leave the call"), ("theme", "change colours"),
                ("nick", "change your nickname here"), ("readall", "mark everything read"))

    async def appCommands(self, ch) -> list:
        #flattened to (path, command, bot name) so subcommands are "name sub"
        cached = self._appCmds.get(ch.id)
        if cached and time.monotonic() - cached[0] < 120:
            return cached[1]
        out = []
        try:
            cmds = await ch.application_commands()
        except Exception:
            log.debug("application commands", exc_info=True)
            cmds = []

        def walk(c, path, bot):
            kids = getattr(c, "children", None) or []
            if kids:
                for k in kids:
                    walk(k, f"{path} {k.name}", bot)
            elif hasattr(c, "options"):
                out.append((path, c, bot))

        for c in cmds:
            if getattr(c, "type", None) != discord.ApplicationCommandType.chat_input:
                continue
            app = getattr(c, "application", None)
            walk(c, c.name, getattr(app, "name", "") or "bot")
        self._appCmds[ch.id] = (time.monotonic(), out)
        return out

    async def runAppCommand(self, text: str) -> None:
        ch = self.current
        name = text.split(" ", 1)[0].lower()
        if ch is None:
            self.notify(f"Unknown command /{name}. F1 for help.", severity="warning", markup=False)
            return
        cmds = await self.appCommands(ch)
        try:
            words = shlex.split(text)
        except ValueError:
            words = text.split()
        #longest matching path wins so /config set works
        best = None
        for path, c, bot in cmds:
            parts = path.split()
            if [w.lower() for w in words[: len(parts)]] == parts and (best is None or len(parts) > len(best[0].split())):
                best = (path, c, bot)
        if best is None:
            self.notify(f"Unknown command /{name}. F1 for help, or this chat has no bot with that command.",
                        severity="warning", markup=False)
            return
        path, c, bot = best
        args = words[len(path.split()):]
        opts = list(getattr(c, "options", []) or [])
        kwargs = {}
        positional = []
        for a in args:
            key, sep, val = a.partition(":")
            if sep and any(o.name == key for o in opts):
                kwargs[key] = val
            else:
                positional.append(a)
        #plain words fill the required options in order and the last one takes the rest of the line
        free = [o for o in opts if o.name not in kwargs and getattr(o, "required", False)]
        for k, o in enumerate(free):
            if k >= len(positional):
                break
            kwargs[o.name] = " ".join(positional[k:]) if k == len(free) - 1 else positional[k]
        missing = [o.name for o in opts if getattr(o, "required", False) and o.name not in kwargs]
        if missing:
            hint = " ".join(f"{o.name}:…" for o in opts)
            self.notify(f"/{path} needs {', '.join(missing)}. Usage: /{path} {hint}", severity="warning", markup=False)
            return
        try:
            await c(ch, **kwargs)
        except Exception as e:
            log.exception("app command")
            self.notify(f"/{path} ({bot}) failed: {e}", severity="error", markup=False)

    async def runCommand(self, cmd: str) -> None:
        name, _, arg = cmd.partition(" ")
        name, arg = name.lower(), arg.strip()
        if name in ("help", "h", "?", "keys"):
            self.action_help()
        elif name in ("join", "call"):
            self.action_join_voice()
        elif name == "answer":
            if self.incoming:
                self.joinVoice(self.incoming, ring=False)
            else:
                self.notify("Nobody is calling.")
        elif name == "decline":
            await self.declineCall()
        elif name in ("leave", "hangup"):
            self.action_leave_voice()
        elif name == "picker":
            if arg in ("auto", "builtin", "portal") or arg in filepick.TUI_CHOOSERS:
                self.cfg["file_picker"] = arg
                self.cfg.save()
                self.notify(f"File picker: {arg}")
            else:
                fm = filepick.defaultFileManager()
                self.notify(
                    f"File picker: {self.cfg.get('file_picker', 'auto')} (default file manager: "
                    f"{fm[1] if fm else 'none'}). /picker auto|builtin|portal|yazi|ranger|lf|nnn|vifm",
                    markup=False,
                    timeout=8,
                )
        elif name in ("share", "golive", "stream"):
            if arg in SHARE_QUALITIES:
                self.cfg["share_quality"] = arg
                self.cfg.save()
                self.notify(f"Screenshare quality: {arg}")
                if self.share is None:
                    self.action_share()
            elif arg in ("stop", "off"):
                await self.stopShare()
            elif arg == "stats":
                self.notify(self.share.statsText() if self.share else "Not sharing.", markup=False, timeout=8)
            else:
                self.action_share()
        elif name in ("inbox", "mentions", "unread"):
            self.action_inbox()
        elif name in ("plugins", "plugin", "tweaks"):
            self.action_plugins()
        elif name in ("camera", "cam", "video"):
            self.action_camera()
        elif name == "watch":
            if arg in ("stop", "off"):
                await self.stopWatch()
            elif arg == "stats":
                self.notify(
                    self.watching.statsText() if self.watching else "Not watching anything.",
                    markup=False,
                    timeout=10,
                )
            elif arg in ("popout", "mpv"):
                if self.watchDecoder is None:
                    self.notify("Not watching anything.")
                elif not self.watchDecoder.popout(f"uncord · {self.watchTitle}"):
                    self.notify("mpv isn't installed.", severity="error")
            elif arg:
                live = self.liveStreamers()
                found = [uid for uid, name in live if arg.lower() in name.lower()]
                if not found:
                    self.notify(
                        f"Nobody matching {arg!r} is live in your call.", severity="warning", markup=False
                    )
                else:
                    self.action_watch(found[0])
            else:
                self.action_watch()
        elif name == "mute":
            self.action_toggle_mute()
        elif name in ("deafen", "deaf"):
            self.action_toggle_deafen()
        elif name == "vad":
            if arg.isdigit():
                n = int(arg)
                self.cfg["vad"] = 0 if n == 0 else 600
                if n:
                    self.cfg["auto_sensitivity"] = False
                    self.cfg["sensitivity_db"] = round(vmod.rmsToDb(n))
                self.cfg.save()
                if self.vc:
                    self.vc.vadThreshold = 0 if n == 0 else 600
                    self.vc.autoSensitivity = bool(self.cfg.get("auto_sensitivity", True))
                    self.vc.sensitivityDb = float(self.cfg.get("sensitivity_db", -50))
                self.notify(
                    "Open mic."
                    if n == 0
                    else f"Input sensitivity: {self.cfg['sensitivity_db']} dB (F9 for the slider)"
                )
            else:
                self.notify(
                    f"Mic threshold is {self.cfg.get('vad')}. Usage: /vad <number> (0 = always transmit)"
                )
        elif name in ("vol", "volume"):
            if arg.isdigit():
                v = max(0, min(int(arg), 300))
                self.cfg["volume"] = v
                self.cfg.save()
                if self.vc and self.vc.speaker:
                    self.vc.speaker.volume = v / 100
                self.notify(f"Output volume {v}%")
            else:
                self.notify("Usage: /vol <0-300>")
        elif name == "dm":
            found = self.findUsers(arg) if arg else []
            if len(found) == 1:
                self.openDm(found[0])
            else:
                self.push_screen(Picker(self.people(), placeholder="DM who?", query=arg), self.goTo)
        elif name == "group":
            names = [n.strip() for n in arg.split(",") if n.strip()]
            users = []
            for n in names:
                f = self.findUsers(n)
                if not f:
                    self.notify(f"No user matching {n!r}", severity="warning", markup=False)
                    return
                users.append(f[0])
            if not users:
                self.notify("Usage: /group alice, bob, carol")
                return
            try:
                ch = await self.client.create_group(*users)
            except discord.HTTPException as e:
                self.notify(f"Couldn't create group: {e.text or e}", severity="error", markup=False)
                return
            self.rebuildDms()
            self.openChannel(ch)
        elif name in ("friends", "f"):
            self.action_friends()
        elif name == "search":
            self.action_search()
        elif name == "pins":
            await self.showPins()
        elif name == "members":
            self.showMembers()
        elif name == "status":
            st = {
                "online": "online",
                "idle": "idle",
                "away": "idle",
                "dnd": "dnd",
                "invisible": "invisible",
            }.get(arg.lower())
            if st:
                await self.setStatus(status=st)
            elif arg:
                await self.setStatus(custom=arg)
            else:
                self.action_status()
        elif name in ("emoji", "e"):
            self.action_emoji_insert()
        elif name in ("sticker", "stickers"):
            self.action_sticker()
        elif name in ("upload", "file"):
            #paths can have spaces so take the longest start of the args that is a file
            words = arg.split(" ")
            path, caption = "", ""
            for n in range(len(words), 0, -1):
                cand = os.path.expanduser(" ".join(words[:n]).strip("'\""))
                if os.path.isfile(cand):
                    path, caption = cand, " ".join(words[n:])
                    break
            if not path or self.current is None:
                self.notify("Usage (in a chat): /upload ~/path/to/file [caption]", markup=False)
            elif os.path.getsize(path) > self.uploadLimit():
                self.notify(f"{os.path.basename(path)} is over the {humanSize(self.uploadLimit())} upload limit "
                            "here.", severity="warning", markup=False)
            else:
                self.notify(f"Uploading {os.path.basename(path)}…", markup=False)
                try:
                    await self.current.send(
                        self.convertOutgoing(caption) if caption else None, file=discord.File(path)
                    )
                except discord.HTTPException as e:
                    self.notify(f"Upload failed: {e.text or e}", severity="error", markup=False)
        elif name == "read":
            if self.current is not None:
                self.scheduleAck(self.current, 0)
                self.notify("Marked as read.")
        elif name == "readall":
            g = self.currentGuild()
            try:
                if g is not None:
                    await g.ack()
                    self.notify(f"Marked {g.name} as read.", markup=False)
                else:
                    for c in self.client.private_channels:
                        if self.unreadCount(c):
                            await c.ack()
                    self.notify("Marked DMs as read.")
            except Exception as e:
                self.notify(f"Couldn't mark read: {e}", markup=False)
            self.buildSidebar()
            if self.current:
                self.reveal(self.current)
        elif name in ("whois", "user", "profile"):
            found = self.findUsers(arg) if arg else []
            if not found:
                self.notify("Usage: /whois <name>", markup=False)
            else:
                self.showProfile(found[0])
        elif name == "nick":
            g = self.currentGuild()
            if g is None:
                self.notify("/nick only works in a server.")
            else:
                try:
                    await g.me.edit(nick=arg or None)
                    self.notify(
                        f"Nickname {'set to ' + arg if arg else 'cleared'} in {g.name}.", markup=False
                    )
                except discord.HTTPException as e:
                    self.notify(f"Couldn't change nick: {e.text or e}", severity="error", markup=False)
        elif name == "theme":
            themes = sorted(self.available_themes)
            if arg in themes:
                self._setTheme(arg)
            else:
                entries = [
                    (Text(t + ("  (follows terminal colours)" if t.startswith("ansi") else "")), t, t)
                    for t in themes
                ]
                self.push_screen(
                    Picker(entries, placeholder="Theme", title=f"Theme (now {self.theme})"),
                    lambda t: self._setTheme(t) if t else None,
                )
        elif name == "sfx":
            on_ = arg.lower() not in ("off", "0", "no") if arg else not self.cfg.get("sfx", True)
            self.cfg["sfx"] = on_
            self.cfg.save()
            self.notify(f"Call sounds {'on' if on_ else 'off'}.")
            if on_:
                self.sfx("join")
        elif name in ("settings", "prefs", "options", "config"):
            self.action_settings()
        elif name in ("voicesettings", "audio", "voice"):
            self.action_voice_settings()
        elif name in ("voicestats", "vs"):
            self.notify(self.vc.statsText() if self.vc else "Not in voice.", markup=False, timeout=15)
        elif name == "media":
            on_ = arg.lower() not in ("off", "0", "no") if arg else not self.cfg.get("inline_media", True)
            self.cfg["inline_media"] = on_
            self.cfg.save()
            self.notify(f"Inline images {'on' if on_ else 'off'}.")
            if self.current is not None:
                self.loadHistory(self.current)
        elif name in ("quit", "q", "exit"):
            await self.action_quit()
        else:
            await self.runAppCommand(cmd)

    def _setTheme(self, t: str) -> None:
        self.theme = t
        self.cfg["theme"] = t
        self.cfg.save()

    async def showPins(self) -> None:
        ch = self.current
        if ch is None:
            return
        try:
            pins = [m async for m in ch.pins()]
        except discord.HTTPException as e:
            self.notify(f"Couldn't load pins: {e.text or e}", severity="error", markup=False)
            return
        if not pins:
            self.notify("No pinned messages here.")
            return
        entries = []
        for m in pins:
            label = (
                Text(m.created_at.astimezone().strftime("%d %b %y "), style="bright_black")
                + Text(m.author.display_name, style=f"bold {nameColour(m.author)}")
                + Text(": " + snippet(m.clean_content or "[attachment]", 90))
            )
            entries.append((label, f"{m.author.display_name} {m.content}".lower(), m))
        self.push_screen(
            Picker(entries, placeholder="Filter pins…", title=f"📌 {len(pins)} pinned"), self.jumpTo
        )

    def showMembers(self) -> None:
        ch = self.current
        if ch is None:
            return
        if isinstance(ch, discord.GroupChannel):
            members = list(ch.recipients)
        elif isinstance(ch, discord.DMChannel):
            members = [ch.recipient] if ch.recipient else []
        else:
            members = list(getattr(ch, "members", []))
        order = {"online": 0, "idle": 1, "dnd": 2}
        members.sort(key=lambda u: (order.get(userStatus(u), 3), u.display_name.lower()))
        entries = []
        for u in members:
            lab = (
                statusDot(u)
                + Text(u.display_name, style=nameColour(u))
                + nameBadges(u)
                + platformIcons(u)
                + Text(f"  @{u.name}", style="bright_black")
            )
            act = activityText(u)
            if act:
                lab.append(f"  {snippet(act, 30)}", style="bright_black italic")
            entries.append((lab, f"{u.display_name} @{u.name}".lower(), u))
        note = "" if isinstance(ch, PRIVATE) else " (members Discord has sent us)"
        self.push_screen(
            Picker(entries, placeholder="Filter…", title=f"{len(entries)} members{note} · enter for profile"),
            lambda u: self.showProfile(u) if u is not None else None,
        )
