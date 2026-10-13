#small optional tweaks in the spirit of vencord plugins, switched on and off with /plugins
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#key name what it does on by default
PLUGINS = (
    ("message_logger", "MessageLogger", "keep deleted messages and show what edited ones said before", True),
    ("show_usernames", "ShowMeYourName", "show @username next to nicknames", True),
    ("platform_icons", "PlatformIndicators", "show 🖥 📱 🌐 for the devices people are on", True),
    ("typing_indicator", "TypingIndicator", "✎ next to channels and DMs where someone is typing", True),
    ("call_timer", "CallTimer", "show how long you've been in a call", True),
    ("clear_urls", "ClearURLs", "strip tracking junk (utm, fbclid, si…) from links you send", True),
    ("fake_nitro", "FakeNitro", "send emoji you can't use here as image links instead of failing", True),
    ("no_reply_mention", "NoReplyMention", "replies don't ping the person you reply to", False),
    ("silent_typing", "SilentTyping", "don't show others that you're typing", False),
    ("relationship_notifier", "RelationshipNotifier", "tell you when someone unfriends you or sends a request", True),
    ("rich_presence", "RichPresence", "games and apps on this pc set your activity like the desktop app (restart after)", True),
)
DEFAULTS = {key: on for key, _n, _d, on in PLUGINS}


def enabled(cfg, key: str) -> bool:
    return bool((cfg.get("plugins") or {}).get(key, DEFAULTS.get(key, False)))


def toggle(cfg, key: str) -> bool:
    plugins = dict(cfg.get("plugins") or {})
    plugins[key] = not enabled(cfg, key)
    cfg["plugins"] = plugins
    cfg.save()
    return plugins[key]


#clearurls

_TRACKING = re.compile(
    r"^(utm_\w+|fbclid|gclid|dclid|gbraid|wbraid|msclkid|mc_eid|mc_cid|igshid|igsh|yclid|ttclid|twclid"
    r"|_hsenc|_hsmi|mkt_tok|ref_src|ref_url|si|feature|pp|share_id|trk|trkCampaign)$"
)
#si and feature are only junk on these, elsewhere they can mean something
_SHARE_ONLY = {"si": ("youtube.com", "youtu.be", "spotify.com"), "feature": ("youtube.com", "youtu.be"),
               "pp": ("youtube.com",)}
_URL = re.compile(r"https?://[^\s<>|]+")


def _clean(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    host = parts.netloc.lower()
    keep = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        junk = bool(_TRACKING.match(k))
        if junk and k in _SHARE_ONLY:
            junk = any(host.endswith(h) for h in _SHARE_ONLY[k])
        if host.endswith(("twitter.com", "x.com")) and k in ("s", "t"):
            junk = True
        if not junk:
            keep.append((k, v))
    return urlunsplit(parts._replace(query=urlencode(keep, doseq=True)))


def clearUrls(text: str) -> str:
    return _URL.sub(lambda m: _clean(m.group(0)), text)
