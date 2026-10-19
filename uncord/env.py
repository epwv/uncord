#everything uncord reads from the environment, plus an optional env file for the same keys
#real environment wins over the file, and the token is taken out of os.environ once read so
#ffmpeg mpv pw-record and friends never inherit it
from __future__ import annotations

import os
import shlex
import stat
import subprocess
from pathlib import Path
from typing import Optional

#name what it does, secret ones never get printed
VARS = (
    ("UNCORD_TOKEN", "your discord token (DISCORD_TOKEN works too)", True),
    ("UNCORD_TOKEN_CMD", "command that prints the token, e.g. 'pass show discord' (no shell)", False),
    ("UNCORD_TOKEN_FILE", "where the token is saved (default <config>/token)", False),
    ("UNCORD_ENV_FILE", "env file to load (default <config>/env)", False),
    ("UNCORD_CONFIG_DIR", "settings and login folder (default ~/.config/uncord)", False),
    ("UNCORD_CACHE_DIR", "cache and log folder (default ~/.cache/uncord)", False),
    ("UNCORD_LOG_FILE", "log file (default <cache>/uncord.log)", False),
    ("UNCORD_LOG_LEVEL", "debug, info, warning or error (default info)", False),
    ("UNCORD_THEME", "theme name, overrides the setting", False),
    ("UNCORD_VAD", "mic voice gate level (default 600)", False),
    ("UNCORD_NOTIFY", "0 turns desktop notifications off", False),
    ("UNCORD_NO_UPDATE_CHECK", "1 stops the 12 hourly github update check", False),
    ("UNCORD_FFMPEG", "path to ffmpeg", False),
    ("UNCORD_MPV", "path to mpv", False),
    ("UNCORD_GSR", "path to gpu-screen-recorder", False),
    ("UNCORD_OPUS", "path to libopus.so.0", False),
    ("UNCORD_CHAFA", "path to libchafa.so.0", False),
)
SECRET = {"UNCORD_TOKEN", "DISCORD_TOKEN"}
ALLOWED = {name for name, _d, _s in VARS} | {"DISCORD_TOKEN"}


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback)


def configDir() -> Path:
    return Path(os.environ.get("UNCORD_CONFIG_DIR") or _xdg("XDG_CONFIG_HOME", ".config") / "uncord").expanduser()


def cacheDir() -> Path:
    return Path(os.environ.get("UNCORD_CACHE_DIR") or _xdg("XDG_CACHE_HOME", ".cache") / "uncord").expanduser()


def tokenFile() -> Path:
    return Path(os.environ.get("UNCORD_TOKEN_FILE") or configDir() / "token").expanduser()


def logFile() -> Path:
    return Path(os.environ.get("UNCORD_LOG_FILE") or cacheDir() / "uncord.log").expanduser()


def envFile() -> Path:
    return Path(os.environ.get("UNCORD_ENV_FILE") or configDir() / "env").expanduser()


#a file holding secrets has to be yours, a real file, and not open to anyone else
def _readPrivate(path: Path) -> Optional[str]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise SystemExit(f"uncord: can't read {path}: {e.strerror} (symlinks aren't followed)")
    with os.fdopen(fd) as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise SystemExit(f"uncord: {path} isn't a regular file")
        if st.st_uid != os.getuid():
            raise SystemExit(f"uncord: {path} belongs to someone else, refusing to use it")
        if st.st_mode & 0o077:
            os.fchmod(f.fileno(), 0o600)
        return f.read(64 * 1024)


def loadEnvFile() -> None:
    #KEY=value lines, # comments, optional export and quotes, only uncord's own keys
    text = _readPrivate(envFile())
    if not text:
        return
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if not sep or key not in ALLOWED:
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        os.environ.setdefault(key, val)


def validToken(tok: str) -> bool:
    #a token is plain printable ascii, anything else could smuggle extra headers
    return bool(tok) and len(tok) < 512 and tok.isascii() and tok.isprintable() and " " not in tok


def _fromCmd(cmd: str) -> str:
    try:
        out = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=30, check=True)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        raise SystemExit(f"uncord: UNCORD_TOKEN_CMD failed: {type(e).__name__}")
    return (out.stdout.strip().splitlines() or [""])[0].strip()


#returns the token from the env or the command or the file, None means log in
def token() -> Optional[str]:
    tok = ""
    for var in ("UNCORD_TOKEN", "DISCORD_TOKEN"):
        tok = tok or os.environ.get(var, "").strip()
        os.environ.pop(var, None)
    cmd = os.environ.pop("UNCORD_TOKEN_CMD", "").strip()
    if not tok and cmd:
        tok = _fromCmd(cmd)
    if not tok:
        tok = (_readPrivate(tokenFile()) or "").strip()
    tok = tok.strip('"')
    if tok and not validToken(tok):
        raise SystemExit("uncord: that token has spaces or odd characters in it, check where it came from")
    return tok or None


def saveToken(tok: str) -> Path:
    path = tokenFile()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    tmp.replace(path)
    return path


def forgetToken() -> None:
    path = tokenFile()
    try:
        if path.is_file() and not path.is_symlink():
            size = path.stat().st_size
            with open(path, "r+b") as f:
                f.write(b"\0" * size)
                f.flush()
                os.fsync(f.fileno())
    except OSError:
        pass
    path.unlink(missing_ok=True)


def describe() -> str:
    rows = ["environment (real env wins over " + str(envFile()) + "):", ""]
    for name, desc, secret in VARS:
        val = os.environ.get(name)
        shown = ("set" if secret else val) if val else "-"
        rows.append(f"  {name:<24} {desc}\n  {'':<24} now: {shown}")
    return "\n".join(rows)
