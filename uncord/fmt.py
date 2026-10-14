#turns discord messages channels and users into rich text for the terminal
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlsplit

import discord
from rich.color import Color, ColorSystem
from rich.console import Group
from rich.measure import Measurement
from rich.segment import Segment
from rich.style import Style
from rich.table import Table
from rich.text import Text

NAME_COLOURS = [
    "red",
    "green",
    "yellow",
    "blue",
    "magenta",
    "cyan",
    "bright_red",
    "bright_green",
    "bright_yellow",
    "bright_blue",
    "bright_magenta",
    "bright_cyan",
]
PRIVATE = (discord.DMChannel, discord.GroupChannel)
TEXTLIKE = (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread)

#discords own colours
BLURPLE = "#5865f2"
LINK = Style(color="#00a8fc", underline=True)
EMOJI_STYLE = Style(color="yellow")
MENTION = Style(color="#c9cdfb", bgcolor="#3c4270")
MENTION_ME = Style(color="white", bgcolor=BLURPLE, bold=True)
TAG = Style(color="white", bgcolor=BLURPLE, bold=True)
GUILD_TAG = Style(color="white", bgcolor="#4e5058", bold=True)
CODE = Style(color="cyan")
SPOILER = Style(color="bright_black", bgcolor="bright_black")


#channels and users


def isVoice(ch) -> bool:
    return isinstance(ch, (discord.VoiceChannel, discord.StageChannel))


def isForum(ch) -> bool:
    return isinstance(ch, discord.ForumChannel)


def channelName(ch) -> str:
    if isinstance(ch, discord.DMChannel):
        return ch.recipient.display_name if ch.recipient else "Unknown"
    if isinstance(ch, discord.GroupChannel):
        return str(ch)
    return getattr(ch, "name", None) or str(ch)


def channelIcon(ch) -> str:
    if isinstance(ch, discord.DMChannel):
        return "@ "
    if isinstance(ch, discord.GroupChannel):
        return "👥 "
    if isVoice(ch):
        return "🔊 "
    if isinstance(ch, discord.Thread):
        return "↳ "
    if isForum(ch):
        return "💬 "
    if getattr(ch, "is_news", lambda: False)():
        return "📢 "
    return "# "


def channelTitle(ch) -> Text:
    t = Text(channelIcon(ch) + channelName(ch), style="bold")
    if isinstance(ch, discord.Thread) and ch.parent is not None:
        t.append(f"  in #{ch.parent.name}", style="dim")
    if not isinstance(ch, PRIVATE) and getattr(ch, "guild", None) is not None:
        t.append(f"  ·  {ch.guild.name}", style="dim")
    topic = getattr(ch, "topic", None)
    if topic:
        t.append(f"  ·  {snippet(topic, 80)}", style="dim italic")
    return t


def canRead(ch) -> bool:
    me = getattr(ch.guild, "me", None)
    if me is None:
        return True
    try:
        return ch.permissions_for(me).read_messages
    except Exception:
        return True


def isMuted(obj) -> bool:
    try:
        if bool(obj.notification_settings.muted):
            return True
    except Exception:
        pass
    g = getattr(obj, "guild", None)
    if g is not None and not isinstance(obj, discord.Guild):
        try:
            return bool(g.notification_settings.muted)
        except Exception:
            return False
    return False


def termColour(colour) -> Optional[str]:
    if colour is None or not getattr(colour, "value", 0):
        return None
    return Color.parse(str(colour)).downgrade(ColorSystem.STANDARD).name


def nameColour(user) -> str:
    return (
        termColour(getattr(user, "colour", None))
        or NAME_COLOURS[int(hashlib.md5(str(user.id).encode()).hexdigest(), 16) % len(NAME_COLOURS)]
    )


STATUS_DOT = {
    "online": ("●", "#23a55a"),
    "idle": ("◐", "#f0b232"),
    "dnd": ("⊖", "#f23f43"),
    "streaming": ("●", "#593695"),
}
STATUS_NAME = {
    "online": "Online",
    "idle": "Idle",
    "dnd": "Do Not Disturb",
    "invisible": "Invisible",
    "offline": "Offline",
}


def userStatus(u) -> str:
    st = getattr(u, "status", None)
    if st is None:
        rel = getattr(u, "relationship", None)
        st = rel.status if rel is not None else None
    return str(st) if st is not None else "offline"


