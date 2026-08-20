#!/usr/bin/env bash
# DBDMap installer for Linux.
#
# Installs the native dependencies for your desktop, creates a virtualenv, and
# writes a ./dbdmap launcher. Run it from the folder it lives in:
#
#     ./install.sh
#
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m warn\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; exit 1; }

if [[ ${EUID} -eq 0 ]]; then
    die "Run this as your normal user, not with sudo. It will ask for sudo when it needs to."
fi

# ---------------------------------------------------------------------------
# Work out what this desktop needs
# ---------------------------------------------------------------------------
desktop="${XDG_CURRENT_DESKTOP:-}${XDG_SESSION_DESKTOP:-}"
desktop="${desktop,,}"
session="${XDG_SESSION_TYPE:-unknown}"

say "Detected: ${XDG_CURRENT_DESKTOP:-unknown} on ${session}"

capture_pkg=""
if [[ "$session" == "wayland" ]]; then
    case "$desktop" in
        *hyprland*|*sway*|*wayfire*|*river*|*niri*|*labwc*|*cosmic*) capture_pkg="grim" ;;
        *kde*|*plasma*) capture_pkg="spectacle" ;;
        *gnome*)        capture_pkg="gnome-screenshot" ;;
        *)              capture_pkg="grim" ;;
    esac
fi

# ---------------------------------------------------------------------------
# Native packages
# ---------------------------------------------------------------------------
install_packages() {
    local -a pkgs=("$@")
    # NB: a bare `[[ ... ]] && return 0` would abort the whole script under
    # `set -e` on the common path where there *are* packages to install.
    if [[ ${#pkgs[@]} -eq 0 ]]; then
        return 0
    fi

    if   command -v pacman  >/dev/null; then sudo pacman -S --needed --noconfirm "${pkgs[@]}"
    elif command -v apt-get >/dev/null; then sudo apt-get update && sudo apt-get install -y "${pkgs[@]}"
    elif command -v dnf     >/dev/null; then sudo dnf install -y "${pkgs[@]}"
    elif command -v zypper  >/dev/null; then sudo zypper install -y "${pkgs[@]}"
    else
        warn "Unknown package manager. Please install manually: ${pkgs[*]}"
        return 1
    fi
}

declare -a needed=()

if ! command -v tesseract >/dev/null; then
    if   command -v pacman  >/dev/null; then needed+=(tesseract tesseract-data-eng)
    elif command -v apt-get >/dev/null; then needed+=(tesseract-ocr)
    elif command -v dnf     >/dev/null; then needed+=(tesseract tesseract-langpack-eng)
    elif command -v zypper  >/dev/null; then needed+=(tesseract-ocr tesseract-ocr-traineddata-english)
    fi
fi

if [[ -n "$capture_pkg" ]] && ! command -v "$capture_pkg" >/dev/null; then
    needed+=("$capture_pkg")
fi

if ! command -v python3 >/dev/null; then
    needed+=(python3)
fi

if [[ ${#needed[@]} -gt 0 ]]; then
    say "Installing native packages: ${needed[*]}"
    install_packages "${needed[@]}" || warn "Continuing - install the missing packages yourself if DBDMap complains."
else
    say "Native dependencies already present."
fi

command -v tesseract >/dev/null || die "tesseract is still missing; DBDMap cannot do OCR without it."

# ---------------------------------------------------------------------------
# Python environment
# ---------------------------------------------------------------------------
# --system-site-packages lets distro builds of PyQt6/OpenCV satisfy the
# requirements. That matters on bleeding-edge Pythons where PyPI has no wheel
# yet and pip would otherwise try (and fail) to build from source.
say "Creating virtualenv in .venv"
python3 -m venv --system-site-packages .venv
./.venv/bin/python -m pip install --upgrade pip --quiet
say "Installing Python dependencies (this can take a minute)"
./.venv/bin/python -m pip install -r requirements.txt --quiet

# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------
install_dir="$(pwd)"
cat > dbdmap <<EOF
#!/usr/bin/env bash
# DBDMap launcher.
#
# sudo is required because the keyboard library reads /dev/input directly, and
# -E keeps WAYLAND_DISPLAY/DISPLAY/XDG_RUNTIME_DIR so the overlay and screen
# capture still work as root.
cd "$install_dir"
exec sudo -E "$install_dir/.venv/bin/python" "$install_dir/dbdmap.py" "\$@"
EOF
chmod +x dbdmap

say "Done."
echo
echo "  Start DBDMap with:   ./dbdmap"
echo "  Check your setup:    ./dbdmap --doctor"
echo
echo "The first run asks a few questions and writes config.ini."
