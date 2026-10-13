#the main app layout sidebar chat view composer and discord events
#more features live in the mixins pickers msgactions attach commands calls
from __future__ import annotations

import asyncio
import collections
import logging
import os
import re
import shutil
import time
from typing import Dict, List, Optional

import discord
from rich.style import Style
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Input, Static, Tree
from textual.widgets.tree import TreeNode

from . import emojis as emo
from . import media, sixelview, voicemsg
from . import __version__, fmt, golive, plugins, streamview
from . import voice as vmod
from .config import Config
from .fmt import (
    PRIVATE,
    isBlocked,
    activityText,
    nameBadges,
    platformIcons,
    userStatus,
    canRead,
    channelIcon,
    channelName,
    channelTitle,
    dateSeparator,
    isForum,
    isMuted,
    isVoice,
    mentionsMe,
    nameColour,
    snippet,
    statusDot,
    termColour,
)
from .attach import AttachMixin
from .calls import CallsMixin
from .client import UncordClient
from .commands import CommandsMixin
from .messages import MessageList, MessageView, canGroup
from .msgactions import MessageActionsMixin
from .panels import ChatInput, ComposerButton, MemberList, Sidebar, SidePanel, TitleBar, VoiceBar
from .pickers import PickersMixin
from .servers import ServersMixin
from .screens import Picker

log = logging.getLogger("uncord")

MAX_MESSAGES = 500
HISTORY_PAGE = 50


#textual kills a widgets message loop after one error so contain it
from textual.message_pump import MessagePump as _MessagePump

_origDispatch = _MessagePump._dispatch_message


async def _safeDispatch(self, message) -> None:
    try:
        await _origDispatch(self, message)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        try:
            app = self.app
        except Exception:
            raise error
        if isinstance(app, Uncord) and app._readyOnce:
            app.reportError(error)
        else:
            raise


_MessagePump._dispatch_message = _safeDispatch


#app


