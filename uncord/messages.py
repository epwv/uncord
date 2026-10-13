#message list widgets one MessageView per message plus reactions
from __future__ import annotations

import logging

from typing import List, Optional

import discord
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, Static

from . import emojis as emo
from . import media, sixelview
from .fmt import renderMessage


log = logging.getLogger("uncord")
GROUP_SECONDS = 7 * 60  #messages closer than this stack under one name


#message widgets


def emojiKey(e) -> str:
    return emo.emojiToStr(e).replace("\ufe0f", "")


class ReactionChip(Horizontal):

    DEFAULT_CSS = "ReactionChip { height: 1; width: auto; }"

    def __init__(self, reaction) -> None:
        super().__init__(classes="chip")
        self.emoji = reaction.emoji
        self.custom = not isinstance(reaction.emoji, str) and getattr(reaction.emoji, "id", None)
        self._count = Static("", classes="chipcount")
        self._label = None
        self.updateFrom(reaction)

    def compose(self) -> ComposeResult:
        if not self.custom:
            self._label = Static(Text(str(self.emoji)), classes="chiplabel")
            yield self._label
        elif not sixelview.GRAPHICS:
            self._label = Static(Text(f":{self.emoji.name}:", style="yellow"), classes="chiplabel")
            yield self._label
        yield self._count

    def on_mount(self) -> None:
        if self.custom and sixelview.GRAPHICS:
            self.loadImage()

    @work(exclusive=True)
    async def loadImage(self) -> None:
        e = self.emoji
        url = f"https://cdn.discordapp.com/emojis/{e.id}.{'gif' if getattr(e, 'animated', False) else 'png'}?size=48&quality=lossless"
        item = media.MediaItem(
            f":{e.name}:", url, url, "gif" if getattr(e, "animated", False) else "emoji", emoji=True
        )
        frames = await self.app.frameCache.frames(item, animate=item.kind == "gif")
        if not frames or not self.is_attached:
            if self.is_attached:
                await self.mount(
                    Static(Text(f":{e.name}:", style="yellow"), classes="chiplabel"), before=self._count
                )
            return
        w = (sixelview.KittyMedia if sixelview.KITTY else sixelview.SixelMedia)(frames, 2, 1, classes="chipimg")
        await self.mount(w, before=self._count)

    def updateFrom(self, reaction) -> None:
        self.count = reaction.count
        self.mine = reaction.me
        self._count.update(Text(str(reaction.count)))
        self.set_class(reaction.me, "mine")

    def on_click(self, event) -> None:
        event.stop()
        view = self.parent.parent if self.parent is not None else None
        if isinstance(view, MessageView):
            self.app.toggleReaction(view.message, self.emoji)


class AddReactionChip(Static):
    def __init__(self) -> None:
        super().__init__(Text(" + "), classes="chip addchip")

    def on_click(self, event) -> None:
        event.stop()
        view = self.parent.parent if self.parent is not None else None
        if isinstance(view, MessageView):
            self.app.reactPicker(view.message)


class ReactionBar(Horizontal):
    DEFAULT_CSS = "ReactionBar { height: auto; width: 1fr; }"

    def sync(self, reactions) -> None:
        if not reactions:
            self.remove_children()
            self.display = False
            return
        self.display = True
        existing = {emojiKey(c.emoji): c for c in self.query(ReactionChip)}
        wanted = [emojiKey(r.emoji) for r in reactions]
        for key, chip in existing.items():
            if key not in wanted:
                chip.remove()
        for r in reactions:
            key = emojiKey(r.emoji)
            chip = existing.get(key)
            if chip is not None:
                chip.updateFrom(r)
            else:
                adds = list(self.query(AddReactionChip))
                self.mount(ReactionChip(r), before=adds[0] if adds else None)
        if not self.query(AddReactionChip):
            self.mount(AddReactionChip())


