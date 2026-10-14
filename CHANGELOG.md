# Changelog

## 0.75h (2026-10-14)

- The installer adds `~/.local/bin` to your PATH (bash, zsh, fish or `~/.profile`) so `uncord` is found, and says how to start it right away. Uninstalling removes the line

## 0.75g (2026-10-14)

- Installing works again: versions with a letter (0.75c to 0.75f) weren't valid for pip. The title bar still shows the letter, and pip sees 0.75g as 0.75.7

## 0.75b to 0.75f (2026-10-14)

Installing from GitHub failed from 0.75c to 0.75f, fixed in 0.75g.


- MIT license
- Readme: Nix install with `nix profile install`, AUR and nixpkgs listed as todos
- Stable AUR package builds from the v0.75b release
- Changelog, CI, issue templates, contributing and security notes

## 0.75 (2026-10-14)

- The C part is required: install needs a C compiler and the Python headers, and uncord says how to fix it if it's missing

## 0.6 → 0.75 (2026-10-14)

- Settings is one screen with tabs (Appearance, Notifications, Chat, Voice & video, Privacy, Plugins, About), opened from ⚙, F3 or F9
- Voice settings are a settings tab, with the live mic meter
- C code for the busiest image and video paths (sixel scans, Kitty image rows, H.264 packets, RTP headers)
- Faster startup: libraries are looked up when first needed
- Security:
  - escape sequences in messages, names and embeds are stripped before they reach the terminal
  - only http and https links open, with an option to ask first
  - masked links show their real site, with a warning when the text names another one
  - tokens never reach the log, and the config, token and log are readable only by you
  - the Rich Presence socket only accepts your own apps and rejects oversized data
  - notification text can't inject markup or options
- APP, SYSTEM and WEBHOOK tags, your reactions and mentions of you use Discord blurple
- Device icons next to names in chat
- Members list shows online and offline counts
- Discord account notices show as a card instead of raw fields
- Nix flake (`nix run github:epwv/uncord`)

## 0.6 (2026-10-13)

- Settings screen (F3) with 25 plugins
- Server and channel menus: notifications, mute, nickname, invites, server info, leave
- Inbox (F4) for unreads and mentions
- Bot slash commands with autocomplete
- Rich Presence for games and apps
- Kitty graphics for images, GIFs, emoji and streams in Kitty and Ghostty
- Members list (F2), server tags, APP badges, device icons
- Embeds with colour bars, inline fields, footer and timestamp
- Highlighted code blocks, numbered lists, date lines and a new-messages marker
- Desktop notifications over D-Bus

## 0.5 (2026-10-13)

- First public release: servers, DMs, threads, voice and video calls, inline images and GIFs, screen sharing both ways, QR login
