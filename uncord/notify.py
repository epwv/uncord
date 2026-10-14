#desktop notifications straight over dbus so notify-send isnt needed, falls back to it if dbus fails
from __future__ import annotations

import asyncio
import shutil
from typing import Optional

from .safety import clean

_ADDR = dict(object_path="/org/freedesktop/Notifications", bus_name="org.freedesktop.Notifications",
             interface="org.freedesktop.Notifications")


#notification daemons read the body as markup so escape it
def _plain(s: str) -> str:
    return clean(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def send(title: str, body: str, urgent: bool = False) -> bool:
    title, body = clean(title), _plain(body)
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.asyncio import open_dbus_router

        async with open_dbus_router(bus="SESSION") as router:
            msg = new_method_call(DBusAddress(**_ADDR), "Notify", "susssasa{sv}i",
                                  ("uncord", 0, "", title, body, [], {"urgency": ("y", 2 if urgent else 1)}, -1))
            await asyncio.wait_for(router.send_and_get_reply(msg), 3)
        return True
    except Exception:
        pass
    tool = shutil.which("notify-send")
    if tool is None:
        return False
    args = [tool, "-a", "uncord"] + (["-u", "critical"] if urgent else []) + ["--", title, body]
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.DEVNULL)
    await proc.wait()
    return True


def available() -> Optional[str]:
    #for --doctor whats going to show notifications if anything
    try:
        from jeepney.bus_messages import message_bus
        from jeepney.io.blocking import open_dbus_connection

        with open_dbus_connection(bus="SESSION") as conn:
            if conn.send_and_get_reply(message_bus.NameHasOwner(_ADDR["bus_name"]), timeout=2).body[0]:
                return "notification service on dbus"
    except Exception:
        pass
    return shutil.which("notify-send")
