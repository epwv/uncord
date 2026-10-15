#server and channel menus: notifications mute nickname invites info and leaving
from __future__ import annotations

import asyncio
import datetime
import logging

import discord
from rich.text import Text

from .fmt import channelName, isVoice
from .screens import Confirm, Picker, Prompt, TextScreen
from .tasks import spawn

log = logging.getLogger("uncord")

MUTE_FOR = (("15 minutes", 15), ("1 hour", 60), ("3 hours", 180), ("8 hours", 480), ("24 hours", 1440),
            ("Until I turn it back on", 0))
LEVELS = ((discord.NotificationLevel.all_messages, "All messages"),
          (discord.NotificationLevel.only_mentions, "Only @mentions"),
          (discord.NotificationLevel.nothing, "Nothing"))


def _mutedText(settings) -> str:
    mute = getattr(settings, "muted", None)
    if not getattr(mute, "muted", False):
        return ""
    until = getattr(mute, "until", None)
    return f" (muted until {until.astimezone():%H:%M %d %b})" if until else " (muted)"


class ServersMixin:

    #right click on a server
    def guildMenu(self, g) -> None:
        ns = g.notification_settings
        muted = bool(getattr(ns.muted, "muted", False))
        entries = [
            (Text("✓ Mark server as read"), "read", "read"),
            (Text("🔔 Notification settings") + Text(f"  {self._levelName(ns.level)}{_mutedText(ns)}",
                                                    style="bright_black"), "notifications", "notify"),
            (Text("🔔 Unmute server" if muted else "🔕 Mute server…"), "mute unmute", "mute"),
            (Text("✎ Change my nickname"), "nickname nick", "nick"),
            (Text("✉ Invite people"), "invite link", "invite"),
            (Text("ℹ Server info"), "info about", "info"),
            (Text("Copy server ID", style="bright_black"), "copy id", "copy"),
        ]
        if g.owner_id != self.meId:
            entries.append((Text("🚪 Leave server", style="bold red"), "leave", "leave"))

        def done(act) -> None:
            if act == "read":
                for c in g.channels:
                    if hasattr(c, "read_state") and self.unreadCount(c):
                        self.clearUnread(c)
                        self.scheduleAck(c, 0)
                        self.relabel(c)
            elif act == "notify":
                self.notifyMenu(g)
            elif act == "mute":
                if muted:
                    self._editSettings(g, "Unmuted", muted_until=False)
                else:
                    self.muteMenu(g)
            elif act == "nick":
                self.push_screen(Prompt(f"Nickname in {g.name}", "blank resets it", g.me.nick or ""),
                                 lambda nick: nick is not None and spawn(self._setNick(g, nick)))
            elif act == "invite":
                spawn(self._invite(g))
            elif act == "info":
                self.serverInfo(g)
            elif act == "copy":
                self.copyText(str(g.id))
            elif act == "leave":
                self.push_screen(Confirm(f"Leave {g.name}?", "You'll need a new invite to come back."),
                                 lambda yes: yes and spawn(self._leaveGuild(g)))

        self.push_screen(Picker(entries, placeholder=g.name, title=g.name), done)

    #right click on a text channel
    def channelMenu(self, ch) -> None:
        ns = ch.notification_settings
        muted = bool(getattr(ns.muted, "muted", False))
        entries = [(Text("🧵 Threads"), "threads", "threads")] if isinstance(ch, discord.TextChannel) else []
        entries += [
            (Text("✓ Mark as read"), "read", "read"),
            (Text("🔔 Notification settings") + Text(f"  {self._levelName(ns.level, channel=True)}{_mutedText(ns)}",
                                                    style="bright_black"), "notifications", "notify"),
            (Text("🔔 Unmute channel" if muted else "🔕 Mute channel…"), "mute unmute", "mute"),
            (Text("🔗 Copy link to channel"), "link", "link"),
            (Text("Copy channel ID", style="bright_black"), "copy id", "copy"),
        ]

        def done(act) -> None:
            if act == "threads":
                self.threadPicker(ch)
            elif act == "read":
                self.clearUnread(ch)
                self.scheduleAck(ch, 0)
                self.relabel(ch)
            elif act == "notify":
                self.notifyMenu(ch)
            elif act == "mute":
                if muted:
                    self._editSettings(ch, "Unmuted", muted_until=False)
                else:
                    self.muteMenu(ch)
            elif act == "link":
                self.copyText(f"https://discord.com/channels/{ch.guild.id}/{ch.id}")
            elif act == "copy":
                self.copyText(str(ch.id))

        self.push_screen(Picker(entries, placeholder=f"#{ch.name}"), done)

    @staticmethod
    def _levelName(level, channel: bool = False) -> str:
        if level == discord.NotificationLevel.server_default:
            return "server default" if channel else "all messages"
        return {discord.NotificationLevel.all_messages: "all messages",
                discord.NotificationLevel.only_mentions: "only @mentions",
                discord.NotificationLevel.nothing: "nothing"}.get(level, str(level))

    #notification level plus the server only switches, picking one applies it and the menu stays open
    def notifyMenu(self, target) -> None:
        guild = isinstance(target, discord.Guild)
        ns = target.notification_settings
        levels = list(LEVELS) + ([] if guild else [(discord.NotificationLevel.server_default, "Use server default")])
        entries = []
        for level, name in levels:
            on = ns.level == level
            entries.append((Text("◉ " if on else "○ ", style="green" if on else "bright_black") + Text(name),
                            name.lower(), ("level", level)))
        if guild:
            for key, label in (("suppress_everyone", "Suppress @everyone and @here"),
                               ("suppress_roles", "Suppress all role @mentions"),
                               ("mobile_push", "Push notifications to my phone"),
                               ("hide_muted_channels", "Hide muted channels")):
                on = bool(getattr(ns, key, False))
                entries.append((Text("☑ " if on else "☐ ", style="green" if on else "bright_black") + Text(label),
                                label.lower(), ("flag", key, not on)))
        name = target.name if guild else f"#{target.name}"

        def picked(v) -> None:
            if not v:
                return
            if v[0] == "level":
                self._editSettings(target, "Saved", reopen=True, level=v[1])
            else:
                self._editSettings(target, "Saved", reopen=True, **{v[1]: v[2]})

        self.push_screen(Picker(entries, placeholder="Filter…", title=f"Notifications · {name}"), picked)

    def muteMenu(self, target) -> None:
        entries = [(Text(f"🔕 For {label.lower()}" if minutes else f"🔕 {label}"), label.lower(), minutes)
                   for label, minutes in MUTE_FOR]

        def picked(minutes) -> None:
            if minutes is None:
                return
            until = True if minutes == 0 else discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
            self._editSettings(target, "Muted", muted_until=until)

        self.push_screen(Picker(entries, placeholder="How long?", title=f"Mute {target.name}"), picked)

    def _editSettings(self, target, done: str, reopen: bool = False, **changes) -> None:
        async def go():
            try:
                await target.notification_settings.edit(**changes)
            except Exception as e:
                log.exception("notification settings")
                self.notify(f"Couldn't change that: {e}", severity="error", markup=False)
                return
            self.notify(done, timeout=2)
            if isinstance(target, discord.Guild):
                node = self.guildNodes.get(target.id)
                if node is not None:
                    node.set_label(self.guildLabel(target))
            else:
                self.relabel(target)
            if reopen:
                await asyncio.sleep(0.3)
                self.notifyMenu(target)

        spawn(go())

    async def _setNick(self, g, nick: str) -> None:
        try:
            await g.me.edit(nick=nick.strip() or None)
            self.notify(f"Nickname {'set to ' + nick.strip() if nick.strip() else 'reset'} in {g.name}.", markup=False)
        except discord.HTTPException as e:
            self.notify(f"Couldn't change nickname: {e.text or e}", severity="error", markup=False)

    async def _invite(self, g) -> None:
        chans = [c for c in g.text_channels if c.permissions_for(g.me).create_instant_invite]
        if not chans:
            self.notify("You can't make invites in this server.", severity="warning")
            return
        target = self.current if self.current in chans else chans[0]
        try:
            inv = await target.create_invite(max_age=7 * 86400, unique=False)
        except discord.HTTPException as e:
            self.notify(f"Couldn't make an invite: {e.text or e}", severity="error", markup=False)
            return
        self.copyText(inv.url)
        self.notify(f"Invite copied: {inv.url} (lasts 7 days)", timeout=8, markup=False)

    async def _leaveGuild(self, g) -> None:
        try:
            await g.leave()
        except discord.HTTPException as e:
            self.notify(f"Couldn't leave: {e.text or e}", severity="error", markup=False)
            return
        if self.current is not None and getattr(self.current, "guild", None) is g:
            self.current = None
        self.notify(f"Left {g.name}.", markup=False)
        self.buildSidebar()

    def serverInfo(self, g) -> None:
        t = Text(g.name + "\n", style="bold")
        if g.description:
            t.append(g.description + "\n", style="italic")
        owner = g.owner or self.client.get_user(g.owner_id)
        rows = [
            ("Owner", owner.display_name if owner else str(g.owner_id)),
            ("Created", f"{g.created_at.astimezone():%d %b %Y}"),
            ("Members", f"{g.member_count or len(g.members):,}"),
            ("Channels", f"{len(g.text_channels)} text · {len([c for c in g.channels if isVoice(c)])} voice"),
            ("Roles", str(len(g.roles))),
            ("Emoji", f"{len(g.emojis)} · stickers {len(g.stickers)}"),
            ("Boosts", f"{g.premium_subscription_count or 0} (level {g.premium_tier})"),
            ("Verification", str(g.verification_level).replace("_", " ")),
        ]
        if g.vanity_url_code:
            rows.append(("Vanity link", f"discord.gg/{g.vanity_url_code}"))
        if g.me and g.me.joined_at:
            rows.append(("You joined", f"{g.me.joined_at.astimezone():%d %b %Y}"))
        for k, v in rows:
            t.append(f"\n{k:<14}", style="bright_black")
            t.append(v)
        rules = getattr(g, "rules_channel", None)
        if rules is not None:
            t.append(f"\n{'Rules':<14}", style="bright_black")
            t.append(f"#{channelName(rules)}")
        self.push_screen(TextScreen(f"About {g.name}", t))