def statusDot(uOrStatus) -> Text:
    st = uOrStatus if isinstance(uOrStatus, str) else userStatus(uOrStatus)
    ch, colour = STATUS_DOT.get(st, ("○", "bright_black"))
    return Text(ch + " ", style=colour)


PLATFORMS = (("desktop_status", "🖥", "desktop"), ("mobile_status", "📱", "phone"), ("web_status", "🌐", "web"),
             ("embedded_status", "🎮", "console"))


#which devices someone is on, members carry it and friends carry it on their relationship
def isBlocked(u) -> bool:
    try:
        return bool(u.is_blocked())
    except Exception:
        return False


#guild presence first then the friend one, a member with no guild presence yet can still be a friend whos online
def _clientStatuses(u):
    for get in (lambda: u.presence, lambda: u.relationship.presence,
                lambda: u._state._presences.get(u.id)):
        try:
            p = get()
        except Exception:
            continue
        if p is not None:
            yield p.client_status


def platforms(u) -> list:
    for cs in _clientStatuses(u):
        #getattr with a default, copied client statuses can be missing embedded
        out = [(icon, name) for attr, icon, name in PLATFORMS
               if getattr(cs, attr.split("_")[0], None) not in (None, "offline", "invisible")]
        if out:
            return out
    return []


def platformIcons(u) -> Text:
    found = platforms(u) if SHOW_PLATFORMS else []
    return Text(" " + "".join(icon for icon, _n in found), style="bright_black") if found else Text()


def activityText(u) -> Optional[str]:
    acts = getattr(u, "activities", None)
    if acts is None:
        rel = getattr(u, "relationship", None)
        acts = getattr(getattr(rel, "presence", None), "activities", None) if rel else None
    for a in acts or ():
        if isinstance(a, discord.CustomActivity):
            txt = " ".join(x for x in (str(a.emoji) if a.emoji else "", a.name or "") if x)
            if txt:
                return txt
        elif isinstance(a, discord.Spotify):
            return f"🎵 {a.title} — {a.artist}"
        elif getattr(a, "name", None):
            verb = {
                discord.ActivityType.playing: "Playing",
                discord.ActivityType.streaming: "Streaming",
                discord.ActivityType.listening: "Listening to",
                discord.ActivityType.watching: "Watching",
                discord.ActivityType.competing: "Competing in",
            }.get(getattr(a, "type", None), "")
            return f"{verb} {a.name}".strip()
    return None


#helpers


