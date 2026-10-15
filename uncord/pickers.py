#jump picker people dms threads forums and your status
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord
from discord.http import Route
from rich.style import Style
from rich.text import Text
from textual import work

from .fmt import (PRIVATE, STATUS_NAME, activityText, ago, canRead, channelIcon, channelName, isForum,
                  isMuted, isVoice, nameBadges, platformIcons, snippet, statusDot, userStatus)
from .screens import MessageSearch, Picker, Prompt

from .panels import SidePanel
from .tasks import spawn

log = logging.getLogger("uncord")


class PickersMixin:

    #pickers

    def dmEntries(self):
        out = []
        for ch in self.privateChannelsSorted():
            label = Text()
            if isinstance(ch, discord.DMChannel) and ch.recipient:
                label.append_text(statusDot(ch.recipient))
            else:
                label.append(channelIcon(ch))
            label.append(channelName(ch))
            hay = channelName(ch)
            if isinstance(ch, discord.DMChannel) and ch.recipient:
                hay += " " + ch.recipient.name
                label.append(f"  @{ch.recipient.name}", style="bright_black")
            if self.mention_count(ch):
                label.append(f" {self.mention_count(ch)}", style="bold white on red")
            out.append((label, hay.lower(), ch))
        return out

    def people(self, friendsOnly: bool = False):
        me = self.meId
        seen, out = set(), []
        friends = [r.user for r in self.client.friends]
        friendIds = {u.id for u in friends}
        others = [] if friendsOnly else [u for u in self.client.users if not u.bot]
        order = {"online": 0, "idle": 1, "dnd": 2}
        friends.sort(key=lambda u: (order.get(userStatus(u), 3), u.display_name.lower()))
        for u in friends + others:
            if u.id in seen or u.id == me or getattr(u, "system", False):
                continue
            seen.add(u.id)
            label = (statusDot(u) + Text(u.display_name) + nameBadges(u) + platformIcons(u)
                     + Text(f"  @{u.name}", style="bright_black"))
            act = activityText(u)
            if act:
                label.append(f"  {snippet(act, 30)}", style="bright_black italic")
            if u.id in friendIds:
                label.append("  friend", style="green")
            out.append((label, f"{u.display_name} @{u.name} {u.global_name or ''}".lower(), u))
        return out

    def allDestinations(self):
        out = []
        seen = set()
        for cid in reversed(self.history[-15:]):
            ch = self.client.get_channel(cid)
            if ch is None or cid in seen:
                continue
            seen.add(cid)
            where = "" if isinstance(ch, PRIVATE) else f"  {ch.guild.name}"
            label = Text(channelIcon(ch) + channelName(ch)) + Text(where + "  recent", style="bright_black")
            out.append((label, f"{channelName(ch)} {where}".lower(), ch))
        for label, hay, ch in self.dmEntries():
            if ch.id not in seen:
                seen.add(ch.id)
                out.append((label, hay, ch))
        for _folder, gs in self.guildLayout():
            for g in gs:
                for _cat, chans in self.guildChannels(g):
                    for c in chans:
                        label = Text(channelIcon(c), style="bright_black") + Text(
                            c.name, style="bold" if self.unreadCount(c) and not isMuted(c) else ""
                        )
                        label.append(f"  {g.name}", style="bright_black")
                        if self.mention_count(c):
                            label.append(f" {self.mention_count(c)}", style="bold white on red")
                        out.append((label, f"{c.name} {g.name}".lower(), c))
                        for t in self.activeThreads(c):
                            out.append(
                                (
                                    Text("↳ " + t.name)
                                    + Text(f"  #{c.name} · {g.name}", style="bright_black"),
                                    f"{t.name} {c.name} {g.name}".lower(),
                                    t,
                                )
                            )
        for g in self.client.guilds:
            out.append(
                (
                    Text("⌂ " + g.name, style="bold") + Text("  server", style="bright_black"),
                    f"{g.name} server".lower(),
                    g,
                )
            )
        return out + self.people()

    #f4 inbox unread channels and your recent mentions in one list
    @work(exclusive=True, group="inbox")
    async def action_inbox(self) -> None:
        if not self.client.is_ready():
            return
        unread = self.orderedChannels(unreadOnly=True)
        unread = [c for c in unread if self.current is None or c.id != self.current.id]
        try:
            mentions = [m async for m in self.client.recent_mentions(limit=25)]
        except Exception:
            log.debug("recent mentions", exc_info=True)
            mentions = []
        entries = []
        if unread:
            entries.append((Text("✓ Mark all of these as read", style="green"), "mark all read", "__readall"))
        entries.append((Text(f"UNREAD — {len(unread)}", style="bold bright_black"), "", None))
        for c in unread:
            label = Text(channelIcon(c), style="bright_black") + Text(channelName(c), style="bold")
            if not isinstance(c, PRIVATE):
                label.append(f"  {c.guild.name}", style="bright_black")
            n = self.mention_count(c)
            if n:
                label.append(f" {n}", style="bold white on red")
            entries.append((label, f"{channelName(c)} {getattr(getattr(c, 'guild', None), 'name', '')}".lower(), c))
        entries.append((Text(f"MENTIONS — {len(mentions)}", style="bold bright_black"), "", None))
        for m in mentions:
            where = channelName(m.channel) if isinstance(m.channel, PRIVATE) else (
                f"#{channelName(m.channel)} · {m.guild.name}" if m.guild else channelName(m.channel))
            label = Text(f"{ago(m.created_at)}  ", style="bright_black") + Text(m.author.display_name, style="bold")
            label.append(f"  {where}", style="bright_black")
            label.append(f"\n   {snippet(m.clean_content or '[attachment]', 90)}")
            entries.append((label, f"{m.author.display_name} {m.content}".lower(), m))

        def picked(v) -> None:
            if v == "__readall":
                for c in unread:
                    self.clearUnread(c)
                    self.scheduleAck(c, 0)
                    self.relabel(c)
                self.notify(f"Marked {len(unread)} chat{'s' if len(unread) != 1 else ''} as read.")
            elif isinstance(v, discord.Message):
                self.jumpTo(v)
            elif v is not None:
                self.openChannel(v)

        self.push_screen(Picker(entries, placeholder="Filter…", title="Inbox"), picked)

    def action_switcher(self) -> None:
        if not self.client.is_ready():
            return
        self.push_screen(
            Picker(
                self.allDestinations(), placeholder="Jump to a person, DM, group, channel, thread or server…"
            ),
            self.goTo,
        )

    def goTo(self, target) -> None:
        if target is None:
            return
        if isinstance(target, discord.Guild):
            self.selectGuild(target)
            return
        if isinstance(target, discord.abc.User):
            self.openDm(target)
        elif isinstance(target, discord.Relationship):
            self.openDm(target.user)
        elif isForum(target):
            self.forumPicker(target)
        elif isVoice(target):
            self.voiceChannelClicked(target)
        else:
            self.openChannel(target)

    def voiceChannelClicked(self, ch) -> None:
        vc = self.vc
        if vc is not None and vc.channel is not None and vc.channel.id == ch.id:
            self.voiceChannelMenu(ch)
            return
        self.joinVoice(ch)

    def voiceChannelMenu(self, ch) -> None:
        vc = self.vc
        inside = vc is not None and vc.channel is not None and vc.channel.id == ch.id
        entries = [
            (
                Text(
                    "✆ Leave call  (F8)" if inside else "🔊 Join voice",
                    style="bold red" if inside else "green",
                ),
                "leave disconnect join",
                "join",
            ),
            (Text("# Open text chat"), "chat", "chat"),
            (Text("✓ Mark as read"), "read", "read"),
            (Text("Copy channel ID", style="bright_black"), "copy", "copy"),
        ]

        def done(act) -> None:
            if act == "join":
                self.action_leave_voice() if inside else self.joinVoice(ch)
            elif act == "chat":
                self.openChannel(ch)
            elif act == "read":
                self.clearUnread(ch)
                self.scheduleAck(ch, 0)
                self.relabel(ch)
            elif act == "copy":
                self.copyText(str(ch.id))

        self.push_screen(Picker(entries, placeholder=channelName(ch)), done)

    #threads and forums

    async def searchThreads(self, ch, archived: bool, limit: int = 25) -> list:
        r = await self.client.http.request(
            Route("GET", "/channels/{channel_id}/threads/search", channel_id=ch.id),
            params={
                "archived": "true" if archived else "false",
                "sort_by": "last_message_time",
                "sort_order": "desc",
                "limit": limit,
                "offset": 0,
            },
        )
        out = []
        for payload in r.get("threads", []):
            try:
                out.append(ch.guild._store_thread(payload))
            except Exception:
                log.debug("thread payload", exc_info=True)
        return out

    def forumPicker(self, forum) -> None:
        self.threadPicker(forum)

    @work(exclusive=True, group="threads")
    async def threadPicker(self, ch) -> None:
        forum = isForum(ch)
        try:
            active, archived = await asyncio.gather(
                self.searchThreads(ch, False), self.searchThreads(ch, True)
            )
        except discord.HTTPException as e:
            self.notify(
                f"Couldn't load {'posts' if forum else 'threads'}: {e.text or e}",
                severity="error",
                markup=False,
            )
            active, archived = [], []
        seen, threads = set(), []
        for t in self.activeThreads(ch) + active + archived:
            if t.id not in seen and canRead(t):
                seen.add(t.id)
                threads.append(t)
        threads.sort(key=lambda t: (bool(getattr(t, "archived", False)), -(t.last_message_id or t.id)))
        entries = [
            (
                Text("+ New post" if forum else "+ New thread", style="bold green"),
                "new create start",
                "__new__",
            )
        ]
        for t in threads:
            label = Text("↳ ") + Text(t.name, style="bold" if self.unreadCount(t) else "")
            n = getattr(t, "message_count", None)
            label.append(
                f"  {n} {'reply' if n == 1 else 'replies'}" if n is not None else "", style="bright_black"
            )
            when = discord.utils.snowflake_time(t.last_message_id or t.id)
            label.append(f" · {ago(when)}", style="bright_black")
            try:
                names = [tag.name for tag in t.applied_tags]
            except Exception:
                names = []
            if names:
                label.append("  " + " ".join(f"[{n}]" for n in names[:3]), style="cyan")
            if getattr(t, "archived", False):
                label.append("  archived", style="bright_black italic")
            if getattr(t, "locked", False):
                label.append(" 🔒", style="bright_black")
            entries.append((label, f"{t.name} {' '.join(names)}".lower(), t))
        if len(entries) == 1:
            entries.append(
                (Text(f"No {'posts' if forum else 'threads'} yet.", style="bright_black"), "", None)
            )

        def picked(v) -> None:
            if v == "__new__":
                self.newThread(ch)
            elif v is not None:
                self.openChannel(v)

        self.push_screen(
            Picker(
                entries,
                placeholder="Search…",
                title=f"{'Posts in' if forum else 'Threads in'} {channelIcon(ch).strip()} {ch.name}",
            ),
            picked,
        )

    def newThread(self, ch, message: Optional[discord.Message] = None) -> None:
        forum = isForum(ch)

        def gotTitle(title) -> None:
            if not title:
                return
            if forum:
                self.push_screen(
                    Prompt(f"New post: {title}", "first message"),
                    lambda body: self._createThread(ch, title, body),
                )
            else:
                self._createThread(ch, title, None, message)

        default = snippet(message.clean_content, 40) if message is not None and message.clean_content else ""
        self.push_screen(Prompt("Post title" if forum else "Thread name", "name", default), gotTitle)

    @work(group="threads")
    async def _createThread(
        self, ch, title: str, body: Optional[str], message: Optional[discord.Message] = None
    ) -> None:
        try:
            if isForum(ch):
                if not body:
                    self.notify("A forum post needs a first message.", severity="warning")
                    return
                res = await ch.create_thread(name=title[:100], content=self.convertOutgoing(body))
                thread = getattr(res, "thread", res)
            elif message is not None:
                thread = await message.create_thread(name=title[:100])
            else:
                thread = await ch.create_thread(name=title[:100], type=discord.ChannelType.public_thread)
        except discord.HTTPException as e:
            self.notify(f"Couldn't create it: {e.text or e}", severity="error", markup=False)
            return
        self.openChannel(thread)

    @work(group="dm")
    async def openDm(self, user) -> None:
        if user is None or user.id == self.meId:
            return
        try:
            ch = user.dm_channel or await user.create_dm()
        except discord.HTTPException as e:
            self.notify(f"Can't DM {user.display_name}: {e.text or e}", severity="error", markup=False)
            return
        self.openChannel(ch)

    def findUsers(self, name: str):
        name = name.lower().lstrip("@")
        exact, partial = [], []
        everyone = self.mentionPool() + [u for _l, _h, u in self.people()]
        for u in everyone:
            names = {u.name.lower(), u.display_name.lower(), (u.global_name or "").lower()}
            if name in names:
                exact.append(u)
            elif any(name in n for n in names if n):
                partial.append(u)
        return exact or partial

    def action_friends(self) -> None:
        if not self.client.is_ready():
            return
        entries = []
        for r in self.client.relationships:
            if r.type in (
                discord.RelationshipType.incoming_request,
                discord.RelationshipType.outgoing_request,
            ):
                incoming = r.type == discord.RelationshipType.incoming_request
                label = Text(
                    ("✉ " if incoming else "→ ") + r.user.display_name,
                    style="bold yellow" if incoming else "",
                ) + Text(
                    f"  @{r.user.name}  "
                    + ("wants to be friends · enter to accept" if incoming else "request sent"),
                    style="bright_black",
                )
                entries.append((label, f"{r.user.display_name} @{r.user.name}".lower(), r))
        entries += self.people(friendsOnly=True)

        def picked(v) -> None:
            if isinstance(v, discord.Relationship):
                if v.type == discord.RelationshipType.incoming_request:
                    spawn(self._accept(v))
            elif v is not None:
                self.openDm(v)

        online = sum(1 for r in self.client.friends if userStatus(r.user) != "offline")
        self.push_screen(
            Picker(
                entries,
                placeholder="Filter friends…",
                title=f"Friends: {online} online, {len(self.client.friends)} total",
            ),
            picked,
        )

    async def _accept(self, rel) -> None:
        try:
            await rel.accept()
            self.notify(f"Now friends with {rel.user.display_name}.", markup=False)
        except discord.HTTPException as e:
            self.notify(f"Accept failed: {e.text or e}", severity="error", markup=False)

    def action_search(self) -> None:
        ch = self.current
        if ch is None:
            self.notify("Open a chat first; search covers that server or DM.")
            return
        scope = ch.guild if not isinstance(ch, PRIVATE) else ch
        scopeName = scope.name if not isinstance(ch, PRIVATE) else channelName(ch)

        async def searcher(q: str):
            kw: dict = {"limit": 50}
            words, authors, channels, has = [], [], [], []
            for w in q.split():
                key, _, val = w.partition(":")
                if key == "from" and val:
                    found = self.findUsers(val)
                    if not found:
                        raise ValueError(f"no user matching {val!r}")
                    authors.append(found[0])
                elif key == "in" and val and not isinstance(ch, PRIVATE):
                    c = discord.utils.find(
                        lambda c: c.name.lower() == val.lower().lstrip("#"), scope.channels
                    )
                    if c is None:
                        raise ValueError(f"no channel {val!r}")
                    channels.append(c)
                elif key == "has" and val:
                    has.append(val)
                else:
                    words.append(w)
            if words:
                kw["content"] = " ".join(words)
            if authors:
                kw["authors"] = authors
            if channels:
                kw["channels"] = channels
            if has:
                kw["has"] = has
            return [m async for m in scope.search(**kw)]

        self.push_screen(MessageSearch(scopeName, searcher), self.jumpTo)

    def jumpTo(self, m) -> None:
        if m is not None:
            self.openChannel(m.channel, around=m)

    #status

    def ownStatus(self) -> str:
        try:
            st = str(self.client.status)
        except Exception:
            st = "online"
        return st

    def ownCustomStatus(self) -> Optional[str]:
        try:
            for a in self.client.activities:
                if isinstance(a, discord.CustomActivity):
                    return " ".join(x for x in (str(a.emoji) if a.emoji else "", a.name or "") if x) or None
        except Exception:
            pass
        return None

    def updateMePanel(self) -> None:
        if self.client.user is None:
            return
        panel = self.query_one("#me", SidePanel)
        width = max(20, panel.size.width - 2 or 28)
        t = Text()
        vc = self.vc
        if vc is not None and vc.channel is not None:
            ok = vc.is_connected()
            head = Text(
                "● Voice Connected" if ok else "◌ Connecting…", style="bold green" if ok else "bold yellow"
            )
            btn = Text(
                " ✆ Leave ", style=Style(bgcolor="red", color="white", bold=True, meta={"sp": "leave"})
            )
            t.append_text(head)
            t.append(" " * max(1, width - head.cell_len - btn.cell_len))
            t.append_text(btn)
            where = channelName(vc.channel) + (
                f" / {vc.channel.guild.name}" if getattr(vc.channel, "guild", None) else ""
            )
            t.append("\n")
            t.append(snippet(where, width), style=Style(color="bright_black", meta={"sp": "call"}))
            t.append("\n")
            t.append("─" * width, style="bright_black")
            t.append("\n")
            panel.add_class("incall")
        else:
            panel.remove_class("incall")
        st = self.ownStatus()
        name = Text(style=Style(meta={"sp": "status"}))
        name.append_text(statusDot(st))
        name.append(snippet(self.client.user.display_name, width - 12), style="bold")
        muted, deaf = self.selfMuted, self.selfDeafened
        btns = Text()
        btns.append(
            " 🎙 " if not muted else " 🔇 ", style=Style(meta={"sp": "mute"}, bgcolor="red" if muted else None)
        )
        btns.append(
            " 🎧 " if not deaf else " 🙉 ",
            style=Style(meta={"sp": "deafen"}, bgcolor="red" if deaf else None),
        )
        btns.append(" ⚙ ", style=Style(meta={"sp": "settings"}))
        t.append_text(name)
        t.append(" " * max(1, width - name.cell_len - btns.cell_len))
        t.append_text(btns)
        custom = self.ownCustomStatus()
        t.append("\n")
        t.append(
            snippet(custom or STATUS_NAME.get(st, st), width),
            style=Style(color="bright_black", italic=bool(custom), meta={"sp": "status"}),
        )
        if t != self._lastPanel:
            self._lastPanel = t
            panel.update(t)

    def gotoCall(self) -> None:
        if self.vc is not None and self.vc.channel is not None:
            self.openChannel(self.vc.channel)

    def action_status(self) -> None:
        if not self.client.is_ready():
            return
        entries = [
            (statusDot(s) + Text(STATUS_NAME[s]), STATUS_NAME[s].lower(), s)
            for s in ("online", "idle", "dnd", "invisible")
        ]
        entries.append((Text("✎ Set custom status…"), "custom status set", "custom"))
        if self.ownCustomStatus():
            entries.append((Text("✕ Clear custom status"), "clear custom status", "clear"))
        self.push_screen(Picker(entries, placeholder="Set your status", title="Status"), self._statusPicked)

    def _statusPicked(self, v) -> None:
        if v is None:
            return
        if v == "custom":
            self.push_screen(
                Prompt("Custom status", "e.g. 🎮 playing cs2", self.ownCustomStatus() or ""),
                lambda text: spawn(self.setStatus(custom=text)) if text is not None else None,
            )
        elif v == "clear":
            spawn(self.setStatus(custom=""))
        else:
            spawn(self.setStatus(status=v))

    async def setStatus(self, status: Optional[str] = None, custom: Optional[str] = None) -> None:
        kw: dict = {}
        if status is not None:
            kw["status"] = discord.Status(status)
        if custom is not None:
            #keep whatever game rich presence is showing next to the custom status
            acts = [discord.CustomActivity(name=custom)] if custom else []
            if self.rpcActivity is not None:
                acts.append(self.rpcActivity)
            kw["activities"] = acts
        try:
            await self.client.change_presence(**kw)
        except Exception as e:
            log.exception("presence")
            self.notify(f"Couldn't change status: {e}", severity="error", markup=False)
            return
        await asyncio.sleep(0.3)
        self.updateMePanel()
