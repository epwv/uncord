#the + menu file picking clipboard paste and uploads
from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
from typing import Optional

import discord
from rich.text import Text
from textual import work
from textual.widgets import Input, Static

from . import filepick
from .fmt import humanSize, snippet
from .screens import FileBrowser, Picker, Prompt

from .panels import ChatInput

log = logging.getLogger("uncord")


class AttachMixin:

    #plus menu

    MEDIA_DIRS = (
        "~/Pictures/Screenshots",
        "~/Pictures",
        "~/Downloads",
        "~/Videos/Clips",
        "~/Videos",
        "~/Documents",
        "~/Music",
    )

    def action_attach_menu(self) -> None:
        if self.current is None:
            self.notify("Open a chat first.")
            return
        items = [
            ("file", "📎 Upload a file", "pick from recent files or browse", "Ctrl+O"),
            ("paste", "🖼 Paste image from clipboard", "screenshot or copied image", "Ctrl+V"),
            ("voice", "🎤 Record a voice message", "Ctrl+R again to send, Esc to cancel", "Ctrl+R"),
            ("gif", "GIF", "your favourites", "Ctrl+G"),
            ("sticker", "🏷 Sticker", "", "Ctrl+T"),
            ("emoji", "☺ Emoji", "", "Ctrl+E"),
        ]
        entries = []
        for key, label, hint, keys in items:
            t = Text(label, style="bold")
            if hint:
                t.append(f"  {hint}", style="bright_black")
            t.append(f"  {keys}", style="cyan")
            entries.append((t, label.lower(), key))

        def done(k) -> None:
            if k == "file":
                self.filePicker()
            elif k == "paste":
                self.pasteClipboard(self.query_one("#input", ChatInput))
            elif k == "voice":
                self.action_record()
            elif k == "gif":
                self.action_gif()
            elif k == "sticker":
                self.action_sticker()
            elif k == "emoji":
                self.action_emoji_insert()

        self.push_screen(Picker(entries, placeholder="Send…", title="Attach"), done)

    #use a tui file manager if you have one (yazi ranger lf nnn vifm) or the desktop dialog or our own browser
    def filePicker(self, folder: Optional[str] = None) -> None:
        mode = self.cfg.get("file_picker", "auto")
        start = folder or self.cfg.get("upload_dir") or os.path.expanduser("~")
        if not os.path.isdir(start):
            start = os.path.expanduser("~")
        if mode == "builtin":
            self.builtinBrowser(start)
            return
        if mode == "portal":
            self.portalPick(start, None)
            return
        fm = filepick.defaultFileManager() if mode == "auto" else (mode, mode, [mode])
        if fm is not None:
            out = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", f"uncord-pick-{os.getpid()}")
            cmd = filepick.tuiCommand(fm[1], out, start)
            if cmd is not None:
                self.runTerminalPicker(cmd, out)
            else:
                self.portalPick(start, fm)
            return

        def got(d) -> None:
            if d:
                d = os.path.expanduser(d)
                if os.path.isdir(d):
                    self.builtinBrowser(d)
                else:
                    self.notify(f"Not a folder: {d}", severity="warning", markup=False)

        self.push_screen(
            Prompt("No file manager found. Which folder should I show?", "~/Pictures", start + "/"), got
        )

    def builtinBrowser(self, start: Optional[str]) -> None:
        self.push_screen(FileBrowser(start if start and os.path.isdir(start) else None), self._pickedFiles)

    def _pickedFiles(self, paths) -> None:
        if paths:
            self.cfg["upload_dir"] = os.path.dirname(paths[0])
            self.cfg.save()
            self.attachFiles(paths)

    def runTerminalPicker(self, cmd: list, out: str) -> None:
        import subprocess

        try:
            with self.suspend():
                subprocess.run(cmd)
        except Exception as e:
            log.exception("terminal file picker")
            self.notify(f"Couldn't run {os.path.basename(cmd[0])}: {e}", severity="error", markup=False)
            return
        paths = filepick.readChosen(out)
        if paths:
            self._pickedFiles(paths)

    @work(exclusive=True, group="picker")
    async def portalPick(self, start: str, fm) -> None:
        paths = await filepick.portalOpenFiles("Upload to Discord", start)
        if paths is None:  #no file dialog so open the file manager
            if fm is not None:
                import subprocess

                subprocess.Popen(
                    fm[2] + [start],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self.notify(
                    f"Opened {fm[1]}. Drag files onto uncord to attach them.", timeout=8, markup=False
                )
            else:
                self.builtinBrowser(start)
            return
        if paths:
            self._pickedFiles(paths)

    #attachments

    IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp")

    def pathsIn(self, text: str) -> list:
        from urllib.parse import unquote, urlsplit

        lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
        if not lines or len(lines) > 10:
            return []
        out = []
        for l in lines:
            if l.startswith("file://"):
                l = unquote(urlsplit(l).path)
            l = os.path.expanduser(l.strip("'\""))
            if not (l.startswith("/") and os.path.isfile(l)):
                return []
            out.append(l)
        return out

    #ctrl+v image goes in as an attachment copied files get attached text gets typed
    @work(group="paste")
    async def pasteClipboard(self, inp: Input) -> None:
        async def run(*args) -> bytes:
            p = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            out, _ = await asyncio.wait_for(p.communicate(), 10)
            return out if p.returncode == 0 else b""

        if not shutil.which("wl-paste"):
            self.notify("Image paste needs wl-paste (wl-clipboard).", severity="warning")
            return
        try:
            types = (await run("wl-paste", "--list-types")).decode(errors="replace").split()
        except Exception:
            types = []
        img = next((t for t in self.IMAGE_TYPES if t in types), None) or next(
            (t for t in types if t.startswith("image/")), None
        )
        if img:
            data = await run("wl-paste", "--no-newline", "--type", img)
            if data:
                ext = {"image/jpeg": "jpg", "image/svg+xml": "svg"}.get(img, img.split("/")[1])
                self.addAttachment(f"image.{ext}", data)
                return
        if "text/uri-list" in types:
            files = self.pathsIn(
                (await run("wl-paste", "--no-newline", "--type", "text/uri-list")).decode(errors="replace")
            )
            if files:
                self.attachFiles(files)
                return
        text = (await run("wl-paste", "--no-newline")).decode(errors="replace") if types else ""
        files = self.pathsIn(text)
        if files:
            self.attachFiles(files)
        elif text:
            inp.insert_text_at_cursor(text)
        else:
            self.notify("Clipboard is empty.")

    def attachFiles(self, paths: list) -> None:
        for p in paths:
            try:
                if os.path.getsize(p) > 500 << 20:
                    self.notify(f"{os.path.basename(p)} is too big.", severity="warning", markup=False)
                    continue
                with open(p, "rb") as f:
                    self.addAttachment(os.path.basename(p), f.read())
            except OSError as e:
                self.notify(f"Can't read {p}: {e}", severity="error", markup=False)

    def addAttachment(self, name: str, data: bytes) -> None:
        if self.plugin("anonymise_files"):
            import secrets

            ext = name.rsplit(".", 1)[1] if "." in name else ""
            name = secrets.token_hex(4) + (f".{ext}" if ext else "")
        if len(self.attachments) >= 10:
            self.notify("Discord allows 10 files per message.", severity="warning")
            return
        names = {n for n, _d in self.attachments}
        base, dot, ext = name.rpartition(".")
        k = 1
        while name in names:
            k += 1
            name = f"{base or ext}-{k}{dot}{ext if base else ''}"
        self.attachments.append((name, data))
        self.renderAttachments()
        self.query_one("#input", ChatInput).focus()

    def clearAttachments(self) -> None:
        self.attachments.clear()
        self.renderAttachments()

    def renderAttachments(self) -> None:
        bar = self.query_one("#attach", Static)
        if not self.attachments:
            bar.remove_class("on")
            return
        t = Text("📎 ", style="bold")
        for name, data in self.attachments:
            t.append(f" {snippet(name, 28)} ", style="bold black on cyan")
            t.append(f" {humanSize(len(data))}  ", style="bright_black")
        t.append("Enter sends · Ctrl+V adds more · Esc removes", style="bright_black")
        bar.update(t)
        bar.add_class("on")

    async def sendWithAttachments(self, text: str) -> None:
        if self.current is None:
            self.notify("Pick a chat first (Ctrl+K).", severity="warning")
            return
        inp = self.query_one("#input", ChatInput)
        files, self.attachments = self.attachments, []
        self.renderAttachments()
        inp.value = ""
        replyTo = self.replyTo
        self.cancelCompose()
        total = sum(len(d) for _n, d in files)
        if total > 1 << 20:
            self.notify(f"Uploading {len(files)} file(s), {humanSize(total)}…", markup=False)
        kw = {"files": [discord.File(io.BytesIO(d), filename=n) for n, d in files]}
        if replyTo is not None:
            kw.update(reference=replyTo, mention_author=not self.plugin("no_reply_mention"))
        try:
            await self.current.send(self.convertOutgoing(text) if text.strip() else None, **kw)
        except discord.HTTPException as e:
            self.notify(f"Upload failed: {e.text or e}", severity="error", markup=False)
            self.attachments = files
            self.renderAttachments()
            inp.value = text

    def deleteMessage(self, m: discord.Message) -> None:
        async def go():
            try:
                await m.delete()
            except discord.HTTPException as e:
                self.notify(f"Delete failed: {e.text or e}", severity="error", markup=False)

        asyncio.create_task(go())

    def copyText(self, text: str) -> None:
        if shutil.which("wl-copy"):

            async def go():
                #wl-copy stays running to serve the clipboard so it mustnt keep our terminal open
                p = await asyncio.create_subprocess_exec("wl-copy", stdin=asyncio.subprocess.PIPE,
                                                         stdout=asyncio.subprocess.DEVNULL,
                                                         stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
                await p.communicate(text.encode())

            asyncio.create_task(go())
        else:
            self.copy_to_clipboard(text)
        self.notify("Copied.")