def snippet(s: str, n: int = 60) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def humanSize(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def shortUrl(url: str) -> str:
    u = urlsplit(url)
    host = u.netloc.removeprefix("www.")
    path = u.path.rstrip("/")
    if ("discordapp" in host or "discord.com" in host) and "/attachments/" in path:
        return path.rsplit("/", 1)[-1] or host
    if u.query and host.endswith(("youtube.com", "google.com")):
        path += "?" + u.query
    label = host + path
    return label if len(label) <= 42 else label[:41] + "…"


def link(text: str, url: str, base: Optional[Style] = None) -> Text:
    return Text(text, style=(base or LINK) + Style(link=url))


def formatTs(ts: int, fmt: Optional[str]) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    if fmt == "R":
        delta = (datetime.now(timezone.utc).astimezone() - dt).total_seconds()
        future, s = delta < 0, abs(delta)
        for size, unit in (
            (86400 * 365, "year"),
            (86400 * 30, "month"),
            (86400, "day"),
            (3600, "hour"),
            (60, "minute"),
        ):
            if s >= size:
                n = int(s // size)
                txt = f"{n} {unit}{'s' if n != 1 else ''}"
                return f"in {txt}" if future else f"{txt} ago"
        return "just now"
    return dt.strftime(
        {"t": "%H:%M", "T": "%H:%M:%S", "d": "%d/%m/%Y", "D": "%d %B %Y", "F": "%A, %d %B %Y %H:%M"}.get(
            fmt or "f", "%d %B %Y %H:%M"
        )
    )


#markdown

INLINE = re.compile(
    r"(?P<code>`+)(?P<codebody>.+?)(?P=code)"
    r"|\|\|(?P<spoiler>.+?)\|\|"
    r"|\*\*\*(?P<bi>.+?)\*\*\*"
    r"|\*\*(?P<b>.+?)\*\*"
    r"|__(?P<u>.+?)__"
    r"|\*(?P<i>[^*\s](?:[^*]*?[^*\s])?)\*"
    r"|(?<![\w])_(?P<i2>[^_\s](?:[^_]*?[^_\s])?)_(?![\w])"
    r"|~~(?P<s>.+?)~~"
    r"|\[(?P<ltext>[^\]\n]+)\]\(<?(?P<lurl>https?://[^)\s>]+)>?\)"
    r"|<(?P<aurl>https?://[^>\s]+)>"
    r"|(?P<url>https?://[^\s<>]+[^\s<>.,:;\"')\]!?*_~|])"
    r"|<(?P<anim>a?):(?P<ename>\w+):(?P<eid>\d+)>"
    r"|<t:(?P<ts>-?\d+)(?::(?P<tsfmt>[tTdDfFR]))?>"
    r"|<@!?(?P<uid>\d+)>"
    r"|<@&(?P<rid>\d+)>"
    r"|<#(?P<cid>\d+)>"
    r"|</(?P<cmd>[\w\- ]+):\d+>"
    r"|(?P<everyone>@everyone|@here)",
    re.S,
)
CODEBLOCK = re.compile(r"```(?:([\w+\-]*)\n)?(.*?)```", re.S)


class MdContext:

    def __init__(
        self,
        meId: int = 0,
        users=None,
        roles=None,
        get_channel: Optional[Callable] = None,
        revealSpoilers: bool = False,
    ) -> None:
        self.meId = meId
        self.users = users or {}
        self.roles = roles or {}
        self.get_channel = get_channel or (lambda _id: None)
        self.reveal = revealSpoilers


#[text](link) can say one site and open another, show where it really goes
def maskedHint(label: str, url: str) -> Text:
    from .safety import domain

    real = domain(url)
    shown = re.search(r"(?:https?://)?((?:[\w-]+\.)+[a-z]{2,})", label, re.I)
    if shown and real:
        claimed = shown.group(1).lower()
        claimed = claimed[4:] if claimed.startswith("www.") else claimed
        if claimed != real and not real.endswith("." + claimed):
            return Text(f" ⚠ goes to {real}", style="bold #f23f43")
        return Text()
    return Text(f" ({real})", style="bright_black") if SHOW_LINK_DOMAINS and real else Text()


def mdInline(text: str, ctx: MdContext, base: Style = Style()) -> Text:
    out = Text()
    pos = 0
    for mt in INLINE.finditer(text):
        if mt.start() > pos:
            out.append(text[pos : mt.start()], style=base)
        pos = mt.end()
        g = mt.groupdict()
        if g["codebody"] is not None:
            out.append(g["codebody"].strip("`") if g["code"] == "`" else g["codebody"], style=base + CODE)
        elif g["spoiler"] is not None:
            inner = mdInline(g["spoiler"], ctx, base)
            if ctx.reveal:
                inner.stylize(Style(underline=True, color="bright_black"))
                out.append_text(inner)
            else:
                out.append("▒" * max(3, min(len(inner.plain), 40)), style=SPOILER)
        elif g["bi"] is not None:
            out.append_text(mdInline(g["bi"], ctx, base + Style(bold=True, italic=True)))
        elif g["b"] is not None:
            out.append_text(mdInline(g["b"], ctx, base + Style(bold=True)))
        elif g["u"] is not None:
            out.append_text(mdInline(g["u"], ctx, base + Style(underline=True)))
        elif g["i"] is not None or g["i2"] is not None:
            out.append_text(mdInline(g["i"] or g["i2"], ctx, base + Style(italic=True)))
        elif g["s"] is not None:
            out.append_text(mdInline(g["s"], ctx, base + Style(strike=True)))
        elif g["ltext"] is not None:
            out.append_text(link(g["ltext"], g["lurl"]))
            out.append_text(maskedHint(g["ltext"], g["lurl"]))
        elif g["aurl"] is not None or g["url"] is not None:
            url = g["aurl"] or g["url"]
            out.append_text(link(shortUrl(url), url))
        elif g["ename"] is not None:
            url = f"https://cdn.discordapp.com/emojis/{g['eid']}.{'gif' if g['anim'] else 'png'}"
            out.append_text(link(f":{g['ename']}:", url, EMOJI_STYLE))
        elif g["ts"] is not None:
            try:
                out.append(formatTs(int(g["ts"]), g["tsfmt"]), style=base + Style(underline=True))
            except (ValueError, OverflowError, OSError):
                out.append(mt.group(), style=base)
        elif g["uid"] is not None:
            uid = int(g["uid"])
            u = ctx.users.get(uid)
            name = u.display_name if u is not None else "unknown-user"
            out.append(f"@{name}", style=MENTION_ME if uid == ctx.meId else MENTION)
        elif g["rid"] is not None:
            r = ctx.roles.get(int(g["rid"]))
            colour = termColour(getattr(r, "colour", None)) if r is not None else None
            out.append(
                f"@{r.name if r is not None else 'deleted-role'}",
                style=Style(color=colour or "cyan", bold=True),
            )
        elif g["cid"] is not None:
            ch = ctx.get_channel(int(g["cid"]))
            out.append(f"#{channelName(ch) if ch is not None else 'unknown'}", style=MENTION)
        elif g["cmd"] is not None:
            out.append(f"/{g['cmd']}", style=MENTION)
        elif g["everyone"] is not None:
            out.append(g["everyone"], style=MENTION_ME)
    if pos < len(text):
        out.append(text[pos:], style=base)
    return out


def mdLines(text: str, ctx: MdContext) -> Text:
    out = Text()
    lines = text.split("\n")
    inBlockQuote = False
    for i, line in enumerate(lines):
        if i:
            out.append("\n")
        if line.startswith(">>> "):
            inBlockQuote = True
            line = line[4:]
        if inBlockQuote or line.startswith("> ") or line == ">":
            body = line if inBlockQuote else line[2:]
            out.append("▎ ", style="bright_black")
            out.append_text(mdInline(body, ctx, Style(color="bright_black") if not body else Style()))
            continue
        m = re.match(r"(#{1,3}) (.+)", line)
        if m:
            out.append_text(mdInline(m.group(2), ctx, Style(bold=True, underline=len(m.group(1)) == 1)))
            continue
        if line.startswith("-# "):
            out.append_text(mdInline(line[3:], ctx, Style(dim=True)))
            continue
        m = re.match(r"(\s*)[-*] (.+)", line)
        if m:
            out.append(m.group(1) + "• ")
            out.append_text(mdInline(m.group(2), ctx))
            continue
        m = re.match(r"(\s*)(\d{1,3})\. (.+)", line)
        if m:
            out.append(f"{m.group(1)}{m.group(2)}. ", style="bright_black")
            out.append_text(mdInline(m.group(3), ctx))
            continue
        out.append_text(mdInline(line, ctx))
    return out


#code blocks get syntax colours from pygments in the terminals own palette
def highlight(code: str, lang: str) -> Text:
    if lang:
        try:
            from rich.syntax import Syntax

            t = Syntax(code, lang, theme="ansi_dark", background_color="default").highlight(code)
            t.rstrip()
            return t
        except Exception:
            pass
    return Text(code, style=CODE)


def markdown(text: str, ctx: MdContext) -> Text:
    out = Text()
    pos = 0
    for mt in CODEBLOCK.finditer(text):
        before = text[pos : mt.start()]
        if before.strip("\n"):
            out.append_text(mdLines(before.strip("\n"), ctx))
            out.append("\n")
        code = mt.group(2).strip("\n")
        if UNINDENT:
            import textwrap

            code = textwrap.dedent(code)
        lang = (mt.group(1) or "").lower()
        if lang:
            out.append(f"┏ {lang}\n", style="bright_black")
        for j, cl in enumerate(highlight(code, lang).split("\n")):
            if j:
                out.append("\n")
            out.append("┃ ", style="bright_black")
            out.append_text(cl)
        out.append("\n")
        pos = mt.end()
    rest = text[pos:]
    if rest.strip("\n") or not out:
        out.append_text(mdLines(rest.strip("\n") if out else rest, ctx))
    out.rstrip()
    return out


#embeds

class Bar:
    #draws a coloured bar down the left of whatever is inside like discord embeds and quotes
    def __init__(self, inner, colour: Optional[str]) -> None:
        self.inner = inner
        self.style = Style(color=colour or "bright_black")

    def __rich_console__(self, console, options):
        width = max(1, options.max_width - 2)
        lines = console.render_lines(self.inner, options.update(width=width, height=None), pad=False)
        for line in lines:
            yield Segment("▎ ", self.style)
            yield from line
            yield Segment.line()

    def __rich_measure__(self, console, options):
        return Measurement(3, options.max_width)


#discord's account and safety notices come as an embed of raw fields meant for the app to lay out
NOTICE_KEYS = {"client_version_message", "header", "body", "icon_type", "theme", "ctas", "classification_id"}


def noticeBlock(e) -> Optional["Bar"]:
    fields = {str(f.name): str(f.value) for f in getattr(e, "fields", []) or []}
    if len(NOTICE_KEYS & fields.keys()) < 3:
        return None
    rows = []
    if e.title:
        rows.append(Text(e.title, style="bold"))
    if fields.get("header"):
        rows.append(Text(fields["header"], style="bold #f0b232"))
    if fields.get("body"):
        rows.append(Text(re.sub(r"\s{2,}", " ", fields["body"]).strip()))
    links = [(k, v) for k, v in fields.items() if k.endswith("_link") and v.startswith("http")]
    for k, url in links:
        label = k.removesuffix("_link").replace("_", " ").capitalize()
        rows.append(link(f"{label} ↗", url))
    try:
        when = datetime.fromtimestamp(float(fields["timestamp"]))
        rows.append(Text(when.strftime("%d %b %Y %H:%M"), style="bright_black"))
    except (KeyError, ValueError, OverflowError, OSError):
        pass
    return Bar(Group(*rows), BLURPLE)


def embedBlock(e, ctx: "MdContext", previewed=frozenset(), hideDesc: bool = False):
    notice = noticeBlock(e)
    if notice is not None:
        return notice
    rows = []
    provider = getattr(getattr(e, "provider", None), "name", None)
    if provider:
        rows.append(Text(provider, style="bright_black"))
    author = getattr(e, "author", None)
    if author is not None and getattr(author, "name", None):
        rows.append(link(author.name, author.url, Style(bold=True)) if getattr(author, "url", None)
                    else Text(author.name, style="bold"))
    if e.title:
        rows.append(link(e.title, e.url, Style(bold=True, color="#00a8fc")) if e.url
                    else Text(e.title, style="bold"))
    if e.description and not hideDesc:
        desc = e.description if len(e.description) <= 1500 else e.description[:1499] + "…"
        rows.append(markdown(desc, ctx))
    fields = list(getattr(e, "fields", []) or [])[:25]
    i = 0
    #inline fields sit up to three across like discord
    while i < len(fields):
        f = fields[i]
        row = [f]
        if getattr(f, "inline", False):
            while len(row) < 3 and i + len(row) < len(fields) and getattr(fields[i + len(row)], "inline", False):
                row.append(fields[i + len(row)])
        grid = Table.grid(padding=(0, 3), expand=False)
        for _ in row:
            grid.add_column(overflow="fold", max_width=40 if len(row) > 1 else None)
        grid.add_row(*[Text(str(x.name), style="bold") + Text("\n") + mdInline(str(x.value)[:1024], ctx)
                       for x in row])
        rows.append(grid)
        i += len(row)
    if e.image and e.image.url and e.image.url not in previewed and not (e.thumbnail and e.thumbnail.url in previewed):
        rows.append(link("🖼 image", e.image.url))
    foot = []
    if e.footer and e.footer.text:
        foot.append(e.footer.text)
    if e.timestamp:
        foot.append(e.timestamp.astimezone().strftime("%d %b %Y %H:%M"))
    if foot:
        rows.append(Text(" · ".join(foot), style="bright_black"))
    if not rows:
        return None
    return Bar(Group(*rows), termColour(e.colour))


#messages


def messageContext(m: discord.Message, meId: int, get_channel, reveal: bool = False) -> MdContext:
    users = {u.id: u for u in m.mentions}
    if isinstance(m.channel, discord.GroupChannel):
        users.update({u.id: u for u in m.channel.recipients})
    return MdContext(meId, users, {r.id: r for r in m.role_mentions}, get_channel, reveal)


def reactionText(reactions) -> Text:
    t = Text()
    for i, r in enumerate(reactions):
        e = r.emoji
        label = f":{e.name}:" if not isinstance(e, str) and getattr(e, "id", None) else str(e)
        style = Style(color="white", bgcolor=BLURPLE, bold=True) if r.me else Style(reverse=True, dim=True)
        t.append(f" {label} {r.count} ", style=style + Style(meta={"reaction": i}))
        t.append(" ")
    t.append(" + ", style=Style(color="bright_black", meta={"reaction": -1}))
    return t


def waveBars(wave, width: int = 24) -> str:
    bars = "▁▂▃▄▅▆▇█"
    if not wave:
        return "▁▂▃▂▁▂▃▄▃▂▁▂▃▂▁▁▂▃▂▁▂▃▂▁"[:width]
    n = len(wave)
    out = []
    for i in range(width):
        seg = wave[i * n // width : max(i * n // width + 1, (i + 1) * n // width)]
        out.append(bars[min(7, max(seg) * 8 // 256)] if seg else "▁")
    return "".join(out)


def mentionsMe(m: discord.Message, meId: int) -> bool:
    if any(u.id == meId for u in m.mentions) or m.mention_everyone:
        return True
    ref = m.reference.resolved if m.reference else None
    return isinstance(ref, discord.Message) and ref.author.id == meId and m.author.id != meId


#badges after a name like the discord app shows them, server tag then app or system
def nameBadges(user, webhook: bool = False) -> Text:
    t = Text()
    try:
        pg = user.primary_guild
        if pg.tag and pg.identity_enabled is not False:
            t.append(" ")
            t.append(f" {pg.tag} ", style=GUILD_TAG)
    except Exception:
        pass
    if webhook:
        label = "WEBHOOK"
    elif getattr(user, "system", False):
        label = "SYSTEM"
    elif getattr(user, "bot", False):
        try:
            verified = user.public_flags.verified_bot
        except Exception:
            verified = False
        label = "✓ APP" if verified else "APP"
    else:
        return t
    t.append(" ")
    t.append(f" {label} ", style=TAG)
    return t


#plugin switches the app sets from config
SHOW_USERNAMES = True
SHOW_PLATFORMS = True
REPLY_TIMESTAMP = True
UNINDENT = True
SHOW_LINK_DOMAINS = True


def renderMessage(
    m: discord.Message,
    meId: int,
    get_channel,
    *,
    grouped: bool = False,
    reveal: bool = False,
    previewed: frozenset = frozenset(),
    reactions: bool = True,
    deleted: bool = False,
    edits: tuple = (),
    translation=None,
):
    ctx = messageContext(m, meId, get_channel, reveal)
    grid = Table.grid(padding=(0, 1), expand=True)
    grid.add_column(width=5, no_wrap=True)
    grid.add_column(ratio=1, overflow="fold")

    pinged = mentionsMe(m, meId)
    ref = m.reference.resolved if m.reference else None
    if isinstance(ref, discord.Message):
        rt = Text("╭ ", style="bright_black")
        rt.append(ref.author.display_name, style=Style(color=nameColour(ref.author), dim=True))
        if REPLY_TIMESTAMP:
            rt.append(ref.created_at.astimezone().strftime(" %H:%M" if ref.created_at.date() == m.created_at.date()
                                                           else " %d %b %H:%M"), style="bright_black")
        rt.append(
            " "
            + snippet(
                ref.clean_content
                or ("[attachment]" if ref.attachments else "[sticker]" if ref.stickers else ""),
                70,
            ),
            style="bright_black",
        )
        grid.add_row("", rt)
    elif m.reference is not None and m.type == discord.MessageType.reply:
        grid.add_row("", Text("╭ original message was deleted", style="bright_black italic"))

    showName = not grouped or isinstance(ref, discord.Message)
    ts = Text(m.created_at.astimezone().strftime("%H:%M"), style="bold yellow" if pinged else "bright_black")
    if grouped and not pinged:
        ts = Text("")

    body = Text()
    if showName:
        body.append(m.author.display_name, style=Style(color=nameColour(m.author), bold=True))
        uname = getattr(m.author, "name", "")
        if SHOW_USERNAMES and uname and uname.lower() != m.author.display_name.lower() and not m.webhook_id:
            body.append(f" @{uname}", style="bright_black")
        #bot command replies also have a webhook id but they come with an application id
        body.append_text(nameBadges(m.author, webhook=m.webhook_id is not None and m.application_id is None))
        if not m.webhook_id:
            body.append_text(platformIcons(m.author))
        body.append("  ")
    content = m.content
    if previewed and content and content.strip() in previewed:
        content = ""
    if m.type not in (
        discord.MessageType.default,
        discord.MessageType.reply,
        discord.MessageType.chat_input_command,
        discord.MessageType.context_menu_command,
    ):
        try:
            body.append(m.system_content or m.type.name, style="italic bright_black")
        except Exception:
            body.append(m.type.name.replace("_", " "), style="italic bright_black")
    elif content:
        body.append_text(markdown(content, ctx))

    extras = []
    for idx, a in enumerate(m.attachments):
        if a.url in previewed and (reveal or not a.is_spoiler()):
            continue
        ct = (a.content_type or "").lower()
        isVoice = getattr(a, "is_voice_message", lambda: False)()
        if (
            isVoice
            or ct.startswith("audio/")
            or a.filename.lower().endswith((".ogg", ".mp3", ".wav", ".m4a", ".flac", ".opus"))
        ):
            dur = getattr(a, "duration", None)
            play = Style(meta={"play": idx})
            line = Text("▶ ", style=Style(color="green", bold=True) + play)
            line.append(
                "🎤 voice message " if isVoice else f"🎵 {a.filename} ", style=Style(bold=isVoice) + play
            )
            if dur:
                line.append(
                    f"{int(dur // 60)}:{int(dur % 60):02d} ", style=Style(color="bright_black") + play
                )
            line.append(waveBars(getattr(a, "waveform", None)), style=Style(color="green") + play)
            line.append("  click or v to play", style="bright_black")
            extras.append(line)
            continue
        kind = (
            "🖼"
            if (a.content_type or "").startswith("image/")
            else "🎞" if (a.content_type or "").startswith("video/") else "📎"
        )
        line = Text(kind + " ") + link(a.filename, a.url, Style(color="cyan", underline=True))
        line.append(f"  {humanSize(a.size)}", style="bright_black")
        if a.is_spoiler():
            line.append("  spoiler · s to reveal", style="red")
        extras.append(line)
    for st in m.stickers:
        if st.url in previewed:
            continue
        extras.append(
            Text("🏷 ")
            + link(st.name, st.url, Style(color="magenta", underline=True))
            + Text("  sticker", style="bright_black")
        )
    for e in m.embeds:
        video = getattr(e, "video", None)
        if e.type in ("gifv", "image") and (
            (video and video.url in previewed) or (e.thumbnail and e.thumbnail.url in previewed)
        ):
            continue
        if e.url and e.url in m.content and not e.title and not e.description:
            continue
        block = embedBlock(e, ctx, previewed, hideDesc=bool(
            e.url and e.url in m.content and e.type in ("video", "gifv", "image")))
        if block is not None:
            extras.append(block)
    if getattr(m, "poll", None) is not None:
        p = m.poll
        extras.append(Text(f"📊 {getattr(p.question, 'text', p.question)}", style="bold"))
        for ans in p.answers:
            extras.append(
                Text(
                    f"   ◦ {ans.text}  ({ans.vote_count})",
                    style="bright_black" if not ans.self_voted else "green",
                )
            )
    if m.edited_at:
        body.append(" (edited)", style="bright_black")
    if deleted:
        body.stylize(Style(color="red", dim=True), 0, len(body))
        body.append("  deleted", style="bold red")
    #message logger keeps what it said before each edit
    if translation:
        extras.append(Text(f"🌐 {translation[0]}: ", style="bright_black") + Text(translation[1], style="italic"))
    for k, old in enumerate(edits):
        extras.insert(k, Text("✎ " + snippet(old, 300), style=Style(color="bright_black", strike=True)))
    if m.reactions and reactions:
        extras.append(reactionText(m.reactions))
    thread = getattr(m, "thread", None)
    if thread is not None:
        extras.append(Text(f"🧵 thread: {thread.name}", style="bright_black"))

    #text extras join the body, embeds are their own blocks in between
    parts = [body]
    for x in extras:
        if isinstance(x, Text):
            last = parts[-1]
            if not isinstance(last, Text):
                parts.append(x)
                continue
            if last.plain.strip():
                last.append("\n")
            last.append_text(x)
        else:
            parts.append(x)
    for x in parts:
        if isinstance(x, Text):
            x.rstrip()
    grid.add_row(ts, parts[0] if len(parts) == 1 else Group(*parts))
    return grid


def ago(when) -> str:
    import datetime as _dt

    secs = max(0, (_dt.datetime.now(_dt.timezone.utc) - when).total_seconds())
    for unit, n in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= n:
            return f"{int(secs // n)}{unit} ago"
    return "just now"


#full width rule with the date in the middle like discord
def dateSeparator(d):
    from rich.rule import Rule

    return Rule(d.strftime("%A %d %B %Y"), style="bright_black", characters="─")


def newSeparator():
    from rich.rule import Rule

    return Rule(Text(" new ", style="bold white on red"), style="red", align="right", characters="─")
