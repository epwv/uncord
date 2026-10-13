<img src="assets/banner.png" alt="uncord, discord in your terminal">

# uncord

Discord in your terminal. Text chat, DMs, servers, threads, voice calls, images and GIFs drawn right in the terminal, and screen sharing both ways.

It logs in as your own account, so you see the same servers, DMs and friends you see in the official app.

Come hang out, get help or share ideas in the [uncord Discord server](https://discord.gg/fubfK8C6Cm).

> **Heads up:** Discord's terms don't allow third-party clients on user accounts. People use them anyway, but there's always some risk of your account getting flagged. Use it at your own risk.

## What it can do

- **Chat:** servers, DMs and group DMs, replies, edits, reactions, pins, threads and forum posts, search, unread and mention counts.
- **Media:** images, GIFs, stickers and custom emoji drawn inline as sixel graphics. Click one to see it full size. Videos open in mpv.
- **Composer:** `@`, `#` and `:emoji:` autocomplete. You can paste images from the clipboard, upload files with your own file manager, send GIFs from your favourites, and record voice messages.
- **Voice and video:**
  - calls in servers and DMs, with mute, deafen and per-person volume
  - turn your camera on and watch other people's cameras
  - pick your input and output devices
  - automatic or manual mic sensitivity, plus automatic gain
- **Screen sharing:** go live in a call, and watch other people's streams in the terminal or in an mpv window.
- **Login:** scan a QR code with the Discord app on your phone. No password needed.

## Requirements

- **Linux.** x86_64 and ARM (aarch64) both work, as do glibc and musl distros.
- **Python 3.10 or newer.**
- **A terminal with sixel graphics** if you want images inline: foot, WezTerm, Konsole, Contour, mlterm, or xterm started with `-ti vt340`. Other terminals still work, but images fall back to blocky previews.
- **For voice:** PipeWire (`pw-play` / `pw-record`), libopus and ffmpeg.
- **Optional:**
  - `chafa`: faster, nicer images
  - `mpv`: pop-out videos and streams
  - `wl-clipboard` or `xclip`: pasting images
  - `libnotify`: desktop notifications
  - `gpu-screen-recorder`: sharing your screen

The install script below sorts all of this out for you on most distros.

## Install

```sh
git clone https://github.com/epwv/uncord.git
cd uncord
./install.sh
```

Then run `uncord`.

**What the script does:**
- installs the system packages with your package manager (it asks first),
- puts uncord in its own Python environment in `~/.local/share/uncord`,
- adds a `uncord` command to `~/.local/bin` and an entry to your app menu,
- runs `uncord --doctor` to show what it found.

It knows these distros:

| Distro | Notes |
| --- | --- |
| Debian, Ubuntu, Mint, Pop!_OS, Raspberry Pi OS | |
| Fedora, Nobara, RHEL clones | ships `ffmpeg-free`. For watching H.264/H.265 streams, swap it for full ffmpeg from RPM Fusion |
| Arch, Manjaro, EndeavourOS, CachyOS, Garuda, Artix | |
| openSUSE Tumbleweed / Leap | full ffmpeg codecs need the Packman repo |
| Void (glibc and musl) | |
| Alpine | |
| Gentoo | prints the `emerge` line instead of running it |
| NixOS | pulls ffmpeg, opus and chafa from nixpkgs itself. Needs `programs.nix-ld.enable = true;` for the Python wheels |

Anything else: install the packages from the requirements list yourself, then run `./install.sh --no-system`.

Options:

```
./install.sh -y            don't ask, just install
./install.sh --no-system   skip system packages, only set up uncord
./install.sh --dry-run     show what it would do without changing anything
./install.sh --uninstall   remove uncord (your login and settings stay)
```

**Updating:** run `uncord --update`. It pulls the latest version from GitHub and reinstalls. uncord also tells you when an update is out (it checks at most twice a day). Installed from the AUR? Update it with your AUR helper instead.

### Manual install

If you'd rather do it yourself:

```sh
python3 -m venv ~/.local/share/uncord/venv
~/.local/share/uncord/venv/bin/pip install .
~/.local/share/uncord/venv/bin/uncord
```

## Logging in

The first time you run `uncord` it asks how you want to log in:

1. **QR code (recommended).** A QR code shows up in the terminal. Open the Discord app on your phone, go to Settings → Scan QR Code and approve it.
2. **Token.** Paste your account token if you already have it.

The token is saved to `~/.config/uncord/token`, readable only by you. `uncord --login` logs in again and `uncord --logout` forgets it. You can also pass it in with `DISCORD_TOKEN=...`.

## Getting around

Press **F1** inside uncord for every key. These are the ones you'll use most:

| Key | Does |
| --- | --- |
| Ctrl+K | jump to any chat, person or server |
| Ctrl+B | show or hide the sidebar (or click ☰ in the title bar) |
| Tab / Esc | move between the message box and the sidebar |
| Alt+↑ / Alt+↓ | previous / next channel |
| Ctrl+Alt+↑ / ↓ | previous / next server |
| ↑ in an empty message box | select messages: `r` reply, `e` edit, `d` delete, `+` react, `t` thread |
| Ctrl+G / Ctrl+E / Ctrl+T | GIFs, emoji, stickers |
| Ctrl+O | attach a file, paste an image, record a voice message |
| Ctrl+F | search (`from:`, `in:`, `has:`) |
| Ctrl+S | set your status |
| F5 / F6 / F7 / F8 | call or join, mute, deafen, leave |
| F9 | voice settings (devices, volume, sensitivity) |
| F10 | share your screen |
| F11 | watch someone's screen share or camera |
| F12 | turn your camera on or off |

Slash commands work in the message box too. `/help` lists them all.

## Voice and screen sharing

Click a voice channel to join it, or press F5 in a DM to call. The bar above the chat shows who's talking and has mute, deafen and leave buttons. Click someone's name to change their volume.

**Sharing your screen (F10)** needs `gpu-screen-recorder`. It encodes on your GPU (NVIDIA, AMD or Intel). If the GPU can't, newer versions fall back to the CPU.
- On GNOME or KDE under Wayland, pick "portal" and choose the screen in the desktop's dialog.
- Quality is 720p30 by default, or 1080p60 with Nitro. Change it with `/share 1080p30`, `/share 1440p60` and so on.
- If you installed gpu-screen-recorder as a Flatpak, point uncord at it:
  ```sh
  export UNCORD_GSR="flatpak run --command=gpu-screen-recorder com.dec05eba.gpu_screen_recorder"
  ```

**Your camera (F12):** turns your webcam on in the call, or use the 📷 Camera button. It needs `gpu-screen-recorder` like screen sharing does. If you have more than one camera it asks which, then remembers.

**Watching a stream or camera (F11):** when someone in your call goes live their name gets a red LIVE tag, and a 📷 when their camera is on. Press F11 or click the tag.
- The stream plays in the terminal. **+** and **-** make the picture bigger or smaller (it remembers), **f** toggles full size and **p** opens it in mpv at full quality and frame rate.
- **m** mutes the stream's sound, **esc** closes the viewer and **?** shows the rest of the keys.
- Streams in H.264, H.265, VP8, VP9 and AV1 all work.

## Files and settings

| What | Where |
| --- | --- |
| settings | `~/.config/uncord/config.json` |
| login token | `~/.config/uncord/token` |
| log | `~/.cache/uncord/uncord.log` |

Most settings change from inside the app, but you can edit the file while uncord is closed. Some useful ones:

| Setting | Default | |
| --- | --- | --- |
| `inline_media` | `true` | show images and GIFs in chat |
| `emoji_images` | `true` | draw custom emoji as images |
| `watch_fps` | `60` | highest frame rate for streams in the terminal (the picture shrinks a bit if your terminal can't keep up) |
| `share_quality` | depends on Nitro | `720p30`, `1080p30`, `1080p60` or `1440p60` |
| `sfx` | `true` | join and leave sounds in calls |
| `mark_read` | `true` | mark chats as read when you open them |

Environment variables, for when uncord can't find something on its own:

| Variable | For |
| --- | --- |
| `UNCORD_FFMPEG` | path to ffmpeg |
| `UNCORD_OPUS` | path to `libopus.so.0` |
| `UNCORD_CHAFA` | path to `libchafa.so.0` |
| `UNCORD_GSR` | the gpu-screen-recorder command |
| `UNCORD_THEME` | Textual theme name |
| `UNCORD_NOTIFY=0` | turn off desktop notifications |

## When something's off

Run `uncord --doctor` first. It lists everything uncord looks for and tells you what's missing.

- **No images, just coloured blocks:** your terminal doesn't do sixel. Try foot, WezTerm or Konsole.
- **Can't hear anyone, or nobody hears you:**
  - Check `pw-play` and `pw-record` exist.
  - Pick the right devices in F9. If you have more than one mic, the default might not be the one you talk into.
  - `/voicestats` shows what the audio is doing.
- **Screen share fails:** run `gpu-screen-recorder --list-monitors`. If that lists nothing, use the portal option or check your distro's gpu-screen-recorder notes about permissions.
- **Anything else:** look in `~/.cache/uncord/uncord.log`. Errors get logged there, and uncord keeps running instead of crashing.
- **Still stuck?** Ask in the [Discord server](https://discord.gg/fubfK8C6Cm) and paste the end of your log.

## Thanks

- [hakuxyz](https://github.com/hakuxyz) for helping track down screen sharing issues.

## Finding your way around the code

Everything is in `uncord/`:

| File | What's in it |
| --- | --- |
| `__main__.py` | the `uncord` command: login, `--doctor`, starting the app |
| `app.py` | the main window: layout, sidebar, chat view, message box, Discord events |
| `pickers.py`, `msgactions.py`, `attach.py`, `commands.py`, `calls.py` | features split out of the app: jump/threads/status, message actions, uploads, slash commands, voice and screen sharing |
| `messages.py`, `panels.py`, `screens.py`, `widgets.py` | widgets and popups |
| `voice.py` | voice calls: mic, speaker, receiving audio |
| `golive.py`, `rtpvideo.py` | screen sharing: sending, receiving and unpacking video |
| `streamview.py`, `sixelview.py`, `chafasixel.py` | drawing images and streams in the terminal |
| `fmt.py`, `media.py`, `emojis.py`, `gifs.py` | turning messages, media and emoji into something the terminal can show |
| `deps.py`, `config.py` | finding ffmpeg/opus/chafa and the settings file |

## Uninstalling

```sh
./install.sh --uninstall
```

This removes the program, the launcher and the menu entry. To also remove your login and settings, delete `~/.config/uncord`.
