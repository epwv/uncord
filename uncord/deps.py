#finds ffmpeg mpv gpu-screen-recorder libopus and libchafa on any distro plus uncord --doctor
from __future__ import annotations

import ctypes
import ctypes.util
import glob
import os
import shlex
import shutil
import sys
from typing import List, Optional

#places distros keep shared libs that find_library can miss
LIB_DIRS = [
    "/usr/lib",
    "/usr/lib64",
    "/usr/local/lib",
    "/usr/lib/x86_64-linux-gnu",
    "/usr/lib/aarch64-linux-gnu",
    "/usr/lib/arm-linux-gnueabihf",
    "/lib",
    "/lib64",
    "/run/current-system/sw/lib",
    os.path.expanduser("~/.nix-profile/lib"),
]


def findLib(env: str, name: str, soname: str) -> Optional[str]:
    override = os.environ.get(env, "")
    if override:
        return override if os.path.exists(override) else None
    found = ctypes.util.find_library(name)
    for cand in filter(None, [found, soname]):
        try:
            ctypes.CDLL(cand)
            return cand
        except OSError:
            pass
    for d in LIB_DIRS:
        for path in sorted(glob.glob(os.path.join(d, soname + "*"))):
            try:
                ctypes.CDLL(path)
                return path
            except OSError:
                pass
    return None


def findBin(env: str, name: str) -> Optional[str]:
    override = os.environ.get(env, "")
    if override:
        return override if os.path.exists(override) else shutil.which(override)
    return shutil.which(name)


def findCmd(env: str, name: str) -> Optional[List[str]]:
    #env can be a whole command eg flatpak run --command=gpu-screen-recorder com.dec05eba.gpu_screen_recorder
    override = os.environ.get(env, "")
    if override:
        return shlex.split(override)
    path = shutil.which(name)
    return [path] if path else None


FFMPEG = findBin("UNCORD_FFMPEG", "ffmpeg")
MPV = findBin("UNCORD_MPV", "mpv")
GSR = findCmd("UNCORD_GSR", "gpu-screen-recorder")
OPUS = findLib("UNCORD_OPUS", "opus", "libopus.so.0")
CHAFA = findLib("UNCORD_CHAFA", "chafa", "libchafa.so.0")


def doctor() -> int:
    def row(ok: bool, what: str, detail: str, hint: str = "") -> None:
        mark = "ok  " if ok else "--  "
        print(f"  {mark}{what:<22} {detail}")
        if not ok and hint:
            print(f"      {hint}")

    print(f"uncord on python {sys.version.split()[0]}\n")
    print("needed for voice, video and gifs")
    row(bool(FFMPEG), "ffmpeg", FFMPEG or "not found", "install ffmpeg (or set UNCORD_FFMPEG)")
    row(bool(OPUS), "libopus", OPUS or "not found", "install opus / libopus0 (or set UNCORD_OPUS)")
    pw = all(shutil.which(t) for t in ("pw-play", "pw-record"))
    row(
        pw,
        "pipewire tools",
        "pw-play, pw-record" if pw else "not found",
        "voice uses pipewire: install pipewire-bin / pipewire-utils / pipewire",
    )
    print("\nnice to have")
    row(bool(CHAFA), "libchafa", CHAFA or "not found", "faster, better looking images (chafa / libchafa0)")
    row(bool(MPV), "mpv", MPV or "not found", "pops streams and videos out into a window")
    row(
        bool(GSR),
        "gpu-screen-recorder",
        " ".join(GSR) if GSR else "not found",
        "needed to share your screen (see the readme)",
    )
    clip = shutil.which("wl-paste") or shutil.which("xclip")
    row(
        bool(clip),
        "clipboard",
        clip or "not found",
        "image paste needs wl-clipboard (wayland) or xclip (x11)",
    )
    from .notify import available

    note = available()
    row(bool(note), "notifications", note or "not found", "needs a notification daemon (mako, dunst, swaync…)")
    term = os.environ.get("TERM_PROGRAM") or os.environ.get("TERM", "?")
    print(
        f"\nterminal: {term}  (images: kitty or ghostty with kitty graphics, foot wezterm konsole contour"
        " mlterm or xterm -ti vt340 with sixel)"
    )
    return 0 if FFMPEG and OPUS else 1
