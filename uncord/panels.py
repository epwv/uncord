#small widgets around the chat input box sidebar title and call bars
from __future__ import annotations


import discord
from rich.text import Text
from textual import work
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, Static, Tree

from .fmt import isForum, isVoice

from .messages import MessageList


class ChatInput(Input):
    BINDINGS = [
        Binding("up", "up", show=False),
        Binding("shift+up,ctrl+up", "edit_last", show=False),
        Binding("down", "down", show=False),
        Binding("tab", "complete", show=False),
        Binding("ctrl+a", "select_all", show=False),
        Binding("ctrl+v", "paste_clip", show=False, priority=True),
        Binding("escape", "escape", show=False),
    ]

    def action_paste_clip(self) -> None:
        self.app.pasteClipboard(self)

    #pasted file paths get attached an empty paste means an image is on the clipboard
    def _on_paste(self, event) -> None:
        #pasted files get attached empty paste means clipboard image
        files = self.app.pathsIn(event.text)
        if files or not event.text.strip():
            event.prevent_default()
            event.stop()
            if files:
                self.app.attachFiles(files)
            else:
                self.app.pasteClipboard(self)

    def action_up(self) -> None:
        if self.app.suggestions:
            self.app.moveSuggestion(-1)
        elif not self.value:
            self.app.query_one("#messages", MessageList).focus()

    def action_edit_last(self) -> None:
        if self.value:
            return
        for w in reversed(self.app.query_one("#messages", MessageList).views()):
            if w.message.author.id == self.app.meId:
                self.app.startEdit(w.message)
                return
        self.app.notify("No message of yours here to edit.")

    def action_down(self) -> None:
        if self.app.suggestions:
            self.app.moveSuggestion(1)

    def action_complete(self) -> None:
        if self.app.suggestions:
            self.app.acceptSuggestion()
        else:
            self.app.query_one("#sidebar").focus()

    def action_escape(self) -> None:
        if self.app.recorder is not None:
            self.app.finishRecording(send=False)
        elif self.app.suggestions:
            self.app.clearSuggestions()
        elif self.app.replyTo or self.app.editing:
            self.app.cancelCompose()
        elif self.app.attachments:
            self.app.clearAttachments()
        else:
            self.app.query_one("#sidebar").focus()


#the members list on the right click a name for the user menu
class MemberList(Static):

    def on_click(self, event) -> None:
        try:
            meta = self.screen.get_style_at(event.screen_x, event.screen_y).meta or {}
        except Exception:
            meta = {}
        if "muser" in meta:
            event.stop()
            self.app.userMenu(meta["muser"])


#discord style column of server icons down the far left
class RailIcon(Vertical):
    DEFAULT_CSS = """
    RailIcon { height: 2; width: 5; margin-bottom: 1; border-left: blank; }
    RailIcon.here { border-left: thick $accent; }
    RailIcon.unread { border-left: thick $foreground; }
    RailIcon.mentioned { border-left: thick $error; }
    RailIcon .railtxt { width: 4; height: 2; content-align: center middle; background: $boost; text-style: bold; }
    """

    def __init__(self, guild=None, **kw) -> None:
        super().__init__(**kw)
        self.guild = guild

    def compose(self):
        g = self.guild
        if g is None:
            yield Static(Text("✉"), classes="railtxt")
        else:
            initials = "".join(w[0] for w in g.name.split()[:2]).upper() or "?"
            yield Static(Text(initials[:2]), classes="railtxt")

    def on_mount(self) -> None:
        if self.guild is not None and self.guild.icon is not None:
            self.loadIcon()

    @work(exclusive=True)
    async def loadIcon(self) -> None:
        from . import media, sixelview

        if not sixelview.GRAPHICS:
            return
        url = self.guild.icon.with_size(64).with_static_format("png").url
        frames = await self.app.frameCache.frames(media.MediaItem(self.guild.name, url, url, "image"), animate=False)
        if not frames or not self.is_attached:
            return
        await self.remove_children()
        w = sixelview.mediaWidget(frames, 4, 2, classes="railimg")
        w.styles.width, w.styles.height = 4, 2
        await self.mount(w)

    def on_click(self, event) -> None:
        event.stop()
        if self.guild is None:
            self.app.gotoDms()
        elif getattr(event, "button", 1) == 3:
            self.app.guildMenu(self.guild)
        else:
            self.app.selectGuild(self.guild)


