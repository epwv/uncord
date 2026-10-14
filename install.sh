#!/bin/sh
# uncord installer
# ./install.sh            install or update
# ./install.sh --uninstall
set -eu

here=$(cd "$(dirname "$0")" && pwd)
dataDir="${XDG_DATA_HOME:-$HOME/.local/share}/uncord"
binDir="${UNCORD_BIN_DIR:-$HOME/.local/bin}"
appDir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
venv="$dataDir/venv"
launcher="$binDir/uncord"
pathMark="# added by uncord install.sh"
desktop="$appDir/uncord.desktop"

yes=0
system=1
dryRun=0
action=install

usage() {
    cat <<EOF
usage: ./install.sh [options]

  -y, --yes        don't ask before installing system packages
  --no-system      skip system packages (ffmpeg, opus, pipewire...)
  --dry-run        show what would happen and change nothing
  --uninstall      remove uncord (keeps your login and settings)
  -h, --help       this text

installs to $dataDir
launcher goes in $binDir (override with UNCORD_BIN_DIR)
EOF
}

for arg in "$@"; do
    case "$arg" in
        -y|--yes) yes=1 ;;
        --no-system) system=0 ;;
        --dry-run) dryRun=1 ;;
        --uninstall) action=uninstall ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $arg" >&2; usage >&2; exit 2 ;;
    esac
done

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
run() {
    if [ "$dryRun" = 1 ]; then
        echo "   would run: $*"
    else
        "$@"
    fi
}

uninstall() {
    say "removing uncord"
    run rm -rf "$venv" "$dataDir/nix" "$dataDir/source"
    if [ -f "$launcher" ] && grep -q "uncord install.sh" "$launcher"; then
        run rm -f "$launcher"
    fi
    if [ -f "$desktop" ] && grep -q "X-Uncord-Installer=true" "$desktop"; then
        run rm -f "$desktop"
    fi
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
        if grep -qs "$pathMark" "$rc"; then
            say "removing the PATH line from $rc"
            if [ "$dryRun" = 0 ]; then
                grep -v "$pathMark" "$rc" > "$rc.uncord-tmp" && cat "$rc.uncord-tmp" > "$rc" && rm -f "$rc.uncord-tmp"
            fi
        fi
    done
    say "done. your login and settings are still in ${XDG_CONFIG_HOME:-$HOME/.config}/uncord"
}

[ "$(uname -s)" = Linux ] || die "uncord only runs on linux"

if [ "$action" = uninstall ]; then
    uninstall
    exit 0
fi

# which distro
distro=unknown
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    for id in ${ID:-} ${ID_LIKE:-}; do
        case "$id" in
            nixos) distro=nixos; break ;;
            debian|ubuntu|linuxmint|pop|raspbian) distro=debian; break ;;
            fedora|rhel|centos|rocky|almalinux|nobara) distro=fedora; break ;;
            arch|manjaro|endeavouros|cachyos|garuda|artix) distro=arch; break ;;
            opensuse*|suse|sles) distro=suse; break ;;
            void) distro=void; break ;;
            alpine) distro=alpine; break ;;
            gentoo|funtoo) distro=gentoo; break ;;
        esac
    done
fi
say "distro: ${PRETTY_NAME:-$distro} ($distro)"

