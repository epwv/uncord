#things you do to a message media reactions pins voice messages
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time

import discord
from rich.text import Text
from textual.widgets import Static

from . import emojis as emo
from . import gifs, media, voicemsg
from .fmt import PRIVATE, snippet
from .screens import Confirm, GifPicker, ImageScreen, Picker, ProfileScreen

from .panels import ChatInput

log = logging.getLogger("uncord")

RATE_BYTES_MIN = 48000 * 2 // 2  #0.5s mono s16


class MessageActionsMixin:

    #profiles media links

    def showProfile(self, u) -> None:
        guild = getattr(self.current, "guild", None) if not isinstance(self.current, PRIVATE) else None
        if guild is not None and not isinstance(u, discord.Member):
            u = guild.get_member(u.id) or u

        def done(v) -> None:
            if v == "dm":
                self.openDm(u)

        self.push_screen(ProfileScreen(u, self.client, guild), done)

    async def fetchCdn(self, url: str) -> bytes:
        return await self.client.http.get_from_cdn(url)

    def openExternal(self, url: str) -> None:
        if shutil.which("xdg-open"):
            asyncio.create_task(
                asyncio.create_subprocess_exec(
                    "xdg-open", url, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
                )
            )
        else:
            self.copyText(url)

    def audioAttachments(self, m: discord.Message) -> list:
        return [
            i
            for i, a in enumerate(m.attachments)
            if getattr(a, "is_voice_message", lambda: False)()
            or (a.content_type or "").startswith("audio/")
            or a.filename.lower().endswith((".ogg", ".mp3", ".wav", ".m4a", ".flac", ".opus"))
        ]

    def viewMedia(self, m: discord.Message, index: int = 0) -> None:
        items = media.itemsOf(m) + media.emojiItems(m.content)
        if not items:
            audio = self.audioAttachments(m)
            if audio:
                self.playAttachment(m, audio[0])
            else:
                self.notify("No image, GIF, video, sticker or voice message in that message.")
            return
        self.openMediaItem(m, items[min(index, len(items) - 1)])

    def openMediaItem(self, m: discord.Message, item) -> None:
        if item.kind == "video":
            direct = item.url.lower().split("?")[0].endswith(media.VIDEO_EXT)
            if not ((direct or shutil.which("yt-dlp")) and media.playExternal(item.url)):
                self.openExternal(item.url)
            return
        stills = [it for it in media.itemsOf(m) + media.emojiItems(m.content) if it.kind != "video"]
        idx = next((i for i, it in enumerate(stills) if it.url == item.url), 0)
        if item.emoji:
            stills = [
                (
                    media.MediaItem(
                        it.title, it.url.replace("size=96", "size=512"), it.thumb, it.kind, emoji=True
                    )
                    if it.emoji
                    else it
                )
                for it in stills
            ]
        self.push_screen(ImageScreen(stills, self.fetchCdn, idx))

    def playAttachment(self, m: discord.Message, index: int) -> None:
        if not (0 <= index < len(m.attachments)):
            return
        a = m.attachments[index]

        async def go():
            try:
                started = await self.audioPlayer.toggle(a.url)
            except Exception as e:
                self.notify(f"Can't play audio: {e}", severity="error", markup=False)
                return
            if started:
                dur = getattr(a, "duration", None)
                self.notify(
                    f"▶ Playing {'voice message' if getattr(a, 'is_voice_message', lambda: False)() else a.filename}"
                    + (f" ({int(dur // 60)}:{int(dur % 60):02d})" if dur else "")
                    + ". Again to stop.",
                    markup=False,
                    timeout=4,
                )
            else:
                self.notify("⏹ Stopped.", timeout=2)

        asyncio.create_task(go())

    #voice messages

    #voice message recording ctrl+r starts and sends esc cancels
    def action_record(self) -> None:
        if self.recorder is not None:
            self.finishRecording(send=True)
            return
        if self.current is None:
            self.notify("Open a chat first.")
            return
        if not media.FFMPEG:
            self.notify("Voice messages need ffmpeg.", severity="error")
            return
        try:
            self.recorder = voicemsg.Recorder()
        except Exception as e:
            self.notify(f"Can't record: {e}", severity="error", markup=False)
            return
        self.sfx("unmute")
        self._recTimer = self.set_interval(0.25, self._recTick)
        self._recTick()

    def _recTick(self) -> None:
        r = self.recorder
        if r is None:
            return
        secs = r.seconds
        lvl = min(8, r.level // 400)
        c = self.query_one("#compose", Static)
        c.update(
            Text.assemble(
                ("● REC ", "bold red"),
                f"{int(secs // 60)}:{int(secs % 60):02d}  ",
                ("▁▂▃▄▅▆▇█"[:lvl].ljust(8, "·"), "green"),
                ("   Ctrl+R send · Esc cancel", "bright_black"),
            )
        )
        c.add_class("on")
        if secs >= 600:
            self.finishRecording(send=True)

    def finishRecording(self, send: bool) -> None:
        r, self.recorder = self.recorder, None
        if r is None:
            return
        if self._recTimer is not None:
            self._recTimer.stop()
            self._recTimer = None
        pcm = r.stop()
        self.query_one("#compose", Static).remove_class("on")
        if self.replyTo:
            self._showCompose(f"↪ replying to {self.replyTo.author.display_name}   esc cancel")
        if not send:
            self.sfx("mute")
            self.notify("Voice message discarded.", timeout=2)
            return
        if len(pcm) < RATE_BYTES_MIN:
            self.notify("Too short, not sent.", timeout=2)
            return
        ch, replyTo = self.current, self.replyTo
        self.cancelCompose()
        self.notify("Sending voice message…", timeout=2)

        async def go():
            try:
                await voicemsg.sendVoiceMessage(ch, pcm, replyTo)
            except Exception as e:
                log.exception("voice message")
                self.notify(f"Voice message failed: {e}", severity="error", markup=False)

        asyncio.create_task(go())

    def linksOf(self, m: discord.Message) -> list:
        urls = re.findall(r"https?://[^\s<>|)]+", m.content)
        urls += [a.url for a in m.attachments] + [s.url for s in m.stickers]
        urls += [e.url for e in m.embeds if e.url]
        seen, out = set(), []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def openLinks(self, m: discord.Message) -> None:
        urls = self.linksOf(m)
        if not urls:
            self.notify("No links in that message.")
        elif len(urls) == 1:
            self.openExternal(urls[0])
        else:
            self.push_screen(
                Picker([(Text(u), u.lower(), u) for u in urls], placeholder="Open which link?"),
                lambda u: self.openExternal(u) if u else None,
            )

    HINT = (
        "↵ actions · r reply · e edit · d delete · + react · - unreact · 1-9 quick react · v view/play · "
        "o open · y copy · p profile · esc back"
    )

    def showHint(self, on_: bool) -> None:
        #uses the typing row so nothing shifts
        self._hintOn = on_
        self.tickTyping()

    def messageMenu(self, m: discord.Message) -> None:
        mine = m.author.id == self.meId
        acts = [("reply", "↪ Reply", "r"), ("react", "☺ Add reaction", "+")]
        if mine:
            acts.append(("edit", "✎ Edit", "e"))
        if mine or self.canManage(m):
            acts.append(("delete", "🗑 Delete", "d"))
        if self.audioAttachments(m):
            acts.insert(0, ("play", "▶ Play voice message / audio", "v"))
        if media.itemsOf(m) or re.search(r"<a?:\w+:\d+>", m.content):
            acts.append(("view", "🖼 View media", "v"))
        if self.linksOf(m):
            acts.append(("open", "↗ Open link", "o"))
        acts += [("copy", "⧉ Copy text", "y"), ("link", "🔗 Copy message link", "c")]
        if not isinstance(m.channel, PRIVATE):
            get_thread = getattr(getattr(m.channel, "guild", None), "get_thread", None)
            has = getattr(m, "thread", None) is not None or (
                get_thread is not None and get_thread(m.id) is not None
            )
            if has or isinstance(m.channel, discord.TextChannel):
                acts.append(("thread", "🧵 Open thread" if has else "🧵 Create thread", "t"))
            if self.canManage(m) or isinstance(m.channel, PRIVATE):
                acts.append(("pin", "📌 Unpin" if m.pinned else "📌 Pin", "P"))
        if not mine:
            acts += [("dm", f"✉ Message {m.author.display_name}", "m")]
        acts.append(("profile", "👤 Profile", "p"))
        entries = [
            (Text(label) + Text(f"   {key}", style="bright_black"), label.lower(), act)
            for act, label, key in acts
        ]

        def done(act) -> None:
            {
                "play": lambda: self.playAttachment(m, self.audioAttachments(m)[0]),
                "reply": lambda: self.startReply(m),
                "react": lambda: self.reactPicker(m),
                "edit": lambda: self.startEdit(m),
                "delete": lambda: self.confirmDelete(m),
                "view": lambda: self.viewMedia(m),
                "open": lambda: self.openLinks(m),
                "copy": lambda: self.copyText(m.content),
                "link": lambda: self.copyText(m.jump_url),
                "thread": lambda: self.openThreadOf(m),
                "pin": lambda: self.togglePin(m),
                "dm": lambda: self.openDm(m.author),
                "profile": lambda: self.showProfile(m.author),
            }.get(act, lambda: None)()

        self.push_screen(
            Picker(
                entries,
                placeholder="Action…",
                title=f"{m.author.display_name}: {snippet(m.clean_content, 50)}",
            ),
            done,
        )

    def confirmDelete(self, m: discord.Message, ask: bool = True) -> None:
        if m.author.id != self.meId and not self.canManage(m):
            self.notify("You can only delete your own messages here.", severity="warning")
            return
        if not ask:
            self.deleteMessage(m)
            return
        self.push_screen(
            Confirm("Delete this message?", snippet(m.clean_content or "[attachment]", 120)),
            lambda yes: self.deleteMessage(m) if yes else None,
        )

    def openThreadOf(self, m: discord.Message) -> None:
        thread = getattr(m, "thread", None)
        if thread is None and hasattr(m.channel, "get_thread"):
            thread = m.channel.get_thread(m.id)
        get_thread = getattr(getattr(m.channel, "guild", None), "get_thread", None)
        if thread is None and get_thread is not None:
            thread = get_thread(m.id)
        if thread is not None:
            self.openChannel(thread)
        elif isinstance(m.channel, discord.TextChannel):
            self.newThread(m.channel, message=m)
        else:
            self.notify("Threads can't be started here.")

    def canManage(self, m: discord.Message) -> bool:
        if isinstance(m.channel, PRIVATE):
            return False
        try:
            return m.channel.permissions_for(m.guild.me).manage_messages
        except Exception:
            return False

    def togglePin(self, m: discord.Message) -> None:
        async def go():
            try:
                if m.pinned:
                    await m.unpin()
                    self.notify("Unpinned.")
                else:
                    await m.pin()
                    self.notify("Pinned.")
            except discord.HTTPException as e:
                self.notify(f"Pin failed: {e.text or e}", severity="error", markup=False)

        asyncio.create_task(go())

    #reactions emoji stickers

    def currentGuild(self):
        ch = self.current
        return getattr(ch, "guild", None) if ch is not None and not isinstance(ch, PRIVATE) else None

    def reactPicker(self, m: discord.Message) -> None:
        entries = emo.pickerEntries(
            self.client, self.currentGuild(), self.cfg.get("recent_emojis", []), m.reactions
        )
        self.push_screen(
            Picker(
                entries,
                placeholder="React with… (picking one you already used removes it)",
                title=f"React to {m.author.display_name}",
            ),
            lambda e: self.toggleReaction(m, e) if e is not None else None,
        )

    def quickReact(self, m: discord.Message, idx: int) -> None:
        keys = list(self.cfg.get("recent_emojis", []))
        for q in emo.DEFAULT_QUICK:
            if q not in keys:
                keys.append(q)
        if idx >= len(keys):
            return
        e = (
            emo.parseEmoji(self.client, keys[idx], self.currentGuild())
            if keys[idx].startswith("<")
            else keys[idx]
        )
        if e is not None:
            self.toggleReaction(m, e)

    @staticmethod
    def _sameEmoji(a, b) -> bool:
        return emo.emojiToStr(a).replace("\ufe0f", "") == emo.emojiToStr(b).replace("\ufe0f", "")

    def _localReact(self, m: discord.Message, e, add: bool) -> None:
        r = next((r for r in m.reactions if self._sameEmoji(r.emoji, e)), None)
        if add:
            if r is None:
                try:
                    m.reactions.append(discord.Reaction(message=m, data={"count": 1, "me": True}, emoji=e))
                except Exception:
                    return
            elif not r.me:
                r.count += 1
                r.me = True
        elif r is not None and r.me:
            r.count -= 1
            r.me = False
            if r.count <= 0:
                m.reactions.remove(r)
        w = self.viewFor(m.id)
        if w is not None:
            w.rerender()

    #update the reaction locally right away then tell discord and refetch to correct the count
    def toggleReaction(self, m: discord.Message, e) -> None:
        w = self.viewFor(m.id)
        if w is not None:
            m = w.message
        if isinstance(e, str):
            e = emo.canonical(e) if len(e) == 1 else e
        key = emo.emojiToStr(e)
        mine = any(r.me and self._sameEmoji(r.emoji, e) for r in m.reactions)
        if not mine and isinstance(e, discord.PartialEmoji) and e.id:
            full = self.client.get_emoji(e.id)
            if full is None and not emo.hasNitro(self.client):
                self.notify(
                    f":{e.name}: is from a server you're not in; reacting with it needs Nitro.",
                    severity="warning",
                    markup=False,
                )
                return
            e = full or e
        if (
            not mine
            and isinstance(e, discord.Emoji)
            and not emo.usableHere(self.client, e, self.currentGuild())
        ):
            self.notify(
                f":{e.name}: is from another server; reacting with it needs Nitro.",
                severity="warning",
                markup=False,
            )
            return
        self._localReact(m, e, add=not mine)

        async def go():
            try:
                if mine:
                    await m.remove_reaction(e, self.client.user)
                else:
                    await m.add_reaction(e)
                    self.cfg.pushRecentEmoji(key)
            except discord.HTTPException as ex:
                log.warning("reaction %s failed: %s", "remove" if mine else "add", ex)
                self.notify(
                    f"Couldn't {'remove' if mine else 'add'} reaction: {ex.text or ex}",
                    severity="error",
                    markup=False,
                )
            except Exception:
                log.exception("reaction")
                self.notify("Reaction failed (see log).", severity="error")
            self.onReactionChange(m.channel.id, m.id, delay=0.6)

        asyncio.create_task(go())

    def unreact(self, m: discord.Message) -> None:
        w = self.viewFor(m.id)
        m = w.message if w is not None else m
        mine = [r for r in m.reactions if r.me]
        if not mine:
            self.notify("You haven't reacted to that message.")
        elif len(mine) == 1:
            self.toggleReaction(m, mine[0].emoji)
        else:
            entries = [
                (
                    Text(f":{r.emoji.name}:" if not isinstance(r.emoji, str) else str(r.emoji))
                    + Text(f"  {r.count}", style="bright_black"),
                    str(r.emoji).lower(),
                    r.emoji,
                )
                for r in mine
            ]
            self.push_screen(
                Picker(entries, placeholder="Remove which reaction?", title="Your reactions"),
                lambda e: self.toggleReaction(m, e) if e is not None else None,
            )

    def onReactionChange(self, channel_id: int, message_id: int, delay: float = 0.4) -> None:
        if self.current is None or channel_id != self.current.id or self.viewFor(message_id) is None:
            return
        if message_id in self._reactionPending:
            return
        self._reactionPending.add(message_id)
        ch = self.current

        async def go():
            await asyncio.sleep(delay)
            self._reactionPending.discard(message_id)
            try:
                fresh = await ch.fetch_message(message_id)
            except discord.HTTPException:
                return
            w = self.viewFor(message_id)
            if w is not None:
                w.refreshFrom(fresh)

        asyncio.create_task(go())

    def action_emoji_insert(self) -> None:
        if not self.client.is_ready():
            return
        entries = emo.pickerEntries(self.client, self.currentGuild(), self.cfg.get("recent_emojis", []))

        def done(e) -> None:
            if e is None:
                return
            inp = self.query_one("#input", ChatInput)
            if isinstance(e, discord.Emoji):
                if not emo.usableHere(self.client, e, self.currentGuild()):
                    if not self.plugin("fake_nitro"):
                        self.notify(f":{e.name}: needs Nitro here.", severity="warning", markup=False)
                        return
                    #fakenitro sends it as an image link when the message goes out
                    inp.insert_text_at_cursor(f":{e.name}: ")
                    inp.focus()
                    return
                txt = str(e)
            else:
                txt = emo.emojiToStr(e)
            self.cfg.pushRecentEmoji(txt)
            inp.insert_text_at_cursor(txt if isinstance(e, str) else f":{e.name}:")
            inp.focus()

        self.push_screen(Picker(entries, placeholder="Insert emoji…", title="Emoji"), done)

    def action_gif(self) -> None:
        if self.current is None:
            self.notify("Open a chat first.")
            return
        ch = self.current

        async def loader():
            cached = self._favGifs
            if cached and time.monotonic() - cached[0] < 300:
                return cached[1]
            favs = await gifs.favourites(self.client)
            self._favGifs = (time.monotonic(), favs)
            return favs

        async def framesFor(g):
            src = g.get("src") or g["url"]
            item = media.MediaItem(g.get("title") or "gif", src, src, "gif")
            return await self.frameCache.frames(item, animate=True)

        def done(g) -> None:
            if not g:
                return
            replyTo = self.replyTo
            self.cancelCompose()

            async def go():
                try:
                    if replyTo is not None:
                        await ch.send(g["url"], reference=replyTo, mention_author=not self.plugin("no_reply_mention"))
                    else:
                        await ch.send(g["url"])
                except discord.HTTPException as e:
                    self.notify(f"GIF failed: {e.text or e}", severity="error", markup=False)

            asyncio.create_task(go())

        self.push_screen(GifPicker(loader, framesFor), done)

    def action_sticker(self) -> None:
        if self.current is None:
            self.notify("Open a chat first.")
            return
        entries = emo.stickerEntries(self.client, self.currentGuild())
        if not entries:
            self.notify("No server stickers available.")
            return
        ch = self.current

        def done(s) -> None:
            if s is None:
                return

            async def go():
                try:
                    (
                        await ch.send(stickers=[s], reference=self.replyTo)
                        if self.replyTo
                        else await ch.send(stickers=[s])
                    )
                    self.cancelCompose()
                except discord.HTTPException as e:
                    self.notify(f"Sticker failed: {e.text or e}", severity="error", markup=False)

            asyncio.create_task(go())

        self.push_screen(Picker(entries, placeholder="Send a sticker…", title="Stickers"), done)
