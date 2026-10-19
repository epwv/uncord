#entry point handles --login --logout --doctor then starts the app
import getpass
import importlib
import logging
import os
import sys

from . import env

env.loadEnvFile()
CONFIG = env.configDir()
TOKEN_FILE = env.tokenFile()
LOG_FILE = env.logFile()
LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}


def saveToken(tok: str) -> None:
    if not env.validToken(tok):
        sys.exit("that doesn't look like a token (spaces or odd characters)")
    env.saveToken(tok)


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
    return env.token() or loginFlow()


DAVE_LOG_PREFIXES = ("Preparing for DAVE", "Executing DAVE", "Set MLS", "MLS ")


def moveOldConfig() -> None:
    #this used to be called concord so bring the login and settings over once
    old = CONFIG.parent / "concord"
    if CONFIG.name != "uncord" or CONFIG.exists() or not old.is_dir():
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
        print("usage: uncord [--login | --logout | --doctor | --update | --env]\n")
        print("  --login   log in again (qr code from the discord app or paste a token)")
        print("  --logout  forget the saved token")
        print("  --doctor  check what uncord can find on this system")
        print("  --update  get the latest uncord from github")
        print("  --env     list the environment variables uncord reads")
        print("\ntoken comes from $UNCORD_TOKEN, $DISCORD_TOKEN, $UNCORD_TOKEN_CMD or", TOKEN_FILE)
        print("Logs:", LOG_FILE)
        return
    if args and args[0] == "--env":
        print(env.describe())
        return
    if args and args[0] == "--update":
        from .update import update

        sys.exit(update())
    if args and args[0] == "--doctor":
        from .deps import doctor

        sys.exit(doctor())
    if args and args[0] == "--logout":
        env.forgetToken()
        print("token removed")
        return
    if args and args[0] == "--login":
        loginFlow()
        args = []
    from .safety import RedactingFormatter, private

    private(CONFIG, isDir=True)
    private(TOKEN_FILE)
    try:
        for mod in ("uncord._native", "uncord._dsp", "uncord._rtp"):
            importlib.import_module(mod)
    except ImportError:
        sys.exit("uncord's C and C++ parts aren't built. Reinstall with gcc, g++ and the python headers installed "
                 "(./install.sh does this), or run: python setup.py build_ext --inplace")
    token = getToken()
    private(LOG_FILE.parent, isDir=True)
    level = LEVELS.get(os.environ.get("UNCORD_LOG_LEVEL", "info").lower(), logging.INFO)
    logging.basicConfig(filename=LOG_FILE, level=level)
    private(LOG_FILE)
    #the formatter scrubs the finished line so tracebacks and args cant leak the token either
    for h in logging.getLogger().handlers:
        h.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s", token))
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
