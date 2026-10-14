#rich presence games and apps talk to discord over a local socket (discord-ipc-0)
#we listen there like the desktop app so whatever youre playing shows as your activity
from __future__ import annotations

import asyncio
import json
import logging
import os
import struct
from typing import Callable, Optional

import discord
from discord.http import Route

log = logging.getLogger("uncord.rpc")

HANDSHAKE, FRAME, CLOSE, PING, PONG = 0, 1, 2, 3, 4
MAX_FRAME = 64 * 1024  #real activity updates are a few hundred bytes
MAX_TEXT = 128
MAX_CLIENTS = 8


def _socketDir() -> str:
    for var in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"):
        if os.environ.get(var):
            return os.environ[var]
    return "/tmp"


def _pack(op: int, data) -> bytes:
    body = json.dumps(data).encode()
    return struct.pack("<II", op, len(body)) + body


class RpcServer:

    def __init__(self, client: discord.Client, onActivity: Callable[[Optional[discord.Activity]], None]) -> None:
        self.client = client
        self.onActivity = onActivity
        self.server = None
        self.path: Optional[str] = None
        self.names: dict = {}
        self.clients = 0
        self.owner = None  #the connection whose activity is showing

    async def start(self) -> str:
        #returns "ok" or why not
        for n in range(10):
            path = os.path.join(_socketDir(), f"discord-ipc-{n}")
            if os.path.exists(path):
                try:
                    _r, w = await asyncio.wait_for(asyncio.open_unix_connection(path), 1)
                    w.close()
                    if n == 0:
                        return "busy"  #another discord app has it and games only try the first
                    continue
                except Exception:
                    try:
                        os.unlink(path)  #left over from a crash
                    except OSError:
                        continue
            old = os.umask(0o077)
            try:
                self.server = await asyncio.start_unix_server(self._serve, path)
            finally:
                os.umask(old)
            os.chmod(path, 0o600)  #only your own apps can talk to it
            self.path = path
            log.info("rich presence listening on %s", path)
            return "ok"
        return "no free socket"

    def stop(self) -> None:
        if self.server is not None:
            self.server.close()
        if self.path:
            try:
                os.unlink(self.path)
            except OSError:
                pass

    async def _appName(self, appId: str) -> str:
        if appId not in self.names:
            try:
                data = await self.client.http.request(Route("GET", "/applications/{app_id}/rpc", app_id=appId))
                self.names[appId] = data.get("name") or "a game"
            except Exception:
                self.names[appId] = "a game"
        return self.names[appId]

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        me = object()
        appId = None
        self.clients += 1
        try:
            if self.clients > MAX_CLIENTS:
                return
            while True:
                head = await asyncio.wait_for(reader.readexactly(8), 600)
                op, size = struct.unpack("<II", head)
                if size > MAX_FRAME:
                    break
                data = json.loads((await asyncio.wait_for(reader.readexactly(size), 10)) or b"{}")
                if not isinstance(data, dict):
                    break
                if op == HANDSHAKE:
                    appId = str(data.get("client_id") or "")
                    if not appId.isdigit() or len(appId) > 20:
                        break
                    u = self.client.user
                    writer.write(_pack(FRAME, {"cmd": "DISPATCH", "evt": "READY", "nonce": None, "data": {
                        "v": 1,
                        "config": {"cdn_host": "cdn.discordapp.com", "api_endpoint": "//discord.com/api",
                                   "environment": "production"},
                        "user": {"id": str(u.id), "username": u.name, "discriminator": "0",
                                 "global_name": u.global_name, "avatar": u.avatar.key if u.avatar else None},
                    }}))
                elif op == FRAME:
                    cmd, nonce = data.get("cmd"), data.get("nonce")
                    reply = None
                    if cmd == "SET_ACTIVITY" and appId:
                        args = data.get("args")
                        raw = args.get("activity") if isinstance(args, dict) else None
                        act = await self._activity(appId, raw) if isinstance(raw, dict) else None
                        self.owner = me if act else None
                        self.onActivity(act)
                        reply = raw
                    writer.write(_pack(FRAME, {"cmd": cmd, "data": reply, "evt": None, "nonce": nonce}))
                elif op == PING:
                    writer.write(_pack(PONG, data))
                elif op == CLOSE:
                    break
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ValueError, asyncio.TimeoutError):
            pass
        except Exception:
            log.debug("rpc connection", exc_info=True)
        finally:
            self.clients -= 1
            if self.owner is me:
                self.owner = None
                self.onActivity(None)  #app closed so its activity goes too
            writer.close()

    async def _activity(self, appId: str, raw: dict) -> discord.Activity:
        def text(v):
            return v[:MAX_TEXT] if isinstance(v, str) and v else None

        def small(v, keys):
            return {k: v[k] for k in keys if k in v} if isinstance(v, dict) else None

        ts = {k: v for k, v in (small(raw.get("timestamps"), ("start", "end")) or {}).items()
              if isinstance(v, (int, float)) and 0 <= v < 1e14}
        for k, v in list(ts.items()):
            ts[k] = int(v * 1000) if v < 10**12 else int(v)  #apps send seconds discord wants ms
        assets = {k: text(v) for k, v in (small(raw.get("assets"), ("large_image", "large_text", "small_image",
                                                                     "small_text")) or {}).items() if text(v)}
        party = small(raw.get("party"), ("id", "size"))
        buttons = [text(b.get("label")) for b in (raw.get("buttons") or [])[:2] if isinstance(b, dict)]
        kind = raw.get("type", 0)
        return discord.Activity(
            type=discord.ActivityType(kind if kind in (0, 2, 3, 5) else 0),
            name=await self._appName(appId),
            application_id=int(appId),
            details=text(raw.get("details")),
            state=text(raw.get("state")),
            timestamps=ts or None,
            assets=assets or None,
            party=party or None,
            buttons=[b for b in buttons if b] or None,
        )