class Uncord(ServersMixin, PickersMixin, MessageActionsMixin, AttachMixin, CommandsMixin, CallsMixin, App):
    TITLE = "uncord"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #side { width: 30; border-right: vkey $foreground 25%; }
    #side.hidden { display: none; }
    #memberpane { width: 32; border-left: vkey $foreground 25%; padding: 0 1; scrollbar-size-vertical: 1; }
    #memberpane.hidden { display: none; }
    #sidebar { height: 1fr; padding: 0; scrollbar-size-vertical: 1; }
    #me { height: 3; padding: 0 1; border-top: hkey $foreground 25%; }
    #me.incall { height: 6; }
    #main { width: 1fr; }
    #title { height: 1; padding: 0 1; }
    #messages { height: 1fr; padding: 0 1 0 0; scrollbar-size-vertical: 1; }
    #messages.loading { display: none; }
    #loading { height: 1fr; content-align: center middle; color: $text-muted; display: none; }
    #loading.on { display: block; }
    #messages:focus { background: transparent; }
    .msg { width: 1fr; padding: 0; border-left: blank; }
    .msg.selected { border-left: thick $accent; background: $accent 12%; }
    #messages.space-1 .msg.groupstart { margin-top: 1; }
    #messages.space-2 .msg.groupstart { margin-top: 2; }
    #messages.space-2 .msg { margin-bottom: 0; }
    .msg.selected .msgtext { text-style: bold; }
    .msgtext { width: 1fr; }
    .media { margin: 0 0 0 6; }
    .emojirow { height: auto; width: 1fr; }
    .reactions { margin: 0 0 0 6; }
    .chip { background: ansi_bright_black; color: ansi_white; margin-right: 1; padding: 0 1 0 1; }
    .chip.mine { background: ansi_cyan; color: ansi_black; text-style: bold; }
    .chip:hover { text-style: bold reverse; }
    .chiplabel, .chipcount { width: auto; height: 1; background: transparent; }
    .chipcount { padding-left: 1; }
    .chipimg { margin-right: 0; }
    .addchip { width: auto; height: 1; color: ansi_bright_black; background: transparent; }
    .emoji { margin-right: 1; }
    .mediacap { height: 1; width: auto; }
    .datesep, .newsep { width: 1fr; height: 1; }
    .newsep { color: $error; }
    #typing { height: 1; padding: 0 1; color: $text-muted; }
    #compose { height: 1; padding: 0 1; color: $warning; display: none; }
    #attach { height: auto; padding: 0 1; display: none; }
    #attach.on { display: block; }
    #compose.on { display: block; }
    #suggest { height: 1; padding: 0 1; display: none; }
    .modal.confirm { width: 60; height: 6; }
    .modal.gifs { width: 110; max-width: 98%; height: 30; }
    .modal.voicesettings { width: 96; max-width: 98%; height: 34; }
    .modal.usermenu { width: 48; height: 16; }
    .vs-row { height: auto; margin-bottom: 1; }
    .vs-col { width: 1fr; height: auto; margin-right: 2; }
    .vs-head { margin-top: 1; }
    .vs-switch { height: 1; }
    .vs-switch Switch { border: none; height: 1; width: 6; padding: 0; margin-right: 1; }
    .vs-switch Static { width: 1fr; }
    #suggest.on { display: block; }
    #composer { height: 1; }
    #input { height: 1; border: none; padding: 0 1; width: 1fr; }
    .cbtn { width: auto; height: 1; padding: 0 1; margin-left: 1; background: ansi_bright_black; color: ansi_white; }
    .cbtn:hover { background: ansi_cyan; color: ansi_black; }
    .cbtn.plus { margin-left: 0; margin-right: 1; text-style: bold; }
    #voicebar { height: 3; padding: 0 1; display: none; border-bottom: hkey $foreground 25%; }
    #voicebar.on { display: block; }
    Toast { width: 50; max-width: 50%; padding: 0 1; }
    ModalScreen { align: center middle; }
    .modal { width: 76; max-width: 95%; height: 26; max-height: 90%; border: round $foreground 50%;
             background: $background; padding: 0 1; }
    .modal.small { height: 5; }
    .modal.tall { height: 90%; width: 90; }
    .modal.image { width: 90%; height: 90%; }
    .modal.profile { width: 80; height: 22; }
    .modal-title { height: 1; margin-bottom: 1; }
    #pk-list { height: 1fr; border: none; background: transparent; }
    #sr-status { height: 1; }
    #pf-row { height: 1fr; }
    #pf-avatar, #pf-img { width: 20; height: 10; margin-right: 2; }
    #pf-info { width: 1fr; }
    #pf-help { height: 1; }
    #img { width: auto; height: 1fr; }
    """

    BINDINGS = [
        #footer keys rest are in f1
        Binding("ctrl+k", "switcher", "Jump", priority=True),
        Binding("ctrl+f", "search", "Search", priority=True),
        Binding("ctrl+g", "gif", "GIF", priority=True),
        Binding("ctrl+e", "emoji_insert", "Emoji", priority=True),
        Binding("f5", "join_voice", "Call", priority=True),
        Binding("f6", "toggle_mute", "Mute", priority=True),
        Binding("f8", "leave_voice", "Leave", priority=True),
        Binding("f9", "voice_settings", "Voice", priority=True),
        Binding("f1", "help", "Keys", priority=True),
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+u", "friends", "Friends", show=False, priority=True),
        Binding("ctrl+t", "sticker", "Sticker", show=False, priority=True),
        Binding("ctrl+o", "attach_menu", "Attach", show=False, priority=True),
        Binding("ctrl+r", "record", "Voice msg", show=False, priority=True),
        Binding("ctrl+s", "status", "Status", show=False, priority=True),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar", priority=True),
        Binding("f2", "toggle_members", "Members", show=False, priority=True),
        Binding("f4", "inbox", "Inbox", priority=True),
        Binding("f3", "settings", "Settings", priority=True),
        Binding("f7", "toggle_deafen", "Deafen", show=False),
        Binding("f10", "share", "Share screen", show=False, priority=True),
        Binding("f11", "watch", "Watch stream", show=False, priority=True),
        Binding("f12", "camera", "Camera", show=False, priority=True),
        Binding("alt+up", "step(-1)", show=False),
        Binding("alt+down", "step(1)", show=False),
        Binding("alt+shift+up", "step_unread(-1)", show=False),
        Binding("alt+shift+down", "step_unread(1)", show=False),
        Binding("alt+left", "history(-1)", show=False),
        Binding("alt+right", "history(1)", show=False),
        Binding("ctrl+alt+up", "server_step(-1)", show=False),
        Binding("ctrl+alt+down", "server_step(1)", show=False),
    ]

    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token
        self.cfg = Config()
        self.client = UncordClient(self)
        self.current = None
        self.nodes: Dict[int, TreeNode] = {}  #channel id to node
        self.guildNodes: Dict[int, TreeNode] = {}
        self.folderNodes: List[TreeNode] = []
        self.dmRoot: Optional[TreeNode] = None
        self.localUnread: Dict[int, int] = {}
        self.localMentions: Dict[int, int] = {}
        self.typing: Dict[int, float] = {}
        self.typingElsewhere: Dict[int, float] = {}  #channel id to when someone last typed there
        self.callStarted = 0.0
        self.rpc = None
        self._appCmds: dict = {}
        self.rpcActivity = None
        self.typingNames: Dict[int, str] = {}
        self.replyTo: Optional[discord.Message] = None
        self.editing: Optional[discord.Message] = None
        self.suggestions: list = []
        self.suggestIdx = 0
        self._suggestSpan = (0, 0)
        self.vc: Optional[vmod.UncordVoiceClient] = None
        self.incoming = None
        self.history: List[int] = []
        self.historyPos = -1
        self._loadGen = 0
        self._loadingOlder = False
        self._lastTypingSent = 0.0
        self._readyOnce = False
        self._lastTalking = None
        self._reactionPending: set = set()
        self._ackTask: Optional[asyncio.Task] = None
        self.notifyCmd = None
        self.applyNotify()
        self.frameCache = media.FrameCache(self.fetchCdn)
        self.audioPlayer = voicemsg.Player()
        self.recorder: Optional[voicemsg.Recorder] = None
        self.selfMuted = False
        self.selfDeafened = False
        self._testMic = None
        self._testLevels: list = []
        self._recTimer = None
        self._favGifs = None
        self.share: Optional[golive.ScreenShare] = None
        self._shareStarting = False
        self.watching = None  #a golive.StreamWatch or golive.CameraWatch
        self.watchKind = "stream"
        self.camera: Optional[golive.CameraShare] = None
        self._cameraStarting = False
        self.watchDecoder: Optional[golive.StreamDecoder] = None
        self.watchTitle = ""
        self._watchStarting = False
        self._watchAudioMuted = False
        self._watchOpus: dict = {}
        self._errorsSeen: Dict[str, float] = {}
        self.attachments: list = []  #files waiting for the next message
        self.msgCache: "collections.OrderedDict[int, list]" = collections.OrderedDict()
        self._micWarned = False
        self._lastBar = None
        self._lastPanel = None

    #stability

    #after startup errors get logged and toasted instead of killing the app
    def _handle_exception(self, error: Exception) -> None:
        if not self._readyOnce or isinstance(error, (KeyboardInterrupt, SystemExit)):
            return super()._handle_exception(error)
        self.reportError(error)

    _GLOBAL_ACTIONS = {
        "attach_menu",
        "share",
        "watch",
        "camera",
        "switcher",
        "search",
        "gif",
        "emoji_insert",
        "join_voice",
        "voice_settings",
        "help",
        "friends",
        "sticker",
        "record",
        "status",
        "toggle_sidebar",
        "toggle_members",
        "inbox",
        "settings",
        "step",
        "step_unread",
        "history",
        "server_step",
    }
    _ALWAYS = {"quit", "toggle_mute", "toggle_deafen", "leave_voice"}

    #global keys like ctrl+k dont fire while a popup or the stream viewer is open
    def check_action(self, action: str, parameters) -> Optional[bool]:
        from textual.screen import ModalScreen

        if (
            isinstance(self.screen, (ModalScreen, streamview.StreamScreen))
            and action not in self._ALWAYS
            and action in self._GLOBAL_ACTIONS
        ):
            return False
        return True

    def reportError(self, error: BaseException) -> None:
        err = getattr(error, "error", None) or error
        key = f"{type(err).__name__}: {err}"[:160]
        now = time.monotonic()
        if now - self._errorsSeen.get(key, -1e9) > 30:
            self._errorsSeen[key] = now
            log.error("unhandled error (kept running): %s", key, exc_info=(type(err), err, err.__traceback__))
            try:
                self.notify(
                    f"Something went wrong: {key[:90]} (details in ~/.cache/uncord/uncord.log)",
                    severity="error",
                    timeout=6,
                    markup=False,
                )
            except Exception:
                pass

    @property
    def meId(self) -> int:
        return self.client.user.id if self.client.user else 0

    #layout

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            with Vertical(id="side"):
                tree = Sidebar("uncord", id="sidebar")
                tree.show_root = False
                tree.guide_depth = 2
                tree.auto_expand = False
                yield tree
                yield SidePanel("", id="me")
            with Vertical(id="main"):
                yield TitleBar("connecting…", id="title")
                yield VoiceBar("", id="voicebar")
                yield MessageList(id="messages")
                yield Static("loading…", id="loading")
                yield Static("", id="typing")
                yield Static("", id="compose")
                yield Static("", id="attach")
                yield Static("", id="suggest")
                with Horizontal(id="composer"):
                    yield ComposerButton(
                        "+", "attach_menu", "Attach: file, image, voice message, GIF… (Ctrl+O)", plus=True
                    )
                    yield ChatInput(
                        placeholder="Connecting to Discord…",
                        id="input",
                        disabled=True,
                        compact=True,
                        select_on_focus=False,
                    )
                    yield ComposerButton("GIF", "gif", "Send a GIF (Ctrl+G)")
                    yield ComposerButton("☺", "emoji_insert", "Emoji (Ctrl+E)")
            with VerticalScroll(id="memberpane", classes="" if self.cfg.get("show_members") else "hidden"):
                yield MemberList("", id="members")
        footer = Footer(show_command_palette=False)
        footer.compact = True
        yield footer

    def updateAvailable(self, msg: str) -> None:
        self.notify(msg, timeout=10, markup=False)
        self._updateNotice = True
        self.refreshTitleRight()

    #right side of the title bar member count then version
    def refreshTitleRight(self) -> None:
        t = Text()
        g = self.currentGuild()
        if g is not None and self.plugin("member_count"):
            total = g.member_count or len(g.members)
            online = getattr(g, "approximate_presence_count", None) or sum(
                1 for mbr in g.members if userStatus(mbr) != "offline")
            t.append("● ", style="green")
            t.append(f"{online:,} online  ", style="bright_black")
            t.append(f"{total:,} members   ", style="bright_black")
        t.append(f"uncord {__version__}", style="bright_black")
        if getattr(self, "_updateNotice", False):
            t.append("  update available", style="bold yellow")
        self.query_one("#title", TitleBar).setRight(t)

    def applyNotify(self) -> None:
        on = os.environ.get("UNCORD_NOTIFY", "1") != "0" and self.cfg.get("desktop_notify", True)
        self.notifyCmd = shutil.which("notify-send") if on else None

    #line spacing adds a gap above each new person in the chat
    def applySpacing(self) -> None:
        ml = self.query_one("#messages")
        n = int(self.cfg.get("message_spacing", 1))
        for k in range(3):
            ml.set_class(k == n, f"space-{k}")

    def action_settings(self, category: str = "appearance") -> None:
        from .settings import SettingsScreen

        self.push_screen(SettingsScreen(category))

    #a plugin was switched so redraw whatever it touches
    def pluginChanged(self, key: str) -> None:
        self.applyPlugins()
        if key in ("show_usernames", "message_logger", "reply_timestamp", "unindent", "translate"):
            for v in self.query_one("#messages").query("MessageView"):
                v.rerender()
        elif key in ("platform_icons",):
            self.refreshMembers()
        elif key == "member_count":
            self.refreshTitleRight()
        elif key == "pin_dms":
            self.rebuildDms()
        elif key in ("hidden_channels", "no_blocked") and self._readyOnce:
            self.buildSidebar()
            if self.current is not None:
                self.loadHistory(self.current)

    def plugin(self, key: str) -> bool:
        return plugins.enabled(self.cfg, key)

    def applyPlugins(self) -> None:
        fmt.SHOW_USERNAMES = self.plugin("show_usernames")
        fmt.SHOW_PLATFORMS = self.plugin("platform_icons")
        fmt.REPLY_TIMESTAMP = self.plugin("reply_timestamp")
        fmt.UNINDENT = self.plugin("unindent")

    def on_mount(self) -> None:
        self.applyPlugins()
        self.applySpacing()
        self.refreshTitleRight()
        theme = os.environ.get("UNCORD_THEME") or self.cfg.get("theme") or "ansi-dark"
        self.theme = theme if theme in self.available_themes else "ansi-dark"
        if not self.cfg.get("show_sidebar", True):
            self.query_one("#side").add_class("hidden")
        self.runDiscord()
        self.set_interval(1.0, self.tickTyping)
        self.set_interval(0.4, self.updateVoicebar)
        self.set_interval(30, self.updateMePanel)

    @work(exclusive=True, group="discord")
    async def runDiscord(self) -> None:
        try:
            await self.client.start(self.token)
        except discord.LoginFailure:
            self.query_one("#title", Static).update(
                Text("Login failed: token invalid. Run `uncord --logout` and try again.", style="red")
            )
        except Exception as e:
            log.exception("discord client crashed")
            self.query_one("#title", Static).update(Text(f"Disconnected: {e!r}", style="red"))

    #unread

    #1 if theres anything unread (discord only tells us the last message and last read ids)
    #localUnread counts messages that arrived while we were running
    def unreadCount(self, ch) -> int:
        n = self.localUnread.get(ch.id, 0)
        if n:
            return n
        try:
            last = ch.last_message_id
            acked = ch.read_state.last_acked_id or 0
        except Exception:
            return 0
        if not last:
            return 0
        if not acked and not isinstance(ch, PRIVATE):
            #never opened so only count messages since join
            me = getattr(ch.guild, "me", None)
            joined = getattr(me, "joined_at", None)
            if joined is None:
                return 0
            acked = discord.utils.time_snowflake(joined)
        return 1 if last > acked else 0

    def mention_count(self, ch) -> int:
        try:
            base = ch.mention_count
        except Exception:
            base = 0
        return max(base, self.localMentions.get(ch.id, 0))

    def clearUnread(self, ch) -> None:
        self.localUnread.pop(ch.id, None)
        self.localMentions.pop(ch.id, None)

    #mark read after a short delay cancelling the previous one so fast channel hopping doesnt spam acks
    def scheduleAck(self, ch, delay: float = 1.0) -> None:
        if not self.cfg.get("mark_read", True):
            return
        if self._ackTask and not self._ackTask.done():
            self._ackTask.cancel()

        async def go():
            await asyncio.sleep(delay)
            try:
                await ch.ack()
            except Exception:
                log.debug("ack failed", exc_info=True)

        self._ackTask = asyncio.create_task(go())

    #sidebar

    def pinnedDms(self) -> list:
        return [int(x) for x in self.cfg.get("pinned_dms", [])] if self.plugin("pin_dms") else []

    def privateChannelsSorted(self):
        chans = [c for c in self.client.private_channels if isinstance(c, PRIVATE)]
        chans.sort(key=lambda c: c.last_message_id or c.id, reverse=True)
        pinned = self.pinnedDms()
        #pindms pinned ones first in the order you pinned them
        return [c for p in pinned for c in chans if c.id == p] + [c for c in chans if c.id not in pinned]

    def dmMenu(self, ch) -> None:
        pinned = ch.id in self.pinnedDms()
        entries = [(Text("✓ Mark as read"), "read", "read"), (Text("Copy channel ID", style="bright_black"), "copy", "copy")]
        if self.plugin("pin_dms"):
            entries.insert(0, (Text("📌 Unpin" if pinned else "📌 Pin to top"), "pin", "pin"))

        def done(act) -> None:
            if act == "pin":
                ids = [x for x in self.pinnedDms() if x != ch.id] + ([] if pinned else [ch.id])
                self.cfg["pinned_dms"] = ids
                self.cfg.save()
                self.rebuildDms()
            elif act == "read":
                self.clearUnread(ch)
                self.scheduleAck(ch, 0)
                self.relabel(ch)
            elif act == "copy":
                self.copyText(str(ch.id))

        self.push_screen(Picker(entries, placeholder=channelName(ch)), done)

    def nodeLabel(self, ch) -> Text:
        unread = self.unreadCount(ch)
        ment = self.mention_count(ch)
        muted = isMuted(ch)
        isCur = self.current is not None and self.current.id == ch.id
        icon = channelIcon(ch)
        name = channelName(ch)
        if not isinstance(ch, PRIVATE) and not canRead(ch):
            icon = "🔒 "
        if muted and not ment:
            style = "bright_black"
        elif unread or ment:
            style = "bold"
        else:
            style = ""
        t = Text()
        if isinstance(ch, discord.DMChannel) and ch.recipient is not None:
            t.append_text(statusDot(ch.recipient))
            t.append(name, style=style)
        else:
            t.append(icon, style="bright_black" if not unread else "")
            t.append(name, style=style)
        if isCur:
            t.stylize("reverse")
        if self.vc and self.vc.channel and self.vc.channel.id == ch.id:
            t.stylize("green")
        if ment:
            t.append(f" {ment}", style="bold white on red")
        elif unread and not muted and isinstance(ch, PRIVATE) and self.localUnread.get(ch.id):
            t.append(f" {self.localUnread[ch.id]}", style="bold yellow")
        if self.incoming is not None and self.incoming.id == ch.id:
            t.append(" 📞")
        if isinstance(ch, PRIVATE) and ch.id in self.pinnedDms():
            t = Text("📌", style="bright_black") + t
        if ch.id in self.typingElsewhere:
            t.append(" ✎", style="bright_black")
        return t

    def guildStats(self, g: discord.Guild):
        ment = unread = 0
        for c in g.channels:
            if not isinstance(c, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel)):
                continue
            m = self.mention_count(c)
            ment += m
            if not unread and not isMuted(c) and self.unreadCount(c) and canRead(c):
                unread = 1
        return ment, unread

    #dm list newest first capped at dm_limit
    def rebuildDms(self) -> None:
        if self.dmRoot is None:
            return
        for cid in [cid for cid, n in self.nodes.items() if n.parent is self.dmRoot]:
            self.nodes.pop(cid, None)
        self.dmRoot.remove_children()
        chans = self.privateChannelsSorted()
        limit = int(self.cfg.get("dm_limit", 30))
        shown = chans[:limit]
        if self.current is not None and isinstance(self.current, PRIVATE) and self.current not in shown:
            shown.append(self.current)
        for ch in shown:
            self.nodes[ch.id] = self.dmRoot.add_leaf(self.nodeLabel(ch), data=("chan", ch))
        if len(chans) > limit:
            self.dmRoot.add_leaf(
                Text(f"… {len(chans) - limit} more (Ctrl+K)", style="bright_black"), data=("more_dms", None)
            )
        self.dmRoot.set_label(self.dmsLabel())

    def dmsLabel(self) -> Text:
        t = Text("DIRECT MESSAGES", style="bright_black bold")
        n = sum(self.mention_count(c) for c in self.client.private_channels if isinstance(c, PRIVATE))
        if n:
            t.append(f" {n}", style="bold white on red")
        return t

    #servers grouped like your discord folders new servers go on top
    def guildLayout(self):
        placed, layout = set(), []
        try:
            for folder in self.client.settings.guild_folders:
                gs = [g for g in folder.guilds if g is not None]
                if not gs:
                    continue
                layout.append((folder if folder.id else None, gs))
                placed.update(g.id for g in gs)
        except Exception:
            log.exception("guild folders")
        new = [g for g in self.client.guilds if g.id not in placed]
        if new:
            layout.insert(0, (None, new))
        return layout

    #dms then servers one server open at a time

    def buildSidebar(self) -> None:
        tree = self.query_one("#sidebar", Tree)
        tree.clear()
        self.nodes.clear()
        self.guildNodes.clear()
        self.folderNodes.clear()
        self.dmRoot = tree.root.add(
            self.dmsLabel(), data=("dms", None), expand=self.cfg.get("dms_open", True)
        )
        self.rebuildDms()
        openFolders = set(self.cfg.get("open_folders", []))
        for folder, gs in self.guildLayout():
            parent = tree.root
            if folder is not None and len(gs) > 1:
                parent = tree.root.add(
                    self.folderLabel(folder, gs),
                    data=("folder", (folder, gs)),
                    expand=str(folder.id) in openFolders,
                )
                self.folderNodes.append(parent)
            for g in gs:
                self.guildNodes[g.id] = parent.add(self.guildLabel(g), data=("guild", g), allow_expand=True)
        if self.current is not None:
            self.reveal(self.current)

    def guildLabel(self, g: discord.Guild) -> Text:
        muted = isMuted(g)
        ment, unread = self.guildStats(g)
        here = (
            self.current is not None
            and not isinstance(self.current, PRIVATE)
            and getattr(self.current, "guild", None) is not None
            and self.current.guild.id == g.id
        )
        t = Text(
            g.name, style="bold" if (unread and not muted) or here else ("bright_black" if muted else "")
        )
        if ment:
            t.append(f" {ment}", style="bold white on red")
        elif unread and not muted:
            t.append(" •", style="bold")
        return t

    def folderLabel(self, folder, gs) -> Text:
        name = folder.name or ", ".join(g.name for g in gs[:2])
        t = Text("▍", style=termColour(getattr(folder, "colour", None)) or "bright_black")
        t.append(snippet(name, 24), style="bold")
        stats = [self.guildStats(g) for g in gs]
        ment = sum(m for m, _u in stats)
        if ment:
            t.append(f" {ment}", style="bold white on red")
        elif any(u and not isMuted(g) for (_m, u), g in zip(stats, gs)):
            t.append(" •", style="bold")
        return t

    def openGuildNode(self, g) -> Optional[TreeNode]:
        node = self.guildNodes.get(g.id)
        if node is None:
            return None
        for other in self.guildNodes.values():
            if other is not node and other.is_expanded:
                other.collapse()
        if node.parent is not None and node.parent.data and node.parent.data[0] == "folder":
            node.parent.expand()
        self.populateGuild(node, g)
        node.expand()
        return node

    def selectGuild(self, g) -> None:
        node = self.openGuildNode(g)
        if node is not None:
            self.call_after_refresh(self._moveCursor, node)
        if (
            self.current is not None
            and not isinstance(self.current, PRIVATE)
            and getattr(self.current, "guild", None) is not None
            and self.current.guild.id == g.id
        ):
            return
        last = self.client.get_channel(int(self.cfg.get("guild_last", {}).get(str(g.id)) or 0))
        if (
            last is None
            or getattr(last, "guild", None) is None
            or last.guild.id != g.id
            or isVoice(last)
            or isForum(last)
        ):
            last = next(
                (c for _cat, cs in self.guildChannels(g) for c in cs if not isVoice(c) and not isForum(c)),
                None,
            )
        if last is not None:
            self.openChannel(last)

    def serverOrder(self) -> list:
        return [g for _folder, gs in self.guildLayout() for g in gs]

    def action_server_step(self, delta: int) -> None:
        order = self.serverOrder()
        if not order:
            return
        cur = (
            getattr(self.current, "guild", None)
            if self.current is not None and not isinstance(self.current, PRIVATE)
            else None
        )
        ids = [g.id for g in order]
        i = (
            ids.index(cur.id) + delta
            if cur is not None and cur.id in ids
            else (0 if delta > 0 else len(order) - 1)
        )
        if 0 <= i < len(order):
            self.selectGuild(order[i])

    def guildChannels(self, g: discord.Guild):
        out = []
        for cat, chans in g.by_category():
            visible = [
                c
                for c in chans
                if isinstance(
                    c, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.ForumChannel)
                )
                and (canRead(c) or self.plugin("hidden_channels"))
            ]
            if visible:
                out.append((cat, visible))
        return out

    def activeThreads(self, ch) -> list:
        try:
            threads = [t for t in ch.threads if not t.archived and canRead(t)]
        except Exception:
            return []
        return sorted(threads, key=lambda t: t.last_message_id or t.id, reverse=True)[:15]

    #fill a servers channels the first time its opened
    def populateGuild(self, node: TreeNode, g: discord.Guild) -> None:
        if node.children:
            return
        collapsed = set(self.cfg.get("collapsed_cats", []))
        for cat, chans in self.guildChannels(g):
            parent = (
                node.add(
                    Text(cat.name.upper(), style="bright_black bold"),
                    data=("cat", cat),
                    expand=str(cat.id) not in collapsed,
                )
                if cat
                else node
            )
            for c in chans:
                if isVoice(c):
                    vnode = parent.add(self.nodeLabel(c), data=("chan", c), expand=True, allow_expand=False)
                    self.nodes[c.id] = vnode
                    self.fillVoiceMembers(vnode, c)
                    continue
                threads = self.activeThreads(c)
                if threads:
                    cnode = parent.add(
                        self.nodeLabel(c),
                        data=("chan", c),
                        expand=isForum(c) is False and False,
                        allow_expand=True,
                    )
                    for t in threads:
                        self.nodes[t.id] = cnode.add_leaf(self.nodeLabel(t), data=("chan", t))
                else:
                    cnode = parent.add_leaf(self.nodeLabel(c), data=("chan", c))
                self.nodes[c.id] = cnode

    def fillVoiceMembers(self, node: TreeNode, ch) -> None:
        node.remove_children()
        talking = set(self.vc.speakingUserIds()) if self.vc else set()
        for uid, vs in list(getattr(ch, "voice_states", {}).items()):
            member = ch.guild.get_member(uid)
            name = member.display_name if member else str(uid)
            flags = (
                ("🔇" if (vs.self_mute or vs.mute) else "")
                + ("🙉" if (vs.self_deaf or vs.deaf) else "")
                + (" 🔴live" if getattr(vs, "self_stream", False) else "")
            )
            label = Text(f"{name} {flags}".rstrip(), style="bold green" if uid in talking else "bright_black")
            vol = int(self.cfg.get("user_volume", {}).get(str(uid), 100))
            if vol != 100:
                label.append(f" {'🔇' if vol == 0 else '🔉'}{vol}%", style="yellow")
            node.add_leaf(label, data=("member", (ch, member, uid)))

    def relabel(self, ch) -> None:
        if ch is None:
            return
        node = self.nodes.get(ch.id)
        if node is not None:
            node.set_label(self.nodeLabel(ch))
        parent = getattr(ch, "parent", None)
        if isinstance(ch, discord.Thread) and parent is not None and parent.id in self.nodes:
            self.nodes[parent.id].set_label(self.nodeLabel(parent))
        if isinstance(ch, PRIVATE):
            if self.dmRoot is not None:
                self.dmRoot.set_label(self.dmsLabel())
            return
        g = getattr(ch, "guild", None)
        gnode = self.guildNodes.get(g.id) if g is not None else None
        if gnode is not None:
            gnode.set_label(self.guildLabel(g))
            f = gnode.parent
            if f is not None and f.data and f.data[0] == "folder":
                f.set_label(self.folderLabel(*f.data[1]))

    def reveal(self, ch) -> None:
        g = getattr(ch, "guild", None)
        if g is not None and not isinstance(ch, PRIVATE):
            self.openGuildNode(g)
        elif self.dmRoot is not None:
            self.dmRoot.expand()
        node = self.nodes.get(ch.id)
        if node is None and isinstance(ch, discord.Thread) and ch.parent is not None:
            pnode = self.nodes.get(ch.parent.id)
            if pnode is not None:
                self.nodes[ch.id] = node = pnode.add_leaf(self.nodeLabel(ch), data=("chan", ch))
        if node is None and isinstance(ch, PRIVATE):
            self.rebuildDms()
            node = self.nodes.get(ch.id)
        if node is not None:
            n = node.parent
            while n is not None:
                n.expand()
                n = n.parent
            self.call_after_refresh(self._moveCursor, node)

    def _moveCursor(self, node: TreeNode) -> None:
        tree = self.query_one("#sidebar", Tree)
        try:
            tree.move_cursor(node)
            tree.scroll_to_node(node, animate=False)
        except Exception:
            pass

    @on(Tree.NodeExpanded, "#sidebar")
    @on(Tree.NodeCollapsed, "#sidebar")
    def _catToggled(self, ev) -> None:
        kind, obj = ev.node.data or (None, None)
        if kind == "guild" and ev.node.is_expanded:
            self.populateGuild(ev.node, obj)
            for other in self.guildNodes.values():
                if other is not ev.node and other.is_expanded:
                    other.collapse()
        elif kind == "folder":
            opened = set(self.cfg.get("open_folders", []))
            (opened.add if ev.node.is_expanded else opened.discard)(str(obj[0].id))
            self.cfg["open_folders"] = sorted(opened)
            self.cfg.save()
        elif kind == "dms":
            self.cfg["dms_open"] = ev.node.is_expanded
            self.cfg.save()
        elif kind == "cat" and obj is not None:
            cats = set(self.cfg.get("collapsed_cats", []))
            (cats.discard if ev.node.is_expanded else cats.add)(str(obj.id))
            self.cfg["collapsed_cats"] = sorted(cats)
            self.cfg.save()

    @on(Tree.NodeHighlighted, "#sidebar")
    def _highlighted(self, ev) -> None:
        kind, obj = ev.node.data or (None, None)
        if kind == "chan" and not isVoice(obj) and not isForum(obj) and obj.id not in self.msgCache:
            self.prefetch(obj)

    @work(exclusive=True, group="prefetch")
    async def prefetch(self, ch) -> None:
        await asyncio.sleep(0.4)
        if ch.id in self.msgCache or (self.current is not None and self.current.id == ch.id):
            return
        try:
            msgs = [m async for m in ch.history(limit=HISTORY_PAGE)]
        except Exception:
            return
        msgs.reverse()
        if ch.id not in self.msgCache:
            self.cacheMessages(ch, msgs)

    @on(Tree.NodeSelected, "#sidebar")
    def _selected(self, ev: Tree.NodeSelected) -> None:
        kind, obj = ev.node.data or (None, None)
        if kind == "chan":
            if not canRead(obj):
                self.notify(f"You can't see #{channelName(obj)}, it's hidden from you.", markup=False)
                return
            if isForum(obj):
                self.forumPicker(obj)
                return
            if isVoice(obj):
                self.voiceChannelClicked(obj)
            else:
                self.openChannel(obj)
        elif kind == "member":
            ch, _m, uid = obj
            vs = getattr(ch, "voice_states", {}).get(uid)
            if (
                getattr(vs, "self_stream", False)
                and self.vc is not None
                and self.vc.channel is not None
                and self.vc.channel.id == ch.id
                and uid != self.meId
            ):
                self.action_watch(uid)
            else:
                self.userMenu(uid)
        elif kind == "guild":
            if (
                ev.node.is_expanded
                and self.current is not None
                and getattr(self.current, "guild", None) is obj
            ):
                ev.node.collapse()
            else:
                self.selectGuild(obj)
        elif kind in ("folder", "cat", "dms"):
            ev.node.toggle()
        elif kind == "more_dms":
            self.push_screen(Picker(self.dmEntries(), placeholder="All DMs and groups"), self.goTo)

    def action_toggle_sidebar(self) -> None:
        side = self.query_one("#side")
        side.toggle_class("hidden")
        self.cfg["show_sidebar"] = not side.has_class("hidden")
        self.cfg.save()

    def action_toggle_members(self) -> None:
        pane = self.query_one("#memberpane")
        pane.toggle_class("hidden")
        self.cfg["show_members"] = not pane.has_class("hidden")
        self.cfg.save()
        self.refreshMembers()

    def channelMembers(self, ch) -> list:
        me = self.client.user
        if isinstance(ch, discord.DMChannel):
            return [u for u in (ch.recipient, me) if u is not None]
        if isinstance(ch, discord.GroupChannel):
            return list(ch.recipients) + ([me] if me else [])
        target = ch.parent if isinstance(ch, discord.Thread) and ch.parent is not None else ch
        try:
            return list(target.members)
        except Exception:
            return []

    #online first like discord with status dots badges and what theyre doing
    def refreshMembers(self) -> None:
        self._membersTimer = None
        try:
            pane = self.query_one("#memberpane")
        except Exception:
            return
        if pane.has_class("hidden") or self.current is None:
            return
        members = self.channelMembers(self.current)
        online = sorted((u for u in members if userStatus(u) != "offline"), key=lambda u: u.display_name.lower())
        offline = sorted((u for u in members if userStatus(u) == "offline"), key=lambda u: u.display_name.lower())
        t = Text()
        for title, group in (("ONLINE", online), ("OFFLINE", offline)):
            if not group:
                continue
            if t:
                t.append("\n")
            t.append(f"{title} — {len(group)}\n", style="bold bright_black")
            for u in group[:150]:
                meta = Style(meta={"muser": u.id})
                t.append_text(statusDot(u))
                t.append(snippet(u.display_name, 18),
                         style=Style(color=nameColour(u), dim=title == "OFFLINE") + meta)
                t.append_text(nameBadges(u))
                t.append_text(platformIcons(u))
                t.append("\n")
                act = activityText(u)
                if act and title == "ONLINE":
                    t.append(f"  {snippet(act, 26)}\n", style=Style(color="bright_black", italic=True) + meta)
            if len(group) > 150:
                t.append(f"  … {len(group) - 150} more\n", style="bright_black")
        if not members:
            t.append("Discord hasn't sent the member list for this channel yet.", style="bright_black")
        self.query_one("#members", MemberList).update(t)

    def membersLater(self) -> None:
        #presence updates come in bursts so redraw at most every 2s
        if getattr(self, "_membersTimer", None) is None and not self.query_one("#memberpane").has_class("hidden"):
            self._membersTimer = self.set_timer(2.0, self.refreshMembers)

    def orderedChannels(self, unreadOnly: bool = False) -> list:
        out = list(self.privateChannelsSorted())
        for _folder, gs in self.guildLayout():
            for g in gs:
                for _cat, chans in self.guildChannels(g):
                    for c in chans:
                        if not isVoice(c) and not isForum(c):
                            out.append(c)
        if unreadOnly:
            out = [
                c
                for c in out
                if self.mention_count(c)
                or (self.unreadCount(c) and not isMuted(c))
                or (self.current is not None and c.id == self.current.id)
            ]
        return out

    def action_step(self, delta: int) -> None:
        if self.current is None:
            return
        if isinstance(self.current, PRIVATE):
            chans = self.privateChannelsSorted()
        else:
            chans = [c for _cat, cs in self.guildChannels(self.current.guild) for c in cs if not isForum(c)]
        ids = [c.id for c in chans]
        cur = self.current.parent if isinstance(self.current, discord.Thread) else self.current
        if cur is None or cur.id not in ids:
            return
        i = ids.index(cur.id) + delta
        if 0 <= i < len(chans):
            self.openChannel(chans[i])

    def action_step_unread(self, delta: int) -> None:
        chans = self.orderedChannels(unreadOnly=True)
        if not chans:
            self.notify("Nothing unread.")
            return
        ids = [c.id for c in chans]
        if self.current is not None and self.current.id in ids:
            i = ids.index(self.current.id) + delta
        else:
            i = 0 if delta > 0 else len(chans) - 1
        if 0 <= i < len(chans) and (self.current is None or chans[i].id != self.current.id):
            self.openChannel(chans[i])
        else:
            self.notify("No more unread channels that way.")

    def action_history(self, delta: int) -> None:
        pos = self.historyPos + delta
        if 0 <= pos < len(self.history):
            ch = self.client.get_channel(self.history[pos])
            if ch is not None:
                self.historyPos = pos
                self.openChannel(ch, record=False)

    #channel view

    #switch the chat to ch and remember it for next start
    def openChannel(self, ch, around=None, record: bool = True) -> None:
        if not isinstance(ch, PRIVATE) and not canRead(ch):
            self.notify(f"You can't see #{channelName(ch)}, it's hidden from you.", markup=False)
            return
        prev = self.current
        if isForum(ch):
            self.forumPicker(ch)
            return
        self.current = ch
        self.clearUnread(ch)
        if prev is not None and prev.id != ch.id:
            self.relabel(prev)
        self.relabel(ch)
        if record and (
            not self.history or self.history[self.historyPos] != ch.id if self.historyPos >= 0 else True
        ):
            self.history = self.history[: self.historyPos + 1] + [ch.id]
            self.history = self.history[-50:]
            self.historyPos = len(self.history) - 1
        self.cfg["last_channel"] = ch.id
        if isinstance(ch, PRIVATE):
            self.cfg["home_last"] = ch.id
        elif getattr(ch, "guild", None) is not None and not isVoice(ch):
            gl = dict(self.cfg.get("guild_last", {}))
            gl[str(ch.guild.id)] = ch.id
            self.cfg["guild_last"] = gl
        self.cfg.save()
        self.typing.clear()
        self.cancelCompose()
        self.clearSuggestions()
        self.query_one("#title", Static).update(channelTitle(ch))
        inp = self.query_one("#input", ChatInput)
        inp.placeholder = f"Message {channelIcon(ch).strip()} {channelName(ch)}"
        inp.focus()
        self.reveal(ch)
        self.loadHistory(ch, around)
        self.refreshMembers()
        self.refreshTitleRight()

    @staticmethod
    def _sig(msgs) -> list:
        return [
            (m.id, m.edited_at, m.pinned, tuple((str(r.emoji), r.count, r.me) for r in m.reactions))
            for m in msgs
        ]

    def cacheMessages(self, ch, msgs) -> None:
        self.msgCache[ch.id] = list(msgs)[-HISTORY_PAGE:]
        self.msgCache.move_to_end(ch.id)
        while len(self.msgCache) > 40:
            self.msgCache.popitem(last=False)

    #shows cached messages instantly then swaps in fresh history if it changed
    #_loadGen makes a slow load give up if you already moved on
    @work(exclusive=True, group="history")
    async def loadHistory(self, ch, around=None) -> None:
        self._loadGen += 1
        gen = self._loadGen
        ml = self.query_one("#messages", MessageList)
        try:
            acked = ch.read_state.last_acked_id
        except Exception:
            acked = None
        cached = self.msgCache.get(ch.id) if around is None else None
        await ml.remove_children()
        if cached:
            await self._showMessages(ml, ch, cached, acked)
        else:
            self.setLoading(True)
        try:
            if around is not None:
                msgs = [m async for m in ch.history(limit=HISTORY_PAGE, around=around)]
                msgs.sort(key=lambda m: m.id, reverse=True)
            else:
                msgs = [m async for m in ch.history(limit=HISTORY_PAGE)]
        except discord.Forbidden:
            msgs = None
        except Exception as e:
            log.exception("history")
            msgs = e
        if gen != self._loadGen:
            return
        if isinstance(msgs, list):
            msgs.reverse()
            if around is None:
                self.cacheMessages(ch, msgs)
            if cached and self._sig(cached) == self._sig(msgs):
                self.scheduleAck(ch, 0.5)
                return
            if cached:
                await ml.remove_children()
                await self._showMessages(ml, ch, msgs, acked)
                self.scheduleAck(ch, 0.5)
                return
        await ml.remove_children()
        if msgs is None:
            self.setLoading(False)
            await ml.mount(
                Static(Text("You don't have permission to read history here.", style="bright_black"))
            )
            return
        if isinstance(msgs, Exception):
            self.setLoading(False)
            await ml.mount(Static(Text(f"Failed to load history: {msgs}", style="red")))
            return
        await self._showMessages(ml, ch, msgs, acked if around is None else None)
        if around is not None:
            ml.focus()
            idx = next((i for i, w in enumerate(ml.views()) if w.message.id == around.id), len(msgs) - 1)
            ml.call_after_refresh(ml.select, idx)
        self.scheduleAck(ch, 0.5)

    #keep the loading screen up until recent images are drawn so the view doesnt jump
    async def _showMessages(self, ml, ch, msgs, acked) -> None:
        if not msgs:
            await ml.mount(
                Static(Text(f"This is the start of {channelName(ch)}. Say hi!", style="bright_black"))
            )
            self.setLoading(False)
            return
        self.setLoading(True)
        gen = self._loadGen
        await ml.mount_all(self.buildWidgets(msgs, None, newAfter=acked))
        #keep loading up until recent media is ready
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and gen == self._loadGen:
            recent = ml.views()[-12:]
            if all(v.mediaDone for v in recent) and all(
                0 in w._encoded for v in recent for w in v.query(sixelview.SixelMedia)
            ):
                break
            await asyncio.sleep(0.03)
        if gen != self._loadGen:
            return
        self.setLoading(False)
        ml.call_after_refresh(ml.scroll_end, animate=False)

    def setLoading(self, on: bool) -> None:
        self.query_one("#messages").set_class(on, "loading")
        self.query_one("#loading").set_class(on, "on")

    #date separators the new messages line and grouping of back to back messages
    def buildWidgets(self, msgs, prev: Optional[discord.Message], newAfter: Optional[int] = None):
        widgets = []
        newDone = newAfter is None or not newAfter
        if self.plugin("no_blocked"):
            msgs = [m for m in msgs if not isBlocked(m.author)]
        for m in msgs:
            d = m.created_at.astimezone().date()
            if prev is None or d != prev.created_at.astimezone().date():
                widgets.append(Static(dateSeparator(d), classes="datesep"))
                prevForGroup = None
            else:
                prevForGroup = prev
            if not newDone and m.id > newAfter and m.author.id != self.meId:
                widgets.append(Static(fmt.newSeparator(), classes="newsep"))
                newDone = True
                prevForGroup = None
            widgets.append(MessageView(m, grouped=canGroup(prevForGroup, m)))
            prev = m
        return widgets

    def loadOlder(self) -> None:
        if self._loadingOlder or self.current is None:
            return
        self._loadingOlder = True
        self._loadOlder(self.current)

    @work(group="older")
    async def _loadOlder(self, ch) -> None:
        try:
            ml = self.query_one("#messages", MessageList)
            views = ml.views()
            if not views:
                return
            first = views[0]
            msgs = [m async for m in ch.history(limit=HISTORY_PAGE, before=first.message)]
            if ch is not self.current:
                return
            if not msgs:
                self.notify("That's the start of the conversation.")
                return
            msgs.reverse()
            children = list(ml.children)
            if children and "datesep" in children[0].classes:
                await children[0].remove()
            widgets = self.buildWidgets(msgs, None)
            if first.message.created_at.astimezone().date() != msgs[-1].created_at.astimezone().date():
                widgets.append(
                    Static(dateSeparator(first.message.created_at.astimezone().date()), classes="datesep")
                )
            await ml.mount_all(widgets, before=0)
            ml.select(len(msgs) - 1)
        finally:
            self._loadingOlder = False

    def viewFor(self, message_id: int) -> Optional[MessageView]:
        for w in self.query_one("#messages", MessageList).views():
            if w.message.id == message_id:
                return w
        return None

    #composing

    def startReply(self, m: discord.Message) -> None:
        self.editing = None
        self.replyTo = m
        self._showCompose(
            f"↪ replying to {m.author.display_name}: {snippet(m.clean_content, 50)}   esc cancel"
        )

    def startEdit(self, m: discord.Message) -> None:
        if m.author.id != self.meId:
            self.notify("You can only edit your own messages.", severity="warning")
            return
        self.replyTo = None
        self.editing = m
        inp = self.query_one("#input", ChatInput)
        inp.value = m.content
        inp.cursor_position = len(m.content)
        self._showCompose("✎ editing message   enter save · esc cancel")

    def _showCompose(self, text: str) -> None:
        c = self.query_one("#compose", Static)
        c.update(Text(text))
        c.add_class("on")
        self.query_one("#input", ChatInput).focus()

    def cancelCompose(self) -> None:
        if self.editing:
            self.query_one("#input", ChatInput).value = ""
        self.replyTo = None
        self.editing = None
        self.query_one("#compose", Static).remove_class("on")

    #completion

    def mentionPool(self) -> list:
        pool, seen = [], set()

        def add(u):
            if u is not None and u.id not in seen and u.id != self.meId:
                seen.add(u.id)
                pool.append(u)

        ch = self.current
        if isinstance(ch, discord.GroupChannel):
            for u in ch.recipients:
                add(u)
            return pool
        if isinstance(ch, discord.DMChannel):
            add(ch.recipient)
            return pool
        for w in reversed(self.query_one("#messages", MessageList).views()[-200:]):
            add(w.message.author)
        target = ch.parent if isinstance(ch, discord.Thread) and ch.parent is not None else ch
        try:
            members = target.members
        except Exception:
            members = getattr(getattr(ch, "guild", None), "members", [])
        for mbr in members:
            add(mbr)
        return pool

    _TOKEN = re.compile(r"(?:^|(?<=\s))([@#:])([^\s@#:]*)$")

    _CLOSED_SHORTCODE = re.compile(r"(?:^|(?<=\s)):([\w+\-]{2,32}):$")

    #:shortcode: turns into the emoji as soon as you close it, also drives autocomplete and typing
    @on(Input.Changed, "#input")
    def _inputChanged(self, ev: Input.Changed) -> None:
        inp = self.query_one("#input", ChatInput)
        before = ev.value[: inp.cursor_position]
        mt = self._CLOSED_SHORTCODE.search(before)
        if mt and not before.startswith("/") and before.count("`") % 2 == 0:
            name = mt.group(1).lower()
            ch = emo.UNICODE.get(name)
            if ch is not None:
                start = before.rfind(":" + mt.group(1) + ":")
                inp.value = ev.value[:start] + ch + ev.value[inp.cursor_position :]
                inp.cursor_position = start + len(ch)
                return
        self.updateSuggestions(ev.value[: inp.cursor_position])
        if not ev.value or ev.value.startswith("/") or self.current is None:
            return
        now = asyncio.get_running_loop().time()
        if now - self._lastTypingSent > 8 and not self.plugin("silent_typing"):
            self._lastTypingSent = now
            ch = self.current

            async def go():
                try:
                    await ch.typing()
                except Exception:
                    pass

            asyncio.create_task(go())

    #autocomplete for @user #channel and :emoji: from the word under the cursor
    def updateSuggestions(self, before: str) -> None:
        if before.startswith("/") and " " not in before and self.current is not None:
            self.slashSuggestions(before[1:].lower())
            return
        m = self._TOKEN.search(before)
        if not m or self.current is None or before.startswith("/"):
            self.clearSuggestions()
            return
        sigil, word = m.group(1), m.group(2).lower()
        out = []
        if sigil == "@":
            for u in self.mentionPool():
                names = (u.name.lower(), u.display_name.lower(), (u.global_name or "").lower())
                if any(n.startswith(word) for n in names) or (
                    len(word) >= 2 and any(word in n for n in names)
                ):
                    out.append(
                        (
                            Text(f"@{u.display_name}", style=nameColour(u))
                            + Text(f" {u.name}", style="bright_black"),
                            f"@{u.name} ",
                            u,
                        )
                    )
                if len(out) >= 8:
                    break
            if "everyone".startswith(word) and self.currentGuild() is not None:
                out.append((Text("@everyone", style="yellow"), "@everyone ", None))
            if "here".startswith(word) and self.currentGuild() is not None:
                out.append((Text("@here", style="yellow"), "@here ", None))
        elif sigil == "#":
            g = self.currentGuild()
            if g is not None:
                for c in g.text_channels:
                    if word in c.name.lower() and canRead(c):
                        out.append((Text(f"#{c.name}", style="cyan"), f"#{c.name} ", c))
                    if len(out) >= 8:
                        break
        elif sigil == ":" and len(word) >= 2:
            out = [
                (lab, ins + ("" if ins.startswith(":") else " "), None)
                for lab, ins in emo.complete(self.client, word, self.currentGuild())
            ]
        if not out:
            self.clearSuggestions()
            return
        self.suggestions = out
        self.suggestIdx = 0
        self._suggestSpan = (m.start(1), len(before))
        self.renderSuggestions()

    def renderSuggestions(self) -> None:
        bar = self.query_one("#suggest", Static)
        t = Text("tab ", style="bright_black")
        for i, (lab, _ins, _v) in enumerate(self.suggestions):
            piece = lab.copy()
            if i == self.suggestIdx:
                piece.stylize("reverse")
            t.append_text(piece)
            t.append("  ")
        bar.update(t)
        bar.add_class("on")

    def moveSuggestion(self, delta: int) -> None:
        if self.suggestions:
            self.suggestIdx = (self.suggestIdx + delta) % len(self.suggestions)
            self.renderSuggestions()

    def clearSuggestions(self) -> None:
        self.suggestions = []
        self.query_one("#suggest", Static).remove_class("on")

    #typing / lists our commands and the bots commands in this chat
    def slashSuggestions(self, word: str) -> None:
        out = [(Text(f"/{n}", style="cyan") + Text(f" {d}", style="bright_black"), f"/{n} ", None)
               for n, d in self.BUILTINS if n.startswith(word)]
        cached = self._appCmds.get(self.current.id)
        if cached is None:
            asyncio.create_task(self._fetchAppCommands(self.current, word))
        for path, c, bot in (cached[1] if cached else []):
            if path.startswith(word):
                desc = snippet(getattr(c, "description", "") or "", 40)
                out.append((Text(f"/{path}", style="yellow") + Text(f" {desc} · {bot}", style="bright_black"),
                            f"/{path} ", None))
            if len(out) >= 10:
                break
        if not out:
            self.clearSuggestions()
            return
        self.suggestions = out[:10]
        self.suggestIdx = 0
        self._suggestSpan = (0, len(word) + 1)
        self.renderSuggestions()

    async def _fetchAppCommands(self, ch, word: str) -> None:
        await self.appCommands(ch)
        inp = self.query_one("#input", ChatInput)
        if self.current is ch and inp.value.startswith("/") and " " not in inp.value:
            self.slashSuggestions(inp.value[1:].lower())

    def acceptSuggestion(self) -> None:
        if not self.suggestions:
            return
        _lab, ins, _v = self.suggestions[self.suggestIdx]
        inp = self.query_one("#input", ChatInput)
        start, end = self._suggestSpan
        value = inp.value
        inp.value = value[:start] + ins + value[end:]
        inp.cursor_position = start + len(ins)
        self.clearSuggestions()

    #turn @name #channel and :emoji: into discord mention syntax skipping code blocks
    def convertOutgoing(self, text: str) -> str:
        g = self.currentGuild()
        pool = None
        parts = re.split(r"(```.*?```|`[^`]*`)", text, flags=re.S)
        for i in range(0, len(parts), 2):

            def mention(m: re.Match) -> str:
                nonlocal pool
                name = m.group(1).lower()
                if name in ("everyone", "here"):
                    return m.group(0)
                if pool is None:
                    pool = self.mentionPool()
                for u in pool:
                    if name in (u.name.lower(), u.display_name.lower(), (u.global_name or "").lower()):
                        return f"<@{u.id}>"
                if g is not None:
                    r = discord.utils.find(lambda r: r.name.lower() == name and r.mentionable, g.roles)
                    if r is not None:
                        return f"<@&{r.id}>"
                return m.group(0)

            def chan(m: re.Match) -> str:
                c = (
                    discord.utils.find(lambda c: c.name.lower() == m.group(1).lower(), g.channels)
                    if g
                    else None
                )
                return f"<#{c.id}>" if c is not None else m.group(0)

            parts[i] = re.sub(r"(?<![<\w])@([\w.\-]{1,32})", mention, parts[i])
            if g is not None:
                parts[i] = re.sub(r"(?<![<\w&])#([\w\-]{1,100})", chan, parts[i])
        out = emo.convertShortcodes(self.client, "".join(parts), g, links=self.plugin("fake_nitro"))
        return plugins.clearUrls(out) if self.plugin("clear_urls") else out

    @on(Input.Submitted, "#input")
    async def _submit(self, ev: Input.Submitted) -> None:
        if self.suggestions:
            self.acceptSuggestion()
            return
        text = ev.value
        if not text.strip() and not self.attachments:
            return
        inp = self.query_one("#input", ChatInput)
        if self.attachments and not self.editing and not text.startswith("/"):
            await self.sendWithAttachments(text)
            return
        if text.startswith("/") and not text.startswith("//"):
            inp.value = ""
            await self.runCommand(text[1:].strip())
            return
        if text.startswith("//"):
            text = text[1:]
        if self.current is None:
            self.notify("Pick a chat first (Ctrl+K).", severity="warning")
            return
        inp.value = ""
        self._lastTypingSent = 0
        editing, replyTo = self.editing, self.replyTo
        self.cancelCompose()
        out = self.convertOutgoing(text)
        try:
            if editing:
                await editing.edit(content=out)
            else:
                #@silent at the start sends without a notification like the desktop app
                silent = out.startswith("@silent ")
                if silent:
                    out = out[len("@silent "):]
                kw = dict(silent=True) if silent else {}
                if replyTo:
                    kw.update(reference=replyTo, mention_author=not self.plugin("no_reply_mention"))
                await self.current.send(out, **kw)
        except discord.HTTPException as e:
            self.notify(f"Send failed: {e.text or e}", severity="error", markup=False)
            inp.value = text

    #discord events

    async def onDiscordReady(self) -> None:
        if self._readyOnce:
            return
        self._readyOnce = True
        from .update import checkInBackground

        checkInBackground(lambda msg: self.call_from_thread(self.updateAvailable, msg))
        if self.plugin("rich_presence"):
            self.startRpc()
        self.buildSidebar()
        self.updateMePanel()
        inp = self.query_one("#input", ChatInput)
        inp.disabled = False
        inp.placeholder = "Pick a chat: Ctrl+K to jump, F1 for keys"
        self.query_one("#title", Static).update(
            Text(f"Connected as {self.client.user.name}  ·  Ctrl+K jump  ·  F1 keys", style="bold")
        )
        if not vmod.load_opus():
            log.warning("libopus not found; voice disabled")
        last = self.cfg.get("last_channel")
        ch = self.client.get_channel(int(last)) if last else None
        if ch is not None and not isForum(ch):
            self.openChannel(ch)
        else:
            self.query_one("#sidebar", Tree).focus()

    #new message either append it to the open chat or bump unread and maybe notify
    async def onDiscordMessage(self, m: discord.Message) -> None:
        if not self._readyOnce:
            return
        if self.plugin("no_blocked") and isBlocked(m.author):
            return
        if self.plugin("moyai") and "🗿" in m.content and self.current is not None and m.channel.id == self.current.id:
            vmod.playSfx("boom", int(self.cfg.get("sfx_volume", 100)) / 100)
        ch = m.channel
        cached = self.msgCache.get(ch.id)
        if cached is not None and (not cached or cached[-1].id < m.id):
            cached.append(m)
            del cached[:-HISTORY_PAGE]
        mine = m.author.id == self.meId
        if self.current is not None and ch.id == self.current.id:
            ml = self.query_one("#messages", MessageList)
            atBottom = ml.scroll_y >= ml.max_scroll_y - 3
            views = ml.views()
            prev = views[-1].message if views else None
            for w in ml.query(".newsep"):
                if mine:
                    await w.remove()
            await ml.mount_all(self.buildWidgets([m], prev))
            self.typing.pop(m.author.id, None)
            if len(views) + 1 > MAX_MESSAGES and ml.selected < 0:
                await views[0].remove()
            if atBottom or mine:
                ml.call_after_refresh(ml.scroll_end, animate=False)
            if not mine:
                if self.app_focus:
                    self.scheduleAck(ch, 2)
                else:
                    self.localUnread[ch.id] = self.localUnread.get(ch.id, 0) + 1
                    if isinstance(ch, PRIVATE) or mentionsMe(m, self.meId):
                        self.desktopNotify(m)
            if isinstance(ch, PRIVATE):
                self.moveDmToTop(ch)
            return
        if mine:
            if isinstance(ch, PRIVATE):
                self.moveDmToTop(ch)
            return
        self.localUnread[ch.id] = self.localUnread.get(ch.id, 0) + 1
        isDm = isinstance(ch, PRIVATE)
        pinged = mentionsMe(m, self.meId) and not isMuted(ch) or isDm and not isMuted(ch)
        if pinged:
            self.localMentions[ch.id] = self.localMentions.get(ch.id, 0) + 1
            where = channelName(ch) if isDm else f"#{channelName(ch)} ({ch.guild.name})"
            self.notify(
                f"{m.author.display_name} in {where}: {snippet(m.clean_content or '(attachment)', 80)}",
                title="New message",
                markup=False,
            )
            self.desktopNotify(m)
        if isDm:
            self.moveDmToTop(ch)
        if isinstance(ch, discord.Thread) and ch.id not in self.nodes and ch.parent is not None:
            pnode = self.nodes.get(ch.parent.id)
            if pnode is not None:
                self.nodes[ch.id] = pnode.add_leaf(self.nodeLabel(ch), data=("chan", ch))
        self.relabel(ch)

    def moveDmToTop(self, ch) -> None:
        if self.dmRoot is None:
            return
        first = self.dmRoot.children[0] if self.dmRoot.children else None
        if first is None or not first.data or first.data[1] is None or first.data[1].id != ch.id:
            self.rebuildDms()

    def desktopNotify(self, m: discord.Message) -> None:
        if not self.notifyCmd:
            return
        ch = m.channel
        title = (
            m.author.display_name
            if isinstance(ch, discord.DMChannel)
            else f"{m.author.display_name} · {channelName(ch)}"
        )
        asyncio.create_task(
            asyncio.create_subprocess_exec(
                self.notifyCmd,
                "-a",
                "uncord",
                title,
                snippet(m.clean_content or "[attachment]", 200),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        )

    def onDiscordEdit(self, payload) -> None:
        cached = self.msgCache.get(payload.channel_id)
        msg = getattr(payload, "message", None)
        if cached and msg is not None:
            cached[:] = [msg if m.id == msg.id else m for m in cached]
        if self.current is None or payload.channel_id != self.current.id:
            return
        w = self.viewFor(payload.message_id)
        msg = getattr(payload, "message", None)
        if w and msg is not None:
            old = w.message.content
            if self.plugin("message_logger") and old and old != msg.content:
                w.edits = (w.edits + [old])[-5:]
            w.refreshFrom(msg)

    def onDiscordDelete(self, payload) -> None:
        cached = self.msgCache.get(payload.channel_id)
        if cached:
            cached[:] = [m for m in cached if m.id != payload.message_id]
        if self.current is None or payload.channel_id != self.current.id:
            return
        w = self.viewFor(payload.message_id)
        if w and self.plugin("message_logger"):
            w.deleted = True
            w.rerender()
        elif w:
            w.remove()

    def onDiscordAck(self, payload) -> None:
        ch = self.client.get_channel(payload.channel_id)
        if ch is None:
            return
        self.localUnread.pop(ch.id, None)
        self.localMentions.pop(ch.id, None)
        self.relabel(ch)

    def onDiscordTyping(self, channel, user) -> None:
        if user.id == self.meId:
            return
        if self.current is None or channel.id != self.current.id:
            #typingindicator ✎ on the channel in the sidebar
            if self.plugin("typing_indicator"):
                self.typingElsewhere[channel.id] = time.monotonic()
                self.relabel(channel)
            return
        self.typing[user.id] = asyncio.get_running_loop().time()
        self.typingNames[user.id] = getattr(user, "display_name", str(user))
        self.tickTyping()

    def tickTyping(self) -> None:
        stale = [cid for cid, t in self.typingElsewhere.items() if time.monotonic() - t > 10]
        for cid in stale:
            self.typingElsewhere.pop(cid, None)
            self.relabel(self.client.get_channel(cid))
        now = asyncio.get_running_loop().time()
        for uid in [u for u, t in self.typing.items() if now - t > 10]:
            self.typing.pop(uid, None)
        names = [self.typingNames[u] for u in self.typing]
        if getattr(self, "_hintOn", False) and not names:
            self._setTyping(Text(self.HINT, style="bright_black"))
            return
        txt = ""
        if len(names) == 1:
            txt = f"{names[0]} is typing…"
        elif 1 < len(names) <= 3:
            txt = f"{', '.join(names)} are typing…"
        elif names:
            txt = "Several people are typing…"
        self._setTyping(Text(txt))

    def _setTyping(self, t: Text) -> None:
        #runs every second so skip when nothing changed
        if t.plain != getattr(self, "_lastTyping", None):
            self._lastTyping = t.plain
            self.query_one("#typing", Static).update(t)

    def onDiscordPresence(self, user) -> None:
        if user is None:
            return
        if user.id == self.meId:
            self.updateMePanel()
            return
        dm = getattr(user, "dm_channel", None)
        if dm is not None and dm.id in self.nodes:
            self.relabel(dm)
        self.membersLater()

    #relationshipnotifier
    def onRelationship(self, rel, added: bool) -> None:
        if not self.plugin("relationship_notifier"):
            return
        name = rel.user.display_name if rel.user else "Someone"
        kind = rel.type
        RT = discord.RelationshipType
        if added and kind == RT.incoming_request:
            msg = f"{name} sent you a friend request (Ctrl+U to accept)"
        elif added and kind == RT.friend:
            msg = f"You and {name} are now friends"
        elif not added and kind == RT.friend:
            msg = f"{name} is no longer your friend"
        elif not added and kind == RT.outgoing_request:
            msg = f"{name} declined or you cancelled the friend request"
        else:
            return
        self.notify(msg, title="Friends", timeout=10, markup=False)

    def sfx(self, kind: str) -> None:
        if self.cfg.get("sfx", True):
            vmod.playSfx(kind, int(self.cfg.get("sfx_volume", 100)) / 100)

    def onDiscordVoiceState(self, member, before, after) -> None:
        vc = self.vc
        if vc is not None and vc.channel is not None and member.id != self.meId:
            cid = vc.channel.id
            was = before.channel is not None and before.channel.id == cid
            now = after.channel is not None and after.channel.id == cid
            if now and not was:
                self.sfx("join")
            elif was and not now:
                self.sfx("leave")
        for ch in {before.channel, after.channel} - {None}:
            node = self.nodes.get(ch.id)
            if node is not None and isVoice(ch):
                self.fillVoiceMembers(node, ch)
        if (
            vc is not None
            and vc.channel is not None
            and member.id != self.meId
            and after.channel is not None
            and after.channel.id == vc.channel.id
        ):
            if getattr(after, "self_stream", False) and not getattr(before, "self_stream", False):
                self.notify(f"{member.display_name} is live", timeout=8, markup=False)
                self.updateVoicebar()
            elif getattr(before, "self_stream", False) and not getattr(after, "self_stream", False):
                self.updateVoicebar()
            if getattr(after, "self_video", False) and not getattr(before, "self_video", False):
                self.notify(f"{member.display_name} turned their camera on (F11 to watch)", timeout=6, markup=False)
                self.updateVoicebar()
            elif getattr(before, "self_video", False) and not getattr(after, "self_video", False):
                if self.isWatching(member.id, "camera"):
                    asyncio.create_task(self.stopWatch(quiet=True))
                    self.notify(f"{member.display_name} turned their camera off.", markup=False)
                self.updateVoicebar()
        if member.id == self.meId and after.channel is None and self.vc is not None:
            if self.share is not None:
                asyncio.create_task(self.stopShare(quiet=True))
            if self.camera is not None:
                asyncio.create_task(self.stopCamera(quiet=True))
            if self.watching is not None:
                asyncio.create_task(self.stopWatch(quiet=True))
            vc, self.vc = self.vc, None
            vc.stopAudio()
            self.sfx("disconnect")
            if before.channel:
                self.relabel(before.channel)
            self.notify("Left voice.")
            self.updateVoicebar()

    def onDiscordCall(self, call, ended: bool = False) -> None:
        ch = call.channel
        ringingMe = not ended and any(u.id == self.meId for u in getattr(call, "ringing", []))
        if ringingMe and (self.incoming is None or self.incoming.id != ch.id):
            self.incoming = ch
            who = getattr(call, "initiator", None)
            whoName = who.display_name if who else channelName(ch)
            self.notify(
                f"📞 {whoName} is calling ({channelName(ch)}). F5 to answer, /decline to ignore.",
                title="Incoming call",
                timeout=30,
                markup=False,
            )
            if self.notifyCmd:
                asyncio.create_task(
                    asyncio.create_subprocess_exec(
                        self.notifyCmd,
                        "-a",
                        "uncord",
                        "-u",
                        "critical",
                        "Incoming call",
                        f"{whoName} is calling",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                )
            self.relabel(ch)
        elif not ringingMe and self.incoming is not None and self.incoming.id == ch.id:
            self.incoming = None
            self.relabel(ch)

    async def declineCall(self) -> None:
        ch = self.incoming
        if not ch:
            self.notify("Nobody is calling.")
            return
        self.incoming = None
        self.relabel(ch)
        call = getattr(ch, "call", None)
        try:
            if isinstance(ch, discord.GroupChannel) and call:
                await call.stop_ringing(self.client.user)
            elif call:
                await call.stop_ringing()
        except Exception as e:
            self.notify(f"Decline failed: {e}", severity="error", markup=False)

    @work(group="rpc")
    async def startRpc(self) -> None:
        from .rpc import RpcServer

        self.rpc = RpcServer(self.client, self.setRpcActivity)
        why = await self.rpc.start()
        if why == "busy":
            self.notify("Another Discord app is running, so games will show their status there, not in uncord.",
                        timeout=8, markup=False)

    def setRpcActivity(self, act) -> None:
        old, self.rpcActivity = self.rpcActivity, act
        if act is not None and (old is None or old.name != act.name):
            self.notify(f"🎮 Now showing: {act.name}", timeout=4, markup=False)
        asyncio.create_task(self._pushPresence())

    async def _pushPresence(self) -> None:
        custom = next((a for a in self.client.activities if isinstance(a, discord.CustomActivity)), None)
        acts = ([custom] if custom else []) + ([self.rpcActivity] if self.rpcActivity is not None else [])
        try:
            await self.client.change_presence(activities=acts)
        except Exception:
            log.debug("rich presence update", exc_info=True)

    async def action_quit(self) -> None:
        if self.rpc is not None:
            self.rpc.stop()
        if self.vc is not None:
            await self._leave()
        try:
            await asyncio.wait_for(self.client.close(), 5)
        except Exception:
            pass
        self.exit()