#one message text plus inline media and reactions
class MessageView(Vertical):

    DEFAULT_CSS = "MessageView { height: auto; }"

    def __init__(self, message: discord.Message, grouped: bool = False) -> None:
        super().__init__(classes="msg" if grouped else "msg groupstart")
        self.message = message
        self.grouped = grouped
        self.revealed = False
        self.deleted = False
        self.edits: list = []
        self.translation = None
        self.previewed: frozenset = frozenset()
        self._mediaKey = None
        self.mediaDone = False

    def compose(self) -> ComposeResult:
        yield Static("", classes="msgtext")
        yield ReactionBar(classes="reactions")

    def on_mount(self) -> None:
        self.rerender()
        self.loadMedia()
        ref = self.message.reference
        if (ref is not None and ref.resolved is None and ref.message_id and self.app.plugin("valid_reply")
                and self.message.type == discord.MessageType.reply):
            self.fetchReply()

    #validreply the replied message isnt loaded so get it rather than calling it deleted
    @work(group="reply")
    async def fetchReply(self) -> None:
        ref = self.message.reference
        try:
            ch = self.app.client.get_channel(ref.channel_id) or self.message.channel
            ref.resolved = await ch.fetch_message(ref.message_id)
        except discord.NotFound:
            return
        except Exception:
            log.debug("fetch reply", exc_info=True)
            return
        if self.is_attached:
            self.rerender()

    def rerender(self) -> None:
        app = self.app
        self.query_one(".msgtext", Static).update(
            renderMessage(
                self.message,
                app.meId,
                app.client.get_channel,
                grouped=self.grouped,
                reveal=self.revealed,
                deleted=self.deleted,
                edits=tuple(self.edits),
                translation=self.translation,
                previewed=self.previewed,
                reactions=False,
            )
        )
        self.query_one(ReactionBar).sync(self.message.reactions)

    def refreshFrom(self, message: discord.Message) -> None:
        self.message = message
        self.rerender()
        self.loadMedia()

    def revealToggle(self) -> None:
        self.revealed = not self.revealed
        self._mediaKey = None
        self.rerender()
        self.loadMedia()

    @work(exclusive=True, group="media")
    async def loadMedia(self) -> None:
        try:
            await self._loadMedia()
        finally:
            self.mediaDone = True

    #load up to 4 images gifs or videos plus custom emoji and mount them under the text
    async def _loadMedia(self) -> None:
        app = self.app
        on = app.cfg.get("inline_media", True)
        items = media.itemsOf(self.message)[:4] if on else []
        items = [it for it in items if self.revealed or not it.spoiler]
        emojis = media.emojiItems(self.message.content) if on and app.cfg.get("emoji_images", True) else []
        jumbo = bool(emojis) and media.onlyEmojis(self.message.content)
        items += emojis
        key = (tuple(it.url for it in items), jumbo)
        if key == self._mediaKey:
            return
        self._mediaKey = key
        for w in list(self.query(".media")):
            await w.remove()
        if not items:
            if self.previewed:
                self.previewed = frozenset()
                self.rerender()
            return
        shown = set()
        widgets = []
        emojiWidgets = []
        for it in items:
            animate = it.kind in ("gif", "sticker")
            frames = await app.frameCache.frames(it, animate=animate)
            if not frames:
                continue
            if it.emoji:
                #jumbo if its only emojis
                w = sixelview.mediaWidget(frames, 6 if jumbo else 4, 3 if jumbo else 2, classes="emoji")
                w.mediaItem = it
                emojiWidgets.append(w)
                continue
            if it.small:
                w = sixelview.mediaWidget(frames, 16, 5)
            elif it.kind == "sticker":
                w = sixelview.mediaWidget(frames, 16, 8)
            else:
                w = sixelview.mediaWidget(frames, 44, 13)
            w.mediaItem = it
            widgets.append(w)
            if it.kind == "video":
                widgets.append(
                    Static(Text("▶ video · v to play", style="bright_black"), classes="media mediacap")
                )
            shown.update(u for u in (it.url, it.page) if u)
            for a in self.message.attachments:
                if a.url == it.url or a.proxy_url and it.thumb.startswith(a.proxy_url):
                    shown.add(a.url)
            for st in self.message.stickers:
                if st.url == it.url:
                    shown.add(st.url)
        if emojiWidgets:
            row = Horizontal(*emojiWidgets, classes="media emojirow")
            widgets.insert(0, row)
            if jumbo:
                shown.add(self.message.content.strip())
        if not self.is_attached:
            return
        ml = self.parent
        stick = isinstance(ml, MessageList) and ml.stickBottom
        if widgets:
            await self.mount_all(widgets, before=self.query_one(ReactionBar))
        self.previewed = frozenset(shown)
        self.rerender()
        if stick:
            ml.call_after_refresh(ml.scroll_end, animate=False)

    def on_click(self, event) -> None:
        ml = self.parent
        try:
            meta = self.screen.get_style_at(event.screen_x, event.screen_y).meta or {}
        except Exception:
            meta = {}
        if "play" in meta and getattr(event, "button", 1) == 1:
            event.stop()
            self.app.playAttachment(self.message, meta["play"])
            return
        #open inline media big
        node = getattr(event, "widget", None)
        while node is not None and node is not self:
            item = getattr(node, "mediaItem", None)
            if item is not None and getattr(event, "button", 1) == 1:
                event.stop()
                self.app.openMediaItem(self.message, item)
                return
            node = node.parent
        if "reaction" in meta and getattr(event, "button", 1) == 1:
            event.stop()
            i = meta["reaction"]
            if i == -1:
                self.app.reactPicker(self.message)
            elif 0 <= i < len(self.message.reactions):
                self.app.toggleReaction(self.message, self.message.reactions[i].emoji)
            return
        if isinstance(ml, MessageList):
            ml.select(ml.views().index(self), scroll=False)
            if getattr(event, "button", 1) == 3:
                ml.action_menu()

    def on_mouse_down(self, event) -> None:
        ml = self.parent
        if isinstance(ml, MessageList):
            ml._mousePick = ml.views().index(self)


