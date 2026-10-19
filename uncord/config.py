#settings saved in ~/.config/uncord/config.json keys stay snake_case so old files keep working
from __future__ import annotations

import json
import os
from typing import Any

from .env import configDir

CONFIG_DIR = configDir()
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS: dict[str, Any] = {
    "theme": "ansi-dark",
    "recent_emojis": [],
    "vad": 600,
    "volume": 100,
    "mark_read": True,
    "show_sidebar": True,
    "dm_limit": 30,
    "last_channel": None,
    "inline_media": True,
    "emoji_images": True,
    "sfx": True,
    "sfx_volume": 100,
    "user_volume": {},  #user id to volume percent
}


#a hand edited value of the wrong type falls back to the default instead of crashing later
def _fits(value: Any, default: Any) -> bool:
    if default is None or isinstance(value, type(default)):
        return True
    num = (int, float)
    return isinstance(default, num) and isinstance(value, num) and not isinstance(value, bool)


class Config(dict):
    def __init__(self) -> None:
        super().__init__(DEFAULTS)
        try:
            saved = json.loads(CONFIG_FILE.read_text())
        except OSError:
            saved = None
        except ValueError:
            #keep a broken file so the next save doesnt wipe every setting
            saved = None
            try:
                CONFIG_FILE.replace(CONFIG_FILE.with_suffix(".json.bad"))
            except OSError:
                pass
        if isinstance(saved, dict):
            self.update({k: v for k, v in saved.items() if _fits(v, DEFAULTS.get(k))})
        self["user_volume"] = dict(self.get("user_volume") or {})  #dont share the defaults dict
        if self.get("vad") == 300:
            self["vad"] = 600

    def save(self) -> None:
        try:
            CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(self, indent=2, ensure_ascii=False))
            os.chmod(tmp, 0o600)
            tmp.replace(CONFIG_FILE)
        except OSError:
            pass

    def pushRecentEmoji(self, key: str) -> None:
        rec = [k for k in self.get("recent_emojis", []) if k != key]
        self["recent_emojis"] = [key] + rec[:23]
        self.save()
