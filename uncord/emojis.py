#emoji names shortcodes the emoji picker and nitro checks
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple, Union

import discord
from rich._emoji_codes import EMOJI as _RICH_EMOJI
from rich.style import Style
from rich.text import Text

#discord aliases rich lacks
_ALIASES = {
    "rofl": "rolling_on_the_floor_laughing",
    "+1": "thumbsup",
    "-1": "thumbsdown",
    "heart": "red_heart",
    "slight_smile": "slightly_smiling_face",
    "upside_down": "upside-down_face",
    "thinking": "thinking_face",
    "pensive": "pensive_face",
    "sweat_smile": "grinning_face_with_sweat",
    "skull_crossbones": "skull_and_crossbones",
    "hugging": "hugging_face",
    "nerd": "nerd_face",
    "salute": "saluting_face",
    "clown": "clown_face",
    "moyai": "moai",
    "cry": "crying_face",
    "tada": "party_popper",
    "eyes": "eyes",
    "ok_hand": "ok_hand",
    "wave": "waving_hand",
    "clap": "clapping_hands",
    "pray": "folded_hands",
    "sob": "loudly_crying_face",
}

UNICODE: dict[str, str] = dict(_RICH_EMOJI)
for alias, target in _ALIASES.items():
    if alias not in UNICODE and target in UNICODE:
        UNICODE[alias] = UNICODE[target]

DEFAULT_QUICK = ["👍", "❤️", "😂", "💀", "🔥", "😭", "👀", "🙏", "💯", "😮"]

_NAME_FOR: dict[str, str] = {}
for _n, _c in sorted(UNICODE.items(), key=lambda kv: len(kv[0])):
    _NAME_FOR.setdefault(_c, _n)


def canonical(ch: str) -> str:
    if len(ch) == 1 and ord(ch) < 0x1F000:
        return ch + "️"
    return ch


def unicodeName(ch: str) -> str:
    return _NAME_FOR.get(ch.replace("️", ""), _NAME_FOR.get(ch, ""))


AnyEmoji = Union[str, discord.Emoji, discord.PartialEmoji]


def hasNitro(client: discord.Client) -> bool:
    try:
        return bool(client.user.premium_type) and client.user.premium_type != discord.PremiumType.none
    except Exception:
        return False


def usableHere(client: discord.Client, e: discord.Emoji, guild: Optional[discord.Guild]) -> bool:
    try:
        if not e.available or not e.is_usable():
            return False
    except Exception:
        pass
    if guild is not None and e.guild_id == guild.id:
        return True
    return hasNitro(client)


def emojiToStr(e: AnyEmoji) -> str:
    if isinstance(e, str):
        return e
    return str(e)


def customEmojis(client: discord.Client, guild: Optional[discord.Guild]) -> List[discord.Emoji]:
    here = list(guild.emojis) if guild is not None else []
    rest = [e for e in client.emojis if guild is None or e.guild_id != guild.id]
    return here + rest


def findCustom(client: discord.Client, name: str, guild: Optional[discord.Guild]) -> Optional[discord.Emoji]:
    name = name.lower()
    for e in customEmojis(client, guild):
        if e.name.lower() == name and usableHere(client, e, guild):
            return e
    return None


def parseEmoji(client: discord.Client, text: str, guild: Optional[discord.Guild]) -> Optional[AnyEmoji]:
    text = text.strip()
    if not text:
        return None
    m = re.fullmatch(r"<(a?):(\w+):(\d+)>", text)
    if m:
        return discord.PartialEmoji(name=m.group(2), id=int(m.group(3)), animated=bool(m.group(1)))
    name = text.strip(":")
    if re.fullmatch(r"[\w+\-]+", name):
        e = findCustom(client, name, guild)
        if e is not None:
            return e
        ch = UNICODE.get(name.lower())
        return canonical(ch) if ch else None
    return text


_SHORTCODE = re.compile(r"(?<![<\w]):([\w+\-]{2,32}):(?!\d)")


