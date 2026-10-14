<img src="assets/banner.png" alt="uncord, discord in your terminal">

# uncord

Discord in your terminal: servers, DMs, voice and video calls, images and GIFs, and screen sharing both ways. It logs in as your own account, so everything you see in the official app is here too.

Questions or ideas? Join the [uncord Discord server](https://discord.gg/fubfK8C6Cm).

> [!WARNING]
> Discord's terms don't allow third-party clients on user accounts. Plenty of people use them anyway, but your account could get flagged. Use it at your own risk.

## Contents

- [Features](#features)
- [Install](#install)
  - [Install script (most distros)](#install-script-most-distros)
  - [Nix](#nix)
  - [By hand](#by-hand)
- [Logging in](#logging-in)
- [Keys](#keys)
- [Voice, video and screen sharing](#voice-video-and-screen-sharing)
- [Settings and plugins](#settings-and-plugins)
- [Privacy and security](#privacy-and-security)
- [Troubleshooting](#troubleshooting)
- [For developers](#for-developers)
- [Todo](#todo)

## Features

- **Chat:** servers, DMs, group DMs, threads and forums. Replies, edits, reactions, pins, search, and unread and mention counts.
- **Looks like Discord:** markdown and highlighted code, embeds, server tags, blurple APP badges, 🖥 📱 🌐 device icons and a members list.
- **Images in the terminal:** pictures, GIFs, stickers and custom emoji. Sharpest in Kitty and Ghostty, sixel in foot, WezTerm, Konsole and others.
- **Voice and video calls:** per-person volume, automatic gain and mic sensitivity, and your camera on or other people's cameras in view.
- **Screen sharing:** go live with one key, or watch someone else's stream right in the terminal.
- **Extras built in:** an inbox for unreads and mentions, bot slash commands, voice messages, Rich Presence for your games, and 25 plugins such as MessageLogger and ClearURLs.
- **End-to-end encryption:** calls and streams use Discord's DAVE encryption, like the official app.
- **Fast:** the busiest code (image and video packet handling) is written in C.

## Install

uncord runs on Linux (x86_64 or ARM, glibc or musl) with Python 3.10 or newer.

### Install script (most distros)

```sh
git clone https://github.com/epwv/uncord.git
cd uncord
./install.sh
```

It installs the system packages with your package manager (it asks first), puts uncord in `~/.local/share/uncord`, adds an `uncord` command and an app menu entry, then runs `uncord --doctor`. It works on Debian, Ubuntu, Fedora, Arch, openSUSE, Void, Alpine and Gentoo.

| Flag | Does |
| --- | --- |
| `-y` | install without asking |
| `--no-system` | skip system packages |
| `--dry-run` | show what it would do |
| `--uninstall` | remove uncord (keeps your login and settings) |

Update with `uncord --update`. uncord also tells you when an update is out.

### Nix

```sh
nix run github:epwv/uncord              # try it
nix profile install github:epwv/uncord  # install it
```

uncord isn't in nixpkgs yet, so `nix-env -iA nixpkgs.uncord` won't find it. On NixOS, add the flake to your system: `inputs.uncord.url = "github:epwv/uncord";`, then add `uncord.packages.${system}.default` to your packages.

### By hand

```sh
python3 -m venv ~/.local/share/uncord/venv
~/.local/share/uncord/venv/bin/pip install .
~/.local/share/uncord/venv/bin/uncord
```

**What it needs:** a C compiler and the Python headers to build it, plus ffmpeg, libopus and PipeWire for voice. Optional extras: chafa (nicer images), mpv (pop-out video), wl-clipboard or xclip (pasting images), a notification daemon, and gpu-screen-recorder (screen sharing and camera).

## Logging in

The first time you run `uncord` it asks how to log in:

1. **QR code:** scan it with the Discord app on your phone (Settings → Scan QR Code). No password needed.
2. **Token:** paste one if you already have it.

Your login is saved in `~/.config/uncord/token`, readable only by you. `uncord --login` logs in again and `uncord --logout` forgets it.

## Keys

Press **F1** in uncord for the full list.

| Key | Does |
| --- | --- |
| Ctrl+K | jump to any chat, person or server |
| Tab / Esc | move between the message box and the sidebar |
| Alt+↑ / Alt+↓ | previous / next channel |
| ↑ in an empty message box | select messages: `r` reply, `e` edit, `d` delete, `+` react |
| Ctrl+G / Ctrl+E / Ctrl+T | GIFs, emoji, stickers |
| Ctrl+O | attach a file or record a voice message |
| Ctrl+F | search |
| Ctrl+B / F2 | sidebar / members list |
| F3 or ⚙ | settings |
| F4 | inbox |
| F5 / F6 / F7 / F8 | call, mute, deafen, leave |
| F10 / F11 / F12 | share screen, watch a stream, camera |

Slash commands work too. Type `/` to see them, including bot commands.

## Voice, video and screen sharing

Click a voice channel to join, or press F5 in a DM to call. Click someone's name in the call bar to change their volume. Devices, volume and mic sensitivity are in **Settings → Voice & video** (F9).

- **Share your screen (F10):** needs `gpu-screen-recorder`. On GNOME or KDE Wayland, pick "portal". Change the quality with `/share 1080p60`.
- **Camera (F12):** turns your webcam on in the call.
- **Watch (F11):** when someone goes live their name gets a red LIVE tag. In the viewer, `+`/`-` resize, `f` is full size, `p` opens it in mpv, `m` mutes and `esc` closes.

## Settings and plugins

Open settings with the ⚙ button by your name, F3 or `/settings`. Changes apply straight away.

| Tab | What's there |
| --- | --- |
| Appearance | theme, line spacing, images, layout, stream size |
| Notifications | desktop notifications, message previews |
| Chat | DM list size, translation language, file picker |
| Voice & video | devices, volumes, mic sensitivity, call sounds, screen share quality, camera |
| Privacy | link safety, Rich Presence, log out |
| Plugins | 25 Vencord-style extras, searchable |
| About | version, updates, what's installed |

Settings live in `~/.config/uncord/config.json` and the log in `~/.cache/uncord/uncord.log`.

## Privacy and security

- Messages, names and embeds can't send escape sequences to your terminal (things like clipboard writes or title changes). They're stripped before anything is drawn.
- Masked links show where they really go, and a ⚠ when the text claims a different site.
- Only http and https links open in your browser. You can have uncord ask first.
- Your token never goes into the log, and your config, token and log are readable only by you.
- The Rich Presence socket only accepts your own apps and drops oversized or malformed messages.

## Troubleshooting

Run `uncord --doctor` first. It shows what uncord found and what's missing.

- **Blocky images:** your terminal can't draw images. Try Kitty, Ghostty, foot, WezTerm or Konsole.
- **No sound or nobody hears you:** pick the right devices in Settings → Voice & video. `/voicestats` shows what the audio is doing.
- **Screen share fails:** check `gpu-screen-recorder --list-monitors`, or use the portal option.
- **Anything else:** check `~/.cache/uncord/uncord.log`, or ask in the [Discord server](https://discord.gg/fubfK8C6Cm).

If uncord can't find something, point it there: `UNCORD_FFMPEG`, `UNCORD_OPUS`, `UNCORD_CHAFA` or `UNCORD_GSR`.

## For developers

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python setup.py build_ext --inplace   # rebuild after editing _native.c
.venv/bin/python -m uncord
```

| Path | What's in it |
| --- | --- |
| `uncord/app.py` | main window, sidebar, chat and Discord events |
| `uncord/settings.py` | the settings tabs |
| `uncord/voice.py`, `golive.py`, `rtpvideo.py` | calls, screen sharing and video |
| `uncord/fmt.py`, `messages.py` | turning messages into terminal text |
| `uncord/_native.c` | the C part: image rows and video packets |
| `uncord/safety.py` | escape stripping, link checks, log scrubbing |
| `packaging/` | AUR and Nix packages |

Thanks to [hakuxyz](https://github.com/hakuxyz) for help tracking down screen sharing bugs.

## Todo

- [ ] Put `uncord` and `uncord-git` on the AUR (AUR sign-ups are down, the packages are ready in `packaging/aur/`)
- [ ] Get uncord into nixpkgs (once it has been around a while)

## License

MIT, see [LICENSE](LICENSE).
