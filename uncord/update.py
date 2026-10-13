#uncord --update pulls the latest uncord from github and reinstalls it
#works for a git checkout (install.sh or running from the repo) or a plain pip install
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

REPO = "https://github.com/epwv/uncord.git"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "uncord"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "uncord"
CHECK_EVERY = 12 * 3600


def _git(src: Path, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(src), *args], capture_output=True, text=True, timeout=timeout)


def sourceDir() -> Optional[Path]:
    #running straight from a checkout
    here = Path(__file__).resolve().parent.parent
    if (here / ".git").exists():
        return here
    #install.sh writes where it was run from
    try:
        src = Path((DATA_DIR / "source").read_text().strip())
    except OSError:
        return None
    return src if (src / ".git").exists() else None


def _runningFrom(src: Path) -> bool:
    return Path(__file__).resolve().is_relative_to(src.resolve())


def _pip(*args: str) -> int:
    return subprocess.call([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *args])


def update() -> int:
    if not os.access(sys.prefix, os.W_OK):
        print("uncord was installed by your package manager (or as root), update it that way instead")
        return 1
    src = sourceDir()
    if src is None:
        print(f"no git checkout found, reinstalling from {REPO}")
        return _pip("--upgrade", f"git+{REPO}")
    if _git(src, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        print(f"{src} has local changes, commit or stash them first")
        return 1
    before = _git(src, "rev-parse", "--short", "HEAD").stdout.strip()
    print(f"pulling in {src}")
    pull = _git(src, "pull", "--ff-only", timeout=180)
    if pull.returncode != 0:
        print(pull.stderr.strip() or pull.stdout.strip())
        return 1
    after = _git(src, "rev-parse", "--short", "HEAD").stdout.strip()
    if before == after:
        ahead = _git(src, "rev-list", "--count", "@{u}..HEAD").stdout.strip()
        if ahead.isdigit() and int(ahead):
            one = ahead == "1"
            print(f"nothing new on github, and your copy has {ahead} commit{'' if one else 's'} "
                  f"that {'isn' if one else 'aren'}'t there yet (git push to share {'it' if one else 'them'})")
        else:
            print(f"already up to date ({after})")
        return 0
    print(_git(src, "log", "--oneline", f"{before}..{after}").stdout.rstrip())
    #a checkout you run from directly only needs the dependencies refreshed
    code = _pip("-e", str(src)) if _runningFrom(src) else _pip("--upgrade", str(src))
    if code == 0:
        print(f"updated {before} -> {after}, restart uncord to use it")
    return code


def checkInBackground(notify: Callable[[str], None]) -> None:
    #fetch at most every 12h and say if theres something new
    src = sourceDir()
    stamp = CACHE_DIR / "update-check"
    try:
        if src is None or time.time() - stamp.stat().st_mtime < CHECK_EVERY:
            return
    except OSError:
        pass

    def run() -> None:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            stamp.touch()
            if _git(src, "fetch", "--quiet", timeout=30).returncode != 0:
                return
            behind = _git(src, "rev-list", "--count", "HEAD..@{u}").stdout.strip()
            if behind.isdigit() and int(behind) > 0:
                notify(f"uncord update available ({behind} new commit{'s' if behind != '1' else ''}). "
                       "Run uncord --update")
        except Exception:
            pass

    threading.Thread(target=run, daemon=True, name="uncord-update-check").start()
