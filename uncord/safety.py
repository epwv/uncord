#keeps things other people send from reaching the terminal raw, tokens out of logs and private files private
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

#c0 and c1 controls (tab and newline stay), delete, and bidi overrides that flip text around
UNSAFE = [c for c in range(0x20) if c not in (9, 10)] + [0x7F] + list(range(0x80, 0xA0)) + \
    list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A))

TOKEN = re.compile(r"(?:mfa\.[\w-]{20,}|[\w-]{24,28}\.[\w-]{6}\.[\w-]{27,40})")
SAFE_SCHEMES = ("http", "https")


#rich and textual each strip a handful of controls with a shared table, widen both so an escape
#sequence in a message name or embed is dropped instead of reaching the terminal
def hardenText() -> None:
    import rich.control
    import textual.content

    for table in (rich.control._CONTROL_STRIP_TRANSLATE, textual.content._CONTROL_STRIP_TRANSLATE):
        for c in UNSAFE:
            table[c] = None
    #a tiny png can claim to be gigapixels, refuse anything past 40mp before decoding it
    try:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = 40_000_000
    except ImportError:
        pass


def clean(text: str) -> str:
    return text.translate({c: None for c in UNSAFE}) if text else text


class RedactTokens(logging.Filter):

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if TOKEN.search(msg):
            record.msg, record.args = TOKEN.sub("[token]", msg), None
        if record.exc_text and TOKEN.search(record.exc_text):
            record.exc_text = TOKEN.sub("[token]", record.exc_text)
        return True


#only you can read the config dir and logs
def private(path: Path, isDir: bool = False) -> None:
    try:
        if isDir:
            path.mkdir(parents=True, exist_ok=True)
        if path.exists():
            mode = path.stat().st_mode & 0o777
            want = 0o700 if isDir else 0o600
            if mode & 0o077:
                os.chmod(path, want)
    except OSError:
        pass


#a link is only handed to xdg-open if its http or https with a real host
def safeUrl(url: str) -> Optional[str]:
    url = clean((url or "").strip())
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in SAFE_SCHEMES or not parts.hostname:
        return None
    return url


def domain(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host