# packages per distro: needed, then nice to have (installed one by one so a missing one doesn't stop the rest)
case "$distro" in
    debian)
        need="python3 python3-venv python3-pip python3-dev gcc git ffmpeg libopus0 pipewire-bin"
        extra="chafa mpv wl-clipboard xclip libnotify-bin gpu-screen-recorder"
        pm="apt-get install -y" ;;
    fedora)
        need="python3 python3-pip python3-devel gcc git ffmpeg-free opus pipewire-utils"
        extra="chafa mpv wl-clipboard xclip libnotify"
        pm="dnf install -y" ;;
    arch)
        need="python python-pip gcc git ffmpeg opus pipewire"
        extra="chafa mpv wl-clipboard xclip libnotify gpu-screen-recorder"
        pm="pacman -S --needed --noconfirm" ;;
    suse)
        need="python3 python3-pip python3-devel gcc git ffmpeg libopus0 pipewire-tools"
        extra="chafa mpv wl-clipboard xclip libnotify-tools gpu-screen-recorder"
        pm="zypper --non-interactive install" ;;
    void)
        need="python3 python3-pip python3-devel gcc git ffmpeg opus pipewire"
        extra="chafa mpv wl-clipboard xclip libnotify gpu-screen-recorder"
        pm="xbps-install -Sy" ;;
    alpine)
        need="python3 py3-pip python3-dev gcc musl-dev git ffmpeg opus pipewire-tools"
        extra="chafa mpv wl-clipboard xclip libnotify gpu-screen-recorder"
        pm="apk add" ;;
    gentoo)
        need="dev-lang/python sys-devel/gcc dev-vcs/git media-video/ffmpeg media-libs/opus media-video/pipewire"
        extra="media-gfx/chafa media-video/mpv gui-apps/wl-clipboard x11-misc/xclip x11-libs/libnotify"
        pm="" ;;
    *)
        need=""; extra=""; pm="" ;;
esac

asRoot() {
    if [ "$(id -u)" = 0 ]; then
        run "$@"
    elif command -v sudo >/dev/null 2>&1; then
        run sudo "$@"
    elif command -v doas >/dev/null 2>&1; then
        run doas "$@"
    else
        die "need root to install packages (no sudo or doas found). rerun with --no-system after installing: $need"
    fi
}

installSystem() {
    if [ "$distro" = nixos ]; then
        return
    fi
    if [ -z "$pm" ]; then
        warn "don't know this distro's package manager. install these yourself if you can:"
        echo "   python 3.10+ with venv, pip and headers, a C compiler, git, ffmpeg, opus, pipewire (pw-play/pw-record)"
        echo "   optional: chafa, mpv, wl-clipboard or xclip, libnotify, gpu-screen-recorder"
        if [ "$distro" = gentoo ]; then
            echo "   emerge --ask --noreplace $need $extra"
        fi
        return
    fi
    say "system packages: $need"
    say "optional: $extra"
    if [ "$yes" = 0 ] && [ "$dryRun" = 0 ] && [ -t 0 ]; then
        printf 'install them now? [Y/n] '
        read -r answer || answer=n
        case "$answer" in n|N|no) warn "skipping system packages"; return ;; esac
    fi
    case "$distro" in
        debian) asRoot apt-get update ;;
        void) asRoot xbps-install -Sy xbps ;;
    esac
    # shellcheck disable=SC2086
    asRoot $pm $need
    for p in $extra; do
        # shellcheck disable=SC2086
        asRoot $pm "$p" || warn "couldn't install $p (optional)"
    done
}

# nixos: no global libs, so keep our own gc roots and point uncord at them
nixEnv=""
setupNix() {
    command -v nix-build >/dev/null 2>&1 || command -v nix >/dev/null 2>&1 || return 0
    say "fetching ffmpeg, opus and chafa from nixpkgs"
    run mkdir -p "$dataDir/nix"
    for p in libopus chafa.out ffmpeg-headless; do
        if [ "$dryRun" = 1 ]; then
            echo "   would build nixpkgs.$p"
            continue
        fi
        nix-build '<nixpkgs>' -A "$p" -o "$dataDir/nix/$p" >/dev/null 2>&1 \
            || nix build "nixpkgs#$p" -o "$dataDir/nix/$p" 2>/dev/null \
            || warn "couldn't get $p from nixpkgs"
    done
    lib() {
        for f in "$dataDir"/nix/"$1"*/lib/"$2"; do
            if [ -e "$f" ]; then echo "$f"; return; fi
        done
    }
    nixEnv="export UNCORD_OPUS=\"$(lib libopus libopus.so.0)\" UNCORD_CHAFA=\"$(lib chafa libchafa.so.0)\""
    for f in "$dataDir"/nix/ffmpeg-headless*/bin/ffmpeg; do
        if [ -e "$f" ]; then nixEnv="$nixEnv UNCORD_FFMPEG=\"$f\""; fi
    done
    if [ ! -e /lib64/ld-linux-x86-64.so.2 ] && [ ! -e /lib/ld-linux-aarch64.so.1 ]; then
        warn "python wheels need nix-ld on nixos: add 'programs.nix-ld.enable = true;' to your config"
    fi
}

