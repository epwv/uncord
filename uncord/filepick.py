#file picking via a tui file manager or the desktop portal dialog
from __future__ import annotations

import configparser
import logging
import os
import shlex
import shutil
import subprocess
import uuid
from typing import List, Optional, Tuple
from urllib.parse import unquote, urlsplit

log = logging.getLogger("uncord")

#out is where the picker writes the chosen paths
TUI_CHOOSERS = {
    "yazi": ["yazi", "--chooser-file={out}", "{start}"],
    "ranger": ["ranger", "--choosefiles={out}", "{start}"],
    "lf": ["lf", "-selection-path", "{out}", "{start}"],
    "nnn": ["nnn", "-p", "{out}", "{start}"],
    "vifm": ["vifm", "--choose-files", "{out}", "{start}"],
}


def _desktopDirs() -> List[str]:
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    dirs = [home] + (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    return [os.path.join(d, "applications") for d in dirs if d]


def defaultFileManager() -> Optional[Tuple[str, str, List[str]]]:
    try:
        desk = subprocess.run(
            ["xdg-mime", "query", "default", "inode/directory"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        desk = ""
    if not desk:
        return None
    for d in _desktopDirs():
        path = os.path.join(d, desk)
        if not os.path.isfile(path):
            continue
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read(path, encoding="utf-8")
            execLine = cp.get("Desktop Entry", "Exec", fallback="")
        except Exception:
            continue
        argv = [a for a in shlex.split(execLine) if not (a.startswith("%") and len(a) == 2)]
        if argv and shutil.which(argv[0]):
            return desk, os.path.basename(argv[0]), argv
    name = desk.removesuffix(".desktop").split(".")[-1].lower()
    return (desk, name, [name]) if shutil.which(name) else None


def tuiCommand(program: str, out: str, start: str) -> Optional[List[str]]:
    tpl = TUI_CHOOSERS.get(program)
    if tpl is None or not shutil.which(tpl[0]):
        return None
    return [shutil.which(tpl[0])] + [a.format(out=out, start=start) for a in tpl[1:]]


def readChosen(out: str) -> List[str]:
    try:
        with open(out, encoding="utf-8", errors="replace") as f:
            lines = [l.strip() for l in f.read().replace("\0", "\n").splitlines() if l.strip()]
    except OSError:
        return []
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass
    return [p for p in lines if os.path.isfile(p)]


async def portalOpenFiles(title: str, folder: Optional[str]) -> Optional[List[str]]:
    try:
        from jeepney import DBusAddress, MatchRule, new_method_call
        from jeepney.bus_messages import message_bus
        from jeepney.io.asyncio import open_dbus_router
    except ImportError:
        return None
    import asyncio

    token = "uncord" + uuid.uuid4().hex[:12]
    try:
        async with open_dbus_router(bus="SESSION") as router:
            sender = router.unique_name.lstrip(":").replace(".", "_")
            handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
            rule = MatchRule(
                type="signal", interface="org.freedesktop.portal.Request", member="Response", path=handle
            )
            await router.send_and_get_reply(message_bus.AddMatch(rule))
            with router.filter(rule, queue=asyncio.Queue()) as queue:
                portal = DBusAddress(
                    "/org/freedesktop/portal/desktop",
                    bus_name="org.freedesktop.portal.Desktop",
                    interface="org.freedesktop.portal.FileChooser",
                )
                opts = {"handle_token": ("s", token), "multiple": ("b", True), "modal": ("b", True)}
                if folder and os.path.isdir(folder):
                    opts["current_folder"] = ("ay", folder.encode() + b"\0")
                reply = await router.send_and_get_reply(
                    new_method_call(portal, "OpenFile", "ssa{sv}", ("", title, opts))
                )
                if reply.header.message_type.name == "error":
                    log.info("file chooser portal error: %s", reply.body)
                    return None
                signal = await queue.get()
            code, results = signal.body
            if code != 0:
                return []
            uris = results.get("uris", ("as", []))[1]
            return [unquote(urlsplit(u).path) for u in uris if u.startswith("file://")]
    except Exception:
        log.info("file chooser portal unavailable", exc_info=True)
        return None