class ServerRail(VerticalScroll):
    DEFAULT_CSS = "ServerRail { width: 6; padding: 1 0 0 0; scrollbar-size-vertical: 0; }"

    def rebuild(self, guilds) -> None:
        self.remove_children()
        self.mount_all([RailIcon(None, id="rail-dms")] + [RailIcon(g, id=f"rail-{g.id}") for g in guilds])

    def mark(self, guildId, unread: bool, here: bool, mentions: int) -> None:
        try:
            icon = self.query_one(f"#rail-{guildId}", RailIcon)
        except Exception:
            return
        icon.set_class(here, "here")
        icon.set_class(unread and not here and not mentions, "unread")
        icon.set_class(bool(mentions) and not here, "mentioned")
        icon.tooltip = f"{icon.guild.name}" + (f" · {mentions} mention{'s' if mentions != 1 else ''}" if mentions else "")


class SidePanel(Static):

    def on_click(self, event) -> None:
        try:
            meta = self.screen.get_style_at(event.screen_x, event.screen_y).meta or {}
        except Exception:
            meta = {}
        act = meta.get("sp")
        if not act:
            return
        event.stop()
        app = self.app
        {
            "leave": app.action_leave_voice,
            "mute": app.action_toggle_mute,
            "deafen": app.action_toggle_deafen,
            "settings": app.action_voice_settings,
            "status": app.action_status,
            "call": app.gotoCall,
        }[act]()


class TitleBar(Static):
    #menu icon then the chat title on the left, version and update notice on the right

    def __init__(self, content="", **kw) -> None:
        self.left = self._withMenu(content)
        self.right = Text()
        super().__init__(self.left, **kw)

    @staticmethod
    def _withMenu(content) -> Text:
        t = Text("☰ ", style="bright_black")
        t.append_text(content if isinstance(content, Text) else Text(str(content)))
        return t

    def update(self, content="", **kw) -> None:
        self.left = self._withMenu(content)
        super().update(self.left, **kw)

    def setRight(self, text: Text) -> None:
        self.right = text
        self.refresh()

    def render(self) -> Text:
        width = self.content_size.width
        left = self.left.copy()
        left.truncate(max(0, width - self.right.cell_len - 2), overflow="ellipsis")
        gap = width - left.cell_len - self.right.cell_len
        if gap < 1 or not self.right:
            return left
        return left + Text(" " * gap) + self.right

    def on_click(self, event) -> None:
        if event.x <= 2:  #padding plus the menu icon
            event.stop()
            self.app.action_toggle_sidebar()


class VoiceBar(Static):

    def on_click(self, event) -> None:
        try:
            meta = self.screen.get_style_at(event.screen_x, event.screen_y).meta or {}
        except Exception:
            meta = {}
        if "vbtn" in meta:
            event.stop()
            {
                "leave": self.app.action_leave_voice,
                "mute": self.app.action_toggle_mute,
                "deafen": self.app.action_toggle_deafen,
                "settings": self.app.action_voice_settings,
                "chat": self.app.gotoCall,
                "share": self.app.action_share,
                "watch": self.app.action_watch,
                "camera": self.app.action_camera,
            }[meta["vbtn"]]()
        elif "vwatch" in meta:
            event.stop()
            self.app.action_watch(meta["vwatch"])
        elif "vcam" in meta:
            event.stop()
            self.app.action_watch(meta["vcam"], camera=True)
        elif "vuser" in meta:
            event.stop()
            self.app.userMenu(meta["vuser"])


class ComposerButton(Static):

    def __init__(self, label: str, action: str, tip: str, plus: bool = False) -> None:
        super().__init__(Text(label), classes="cbtn plus" if plus else "cbtn")
        self.action_name = action
        self.tooltip = tip

    def on_click(self, event) -> None:
        event.stop()
        getattr(self.app, f"action_{self.action_name}")()


class Sidebar(Tree):
    BINDINGS = [Binding("i,escape", "to_input", show=False), Binding("slash", "app.switcher", show=False)]

    def action_to_input(self) -> None:
        self.app.query_one("#input", Input).focus()

    def on_click(self, event) -> None:
        if getattr(event, "button", 1) != 3:
            return
        node = self.get_node_at_line(int(event.y + self.scroll_offset.y))
        if node is None or not node.data:
            return
        kind, obj = node.data
        event.stop()
        if kind == "member":
            self.app.userMenu(obj[2])
        elif kind == "chan" and isVoice(obj):
            self.app.voiceChannelMenu(obj)
        elif kind == "guild":
            self.app.guildMenu(obj)
        elif kind == "chan" and isinstance(obj, discord.TextChannel):
            self.app.channelMenu(obj)
        elif kind == "chan" and isForum(obj):
            self.app.threadPicker(obj)
        elif kind == "chan" and isinstance(obj, (discord.DMChannel, discord.GroupChannel)):
            self.app.dmMenu(obj)
