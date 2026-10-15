#entry point handles --login --logout --doctor then starts the app
import getpass
import importlib
import logging
import os
import sys
from pathlib import Path

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "uncord"
TOKEN_FILE = CONFIG / "token"
LOG_FILE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "uncord" / "uncord.log"


def saveToken(tok: str) -> None:
    CONFIG.mkdir(parents=True, exist_ok=True)
    old = os.umask(0o077)
    try:
        TOKEN_FILE.write_text(tok + "\n")
    finally:
        os.umask(old)
    TOKEN_FILE.chmod(0o600)


def loginFlow() -> str:
    print("uncord: log in to Discord\n")
    print("  1) Scan a QR code with the Discord app (recommended: no password, no captcha)")
    print("  2) Paste a token")
    choice = input("\nChoose 1 or 2 [1]: ").strip() or "1"
    if choice == "1":
        import asyncio
        from . import login

        def show(url: str) -> None:
            print("\n" + login.qrText(url) + "\n")

        def status(text: str) -> None:
            print("  " + text, flush=True)

        try:
            tok = asyncio.run(login.qrLogin(show, status))
        except KeyboardInterrupt:
            sys.exit("\ncancelled")
        except Exception as e:
            print(f"\nQR login didn't work: {e}\nFalling back to pasting a token.\n")
        else:
            saveToken(tok)
            print(f"\nLogged in. Token saved to {TOKEN_FILE} (only readable by you).")
            return tok
    print(
        "Get it from Discord in a browser: DevTools (F12) → Network → any request to discord.com/api → 'Authorization' header."
    )
    tok = getpass.getpass("Discord token (input hidden): ").strip().strip('"')
    if not tok:
        sys.exit("no token given")
    saveToken(tok)
    print(f"saved to {TOKEN_FILE}")
    return tok


def getToken() -> str:
    tok = os.environ.get("DISCORD_TOKEN", "").strip()
    if tok:
        return tok
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    return loginFlow()


DAVE_LOG_PREFIXES = ("Preparing for DAVE", "Executing DAVE", "Set MLS", "MLS ")


def moveOldConfig() -> None:
    #this used to be called concord so bring the login and settings over once
    old = CONFIG.parent / "concord"
    if CONFIG.exists() or not old.is_dir():
        return
    import shutil

    try:
        shutil.copytree(old, CONFIG)
        if (CONFIG / "token").exists():
            (CONFIG / "token").chmod(0o600)
    except OSError:
        pass


def main() -> None:
    moveOldConfig()
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print("usage: uncord [--login | --logout | --doctor | --update]\n")
        print("  --login   log in again (qr code from the discord app or paste a token)")
        print("  --logout  forget the saved token")
        print("  --doctor  check what uncord can find on this system")
        print("  --update  get the latest uncord from github")
        print("\ntoken comes from $DISCORD_TOKEN or", TOKEN_FILE)
        print("Logs:", LOG_FILE)
        return
    if args and args[0] == "--update":
        from .update import update

        sys.exit(update())
    if args and args[0] == "--doctor":
        from .deps import doctor

        sys.exit(doctor())
    if args and args[0] == "--logout":
        TOKEN_FILE.unlink(missing_ok=True)
        print("token removed")
        return
    if args and args[0] == "--login":
        loginFlow()
        args = []
    from .safety import RedactTokens, private

    private(CONFIG, isDir=True)
    private(TOKEN_FILE)
    try:
        for mod in ("uncord._native", "uncord._dsp"):
            importlib.import_module(mod)
    except ImportError:
        sys.exit("uncord's C and C++ parts aren't built. Reinstall with gcc, g++ and the python headers installed "
                 "(./install.sh does this), or run: python setup.py build_ext --inplace")
    token = getToken()
    private(LOG_FILE.parent, isDir=True)
    logging.basicConfig(
        filename=LOG_FILE, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    private(LOG_FILE)
    for h in logging.getLogger().handlers:
        h.addFilter(RedactTokens())
    #gateway debug is huge so only keep the e2ee (dave mls) handshake lines from it
    gw = logging.getLogger("discord.gateway")
    gw.setLevel(logging.DEBUG)
    gw.addFilter(lambda r: r.levelno >= logging.WARNING or r.getMessage().startswith(DAVE_LOG_PREFIXES))
    for name in ("discord.voice_state", "discord.voice_client"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    try:  #query graphics support before textual grabs the tty
        importlib.import_module("textual_image.widget")
    except Exception:
        logging.getLogger("uncord").warning("textual-image unavailable", exc_info=True)
    from .app import Uncord

    Uncord(token).run()


if __name__ == "__main__":
    main()