def convertShortcodes(client: discord.Client, text: str, guild: Optional[discord.Guild], links: bool = False) -> str:
    parts = re.split(r"(```.*?```|`[^`]*`)", text, flags=re.S)
    for i in range(0, len(parts), 2):

        def sub(m: re.Match) -> str:
            name = m.group(1)
            e = findCustom(client, name, guild)
            if e is not None:
                return str(e)
            if links:
                #fakenitro an emoji we cant use here goes as its image link
                e = next((x for x in customEmojis(client, guild) if x.name.lower() == name.lower()), None)
                if e is not None:
                    return f"https://cdn.discordapp.com/emojis/{e.id}.{'gif' if e.animated else 'webp'}?size=48&name={e.name}"

            ch = UNICODE.get(name.lower())
            return ch if ch else m.group(0)

        parts[i] = _SHORTCODE.sub(sub, parts[i])
    return "".join(parts)


def complete(
    client: discord.Client, prefix: str, guild: Optional[discord.Guild], limit: int = 8
) -> List[Tuple[Text, str]]:
    prefix = prefix.lower()
    out: List[Tuple[Text, str]] = []
    seen = set()
    for e in customEmojis(client, guild):
        if prefix in e.name.lower() and e.name.lower() not in seen and usableHere(client, e, guild):
            seen.add(e.name.lower())
            out.append((Text(f":{e.name}:", style="yellow"), f":{e.name}:"))
            if len(out) >= limit:
                return out
    starts = [n for n in UNICODE if n.startswith(prefix)]
    contains = [n for n in UNICODE if prefix in n and not n.startswith(prefix)]
    for n in sorted(starts, key=len) + sorted(contains, key=len):
        ch = UNICODE[n]
        if ch in seen:
            continue
        seen.add(ch)
        out.append((Text(f"{ch} :{n}:"), ch))
        if len(out) >= limit:
            break
    return out


def pickerEntries(
    client: discord.Client, guild: Optional[discord.Guild], recents: Iterable[str], on_message: Iterable = ()
) -> list:
    entries = []
    seen = set()

    def add(label: Text, hay: str, value, key: str) -> None:
        if key in seen:
            return
        seen.add(key)
        entries.append((label, hay.lower(), value))

    for r in on_message:
        e = r.emoji
        key = emojiToStr(e)
        name = e.name if not isinstance(e, str) else unicodeName(e)
        lab = Text(
            f":{name}:" if not isinstance(e, str) else e, style="yellow" if not isinstance(e, str) else ""
        )
        lab.append(f"  {r.count}  on this message" + (" · you reacted" if r.me else ""), style="bright_black")
        add(lab, f"{name} {key}", e, key)
    for key in recents:
        e = parseEmoji(client, key, guild) if key.startswith("<") else key
        if e is None:
            continue
        if isinstance(e, discord.PartialEmoji):
            e = client.get_emoji(e.id) or e
        name = getattr(e, "name", None) or unicodeName(e)
        lab = Text(e if isinstance(e, str) else f":{name}:", style="" if isinstance(e, str) else "yellow")
        lab.append(f"  {name}  recent", style="bright_black")
        add(lab, f"{name} {key}", e, emojiToStr(e))
    for ch in DEFAULT_QUICK:
        add(Text(ch) + Text(f"  {unicodeName(ch)}", style="bright_black"), unicodeName(ch) + " " + ch, ch, ch)
    for e in customEmojis(client, guild):
        ok = usableHere(client, e, guild)
        lab = Text(f":{e.name}:", style="yellow" if ok else "bright_black")
        lab.append(
            f"  {e.guild.name if e.guild else ''}" + ("" if ok else "  (needs Nitro)"), style="bright_black"
        )
        add(lab, f"{e.name} {e.guild.name if e.guild else ''}", e, str(e))
    for name, ch in UNICODE.items():
        if ch in seen:
            continue
        add(Text(ch) + Text(f"  {name}", style="bright_black"), name, ch, ch)
    return entries


def stickerEntries(client: discord.Client, guild: Optional[discord.Guild]) -> list:
    nitro = hasNitro(client)
    here = list(guild.stickers) if guild is not None else []
    rest = [s for s in client.stickers if guild is None or s.guild_id != guild.id]
    out = []
    for s in here + rest:
        ok = (guild is not None and s.guild_id == guild.id) or nitro
        g = client.get_guild(s.guild_id)
        lab = Text("🏷 " + s.name, style="magenta" if ok else "bright_black")
        lab.append(f"  {g.name if g else ''}" + ("" if ok else "  (needs Nitro)"), style="bright_black")
        if s.description:
            lab.append(f"  {s.description[:40]}", style="bright_black italic")
        out.append((lab, f"{s.name} {g.name if g else ''} {s.description or ''}".lower(), s))
    return out


RECENT_STYLE = Style(color="yellow")