# python
findPython() {
    for py in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
            echo "$py"
            return
        fi
    done
}

if [ "$system" = 1 ]; then
    installSystem
fi
if [ "$distro" = nixos ]; then
    setupNix
fi

python=$(findPython)
[ -n "$python" ] || die "uncord needs python 3.10 or newer"
say "python: $($python --version 2>&1)"
command -v git >/dev/null 2>&1 || die "git is needed to fetch discord.py-self"
"$python" -c 'import venv, ensurepip' 2>/dev/null || die "python venv/pip missing (debian/ubuntu: apt install python3-venv)"

say "setting up $venv"
run mkdir -p "$dataDir"
[ -d "$venv" ] || run "$python" -m venv "$venv"
run "$venv/bin/python" -m pip install --quiet --upgrade pip
say "installing uncord and its python packages (takes a minute the first time)"
run "$venv/bin/python" -m pip install --quiet --upgrade "$here"
#uncord --update pulls here
[ "$dryRun" = 1 ] || printf '%s\n' "$here" > "$dataDir/source"

say "launcher: $launcher"
run mkdir -p "$binDir"
if [ "$dryRun" = 0 ]; then
    cat > "$launcher" <<EOF
#!/bin/sh
# made by uncord install.sh
${nixEnv}
exec "$venv/bin/uncord" "\$@"
EOF
    chmod +x "$launcher"
fi

if [ ! -e "$desktop" ]; then
    say "menu entry: $desktop"
    if [ "$dryRun" = 0 ]; then
        mkdir -p "$appDir"
        cat > "$desktop" <<EOF
[Desktop Entry]
Type=Application
Name=uncord
GenericName=Discord client
Comment=Discord in your terminal
Exec=$launcher
Icon=internet-chat
Terminal=true
Categories=Network;Chat;InstantMessaging;
Keywords=discord;chat;voice;
X-Uncord-Installer=true
EOF
    fi
fi

# put the launcher dir on PATH for new terminals, debian and others only add ~/.local/bin at login if it already existed
onPath=1
case ":$PATH:" in
    *":$binDir:"*) ;;
    *) onPath=0 ;;
esac
if [ "$onPath" = 0 ]; then
    case "${SHELL##*/}" in
        zsh) rc="$HOME/.zshrc" ;;
        bash) rc="$HOME/.bashrc" ;;
        fish) rc="" ;;
        *) rc="$HOME/.profile" ;;
    esac
    if [ -z "$rc" ]; then
        say "adding $binDir to your fish PATH"
        run fish -c "fish_add_path -U '$binDir'"
    elif ! grep -qs "$pathMark" "$rc"; then
        say "adding $binDir to your PATH in $rc"
        if [ "$dryRun" = 0 ]; then
            # shellcheck disable=SC2016 # $PATH is meant to stay literal in the rc file
            printf '\nexport PATH="%s:$PATH"  %s\n' "$binDir" "$pathMark" >> "$rc"
        fi
    fi
fi

if [ "$dryRun" = 0 ]; then
    echo
    "$launcher" --doctor || true
    echo
    if [ "$onPath" = 1 ]; then
        say "all set. run: uncord"
    else
        say "all set. open a new terminal and run: uncord"
        say "or start it right now with: $launcher"
    fi
fi
