#favourite gifs trending and gif search from discords own endpoints
from __future__ import annotations

import base64
import logging
from typing import List
from urllib.parse import urlsplit

import discord
from discord.http import Route
from rich.text import Text

log = logging.getLogger("uncord")

FRECENCY = 2


def _titleFromUrl(url: str) -> str:
    path = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    words = path.replace("-gif", "").replace("-", " ").split()
    words = [w for w in words if not w.isdigit()]
    return " ".join(words)[:60] or urlsplit(url).netloc


async def favourites(client: discord.Client) -> List[dict]:
    from discord_protos import FrecencyUserSettings

    try:
        data = await client.http.get_proto_settings(FRECENCY)
        raw = base64.b64decode(data.get("settings") or "")
        fs = FrecencyUserSettings()
        fs.ParseFromString(raw)
    except Exception:
        log.debug("favourite gifs failed", exc_info=True)
        return []
    out = []
    for url, g in fs.favorite_gifs.gifs.items():
        out.append({"url": url, "src": g.src, "order": g.order, "title": _titleFromUrl(url), "fav": True})
    out.sort(key=lambda g: g["order"], reverse=True)
    return out


async def trending(client: discord.Client) -> List[dict]:
    try:
        res = await client.http.request(
            Route("GET", "/gifs/trending-gifs"),
            params={"media_format": "mp4", "provider": "tenor", "locale": "en-US"},
        )
    except Exception:
        log.debug("trending gifs failed", exc_info=True)
        return []
    return [
        {"url": g.get("url"), "src": g.get("src"), "title": g.get("title") or _titleFromUrl(g.get("url", ""))}
        for g in (res or [])
        if g.get("url")
    ]


async def search(client: discord.Client, q: str, limit: int = 50) -> List[dict]:
    res = await client.http.request(
        Route("GET", "/gifs/search"),
        params={"q": q, "media_format": "mp4", "provider": "tenor", "locale": "en-US", "limit": limit},
    )
    return [
        {"url": g.get("url"), "src": g.get("src"), "title": g.get("title") or _titleFromUrl(g.get("url", ""))}
        for g in (res or [])
        if g.get("url")
    ]


def label(g: dict) -> Text:
    t = Text("★ " if g.get("fav") else "  ", style="yellow")
    t.append(g.get("title") or "gif")
    return t