def canGroup(prev: Optional[discord.Message], m: discord.Message) -> bool:
    return (
        prev is not None
        and prev.author.id == m.author.id
        and m.reference is None
        and m.type in (discord.MessageType.default,)
        and prev.type in (discord.MessageType.default, discord.MessageType.reply)
        and (m.created_at - prev.created_at).total_seconds() < GROUP_SECONDS
        and m.created_at.astimezone().date() == prev.created_at.astimezone().date()
    )


#the scrolling chat press up in an empty message box to select messages and act on them
class MessageList(VerticalScroll):

    can_focus = True
    BINDINGS = [
        Binding("up,k", "move(-1)", "Up", show=False),
        Binding("down,j", "move(1)", "Down", show=False),
        Binding("pageup", "move(-10)", show=False),
        Binding("pagedown", "move(10)", show=False),
        Binding("g,home", "edge(0)", show=False),
        Binding("G,end", "edge(1)", show=False),
        Binding("enter,space", "menu", "Actions"),
        Binding("r", "reply", "Reply"),
        Binding("e", "edit", "Edit"),
        Binding("d,delete", "delete", "Delete"),
        Binding("D", "delete(1)", "Delete now", show=False),
        Binding("plus,equals_sign", "react", "React"),
        Binding("minus", "unreact", "Unreact", show=False),
        Binding("1,2,3,4,5,6,7,8,9", "quick_react", show=False),
        Binding("s", "spoilers", "Spoilers", show=False),
        Binding("v", "view", "View image"),
        Binding("o", "open", "Open link", show=False),
        Binding("y", "copy", "Copy", show=False),
        Binding("c", "copy_link", "Copy link", show=False),
        Binding("m", "dm_author", "DM author", show=False),
        Binding("p", "profile", "Profile", show=False),
        Binding("t", "thread", "Thread", show=False),
        Binding("P", "pin", "Pin", show=False),
        Binding("escape,i,slash", "back", "Back"),
    ]

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.selected: int = -1
        self._deleteArmed: Optional[int] = None
        self.stickBottom = True

    def watch_scroll_y(self, old: float, new: float) -> None:
        super().watch_scroll_y(old, new)
        self.stickBottom = new >= self.max_scroll_y - 2

    def views(self) -> List[MessageView]:
        return list(self.query(MessageView))

    def current(self) -> Optional[MessageView]:
        v = self.views()
        return v[self.selected] if 0 <= self.selected < len(v) else None

    def select(self, idx: int, scroll: bool = True) -> None:
        v = self.views()
        for w in v:
            w.remove_class("selected")
        if not v:
            self.selected = -1
            return
        self.selected = max(0, min(idx, len(v) - 1))
        v[self.selected].add_class("selected")
        if scroll:
            self.scroll_to_widget(v[self.selected], animate=False)
        self._deleteArmed = None

    def on_focus(self) -> None:
        #select the clicked message not the newest
        self.call_after_refresh(self._focusSelect)
        self.app.showHint(True)

    def _focusSelect(self) -> None:
        pick = getattr(self, "_mousePick", None)
        self._mousePick = None
        if pick is not None:
            if pick != self.selected:
                self.select(pick, scroll=False)
        elif not (0 <= self.selected < len(self.views())):
            self.select(len(self.views()) - 1)

    def on_blur(self) -> None:
        for w in self.views():
            w.remove_class("selected")
        self.selected = -1
        self.app.showHint(False)

    def action_menu(self) -> None:
        if m := self._msg():
            self.app.messageMenu(m)

    def action_move(self, delta: int) -> None:
        if delta < 0 and self.selected <= 0:
            self.app.loadOlder()
            return
        if delta > 0 and self.selected >= len(self.views()) - 1:
            self.action_back()
            return
        self.select(self.selected + delta)

    def action_edge(self, bottom: int) -> None:
        self.select(len(self.views()) - 1 if bottom else 0)

    def action_back(self) -> None:
        self.app.query_one("#input", Input).focus()

    def _msg(self) -> Optional[discord.Message]:
        w = self.current()
        return w.message if w else None

    def action_reply(self) -> None:
        if m := self._msg():
            self.app.startReply(m)

    def action_edit(self) -> None:
        if m := self._msg():
            self.app.startEdit(m)

    def action_delete(self, now: int = 0) -> None:
        if m := self._msg():
            self.app.confirmDelete(m, ask=not now)

    def action_react(self) -> None:
        if m := self._msg():
            self.app.reactPicker(m)

    def action_unreact(self) -> None:
        if m := self._msg():
            self.app.unreact(m)

    def action_quick_react(self) -> None:
        pass

    def on_key(self, event) -> None:
        if event.character and event.character in "123456789" and (m := self._msg()):
            event.stop()
            self.app.quickReact(m, int(event.character) - 1)

    def action_spoilers(self) -> None:
        if w := self.current():
            w.revealToggle()

    def action_view(self) -> None:
        if m := self._msg():
            self.app.viewMedia(m)

    def action_open(self) -> None:
        if m := self._msg():
            self.app.openLinks(m)

    def action_copy(self) -> None:
        if m := self._msg():
            self.app.copyText(m.content or "\n".join(a.url for a in m.attachments))

    def action_copy_link(self) -> None:
        if m := self._msg():
            self.app.copyText(m.jump_url)

    def action_dm_author(self) -> None:
        if m := self._msg():
            self.app.openDm(m.author)

    def action_profile(self) -> None:
        if m := self._msg():
            self.app.showProfile(m.author)

    def action_thread(self) -> None:
        if m := self._msg():
            self.app.openThreadOf(m)

    def action_pin(self) -> None:
        if m := self._msg():
            self.app.togglePin(m)
